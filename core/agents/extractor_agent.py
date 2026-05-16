from __future__ import annotations

from pydantic import BaseModel, Field

from config.settings import Settings
from core.llm import completion_structured
from plugins.base_scraper import RawItem

SYSTEM_ZH = """你是 IdeaHunter 的「痛点提取」助手。只根据给定帖子判断是否存在可做成小工具/SaaS 的「抱怨、痛点、效率浪费」。
输出必须是合法 JSON 对象，字段：
- has_pain_point: boolean
- summary: string，一句话概括（无痛点可为空）
- extracted_complaint: string，提炼出的核心抱怨原文风格复述（无痛点可为空）
不要输出 markdown 代码块。"""

SYSTEM_EN = """You are the "Pain Point Extractor" for IdeaHunter. Based only on the given post, determine whether it contains a complaint, pain point, or efficiency waste that could be turned into a small tool or SaaS product.
Output must be a valid JSON object with fields:
- has_pain_point: boolean
- summary: string, one-sentence summary (empty if no pain point)
- extracted_complaint: string, a faithful rephrasing of the core complaint in the user's own voice (empty if no pain point)
Do not output markdown code blocks."""


class ExtractorResult(BaseModel):
    has_pain_point: bool = Field(default=False)
    summary: str = ""
    extracted_complaint: str = ""


def run_extractor(settings: Settings, item: RawItem) -> ExtractorResult:
    lang = getattr(settings, "output_language", "zh")
    system = SYSTEM_EN if lang == "en" else SYSTEM_ZH
    if lang == "en":
        user = f"""Title: {item.title}
URL: {item.url}
Source: {item.source}
Content:
{item.body}
"""
    else:
        user = f"""标题：{item.title}
链接：{item.url}
来源：{item.source}
正文：
{item.body}
"""
    return completion_structured(
        settings,
        system=system,
        user=user,
        response_model=ExtractorResult,
        temperature=0.1,
    )
