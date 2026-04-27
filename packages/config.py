from functools import lru_cache
import os

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

    openai_api_key: str = os.getenv("OPENAI_API_KEY")
    model_agent1: str = "gpt-4.1-nano"
    model_agent4: str = "gpt-4.1-mini"

    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60 * 24 * 7  # 7 days

    # base64url-encoded 32-byte key; generate with: python -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    token_encryption_key: str = ""

    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_env: str = "sandbox"
    ebay_redirect_uri: str = "http://localhost:8000/auth/ebay/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
