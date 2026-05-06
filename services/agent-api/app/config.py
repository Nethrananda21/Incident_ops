from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_llm_model: str = "meta/llama-3.3-70b-instruct"
    llm_max_tokens: int = 700
    llm_temperature: float = 0.1
    llm_timeout_seconds: float = 12.0

    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_database: str = "incident_ai"
    clickhouse_user: str = "default"
    clickhouse_password: str = "clickhouse"

    privacy_shield_url: str = "http://localhost:8080"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384
    routing_confidence_threshold: float = 0.85
    fast_path_similarity_threshold: float = 0.95
    rag_similarity_threshold: float = 0.70
    rag_top_k: int = 5

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
