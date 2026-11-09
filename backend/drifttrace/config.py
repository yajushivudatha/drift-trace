"""Runtime configuration. Everything deployment-specific comes from the environment or `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql+psycopg://drifttrace:drifttrace@localhost:5432/drifttrace"
    redis_url: str = "redis://localhost:6379/0"
    grobid_url: str = "http://localhost:8070"
    data_dir: Path = Path("./data")
    cors_origins: str = "http://localhost:3000"

    # LLM. The model has no default on purpose: it must be chosen in `.env`.
    anthropic_api_key: str = ""
    llm_model: str = ""
    llm_effort_extract: str = "low"
    llm_effort_rerank: str = "low"
    llm_effort_judge: str = "high"
    llm_max_tokens: int = 16000
    llm_max_retries: int = 3
    llm_refusal_fallback: bool = True
    llm_price_input_per_mtok: float = 0.0
    llm_price_output_per_mtok: float = 0.0
    judge_double_check: bool = False

    # External APIs
    contact_email: str = ""
    semantic_scholar_api_key: str = ""
    openalex_api_key: str = ""

    # Embeddings
    embedding_model: str = "pritamdeka/S-PubMedBert-MS-MARCO"
    embedding_dim: int = 768

    @property
    def upload_dir(self) -> Path:
        path = self.data_dir / "pdfs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def llm_configured(self) -> bool:
        return bool(self.anthropic_api_key and self.llm_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
