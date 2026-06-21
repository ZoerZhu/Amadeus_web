"""Storage CRUD tests - conversations and settings."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestConversations:
    def test_list_empty(self, client: TestClient):
        response = client.get("/api/conversations")
        assert response.status_code == 200
        assert response.json()["conversations"] == []

    def test_create_conversation(self, client: TestClient):
        response = client.post(
            "/api/conversations",
            json={"title": "测试对话", "personaId": "kurisu_amadeus", "mode": "fast"},
        )
        assert response.status_code == 200
        data = response.json()
        conv = data["conversation"]
        assert conv["title"] == "测试对话"
        assert conv["personaId"] == "kurisu_amadeus"
        assert conv["mode"] == "fast"
        assert "id" in conv

    def test_get_conversation(self, client: TestClient):
        # Create first
        create_resp = client.post(
            "/api/conversations",
            json={"title": "获取测试", "personaId": "kurisu_amadeus", "mode": "thinking"},
        )
        conv_id = create_resp.json()["conversation"]["id"]

        # Get
        response = client.get(f"/api/conversations/{conv_id}")
        assert response.status_code == 200
        conv = response.json()["conversation"]
        assert conv["title"] == "获取测试"
        assert conv["mode"] == "thinking"
        assert conv["messages"] == []

    def test_get_nonexistent(self, client: TestClient):
        response = client.get("/api/conversations/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    def test_delete_conversation(self, client: TestClient):
        create_resp = client.post(
            "/api/conversations",
            json={"title": "待删除", "personaId": "kurisu_amadeus", "mode": "fast"},
        )
        conv_id = create_resp.json()["conversation"]["id"]

        delete_resp = client.delete(f"/api/conversations/{conv_id}")
        assert delete_resp.status_code == 200
        assert delete_resp.json()["ok"] is True

        # Verify gone
        get_resp = client.get(f"/api/conversations/{conv_id}")
        assert get_resp.status_code == 404


class TestSettings:
    def test_get_settings_returns_null(self, client: TestClient):
        response = client.get("/api/settings")
        assert response.status_code == 200
        assert response.json()["settings"] is None

    def test_save_and_get_settings(self, client: TestClient):
        settings = {
            "model": {
                "providerName": "OpenAI",
                "baseUrl": "https://api.openai.com/v1",
                "model": "gpt-4.1-mini",
                "apiKey": "",
                "useRemote": True,
            },
            "vision": {
                "enabled": True,
                "screenCaptureEnabled": False,
                "providerName": "",
                "baseUrl": "",
                "model": "gpt-4.1-mini",
                "apiKey": "",
                "useRemote": True,
            },
            "speechInput": {
                "enabled": True,
                "providerName": "小米 MiMo ASR",
                "baseUrl": "https://api.xiaomimimo.com/v1",
                "model": "mimo-v2.5-asr",
                "apiKey": "",
                "language": "auto",
                "useRemote": True,
            },
            "voice": {
                "ttsBackend": "local",
                "siliconFlowApiKey": "",
                "clonedVoiceUri": "",
                "autoPlay": False,
                "syncTextOutput": True,
                "speed": 1.0,
                "gain": 0.0,
            },
            "desktopAssistant": {
                "subtitleEnabled": True,
                "voiceOutputEnabled": False,
                "autoVoiceInputEnabled": True,
                "autoScreenshotEnabled": False,
                "screenshotIntervalSeconds": 15,
                "cameraEnabled": False,
            },
            "mode": "fast",
        }
        save_resp = client.put("/api/settings", json=settings)
        assert save_resp.status_code == 200
        assert save_resp.json()["ok"] is True

        get_resp = client.get("/api/settings")
        assert get_resp.status_code == 200
        saved = get_resp.json()["settings"]
        assert saved is not None
        assert saved["mode"] == "fast"
        assert saved["model"]["providerName"] == "OpenAI"
