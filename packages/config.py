import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration class. Pydantic automatically reads environment variables
    and matches them to these attributes (e.g., APP_ENV in .env maps to app_env).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Don't crash if extra variables are in the .env
        case_sensitive=False,
    )

    # General environment
    app_env: str = "development"
    log_level: str = "INFO"

    # Database (Postgres)
    database_url: str = "postgresql+asyncpg://salesrep:salesrep@localhost:5432/salesrep"  # Uses asyncpg for asynchronous database operations.

    # Redis (for short-lived caching, e.g. OAuth state)
    redis_url: str = "redis://localhost:6379/0"  # use ElastiCache in production

    # AWS SQS (for asynchronous job processing)
    sqs_queue_url: str = ""  # URL of the SQS queue for asynchronous processing
    sqs_region: str = "us-east-1"
    internal_api_key: str = ""  # shared secret for EventBridge Scheduler → /internal/* endpoints

    eventbridge_bus_name: str = ""  # empty = log events locally instead of sending to EventBridge
    aws_region: str = "eu-west-2"

    # AWS S3 (for storing item images)
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

    # eBay API credentials
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_env: str = "production"
    ebay_ru_name: str = (
        ""  # RuName from eBay Developer Portal (used as redirect_uri in OAuth requests)
    )
    ebay_redirect_uri: str = ""  # actual callback URL (registered under the RuName)
    ebay_marketplace_id: str = "EBAY_GB"
    ebay_verification_token: str = ""  # for Event Notification API endpoint validation
    ebay_webhook_endpoint: str = ""  # public URL where the webhook will be hosted
    frontend_base_url: str = "http://localhost:3000"  # override in production

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
    """
    This is a performance optimization.
    Creating a Pydantic object and reading the disk for a .env file is "expensive" in terms of CPU time.
    By using @lru_cache, the settings are loaded once the first time the function is called.
    Every subsequent call returns the exact same object from memory instantly.
    """
    return Settings()


settings = get_settings()


def configure_tracing() -> None:
    """
    Injects LangSmith configuration into the system environment.
    This must be called at the very beginning of the application lifecycle
    so that the LangChain/LangGraph SDKs detect the environment variables.

    Push LangSmith env vars so the SDK picks them up globally.

    Call this once at process startup (API, Celery worker) *before* any
    LangGraph graph is compiled or invoked.
    """
    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return

    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
