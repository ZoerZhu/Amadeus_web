"""Shared test fixtures for Amadeus Web."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Set test SQLite path before importing app — must be done at module level
os.environ["AMADEUS_SQLITE_PATH"] = ":memory:"
os.environ["AMADEUS_LOG_LEVEL"] = "WARNING"
os.environ["AMADEUS_RATE_LIMIT_DISABLED"] = "1"


@pytest.fixture
def client():
    """Return a FastAPI TestClient with a fresh in-memory SQLite backend."""
    from backend.amadeus_app.main import app

    with TestClient(app) as c:
        yield c
