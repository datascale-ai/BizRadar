"""
techlead_agent.py — 技术合伙人 Agent

评估痛点解决方案的技术可行性，估算 MVP 开发周期，识别关键技术风险。
在 PM Agent 之后、Critic Agent 之前执行，结果注入 Critic 的上下文中，
防止技术不可行或开发成本过高的点子浪费后续评审资源。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from config.settings import Settings
from core.llm import completion_structured

SYSTEM_ZH = """你是一个务实的技术合伙人（独立开发者 CTO 视角）。
基于给定的用户痛点和初步用户故事，评估以 1-2 人独立开发团队实现 MVP 的技术可行性。

【评估维度】：

1. 开发周期 dev_weeks（整数工作周）：
   - 1-2周：纯前端/简单脚本，无复杂后端
   - 3-6周：标准 CRUD + 第三方 API 集成
   - 7-12周：复杂集成、多平台适配、需要爬虫或特殊 API
   - 12+周：需训练模型、大规模数据工程、或复杂硬件

2. 技术可行性 feasibility_score（0-100 整数）：
   - 90+：完全可用现成 API/SDK 拼接，零技术壁垒
   - 70-89：标准开发栈，有 1-2 个技术挑战但路径明确
   - 50-69：关键依赖有不确定性（如需逆向封闭 API、强反爬平台）
   - 30-49：主要依赖不稳定（第三方限流、平台政策风险高）
   - 0-29：几乎不可行（需训练底层大模型、严重违反平台条款、需要硬件）

3. 主要技术风险 tech_risk：一句话描述最关键障碍（聚焦在"做不出"或"做了被封"）

4. MVP 路径建议 mvp_approach：1-2 句话，最低成本、最快验证的实现路径

【重要原则】：
- 严格区分"难做"和"不可做"，不要因为复杂就判为不可行
- 微信内部 API、Apple 审核、TikTok 反爬 等属于高风险，须在 tech_risk 中点明
- 优先推荐 API 拼接而非自研，减少估算偏差

输出合法 JSON（不含 markdown 代码块）：
- dev_weeks: number，正整数
- feasibility_score: number，0-100 整数
- tech_risk: string，最关键技术风险一句话
- mvp_approach: string，最低成本实现路径"""

SYSTEM_EN = """You are a pragmatic technical co-founder (indie developer CTO perspective).
Based on the given pain point and initial user story, assess the technical feasibility of building an MVP with a 1-2 person indie team.

[Assessment Dimensions]:

1. dev_weeks (integer work weeks):
   - 1-2 weeks: Pure frontend / simple scripts, no complex backend
   - 3-6 weeks: Standard CRUD + third-party API integration
   - 7-12 weeks: Complex integrations, multi-platform adaptation, scraping or special APIs
   - 12+ weeks: Model training, large-scale data engineering, or complex hardware

2. feasibility_score (integer 0-100):
   - 90+: Fully achievable by composing existing APIs/SDKs, zero technical barrier
   - 70-89: Standard dev stack, 1-2 technical challenges but path is clear
   - 50-69: Key dependencies have uncertainty (e.g., reverse-engineering closed APIs, heavy anti-scraping)
   - 30-49: Major dependencies are unstable (rate limits, high platform policy risk)
   - 0-29: Nearly infeasible (requires training a base LLM, serious ToS violations, hardware required)

3. tech_risk: One sentence describing the most critical obstacle (focus on "can't build" or "will get banned")

4. mvp_approach: 1-2 sentences, the lowest-cost, fastest validation implementation path

[Key Principles]:
- Strictly distinguish "hard to build" from "impossible to build"
- WeChat internal APIs, Apple Review, TikTok anti-scraping are high-risk — flag them explicitly
- Prefer API composition over custom development to reduce estimation errors

Output valid JSON (no markdown code blocks):
- dev_weeks: number, positive integer
- feasibility_score: number, integer 0-100
- tech_risk: string, one sentence on the most critical technical risk
- mvp_approach: string, lowest-cost implementation path"""


class TechLeadResult(BaseModel):
    dev_weeks: int = Field(default=4, ge=1)
    feasibility_score: int = Field(default=70, ge=0, le=100)
    tech_risk: str = ""
    mvp_approach: str = ""

    @field_validator("dev_weeks", "feasibility_score", mode="before")
    @classmethod
    def _coerce_int(cls, v: object) -> int:
        try:
            return max(1, int(round(float(str(v)))))
        except (ValueError, TypeError):
            return 4


def run_techlead(
    settings: Settings,
    *,
    title: str,
    user_story: str,
    persona: str,
    summary: str = "",
) -> TechLeadResult:
    lang = getattr(settings, "output_language", "zh")
    system = SYSTEM_EN if lang == "en" else SYSTEM_ZH
    if lang == "en":
        user = f"""Post title: {title}
Pain point summary: {summary}
User story: {user_story}
User persona: {persona}
"""
    else:
        user = f"""原帖标题：{title}
痛点摘要：{summary}
用户故事：{user_story}
用户画像：{persona}
"""
    return completion_structured(
        settings,
        system=system,
        user=user,
        response_model=TechLeadResult,
        temperature=0.3,
    )
