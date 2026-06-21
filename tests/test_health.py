"""Health and basic API tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"ok": True}

    def test_storage_status_returns_data(self, client: TestClient):
        response = client.get("/api/storage")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["type"] == "sqlite"

    def test_providers_list(self, client: TestClient):
        response = client.get("/api/providers")
        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        assert len(data["providers"]) > 0
        # First provider should be 演示模型
        names = [p["name"] for p in data["providers"]]
        assert "演示模型" in names or "OpenAI" in names

    def test_personas_list(self, client: TestClient):
        response = client.get("/api/personas")
        assert response.status_code == 200
        data = response.json()
        assert "personas" in data
        assert len(data["personas"]) == 1
        assert data["personas"][0]["id"] == "kurisu_amadeus"
