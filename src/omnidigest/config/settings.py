# src/omnidigest/config/settings.py

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from loguru import logger
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import yaml


def _mask(s: str | None, keep: int = 4) -> str | None:
    if not s:
        return s
    if len(s) <= keep * 2:
        return "*" * len(s)
    return s[:keep] + "*" * (len(s) - keep * 2) + s[-keep:]


class AppSettings(BaseSettings):
    """
    统一的应用配置：
    - 从 .env 读取（别名使用大写 KEY）
    - 代码中推荐访问小写属性（如 settings.email_provider）
    - 同时提供兼容的大写属性（如 settings.EMAIL_PROVIDER）
    """

    # pydantic-settings v2
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 未声明的 .env 键忽略即可
    )

    # --- App ---
    app_name: str = Field(default="OmniDigest", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    timezone: str = Field(default="UTC", alias="TIMEZONE")

    # --- LLM Providers ---
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")

    # --- Email ---
    email_provider: str | None = Field(default=None, alias="EMAIL_PROVIDER")
    sendgrid_api_key: str | None = Field(default=None, alias="SENDGRID_API_KEY")
    email_from: str | None = Field(default=None, alias="EMAIL_FROM")
    email_test_to: str | None = Field(default=None, alias="EMAIL_TEST_TO")
    email_reply_to: str | None = Field(default=None, alias="EMAIL_REPLY_TO")

    # --- Storage / Features ---
    database_url: str = Field(default="sqlite:///./omnidigest.db", alias="DATABASE_URL")
    enable_analysis: bool = Field(default=False, alias="ENABLE_ANALYSIS")
    enable_html_email: bool = Field(default=True, alias="ENABLE_HTML_EMAIL")

    # ----------------- helpers -----------------
    def load_domain_config(self, domain: str) -> Dict[str, Any]:
        """加载领域配置：src/omnidigest/domains/{domain}.yaml"""
        domain_file = Path(__file__).resolve().parents[1] / "domains" / f"{domain}.yaml"
        if not domain_file.exists():
            msg = f"Domain configuration for '{domain}' not found"
            logger.error(msg)
            raise ValueError(msg)
        with domain_file.open("r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
        logger.debug("Loaded domain config for {}: {}", domain, config)
        return config

    # ----------------- UPPERCASE compatibility properties -----------------
    # 兼容旧代码里用大写属性访问的写法（例如 settings.EMAIL_PROVIDER）
    @property
    def APP_NAME(self) -> str: return self.app_name

    @property
    def APP_ENV(self) -> str: return self.app_env

    @property
    def TIMEZONE(self) -> str: return self.timezone

    @property
    def OPENAI_API_KEY(self) -> str | None: return self.openai_api_key

    @property
    def ANTHROPIC_API_KEY(self) -> str | None: return self.anthropic_api_key

    @property
    def GOOGLE_API_KEY(self) -> str | None: return self.google_api_key

    @property
    def EMAIL_PROVIDER(self) -> str | None: return self.email_provider

    @property
    def SENDGRID_API_KEY(self) -> str | None: return self.sendgrid_api_key

    @property
    def EMAIL_FROM(self) -> str | None: return self.email_from

    @property
    def EMAIL_TEST_TO(self) -> str | None: return self.email_test_to

    @property
    def EMAIL_REPLY_TO(self) -> str | None: return self.email_reply_to

    @property
    def DATABASE_URL(self) -> str: return self.database_url

    @property
    def ENABLE_ANALYSIS(self) -> bool: return self.enable_analysis

    @property
    def ENABLE_HTML_EMAIL(self) -> bool: return self.enable_html_email

    # 自定义 __repr__：打印时对敏感信息做掩码
    def __repr__(self) -> str:
        return (
            "AppSettings("
            f"app_name={self.app_name!r}, app_env={self.app_env!r}, timezone={self.timezone!r}, "
            f"openai_api_key={_mask(self.openai_api_key)!r}, "
            f"anthropic_api_key={_mask(self.anthropic_api_key)!r}, "
            f"google_api_key={_mask(self.google_api_key)!r}, "
            f"email_provider={self.email_provider!r}, "
            f"sendgrid_api_key={_mask(self.sendgrid_api_key)!r}, "
            f"email_from={self.email_from!r}, email_test_to={self.email_test_to!r}, "
            f"database_url={self.database_url!r}, "
            f"enable_analysis={self.enable_analysis!r}, enable_html_email={self.enable_html_email!r}"
            ")"
        )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """全局单例，供各模块调用。"""
    s = AppSettings()
    logger.debug("Application settings loaded: {}", s)
    return s


__all__ = ["AppSettings", "get_settings"]
