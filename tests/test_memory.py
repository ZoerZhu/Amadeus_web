"""记忆系统测试 — 树结构、FTS检索、API端点。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestMemoryTreeInit:
    """测试记忆树默认结构初始化。"""

    def test_memory_tree_has_domains(self, client: TestClient):
        """启动后应自动创建 root + 6 个 domain 节点。"""
        response = client.get("/api/memory/tree?path=/&depth=1")
        assert response.status_code == 200
        data = response.json()
        nodes = data.get("nodes", [])
        # 应该有 6 个 domain 节点
        domain_labels = [n.get("label", "") for n in nodes]
        assert "日常聊天" in domain_labels
        assert "代码相关" in domain_labels
        assert "环境与工具" in domain_labels


class TestMemoryNodeCRUD:
    """测试记忆节点 CRUD。"""

    def test_create_and_get_leaf(self, client: TestClient):
        """创建叶子节点并获取。"""
        # 先获取一个 domain 节点
        tree_resp = client.get("/api/memory/tree?path=/&depth=1")
        domains = tree_resp.json()["nodes"]
        assert len(domains) > 0
        domain_id = domains[0]["id"]

        # 创建叶子
        create_resp = client.post(
            "/api/memory/nodes",
            json={
                "parentId": domain_id,
                "domain": "daily_chat",
                "label": "测试记忆",
                "summary": "这是一条测试记忆摘要",
                "fullContent": "这是完整的测试记忆内容，用于验证记忆系统。",
                "category": "fact",
                "keywords": ["测试", "记忆"],
                "importance": 0.8,
                "confidence": 0.9,
            },
        )
        assert create_resp.status_code == 200
        node = create_resp.json()["node"]
        assert node["label"] == "测试记忆"
        assert node["nodeType"] == "leaf"
        node_id = node["id"]

        # 获取节点
        get_resp = client.get(f"/api/memory/nodes/{node_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["node"]["id"] == node_id

    def test_update_node(self, client: TestClient):
        """更新节点。"""
        tree_resp = client.get("/api/memory/tree?path=/&depth=1")
        domain_id = tree_resp.json()["nodes"][0]["id"]

        create_resp = client.post(
            "/api/memory/nodes",
            json={
                "parentId": domain_id,
                "domain": "code",
                "label": "原始标题",
                "summary": "原始摘要",
                "fullContent": "原始内容",
                "category": "technical",
            },
        )
        node_id = create_resp.json()["node"]["id"]

        update_resp = client.put(
            f"/api/memory/nodes/{node_id}",
            json={"label": "更新标题", "importance": 0.95},
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()["node"]
        assert updated["label"] == "更新标题"
        assert updated["importance"] == 0.95

    def test_delete_node(self, client: TestClient):
        """删除节点。"""
        tree_resp = client.get("/api/memory/tree?path=/&depth=1")
        domain_id = tree_resp.json()["nodes"][0]["id"]

        create_resp = client.post(
            "/api/memory/nodes",
            json={
                "parentId": domain_id,
                "domain": "task",
                "label": "待删除",
                "summary": "将被删除",
                "fullContent": "将被删除的内容",
            },
        )
        node_id = create_resp.json()["node"]["id"]

        del_resp = client.delete(f"/api/memory/nodes/{node_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["ok"] is True

        # 确认已归档（isActive=False）
        get_resp = client.get(f"/api/memory/nodes/{node_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["node"]["isActive"] is False


class TestMemorySearch:
    """测试记忆检索。"""

    def test_search_empty_tree(self, client: TestClient):
        """空树检索不报错。"""
        response = client.post(
            "/api/memory/search",
            json={"query": "测试查询", "leafTopK": 5},
        )
        assert response.status_code == 200
        result = response.json()["result"]
        assert "contextText" in result


class TestProjectCRUD:
    """测试项目管理。"""

    def test_create_and_list_project(self, client: TestClient):
        """创建项目并列出。"""
        create_resp = client.post(
            "/api/projects",
            json={
                "name": "测试项目",
                "description": "用于测试的项目",
                "workspacePath": "/test/path",
                "color": "#ff0000",
            },
        )
        assert create_resp.status_code == 200
        project = create_resp.json()["project"]
        assert project["name"] == "测试项目"
        project_id = project["id"]

        # 列出
        list_resp = client.get("/api/projects")
        assert list_resp.status_code == 200
        projects = list_resp.json()["projects"]
        assert any(p["id"] == project_id for p in projects)

    def test_delete_project(self, client: TestClient):
        """删除项目。"""
        create_resp = client.post(
            "/api/projects",
            json={"name": "待删除项目"},
        )
        project_id = create_resp.json()["project"]["id"]

        del_resp = client.delete(f"/api/projects/{project_id}")
        assert del_resp.status_code == 200

        list_resp = client.get("/api/projects")
        assert not any(p["id"] == project_id for p in list_resp.json()["projects"])


class TestConversationProjectLink:
    """测试会话-项目关联。"""

    def test_link_conversation_to_project(self, client: TestClient):
        """关联会话到项目。"""
        # 创建项目
        proj_resp = client.post("/api/projects", json={"name": "关联测试项目"})
        project_id = proj_resp.json()["project"]["id"]

        # 创建会话
        conv_resp = client.post("/api/conversations", json={"title": "测试会话"})
        conv_id = conv_resp.json()["conversation"]["id"]

        # 关联
        link_resp = client.post(
            f"/api/conversations/{conv_id}/project",
            json={"projectId": project_id},
        )
        assert link_resp.status_code == 200
        assert link_resp.json()["ok"] is True
