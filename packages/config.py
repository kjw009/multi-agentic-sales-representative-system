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
    sns_enabled: bool = False

    # Database (Postgres)
    database_url: str = "postgresql+asyncpg://salesrep:salesrep@localhost:5432/salesrep"  # Uses asyncpg for asynchronous database operations.

    # AWS SQS (for asynchronous job processing)
    sqs_queue_url: str = ""  # URL of the SQS queue for asynchronous processing
    sqs_region: str = "us-east-1"
    internal_api_key: str = ""  # shared secret for EventBridge Scheduler → /internal/* endpoints

    eventbridge_bus_name: str = ""  # empty = log events locally instead of sending to EventBridge
    aws_region: str = "eu-west-2"

    # AWS S3 (for storing item images)
    # Leave s3_endpoint_url empty in prod so boto3 hits real AWS S3.
    # s3_public_base_url is the externally reachable URL base eBay (and buyers)
    # will hit — e.g. https://salesrep-images.s3.eu-west-2.amazonaws.com
    # or a CloudFront domain. If empty, falls back to {s3_endpoint_url}/{bucket},
    # which is correct for local MinIO only.
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "salesrep-images"
    s3_region: str = "us-east-1"
    s3_public_base_url: str = ""

    openai_api_key: str = ""
    openai_base_url: str = ""
    model_agent1: str = "gpt-4.1-mini"
    model_agent2: str = "gpt-4.1-mini"  # relevance filter LLM for pricing comparables
    model_agent3: str = "gpt-4.1-mini"  # eBay item-specifics inference for the publisher
    model_agent4: str = "gpt-4.1"
    model_intake_vision: str = "gpt-4.1-mini"

    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # base64url-encoded 32-byte key; generate with: python -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    token_encryption_key: str = ""

    # eBay API credentials
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    # DevID is only required for legacy Trading API SOAP NotificationSignature
    # verification (buyer-message Platform Notifications). Find it in the eBay
    # Developer Portal alongside AppID/CertID.
    ebay_dev_id: str = ""
    ebay_env: str = "production"
    ebay_ru_name: str = (
        ""  # RuName from eBay Developer Portal (used as redirect_uri in OAuth requests)
    )
    ebay_redirect_uri: str = ""  # actual callback URL (registered under the RuName)
    ebay_marketplace_id: str = "EBAY_GB"
    ebay_verification_token: str = ""  # for Event Notification API endpoint validation
    ebay_webhook_endpoint: str = ""  # public URL where the webhook will be hosted
    skip_webhook_hmac: bool = False  # bypass HMAC validation in dev/test
    frontend_base_url: str = "http://localhost:3000"  # override in production

    # Comma-separated list of origins allowed to make CORS requests against
    # the API. Set this to your Vercel domain (and localhost for dev) so the
    # browser can call the API directly instead of going through the Next.js
    # rewrite proxy (which has its own short timeout).
    cors_allowed_origins: str = "http://localhost:3000"

    # Browse API credentials — can point at production even while OAuth uses sandbox
    # (sandbox Browse index is sparse; production gives real comparable data)
    ebay_browse_env: str = "production"
    ebay_browse_client_id: str = ""  # falls back to ebay_client_id if empty
    ebay_browse_client_secret: str = ""  # falls back to ebay_client_secret if empty

    # ── LangSmith tracing ──────────────────────────────────────────────────
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "salesrep"

    # ── Agent 2 — dynamic floor and negotiating posture ───────────────────
    pricing_risk_multiplier_lambda: float = 2.0
    pricing_volatility_threshold: float = 0.15  # fraction of recommended_price
    pricing_confidence_threshold: float = 0.60

    # ── Stripe billing ─────────────────────────────────────────────────────
    billing_enabled: bool = False
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id_pro: str = ""  # price_xxx from Stripe dashboard
    stripe_publishable_key: str = ""


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

    Call this once at process startup (API, SQS worker) *before* any
    LangGraph graph is compiled or invoked.
    """
    if not settings.langsmith_tracing or not settings.langsmith_api_key:
        return

    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
