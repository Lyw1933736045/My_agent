"""独立 Agent 配置，仅从环境变量或本目录 .env 加载。"""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    QUERY_ENGINE_API_KEY: str = Field(..., description="OpenAI 兼容 LLM API Key")
    QUERY_ENGINE_BASE_URL: Optional[str] = None
    QUERY_ENGINE_MODEL_NAME: str = Field(..., description="LLM 模型名")
    TAVILY_API_KEY: str = Field("", description="Tavily API Key（仅搜索流程需要）")

    SEARCH_CONTENT_MAX_LENGTH: int = 20000
    MAX_REFLECTIONS: int = 1
    MAX_PARAGRAPHS: int = 4
    MAX_SEARCH_RESULTS: int = 7
    OUTPUT_DIR: str = "reports"
    SAVE_INTERMEDIATE_STATES: bool = True
    LLM_REQUEST_TIMEOUT: float = 300.0
    WEB_REQUEST_TIMEOUT: float = 30.0
    WEB_MAX_CONTENT_BYTES: int = 5_000_000
    WEB_MAX_TEXT_LENGTH: int = 100_000
    WEB_USER_AGENT: str = "FinancialFactResearch/0.1"

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )
