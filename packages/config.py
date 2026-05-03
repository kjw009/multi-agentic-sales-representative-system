import os
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
    redis_url: str = "redis://localhost:6379/0"  # used for short-lived caching (OAuth state); use ElastiCache in prod

    sqs_queue_url: str = ""
    sqs_region: str = "us-east-1"
    internal_api_key: str = ""  # shared secret for EventBridge Scheduler → /internal/* endpoints

    eventbridge_bus_name: str = ""  # empty = log events locally instead of sending to EventBridge
    aws_region: str = "eu-west-2"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "salesrep-images"
    s3_region: str = "us-east-1"

    openai_api_key: str = ""
    openai_base_url: str = ""
    model_agent1: str = "gpt-5-nano-1"
    model_agent2: str = "gpt-4.1-mini"  # relevance filter LLM for pricing comparables
    model_agent4: str = "gpt-4.1-mini"

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # base64url-encoded 32-byte key; generate with: python -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    token_encryption_key: str = ""

    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_env: str = "production"
    ebay_ru_name: str = ""  # RuName from eBay Developer Portal (used as redirect_uri in OAuth requests)
    ebay_redirect_uri: str = ""  # actual callback URL (registered under the RuName)
    ebay_marketplace_id: str = "EBAY_GB"

    # Browse API credentials — can point at production even while OAuth uses sandbox
    # (sandbox Browse index is sparse; production gives real comparable data)
    ebay_browse_env: str = "production"
    ebay_browse_client_id: str = ""  # falls back to ebay_client_id if empty
    ebay_browse_client_secret: str = ""  # falls back to ebay_client_secret if empty

    # ── LangSmith tracing ──────────────────────────────────────────────────
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "salesrep"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def configure_tracing() -> None:
    """Push LangSmith env vars so the SDK picks them up globally.

    Call this once at process startup (API, Celery worker) *before* any
    LangGraph graph is compiled or invoked.
    """
    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return

    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
