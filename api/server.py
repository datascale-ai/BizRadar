from __future__ import annotations

import asyncio
import json
import logging
import queue as _syncq
import sys
import uuid
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 把 .env 真正注入 os.environ，使插件里的 os.getenv() 能读到所有配置
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_ROOT / ".env", override=False)
except ImportError:
    pass  # python-dotenv 未安装时跳过，不影响主流程

from config.settings import Settings, get_settings
from core import event_bus
from core.logging_config import setup_logging
from core.memory.sqlite_manager import SqliteManager
from core.orchestrator import run_ingested_contents, run_pipeline
from core.scheduler import start_scheduler

logger = logging.getLogger(__name__)

_scheduler = None  # will hold BackgroundScheduler when schedule_enabled

API_PREFIX = "/api/v1"


def ok(data: Any = None, msg: str = "success", code: int = 200) -> dict[str, Any]:
    return {"code": code, "msg": msg, "data": data}


def require_auth(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[Optional[str], Header()] = None,
) -> None:
    key = (settings.ideahunter_api_key or "").strip()
    if not key:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": 40101, "msg": "Unauthorized"})
    token = authorization[7:].strip()
    if token != key:
        raise HTTPException(status_code=401, detail={"code": 40101, "msg": "Unauthorized"})


def get_db(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[SqliteManager]:
    db = SqliteManager(settings.ideahunter_sqlite_path)
    try:
        yield db
    finally:
        db.close()


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="IdeaHunter API", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(
    RateLimitExceeded,
    lambda req, exc: JSONResponse(
        status_code=429,
        content={"code": 42901, "msg": "Too Many Requests: 触发频率超限", "data": None},
    ),
)

# 挂载静态资源与 Web UI
_WEB_DIR = _ROOT / "web"
_ASSETS_DIR = _ROOT / "assets"

if _ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

if _WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(_WEB_DIR)), name="web")


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    """根路径重定向到 Web UI 大盘。"""
    index = _WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return FileResponse.__new__(FileResponse)  # fallback


@app.get(f"{API_PREFIX}/sources", summary="获取所有已注册数据源")
def list_sources_endpoint():
    """返回所有已注册数据源的名称列表，供前端动态渲染下拉选项。"""
    from plugins.registry import list_sources
    sources = list_sources()
    # 去掉 alias（xiaohongshu 是 xhs 的别名）
    display = [s for s in sources if s != "xiaohongshu"]
    return {"sources": display}


class ScanBody(BaseModel):
    source: str = Field(..., description="数据源插件名，如 v2ex")
    keywords: Optional[list[str]] = None
    max_items: Optional[int] = Field(default=None, ge=1, le=500)
    language: str = Field(default="zh", pattern="^(zh|en)$", description="Output language: zh or en")


class IngestBody(BaseModel):
    source_name: str
    content_list: list[str] = Field(..., min_length=1, max_length=100)
    keywords: Optional[list[str]] = None
    language: str = Field(default="zh", pattern="^(zh|en)$", description="Output language: zh or en")


@app.on_event("startup")
def _startup() -> None:
    global _scheduler
    setup_logging()
    settings = get_settings()
    if settings.schedule_enabled:
        _scheduler = start_scheduler(settings)
        logger.info("Background scheduler started")


@app.on_event("shutdown")
def _shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped")
        _scheduler = None


