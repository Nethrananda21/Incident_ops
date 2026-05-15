from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "IncidentOps AI API"
    app_version: str = "1.0.0"
    app_env: str = "development"
    log_level: str = "INFO"
    cors_allowed_origins: str = "http://localhost:8081,http://127.0.0.1:8081"
    trusted_hosts: str = "*"
    max_request_body_bytes: int = Field(default=64_000, ge=1024, le=2_000_000)
    route_timeout_seconds: float = Field(default=45.0, gt=0, le=300)

    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_llm_model: str = "meta/llama-3.3-70b-instruct"
    llm_max_tokens: int = Field(default=700, ge=64, le=4096)
    llm_temperature: float = Field(default=0.1, ge=0, le=2)
    llm_timeout_seconds: float = Field(default=12.0, gt=0, le=120)
    llm_max_retries: int = Field(default=1, ge=0, le=3)
    llm_max_input_chars: int = Field(default=8000, ge=500, le=50000)
    llm_max_context_chars: int = Field(default=12000, ge=1000, le=60000)
    llm_max_context_items: int = Field(default=5, ge=1, le=20)

    clickhouse_host: str = "localhost"
    clickhouse_port: int = Field(default=8123, ge=1, le=65535)
    clickhouse_database: str = "incident_ai"
    clickhouse_user: str = "default"
    clickhouse_password: str = "clickhouse"
    clickhouse_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    clickhouse_send_receive_timeout_seconds: float = Field(default=30.0, gt=0, le=300)

    privacy_shield_url: str = "http://localhost:8080"
    privacy_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_local_files_only: bool = True
    embedding_dim: int = Field(default=384, ge=16, le=4096)
    routing_confidence_threshold: float = Field(default=0.85, ge=0, le=1)
    fast_path_similarity_threshold: float = Field(default=0.95, ge=0, le=1)
    rag_similarity_threshold: float = Field(default=0.70, ge=0, le=1)
    rag_top_k: int = Field(default=5, ge=1, le=50)
    approved_knowledge_sources: str = "6StringNinja/synthetic-servicenow-incidents,curated,historical,seed"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("app_env", "log_level")
    @classmethod
    def normalize_names(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("nvidia_base_url", "privacy_shield_url")
    @classmethod
    def require_http_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("must be an http(s) URL")
        return value

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "Settings":
        if self.fast_path_similarity_threshold < self.rag_similarity_threshold:
            raise ValueError("FAST_PATH_SIMILARITY_THRESHOLD must be >= RAG_SIMILARITY_THRESHOLD")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return split_csv(self.cors_allowed_origins)

    @property
    def trusted_host_patterns(self) -> list[str]:
        return split_csv(self.trusted_hosts) or ["*"]

    @property
    def approved_knowledge_source_values(self) -> tuple[str, ...]:
        return tuple(split_csv(self.approved_knowledge_sources)) or ("__none__",)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
