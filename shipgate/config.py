from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    shipgate_db_url: str | None = None
    gemini_api_key: str | None = None
    git_sha: str = "dev"


def get_settings() -> Settings:
    """Read fresh each call so tests can monkeypatch env without fighting a cache."""
    return Settings()
