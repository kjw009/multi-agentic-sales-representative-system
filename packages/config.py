from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://salesrep:salesrep@localhost:5432/salesrep"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "salesrep-images"
    s3_region: str = "us-east-1"

    anthropic_api_key: str = ""

    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_env: str = "sandbox"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
