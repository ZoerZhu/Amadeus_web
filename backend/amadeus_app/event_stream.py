"""Shared Server-Sent Events formatting helpers."""

from __future__ import annotations

import json
from typing import Any


def sse(event: str, payload: dict[str, Any]) -> str:
    """Format one Server-Sent Events chunk."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
