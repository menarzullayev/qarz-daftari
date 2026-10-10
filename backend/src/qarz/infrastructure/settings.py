"""Runtime configuration read from the environment. Secrets never live in the repository."""

import re

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QD_", extra="ignore")

    # Connection string of the application role (qd_app), which cannot bypass row-level security.
    database_url: str = "postgresql://qd_app@localhost:5432/qarz"
    # Connection string of the administrators' role (qd_admin): the administrators' side of the API and
    # the command that rotates their second-factor secrets. Required wherever that side is served.
    admin_database_url: str = Field(default="", repr=False)
    # Connection string of the worker's role (qd_worker): the worker and the measurement command.
    worker_database_url: str = Field(default="", repr=False)
    # Token of this environment's bot: verifies Telegram signatures and sends messages.
    bot_token: str = ""
    # Secret Telegram sends with every webhook call.
    webhook_secret: str = ""
    # Telegram identifiers of the people who may be administrators (ADR-017), separated by commas.
    # Empty means nobody: the administrator's API is then not served at all.
    admin_tg_ids: str = Field(default="", repr=False)
    # The administrators' second factor (ADR-017): "required" (the default) or "off". Off, an administrator
    # who has a confirmed second factor is asked for no code anywhere: the allow-list and the Telegram
    # sign-in are then the only controls on the administrators' side, sensitive settings included. Stored
    # secrets are left as they are, so "required" brings the codes back with no new enrolment. A person
    # who never confirmed a second factor still enrols once. Any other value refuses to start.
    admin_second_factor: str = "required"
    # The public host administrators' passkeys are made for (a passkey belongs to one site). Empty: no
    # passkey can be registered or used. A bare host name: no scheme, no port, no path.
    passkey_host: str = ""
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
    # Server secret from which purpose-specific keys are derived: the key that signs links to stored
    # files, and the key that encrypts the administrators' second-factor secrets. Empty: no link is given,
    # no file is served, and the administrator's API is not served.
    secrets_key: str = Field(default="", repr=False)
    # The server secret that was in use before the current one, set only while a rotation is under way
    # (runbook 4). File links signed with it are honoured until they expire, and second-factor secrets
    # still encrypted with it can be read; nothing new is signed or encrypted with it. Empty otherwise.
    secrets_key_previous: str = Field(default="", repr=False)
    # Bearer token the monitoring system sends to read /metrics. Empty: the endpoint is not served.
    metrics_token: str = ""
    # Online payment of the subscription. Empty until provider contracts exist; even when set, the
    # platform switch `online_pay_on` decides, and it is off unless an administrator turns it on.
    payme_merchant_id: str = ""
    payme_secret_key: str = ""
    click_service_id: str = ""
    click_merchant_id: str = ""
    click_secret_key: str = ""
    # SMS through Eskiz (eskiz.uz), read by the worker only: the account's e-mail and password, and the
    # sender name registered for it ("4546" until an alpha name is). Empty until a contract exists. The
    # sender is configured only when all three are set; even then the platform switch `sms_on` decides,
    # and it is off unless an administrator turns it on.
    eskiz_email: str = Field(default="", repr=False)
    eskiz_password: str = Field(default="", repr=False)
    eskiz_sender: str = ""
    # The oldest signed data the web login accepts, in seconds (security review, finding 9): from 1 to
    # 3600, which is what a Mini App's launch data is given. The widget signs at the moment of the press,
    # so a few minutes cover a slow connection and a clock that is a little off.
    web_login_max_age_seconds: int = Field(default=300, ge=1, le=3600)
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
    # The operations watch (DEC-078), read by the worker only. Telegram chats that are told when something
    # is wrong, separated by commas: a person's identifier, or a group's (a negative number). It may be
    # the review group, and is set apart from it. Empty: the watch runs and logs, and tells nobody.
    alert_chat_ids: str = Field(default="", repr=False)
    # The API as the worker reaches it inside the Compose network, scheme://host:port. Empty: the API is
    # not watched. With it and QD_METRICS_TOKEN the worker also reads the API's counters.
    alert_api_url: str = ""
    # Directories, mounted read-only, where the backup jobs and the copy of the stored files write their
    # figures (the single host). Empty: backups are not watched from here.
    alert_backup_figures_dir: str = ""
    alert_files_figures_dir: str = ""
    # Paths whose filesystems are watched for space, separated by commas. Empty: none.
    alert_disk_paths: str = ""

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

    def second_factor_required(self) -> bool:
        """Whether administrators must pass the second factor. Anything other than the two values refuses
        to start: a typing mistake must not silently mean either of them."""
        if self.admin_second_factor not in ("required", "off"):
            raise ValueError('QD_ADMIN_SECOND_FACTOR must be "required" or "off"')
        return self.admin_second_factor == "required"

    def passkey_site_host(self) -> str | None:
        """The host of the administrators' passkeys, or None when none is set. Anything that is not a bare
        host name refuses to start: a passkey made for a mistyped site would work nowhere."""
        host = self.passkey_host.strip().lower()
        if not host:
            return None
        if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", host):
            raise ValueError("QD_PASSKEY_HOST must be a bare host name, such as admin.example.uz")
        return host

    def alert_chats(self) -> tuple[int, ...]:
        """The chats of the operations alerts, in the order written. Anything that is not a whole number
        other than zero refuses to start: a typing mistake must not silently mean "tell nobody"."""
        chats: list[int] = []
        for part in self.alert_chat_ids.split(","):
            raw = part.strip()
            if not raw:
                continue
            digits = raw[1:] if raw.startswith("-") else raw
            if not (digits.isascii() and digits.isdigit()) or int(digits) == 0:
                raise ValueError("QD_ALERT_CHAT_IDS must be Telegram chat identifiers separated by commas")
            if int(raw) not in chats:
                chats.append(int(raw))
        return tuple(chats)

    def alert_disks(self) -> tuple[str, ...]:
        return tuple(part.strip() for part in self.alert_disk_paths.split(",") if part.strip())
