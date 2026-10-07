"""Runtime configuration read from the environment. Secrets never live in the repository."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QD_", extra="ignore")

    # Connection string of the application role (qd_app), which cannot bypass row-level security.
    database_url: str = "postgresql://qd_app@localhost:5432/qarz"
    # Token of this environment's bot: verifies Telegram signatures and sends messages.
    bot_token: str = ""
    # Secret Telegram sends with every webhook call.
    webhook_secret: str = ""
    # Where files are kept (ADR-020): "filesystem" (development, tests) or "s3" (production). Empty means
    # no store is configured and every file is refused.
    file_store: str = ""
    # Directory of the filesystem store.
    file_root: str = ""
    # The S3-compatible store: scheme://host[:port], bucket, signing region and credentials.
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    # Bearer token the monitoring system sends to read /metrics. Empty: the endpoint is not served.
    metrics_token: str = ""
    # Online payment of the subscription. Empty until provider contracts exist; even when set, the
    # platform switch `online_pay_on` decides, and it is off unless an administrator turns it on.
    payme_merchant_id: str = ""
    payme_secret_key: str = ""
    click_service_id: str = ""
    click_merchant_id: str = ""
    click_secret_key: str = ""
    # API rate limits for signed-in callers: a steady rate per minute and the burst allowed above it.
    rate_user_per_minute: int = 120
    rate_user_burst: int = 60
    rate_shop_per_minute: int = 600
    rate_shop_burst: int = 200
