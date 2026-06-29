# backend/amadeus_app/orchestrator/model_retry.py
"""Retry and fallback logic for model calls in the agent loop."""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx


# Error categories
RETRYABLE = "retryable"
FATAL = "fatal"


@dataclass
class ModelRetryConfig:
    """Configuration for model call retries."""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: bool = True


def classify_error(error: Exception) -> str:
    """Classify an error as retryable or fatal.

    Retryable: timeouts, connection errors, 429, 500, 502, 503, 504.
    Fatal: 400, 401, 403, 404, validation errors, everything else.
    """
    if isinstance(error, httpx.TimeoutException):
        return RETRYABLE
    if isinstance(error, httpx.ConnectError):
        return RETRYABLE
    if isinstance(error, httpx.NetworkError):
        return RETRYABLE
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 429:
            return RETRYABLE
        if 500 <= status < 600:
            return RETRYABLE
        return FATAL
    if isinstance(error, asyncio.TimeoutError):
        return RETRYABLE
    return FATAL


async def retry_model_call(
    coro_factory: Callable[[], Awaitable[dict[str, Any] | None]],
    config: ModelRetryConfig,
) -> dict[str, Any] | None:
    """Call a coroutine factory with retry and exponential backoff.

    Args:
        coro_factory: A callable that returns a new coroutine each call.
        config: Retry configuration.

    Returns:
        The result dict on success, or None if all retries exhausted.
    """
    last_error: Exception | None = None
    for attempt in range(config.max_retries + 1):
        try:
            result = await coro_factory()
            return result
        except Exception as error:  # noqa: BLE001
            last_error = error
            category = classify_error(error)
            if category == FATAL:
                return None
            if attempt >= config.max_retries:
                return None
            delay = min(
                config.base_delay * (2 ** attempt),
                config.max_delay,
            )
            if config.jitter:
                delay = delay * (0.5 + random.random() * 0.5)
            await asyncio.sleep(delay)

    return None
