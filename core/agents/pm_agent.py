from __future__ import annotations

from pydantic import BaseModel, Field

from config.settings import Settings
from core.llm import completion_structured

SYSTEM_ZH = """你是资深产品经理。把下面的痛点整理成清晰的用户故事（User Story）。
输出合法 JSON：
- user_story: string，格式建议「作为…我希望…以便…」
- persona: string，一句话目标用户画像
不要输出 markdown 代码块。"""

SYSTEM_EN = """You are a senior product manager. Organize the given pain point into a clear User Story.
Output valid JSON:
- user_story: string, format suggestion "As a [user], I want to [goal], so that [benefit]"
- persona: string, one-sentence target user persona
Do not output markdown code blocks."""


class PMResult(BaseModel):
    user_story: str = ""
    persona: str = ""


def run_pm(
    settings: Settings,
    *,
    title: str,
    url: str,
    extracted_complaint: str,
    summary: str,
) -> PMResult:
    lang = getattr(settings, "output_language", "zh")
    system = SYSTEM_EN if lang == "en" else SYSTEM_ZH
    if lang == "en":
        user = f"""Post title: {title}
URL: {url}
Summary: {summary}
Core complaint: {extracted_complaint}
"""
    else:
        user = f"""原帖标题：{title}
链接：{url}
提取摘要：{summary}
核心抱怨：{extracted_complaint}
"""
    return completion_structured(
        settings,
        system=system,
        user=user,
        response_model=PMResult,
        temperature=0.2,
    )
