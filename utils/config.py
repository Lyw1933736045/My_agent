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
    JUDGE_API_KEY: str = Field("", description="评测 Judge API Key，空则回退 QUERY_ENGINE_API_KEY")
    JUDGE_BASE_URL: Optional[str] = Field(None, description="评测 Judge Base URL")
    JUDGE_MODEL_NAME: str = Field(
        "deepseek-v4-flash",
        description="简报生成用 QUERY_ENGINE_MODEL_NAME；覆盖评测默认 deepseek-v4-flash",
    )
    JUDGE_USE_QUERY_ENGINE_API: bool = Field(
        False,
        description="true 时评测走 QUERY_ENGINE 的 key/base_url，但仍用 JUDGE_MODEL_NAME",
    )
    JUDGE_LLM_REQUEST_TIMEOUT: float = 300.0
    EVAL_JUDGE_RUNS: int = Field(3, description="单份简报每段独立打分次数，再取平均")
    EVAL_JUDGE_TEMPERATURE: float = Field(0.7, description="重复打分时的采样温度，避免三次完全相同")
    TAVILY_API_KEY: str = Field("", description="Tavily API Key（仅搜索流程需要）")
    DATABASE_URL: str = Field(
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/my_agent",
        description="项目唯一 PostgreSQL 连接，例如 postgresql+psycopg://user:pass@host/db",
    )
    DASHSCOPE_API_KEY: str = Field("", description="阿里云百炼 API Key（Embedding / Rerank）")
    EMBEDDING_API_KEY: str = Field("", description="Embedding API Key，空则回退 DASHSCOPE_API_KEY")
    EMBEDDING_BASE_URL: str = Field(
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="Qwen Embedding OpenAI 兼容地址",
    )
    EMBEDDING_MODEL: str = Field("qwen3.7-text-embedding")
    EMBEDDING_DIMENSION: int = Field(
        1024,
        description="qwen3.7-text-embedding 默认维度；换维度需要新的 Alembic migration",
    )
    RERANKER_API_KEY: str = Field("", description="Rerank API Key，空则回退 DASHSCOPE_API_KEY")
    RERANKER_BASE_URL: str = Field(
        "https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
        description="Qwen Rerank HTTP 地址，与 Embedding endpoint 不同",
    )
    RERANKER_MODEL: str = Field("qwen3-rerank")
    RAG_VECTOR_TOP_K: int = Field(30)
    RAG_CASE_TOP_K: int = Field(20)
    RAG_GLOBAL_TOP_K: int = Field(15)
    RAG_RERANK_TOP_N: int = Field(8)

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
