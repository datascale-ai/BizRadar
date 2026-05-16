from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from config.settings import Settings
from core.llm import completion_structured

SYSTEM_ZH = """你是毒舌投资人 + 技术合伙人，对痛点商业价值做严格评审。
请按「黄金三定律」打分，**三项子分之和 = score 总分**：

1. 高频重复性 freq_score（0-35分）：日常是否反复发生？大量手工/重复劳动？
   - 35分：每天发生，无法忍受 | 10分：偶发性，可以接受
2. 平台缝隙/大厂免疫 gap_score（0-35分）：大厂是否不便做/不会做？
   - 35分：因平台割裂/规模限制，大厂天然不做 | 10分：大厂随时可抄
3. 商业闭环 roi_score（0-30分）：用户愿意付费吗？成本回收路径清晰吗？
   - 30分：用户明确愿意付钱，竞品稀少 | 5分：免费替代品已存在

【竞品参考】：
- 若用户信息中提供了真实竞品搜索结果，**优先据此判断竞品成熟度**，并直接影响 roi_score
- 若竞品成熟度高（Notion/Trello/Jira 级别），roi_score 强制 ≤10，score 压至 40 以下
- 若搜索结果显示竞品稀少或均为弱竞品，可适当拉高 roi_score

【技术可行性参考】：
- 若用户信息中提供了「技术评估」，参考其中的 feasibility_score 与 dev_weeks
- feasibility_score < 50：说明该方案技术壁垒极高或存在平台违规风险，freq_score 强制扣减 10 分
- dev_weeks > 12：说明独立开发者难以快速验证，gap_score 酌情扣减 5 分
- 这些惩罚项已体现在你的子分中，无需额外说明

【评分校准锚点】：
- 90+：需求爆炸、大厂空白、用户极度痛苦、付费意愿强
- 75-89：痛点明确，市场有空间，但有竞品或频率稍低
- 55-74：痛点真实但规模有限，或竞争已较激烈
- 35-54：用户抱怨但多有免费替代，商业闭环模糊
- 0-34：无商业价值，或已有成熟解决方案

【严格要求】：
- freq_score + gap_score + roi_score 必须严格等于 score
- 不得将大多数项目打在 70-80 区间，超过 85 分须在 reasoning 中说明具体优势
- 不要为了"中庸"而打 72/78 这种"安全分"；逼迫自己做出判断

输出合法 JSON（不含 markdown 代码块），字段：
- score: number，0-100 整数（= freq_score + gap_score + roi_score）
- freq_score: number，0-35 整数
- gap_score: number，0-35 整数
- roi_score: number，0-30 整数
- reasoning: string，中文理由，须说明三项各得多少分及原因
- competitors_note: string，基于搜索结果的竞品判断，无真实搜索则写推测"""

SYSTEM_EN = """You are a ruthless investor + technical co-founder conducting strict business value reviews of pain points.
Score based on the "Golden Three Laws" — the sum of the three sub-scores must equal the total score:

1. High-Frequency Recurrence freq_score (0-35): Does this happen repeatedly every day? Involves lots of manual or repetitive work?
   - 35: Happens daily, unbearable | 10: Occasional, tolerable
2. Platform Gap / Big-Tech Immunity gap_score (0-35): Is this inconvenient or unlikely for big tech to address?
   - 35: Structurally impossible for big tech due to platform fragmentation or scale | 10: Big tech can copy at any time
3. Business Loop roi_score (0-30): Are users willing to pay? Is the monetization path clear?
   - 30: Users clearly willing to pay, few competitors | 5: Free alternatives already exist

[Competitor Reference]:
- If real competitor search results are provided, prioritize them to judge competitor maturity and directly influence roi_score
- If competitors are mature (Notion/Trello/Jira level), force roi_score ≤10, push total score below 40
- If search results show scarce or weak competitors, you may increase roi_score

[Technical Feasibility Reference]:
- If a technical assessment is provided, reference its feasibility_score and dev_weeks
- feasibility_score < 50: very high technical barrier or platform violation risk; force freq_score down by 10
- dev_weeks > 12: hard for indie devs to validate quickly; reduce gap_score by 5 as appropriate

[Scoring Calibration Anchors]:
- 90+: Explosive demand, big-tech vacuum, extreme user pain, strong payment intent
- 75-89: Clear pain point, market room, but some competitors or lower frequency
- 55-74: Real pain but limited scale, or competition already notable
- 35-54: Users complain but free alternatives exist, monetization unclear
- 0-34: No commercial value, or mature solutions already exist

[Strict Requirements]:
- freq_score + gap_score + roi_score must strictly equal score
- Do not cluster most projects in the 70-80 range; scores above 85 require specific justification in reasoning
- Do not give "safe" scores like 72/78; force yourself to make a judgment

Output valid JSON (no markdown code blocks):
- score: integer 0-100 (= freq_score + gap_score + roi_score)
- freq_score: integer 0-35
- gap_score: integer 0-35
- roi_score: integer 0-30
- reasoning: string, reasoning in English explaining each sub-score
- competitors_note: string, competitor judgment based on search results, or inference if no real search"""


class CriticResult(BaseModel):
    score: int = Field(default=0, ge=0, le=100)
    freq_score: int = Field(default=0, ge=0, le=35)   # 高频重复性
    gap_score: int = Field(default=0, ge=0, le=35)    # 平台缝隙/大厂免疫
    roi_score: int = Field(default=0, ge=0, le=30)    # 商业闭环
    reasoning: str = ""
    competitors_note: str = ""

    @field_validator("score", "freq_score", "gap_score", "roi_score", mode="before")
    @classmethod
    def _coerce_int(cls, v: object) -> int:
        if isinstance(v, bool):
            return 0
        if isinstance(v, float):
            return int(round(v))
        if isinstance(v, str):
            try:
                return int(round(float(v.strip())))
            except ValueError:
                return 0
        try:
            return int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    def model_post_init(self, __context: object) -> None:
        """若 LLM 子分之和与 score 出入过大，以子分之和为准修正 score。"""
        sub_sum = self.freq_score + self.gap_score + self.roi_score
        if sub_sum > 0 and abs(sub_sum - self.score) > 3:
            object.__setattr__(self, "score", max(0, min(100, sub_sum)))


def run_critic(
    settings: Settings,
    *,
    user_story: str,
    persona: str,
    title: str,
    url: str,
    summary: str = "",
    competitor_context: str = "",
    tech_context: str = "",
) -> CriticResult:
    lang = getattr(settings, "output_language", "zh")
    system = SYSTEM_EN if lang == "en" else SYSTEM_ZH
    if lang == "en":
        comp_section = (
            f"\n\n{competitor_context}"
            if competitor_context
            else "\n\n[Competitor Search]: Not executed (SERPER_API_KEY not configured). Please infer from existing knowledge."
        )
        tech_section = f"\n\n{tech_context}" if tech_context else ""
        user = f"""Post title: {title}
URL: {url}
Pain point summary: {summary}
User story: {user_story}
User persona: {persona}{comp_section}{tech_section}
"""
    else:
        comp_section = (
            f"\n\n{competitor_context}"
            if competitor_context
            else "\n\n【竞品搜索】：未执行（SERPER_API_KEY 未配置），请凭已有知识推测。"
        )
        tech_section = f"\n\n{tech_context}" if tech_context else ""
        user = f"""原帖标题：{title}
链接：{url}
痛点摘要（一句话核心）：{summary}
用户故事：{user_story}
用户画像：{persona}{comp_section}{tech_section}
"""
    return completion_structured(
        settings,
        system=system,
        user=user,
        response_model=CriticResult,
        temperature=0.5,
    )
