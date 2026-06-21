"""Route registration tests - verify all routers are mounted.

NOTE: SSE (Server-Sent Events) endpoints cannot be tested with a simple
HTTP call because they block indefinitely waiting for events.  They are
verified separately through integration / end-to-end testing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestRouteRegistration:
    """Verify that all expected routes return appropriate status codes."""

    @pytest.mark.parametrize(
        "method,path,expected_status,expected_key",
        [
            # Health & storage
            ("GET", "/api/health", 200, "ok"),
            ("GET", "/api/storage", 200, "type"),
            # Providers & personas
            ("GET", "/api/providers", 200, "providers"),
            ("GET", "/api/personas", 200, "personas"),
            # Agent
            ("GET", "/api/agent/capabilities", 200, "ok"),
            # Conversations
            ("GET", "/api/conversations", 200, "conversations"),
            # Settings
            ("GET", "/api/settings", 200, "settings"),
            # Code tasks (SSE /events excluded — blocks indefinitely)
            ("POST", "/api/code-tasks/sync", 200, "ok"),
            # Mobile
            ("GET", "/api/mobile/connect-info", 200, "urls"),
            ("GET", "/api/mobile/code-tasks", 200, "tasks"),
            ("GET", "/api/mobile/task-views", 200, "views"),
            # File download (missing path = 400)
            ("GET", "/api/files/download?path=", 400, None),
        ],
    )
    def test_route_exists(self, client: TestClient, method, path, expected_status, expected_key):
        if method == "GET":
            response = client.get(path)
        elif method == "POST":
            response = client.post(path, json={"tasks": []})
        else:
            pytest.fail(f"Unknown method: {method}")

        assert response.status_code == expected_status, f"{method} {path} returned {response.status_code}: {response.text[:200]}"

        if expected_key:
            data = response.json()
            assert expected_key in data, f"Key '{expected_key}' not in response for {method} {path}"


class TestCORSHeaders:
    def test_options_request_has_cors_headers(self, client: TestClient):
        response = client.options(
            "/api/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers


class TestSecurityHeaders:
    """Verify security-related HTTP response headers are present."""

    def test_security_headers_on_health(self, client: TestClient):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-frame-options") == "DENY"
        assert response.headers.get("x-xss-protection") == "1; mode=block"
        assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        assert "content-security-policy" in response.headers
        assert "permissions-policy" in response.headers

    def test_no_hsts_by_default(self, client: TestClient):
        """HSTS should be off by default (local dev)."""
        response = client.get("/api/health")
        assert "strict-transport-security" not in response.headers
