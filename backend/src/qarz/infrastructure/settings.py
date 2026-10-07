"""Runtime configuration read from the environment. Secrets never live in the repository."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QD_", extra="ignore")

    # Connection string of the application role (qd_app), which cannot bypass row-level security.
    database_url: str = "postgresql://qd_app@localhost:5432/qarz"
    # Token of this environment's bot: verifies Telegram signatures and sends messages.
    bot_token: str = ""
    # Secret Telegram sends with every webhook call.
    webhook_secret: str = ""
    # Telegram identifiers of the people who may be administrators (ADR-017), separated by commas.
    # Empty means nobody: the administrator's API is then not served at all.
    admin_tg_ids: str = Field(default="", repr=False)
    # Key that encrypts secrets stored in the database (the administrators' second-factor secrets):
    # 32 random bytes, base64. Without it the administrator's API is not served.
    secrets_key: str = Field(default="", repr=False)
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
    # The longest one SQL statement may run, in milliseconds, before the database cancels it. Set by
    # the application on its own connections (not on the role), so migrations are not limited.
    # The API's limit bounds what one request can cost every other shop; the worker's jobs read
    # across shops and get longer. 0 means no limit.
    statement_timeout_ms: int = 5000
    worker_statement_timeout_ms: int = 60000

    def admin_allow_list(self) -> frozenset[int]:
        """The allow-list as numbers. Anything that is not a positive whole number refuses to start."""
        ids: set[int] = set()
        for part in self.admin_tg_ids.split(","):
            raw = part.strip()
            if not raw:
                continue
            if not (raw.isascii() and raw.isdigit()) or int(raw) <= 0:
                raise ValueError("QD_ADMIN_TG_IDS must be Telegram user identifiers separated by commas")
            ids.add(int(raw))
        return frozenset(ids)
