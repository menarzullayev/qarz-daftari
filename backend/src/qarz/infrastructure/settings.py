"""Runtime configuration read from the environment. Secrets never live in the repository."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QD_", extra="ignore")

    # Connection string of the application role (qd_app), which cannot bypass row-level security.
    database_url: str = "postgresql://qd_app@localhost:5432/qarz"
