"""Security middleware: rate limiting and security headers."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from .logging_config import get_logger

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Security Headers middleware
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add HTTP security headers to every response.

    Configurable via env vars:
      - AMADEUS_SECURITY_CSP: Content-Security-Policy (default: restrictive)
      - AMADEUS_SECURITY_HSTS: Strict-Transport-Security (default: off for local dev)
      - AMADEUS_SECURITY_FRAME_ANCESTORS: frame-ancestors CSP directive
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Prevent MIME-type sniffing
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

        # Prevent clickjacking (deny framing from other origins)
        response.headers.setdefault("X-Frame-Options", "DENY")

        # Enable browser XSS filter
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")

        # Referrer policy: only send origin for same-origin
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

        # Permissions policy: restrict sensitive browser features
        # camera/microphone 默认允许 self（语音输入/摄像头需要），使用标准括号形式
        # 可通过环境变量 AMADEUS_SECURITY_PERMISSIONS_POLICY 收紧
        permissions_policy = os.getenv(
            "AMADEUS_SECURITY_PERMISSIONS_POLICY",
            "camera=(self), microphone=(self), geolocation=(), interest-cohort=()",
        ).strip()
        if permissions_policy:
            response.headers.setdefault("Permissions-Policy", permissions_policy)

        # Content-Security-Policy (configurable)
        csp = os.getenv("AMADEUS_SECURITY_CSP", "").strip()
        if not csp:
            csp = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                "media-src 'self' blob:; "
                "connect-src 'self' ws: wss:; "
                "font-src 'self' data:; "
                "worker-src 'self' blob:; "
                "frame-ancestors 'self'"
            )
        response.headers.setdefault("Content-Security-Policy", csp)

        # HSTS (off by default for local development)
        if os.getenv("AMADEUS_SECURITY_HSTS", "").lower() in ("1", "true", "yes"):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload",
            )

        return response


# ---------------------------------------------------------------------------
# Rate Limiter middleware
# ---------------------------------------------------------------------------


def _parse_rate_limit(env_key: str, default: int) -> int:
    try:
        return int(os.getenv(env_key, str(default)))
    except (ValueError, TypeError):
        return default


# Default limits — can be overridden via env vars
_RATE_LIMIT_WINDOW_SECONDS = _parse_rate_limit("AMADEUS_RATE_LIMIT_WINDOW_SECONDS", 60)
_RATE_LIMIT_MAX_REQUESTS = _parse_rate_limit("AMADEUS_RATE_LIMIT_MAX_REQUESTS", 300)
# Stricter limits for expensive endpoints (file uploads, TTS, etc.)
_RATE_LIMIT_HEAVY_MAX = _parse_rate_limit("AMADEUS_RATE_LIMIT_HEAVY_MAX", 30)


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """Simple in-memory token-bucket rate limiter per client IP.

    Limits are configurable via:
      - AMADEUS_RATE_LIMIT_MAX_REQUESTS (default: 300 / window)
      - AMADEUS_RATE_LIMIT_WINDOW_SECONDS (default: 60s)
      - AMADEUS_RATE_LIMIT_HEAVY_MAX (default: 30 / window, for heavy endpoints)
      - AMADEUS_RATE_LIMIT_DISABLED=1 to disable
    """

    def __init__(self, app, *, window_seconds: int | None = None, max_requests: int | None = None,
                 heavy_max: int | None = None):
        super().__init__(app)
        self.window_seconds = window_seconds or _RATE_LIMIT_WINDOW_SECONDS
        self.max_requests = max_requests or _RATE_LIMIT_MAX_REQUESTS
        self.heavy_max = heavy_max or _RATE_LIMIT_HEAVY_MAX
        self._buckets: dict[str, tuple[int, float]] = defaultdict(lambda: (0, time.monotonic()))
        self._disabled = os.getenv("AMADEUS_RATE_LIMIT_DISABLED", "").lower() in ("1", "true", "yes")

    # Endpoints that consume more resources and get stricter limits
    _heavy_prefixes = ("/api/voice", "/api/files/upload", "/api/chat/stream", "/api/desktop/observe")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self._disabled:
            return await call_next(request)

        # Determine client identity by forwarded or direct IP
        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        # Check limit
        limit = self.heavy_max if request.url.path.startswith(self._heavy_prefixes) else self.max_requests
        now = time.monotonic()
        count, window_start = self._buckets[client_ip]

        if now - window_start >= self.window_seconds:
            # New window
            count = 0
            window_start = now

        if count >= limit:
            retry_after = int(self.window_seconds - (now - window_start)) + 1
            _log.warning("Rate limit hit for %s: %d/%d in window", client_ip, count + 1, limit)
            return JSONResponse(
                {"detail": "Too many requests. Please try again later."},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        self._buckets[client_ip] = (count + 1, window_start)
        return await call_next(request)
