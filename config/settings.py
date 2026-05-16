from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM ──
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # ── API 鉴权 ──
    ideahunter_api_key: str = ""

    # ── 存储路径 ──
    ideahunter_sqlite_path: str = "data/ideahunter.db"
    output_dir: str = "output"

    # ── 筛选阈值 ──
    score_approve_min: int = 65     # Critic 评分通过阈值
    min_text_chars: int = 12        # 帖子最短有效字符数

    # ── 定时调度 ──
    schedule_enabled: bool = False
    schedule_cron: str = "0 9 * * *"
    schedule_sources: list[str] = ["v2ex"]

    # ── 第三方 API Key ──
    serper_api_key: str = ""        # Serper.dev（小红书/知乎/竞品搜索）
    twitter_bearer_token: str = ""  # Twitter/X API v2

    # ── 关键词配置 ──
    keyword_pool: list[str] = []
    keywords_per_run: int = 3

    # ── 多跳采集 ──
    multi_hop_enabled: bool = False
    multi_hop_max_comments: int = 3

    # ── Hot + New 双轨 ──
    hot_mode_enabled: bool = False
    hn_hot_min_points: int = 30

    # ── 输出语言 ──
    output_language: str = "zh"  # "zh" = 中文, "en" = English


@lru_cache
def get_settings() -> Settings:
    return Settings()
