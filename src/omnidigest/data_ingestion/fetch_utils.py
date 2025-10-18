"""Network helper utilities for OmniDigest."""

from typing import Any

import httpx
from loguru import logger


async def async_fetch(url: str, **kwargs: Any) -> httpx.Response:
    """Perform an async HTTP GET request."""
    logger.debug("Fetching URL asynchronously: {}", url)
    async with httpx.AsyncClient(timeout=kwargs.pop("timeout", 10.0)) as client:
        response = await client.get(url, **kwargs)
        response.raise_for_status()
        return response


def fetch(url: str, **kwargs: Any) -> httpx.Response:
    """Perform a synchronous HTTP GET request."""
    logger.debug("Fetching URL synchronously: {}", url)
    response = httpx.get(url, timeout=kwargs.pop("timeout", 10.0), **kwargs)
    response.raise_for_status()
    return response


__all__ = ["async_fetch", "fetch"]
