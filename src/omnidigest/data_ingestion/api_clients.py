"""Placeholder for future API client integrations."""

from typing import Any

from loguru import logger


def fetch_from_api(name: str, **kwargs: Any) -> list[dict]:
    """Return stubbed API data while integrations are pending."""
    logger.debug("API client '{}' called with {}", name, kwargs)
    return []


__all__ = ["fetch_from_api"]
