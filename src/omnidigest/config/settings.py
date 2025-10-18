from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from loguru import logger
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="OmniDigest", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    timezone: str = Field(default="UTC", alias="TIMEZONE")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")

    email_provider: str | None = Field(default=None, alias="EMAIL_PROVIDER")
    sendgrid_api_key: str | None = Field(default=None, alias="SENDGRID_API_KEY")
    email_from: str | None = Field(default=None, alias="EMAIL_FROM")
    email_test_to: str | None = Field(default=None, alias="EMAIL_TEST_TO")

    database_url: str = Field(default="sqlite:///./omnidigest.db", alias="DATABASE_URL")
    enable_analysis: bool = Field(default=False, alias="ENABLE_ANALYSIS")
    enable_html_email: bool = Field(default=True, alias="ENABLE_HTML_EMAIL")

    def load_domain_config(self, domain: str) -> Dict[str, Any]:
        domain_file = Path(__file__).resolve().parents[1] / "domains" / f"{domain}.yaml"
        if not domain_file.exists():
            msg = f"Domain configuration for '{domain}' not found"
            logger.error(msg)
            raise ValueError(msg)
        with domain_file.open("r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
        logger.debug("Loaded domain config for {}: {}", domain, config)
        return config


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    settings = AppSettings()
    logger.debug("Application settings loaded: {}", settings)
    return settings


__all__ = ["AppSettings", "get_settings"]