@app.post(f"{API_PREFIX}/tasks/scan")
@limiter.limit("10/minute")
def post_scan(
    request: Request,
    body: ScanBody,
    background_tasks: BackgroundTasks,
    _: Annotated[None, Depends(require_auth)],
    db: Annotated[SqliteManager, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    # 提前校验数据源，避免后台任务才报错
    from core.orchestrator import get_scraper
    try:
        get_scraper(body.source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": 40001, "msg": str(e)})

    task_id = f"tsk_{uuid.uuid4().hex[:16]}"
    db.create_task(task_id)
    event_bus.create(task_id)   # SSE event queue

    def job() -> None:
        ldb = SqliteManager(settings.ideahunter_sqlite_path)
        try:
            ldb.update_task(
                task_id,
                status="processing",
                progress="Starting scan...",
                result_count=0,
            )

            def prog(m: str) -> None:
                ldb.update_task(task_id, progress=m)

            def emit(e: dict) -> None:
                event_bus.push(task_id, e)

            # 按请求语言覆盖 settings（不影响全局单例）
            req_settings = settings.model_copy(update={"output_language": body.language})

            stats = run_pipeline(
                source=body.source,
                mode="daily_report",
                max_items=body.max_items,
                keywords=body.keywords,
                settings=req_settings,
                db=ldb,
                output_dir=settings.output_dir,
                on_progress=prog,
                on_event=emit,
                is_cancelled=lambda: ldb.get_task(task_id).get("status") == "cancelled" if ldb.get_task(task_id) else False,
            )
            # 检查是否因为取消而结束
            current_status = ldb.get_task(task_id).get("status") if ldb.get_task(task_id) else ""
            if current_status == "cancelled":
                return
            ldb.update_task(
                task_id,
                status="completed",
                progress="completed",
                result_count=stats.approved,
            )
        except Exception as e:
            event_bus.push(task_id, {"type": "pipeline_error", "msg": str(e)[:200]})
            ldb.update_task(
                task_id,
                status="failed",
                progress="failed",
                error=str(e)[:2000],
            )
        finally:
            event_bus.close(task_id)   # sentinel -> closes SSE
            ldb.close()

    background_tasks.add_task(job)
    return ok({"task_id": task_id, "status": "pending"}, msg="Task created successfully")


@app.get(f"{API_PREFIX}/tasks/{{task_id}}/stream", summary="SSE real-time stream")
async def stream_task_events(task_id: str):
    """Server-Sent Events stream for real-time scan progress. Use EventSource in browser."""
    async def generator() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps({'type': 'ping'}, ensure_ascii=False)}\n\n"
        q = event_bus.get_queue(task_id)
        if q is None:
            yield f"data: {json.dumps({'type': 'error', 'msg': 'no stream'}, ensure_ascii=False)}\n\n"
            return
        loop = asyncio.get_event_loop()
        while True:
            try:
                event = await loop.run_in_executor(None, lambda: q.get(timeout=30))
                if event is None:   # sentinel = pipeline done
                    yield f"data: {json.dumps({'type': '__close__'}, ensure_ascii=False)}\n\n"
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except _syncq.Empty:
                yield ": keepalive\n\n"
            except Exception:
                break

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post(f"{API_PREFIX}/tasks/scan-all", summary="一键扫描所有数据源")
@limiter.limit("3/minute")
def post_scan_all(
    request: Request,
    background_tasks: BackgroundTasks,
    _: Annotated[None, Depends(require_auth)],
    db: Annotated[SqliteManager, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """依次扫描所有已注册且已配置的数据源，通过 SSE 流推送实时进度。"""
    import os
    from plugins.registry import list_sources

    # 过滤掉别名和未配置 Key 的数据源
    all_sources = [s for s in list_sources() if s != "xiaohongshu"]
    skip = set()
    if not os.getenv("TWITTER_BEARER_TOKEN", "").strip():
        skip.add("twitter")
    if not settings.serper_api_key:
        # xhs/zhihu 无 serper key 会返回空，跳过以节省时间
        skip.add("xhs")
        skip.add("zhihu")
    active_sources = [s for s in all_sources if s not in skip]

    task_id = f"tsk_{uuid.uuid4().hex[:16]}"
    db.create_task(task_id)
    event_bus.create(task_id)

    def emit(e: dict) -> None:
        event_bus.push(task_id, e)

    def all_job() -> None:
        ldb = SqliteManager(settings.ideahunter_sqlite_path)
        total_approved = 0
        try:
            ldb.update_task(task_id, status="processing",
                            progress=f"全源扫描启动，共 {len(active_sources)} 个数据源",
                            result_count=0)
            emit({"type": "scan_all_start",
                  "sources": active_sources,
                  "count": len(active_sources)})

            for idx, source in enumerate(active_sources):
                if ldb.get_task(task_id).get("status") == "cancelled":
                    break
                
                emit({"type": "source_start",
                      "source": source,
                      "index": idx + 1,
                      "total": len(active_sources)})
                ldb.update_task(task_id,
                                progress=f"[{idx+1}/{len(active_sources)}] 扫描 {source}…")
                try:
                    def tagged_emit(e: dict, src: str = source) -> None:
                        emit({**e, "source": src})

                    stats = run_pipeline(
                        source=source,
                        mode="daily_report",
                        settings=settings,
                        db=ldb,
                        output_dir=settings.output_dir,
                        on_progress=lambda m, s=source: ldb.update_task(
                            task_id, progress=f"[{s}] {m}"
                        ),
                        on_event=tagged_emit,
                        is_cancelled=lambda: ldb.get_task(task_id).get("status") == "cancelled" if ldb.get_task(task_id) else False,
                    )
                    total_approved += stats.approved
                    emit({"type": "source_done",
                          "source": source,
                          "approved": stats.approved,
                          "fetched": stats.fetched,
                          "rejected": stats.rejected})
                except Exception as exc:
                    emit({"type": "source_error",
                          "source": source,
                          "msg": str(exc)[:150]})
                    logger.warning("全源扫描 [%s] 出错: %s", source, exc)

            current_status = ldb.get_task(task_id).get("status") if ldb.get_task(task_id) else ""
            if current_status != "cancelled":
                ldb.update_task(task_id, status="completed",
                                progress="全源扫描完成",
                                result_count=total_approved)
            emit({"type": "scan_all_done",
                  "total_approved": total_approved,
                  "sources_count": len(active_sources)})
        except Exception as exc:
            emit({"type": "pipeline_error", "msg": str(exc)[:200]})
            ldb.update_task(task_id, status="failed",
                            progress="失败", error=str(exc)[:2000])
        finally:
            event_bus.close(task_id)
            ldb.close()

    background_tasks.add_task(all_job)
    return ok({
        "task_id": task_id,
        "sources": active_sources,
        "skipped": list(skip),
        "count": len(active_sources),
    }, msg="全源扫描任务已创建")


@app.post(f"{API_PREFIX}/webhooks/ingest")
@limiter.limit("30/minute")
def post_ingest(
    request: Request,
    body: IngestBody,
    background_tasks: BackgroundTasks,
    _: Annotated[None, Depends(require_auth)],
    db: Annotated[SqliteManager, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    task_id = f"tsk_{uuid.uuid4().hex[:16]}"
    db.create_task(task_id)

    def job() -> None:
        ldb = SqliteManager(settings.ideahunter_sqlite_path)
        try:
            ldb.update_task(task_id, status="processing", progress="Ingesting…")
            req_settings = settings.model_copy(update={"output_language": body.language})
            stats = run_ingested_contents(
                source_name=body.source_name,
                content_list=body.content_list,
                keywords=body.keywords,
                settings=req_settings,
                db=ldb,
                output_dir=settings.output_dir,
                on_progress=lambda m: ldb.update_task(task_id, progress=m),
                is_cancelled=lambda: ldb.get_task(task_id).get("status") == "cancelled" if ldb.get_task(task_id) else False,
            )
            current_status = ldb.get_task(task_id).get("status") if ldb.get_task(task_id) else ""
            if current_status == "cancelled":
                return
            ldb.update_task(
                task_id,
                status="completed",
                progress="completed",
                result_count=stats.approved,
            )
        except Exception as e:
            ldb.update_task(
                task_id,
                status="failed",
                progress="failed",
                error=str(e)[:2000],
            )
        finally:
            ldb.close()

    background_tasks.add_task(job)
    return ok({"task_id": task_id, "status": "pending"}, msg="Task created successfully")


@app.post(f"{API_PREFIX}/tasks/{{task_id}}/cancel", summary="取消/停止正在运行的任务")
def cancel_task(
    task_id: str,
    _: Annotated[None, Depends(require_auth)],
    db: Annotated[SqliteManager, Depends(get_db)],
) -> dict[str, Any]:
    row = db.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40401, "msg": "Task Not Found"})
    if row["status"] in ["completed", "failed", "cancelled"]:
        return ok({"status": row["status"]}, msg="Task already finished or cancelled")
    
    db.update_task(task_id, status="cancelled", progress="Cancelling...")
    return ok({"task_id": task_id, "status": "cancelled"}, msg="Task cancellation requested")


@app.get(f"{API_PREFIX}/tasks/{{task_id}}")
def get_task(
    task_id: str,
    _: Annotated[None, Depends(require_auth)],
    db: Annotated[SqliteManager, Depends(get_db)],
) -> dict[str, Any]:
    row = db.get_task(task_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": 40401, "msg": "Not Found"})
    return ok(
        {
            "task_id": row["task_id"],
            "status": row["status"],
            "progress": row["progress"],
            "result_count": row["result_count"],
            "error": row["error"],
        }
    )


@app.get(f"{API_PREFIX}/ideas")
def list_ideas(
    _: Annotated[None, Depends(require_auth)],
    db: Annotated[SqliteManager, Depends(get_db)],
    min_score: int = Query(default=0, ge=0, le=100),
    source: Optional[str] = None,
    search: Optional[str] = Query(default=None, description="行业关键词全文搜索，匹配 title/target_audience/markdown_prd"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    total, items = db.list_ideas(min_score=min_score, source=source, search=search, page=page, size=size)
    return ok(
        {
            "total": total,
            "items": [
                {
                    "idea_id": it.idea_id,
                    "title": it.title,
                    "score": it.score,
                    "source": it.source,
                    "created_at": it.created_at,
                }
                for it in items
            ],
        }
    )


@app.get(f"{API_PREFIX}/ideas/{{idea_id}}")
def get_idea(
    idea_id: str,
    _: Annotated[None, Depends(require_auth)],
    db: Annotated[SqliteManager, Depends(get_db)],
) -> dict[str, Any]:
    it = db.get_idea(idea_id)
    if it is None:
        raise HTTPException(status_code=404, detail={"code": 40401, "msg": "Not Found"})
    return ok(
        {
            "idea_id": it.idea_id,
            "title": it.title,
            "score": it.score,
            "source": it.source,
            "raw_complaints_analyzed": it.raw_complaints_analyzed,
            "markdown_prd": it.markdown_prd,
            "tech_stack": it.tech_stack,
            "target_audience": it.target_audience,
        }
    )


@app.get(f"{API_PREFIX}/processed-items")
def list_processed_items(
    _: Annotated[None, Depends(require_auth)],
    db: Annotated[SqliteManager, Depends(get_db)],
    source: Optional[str] = None,
    outcome: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    total, items = db.list_processed_items(
        source=source,
        outcome=outcome,
        page=page,
        size=size,
    )
    return ok(
        {
            "total": total,
            "items": [
                {
                    "source": it.source,
                    "external_id": it.external_id,
                    "outcome": it.outcome,
                    "title": it.title,
                    "url": it.url,
                    "details": it.details,
                    "created_at": it.created_at,
                    "updated_at": it.updated_at,
                }
                for it in items
            ],
        }
    )



@app.get(f"{API_PREFIX}/scheduler/jobs")
def list_scheduler_jobs(
    _: Annotated[None, Depends(require_auth)],
) -> dict[str, Any]:
    """列出当前所有活跃的调度任务（仅在 SCHEDULE_ENABLED=true 时有内容）。"""
    if _scheduler is None:
        return ok({"enabled": False, "jobs": []})
    jobs = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "next_run_time": next_run.isoformat() if next_run else None,
                "trigger": str(job.trigger),
            }
        )
    return ok({"enabled": True, "jobs": jobs})


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "scheduler_running": _scheduler is not None and _scheduler.running,
    }

