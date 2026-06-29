"""Persistence helpers for MCP servers and skill packages.

This module owns integration settings persistence for the rebuilt orchestrator stack.
It intentionally uses the existing SQLite tables so current user data and
legacy task history remain compatible.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..storage import SQLiteStorage, _decode_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


async def list_mcp_servers(storage: SQLiteStorage, user_id: str) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM mcp_servers WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [_serialize_mcp_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


async def upsert_mcp_server(storage: SQLiteStorage, *, user_id: str, server: dict[str, Any]) -> dict[str, Any]:
    server_id = server.get("id") or uuid4().hex
    now = _now_iso()
    record = {
        "id": server_id,
        "user_id": user_id,
        "name": server.get("name", ""),
        "enabled": 1 if server.get("enabled", True) else 0,
        "transport": server.get("transport", "stdio"),
        "command": server.get("command", ""),
        "args_json": _json_dumps(server.get("args", [])),
        "cwd": server.get("cwd", ""),
        "env_json": _json_dumps(server.get("env", {})),
        "url": server.get("url", ""),
        "headers_json": _json_dumps(server.get("headers", {})),
        "auth_type": server.get("authType", server.get("auth_type", "none")),
        "auth_token": server.get("authToken", server.get("auth_token", "")),
        "allowed_tools_json": _json_dumps(server.get("allowedTools", server.get("allowed_tools", []))),
        "allowed_resources_json": _json_dumps(server.get("allowedResources", server.get("allowed_resources", []))),
        "trusted": 1 if server.get("trusted", False) else 0,
        "timeout_seconds": int(server.get("timeoutSeconds", server.get("timeout_seconds", 30))),
        "created_at": now,
        "updated_at": now,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO mcp_servers
            (id, user_id, name, enabled, transport, command, args_json, cwd, env_json, url,
             headers_json, auth_type, auth_token, allowed_tools_json, allowed_resources_json,
             trusted, timeout_seconds, created_at, updated_at)
            VALUES (:id, :user_id, :name, :enabled, :transport, :command, :args_json, :cwd, :env_json, :url,
                    :headers_json, :auth_type, :auth_token, :allowed_tools_json, :allowed_resources_json,
                    :trusted, :timeout_seconds, :created_at, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name, enabled = excluded.enabled, transport = excluded.transport,
                command = excluded.command, args_json = excluded.args_json, cwd = excluded.cwd,
                env_json = excluded.env_json, url = excluded.url, headers_json = excluded.headers_json,
                auth_type = excluded.auth_type, auth_token = excluded.auth_token,
                allowed_tools_json = excluded.allowed_tools_json,
                allowed_resources_json = excluded.allowed_resources_json,
                trusted = excluded.trusted, timeout_seconds = excluded.timeout_seconds,
                updated_at = excluded.updated_at
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    servers = await list_mcp_servers(storage, user_id)
    return next((s for s in servers if s["id"] == server_id), _serialize_mcp_record(record))


async def delete_mcp_server(storage: SQLiteStorage, server_id: str) -> bool:
    def _exec(conn) -> int:
        cursor = conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        return cursor.rowcount

    rowcount = await storage.run_in_thread(_exec)
    return rowcount > 0


async def list_skill_packages(storage: SQLiteStorage, user_id: str) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM skill_packages WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [_serialize_skill_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


async def get_skill_package(storage: SQLiteStorage, skill_id: str) -> dict[str, Any] | None:
    def _exec(conn) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM skill_packages WHERE id = ?",
            (skill_id,),
        ).fetchone()
        return _serialize_skill_row(row) if row else None

    return await storage.run_in_thread(_exec)


async def upsert_skill_package(storage: SQLiteStorage, *, user_id: str, skill: dict[str, Any]) -> dict[str, Any]:
    skill_id = skill.get("id") or uuid4().hex
    now = _now_iso()
    record = {
        "id": skill_id,
        "user_id": user_id,
        "name": skill.get("name", ""),
        "version": skill.get("version", "0.1.0"),
        "enabled": 1 if skill.get("enabled", True) else 0,
        "source": skill.get("source", "local"),
        "path": skill.get("path", ""),
        "description": skill.get("description", ""),
        "triggers_json": _json_dumps(skill.get("triggers", [])),
        "required_mcp_servers_json": _json_dumps(skill.get("requiredMcpServers", skill.get("required_mcp_servers", []))),
        "tool_allowlist_json": _json_dumps(skill.get("toolAllowlist", skill.get("tool_allowlist", []))),
        "roles_json": _json_dumps(skill.get("roles", [])),
        "budgets_json": _json_dumps(skill.get("budgets", {})),
        "manifest_json": _json_dumps(skill.get("manifest", {})),
        "created_at": now,
        "updated_at": now,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO skill_packages
            (id, user_id, name, version, enabled, source, path, description,
             triggers_json, required_mcp_servers_json, tool_allowlist_json, roles_json,
             budgets_json, manifest_json, created_at, updated_at)
            VALUES (:id, :user_id, :name, :version, :enabled, :source, :path, :description,
                    :triggers_json, :required_mcp_servers_json, :tool_allowlist_json, :roles_json,
                    :budgets_json, :manifest_json, :created_at, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name, version = excluded.version, enabled = excluded.enabled,
                source = excluded.source, path = excluded.path, description = excluded.description,
                triggers_json = excluded.triggers_json,
                required_mcp_servers_json = excluded.required_mcp_servers_json,
                tool_allowlist_json = excluded.tool_allowlist_json,
                roles_json = excluded.roles_json, budgets_json = excluded.budgets_json,
                manifest_json = excluded.manifest_json, updated_at = excluded.updated_at
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    result = await get_skill_package(storage, skill_id)
    return result or _serialize_skill_record(record)


async def delete_skill_package(storage: SQLiteStorage, skill_id: str) -> bool:
    def _exec(conn) -> int:
        cursor = conn.execute("DELETE FROM skill_packages WHERE id = ?", (skill_id,))
        return cursor.rowcount

    rowcount = await storage.run_in_thread(_exec)
    return rowcount > 0


def _serialize_mcp_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "transport": row["transport"],
        "command": row["command"],
        "args": _decode_json(row["args_json"]),
        "cwd": row["cwd"],
        "env": _decode_json(row["env_json"]),
        "url": row["url"],
        "headers": _decode_json(row["headers_json"]),
        "authType": row["auth_type"],
        "authToken": row["auth_token"],
        "allowedTools": _decode_json(row["allowed_tools_json"]),
        "allowedResources": _decode_json(row["allowed_resources_json"]),
        "trusted": bool(row["trusted"]),
        "timeoutSeconds": row["timeout_seconds"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _serialize_mcp_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
        "enabled": bool(record["enabled"]),
        "transport": record["transport"],
        "command": record["command"],
        "args": _decode_json(record["args_json"]),
        "cwd": record["cwd"],
        "env": _decode_json(record["env_json"]),
        "url": record["url"],
        "headers": _decode_json(record["headers_json"]),
        "authType": record["auth_type"],
        "authToken": record["auth_token"],
        "allowedTools": _decode_json(record["allowed_tools_json"]),
        "allowedResources": _decode_json(record["allowed_resources_json"]),
        "trusted": bool(record["trusted"]),
        "timeoutSeconds": record["timeout_seconds"],
        "createdAt": record["created_at"],
        "updatedAt": record["updated_at"],
    }


def _serialize_skill_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "enabled": bool(row["enabled"]),
        "source": row["source"],
        "path": row["path"],
        "description": row["description"],
        "triggers": _decode_json(row["triggers_json"]),
        "requiredMcpServers": _decode_json(row["required_mcp_servers_json"]),
        "toolAllowlist": _decode_json(row["tool_allowlist_json"]),
        "roles": _decode_json(row["roles_json"]),
        "budgets": _decode_json(row["budgets_json"]),
        "manifest": _decode_json(row["manifest_json"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _serialize_skill_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
        "version": record["version"],
        "enabled": bool(record["enabled"]),
        "source": record["source"],
        "path": record["path"],
        "description": record["description"],
        "triggers": _decode_json(record["triggers_json"]),
        "requiredMcpServers": _decode_json(record["required_mcp_servers_json"]),
        "toolAllowlist": _decode_json(record["tool_allowlist_json"]),
        "roles": _decode_json(record["roles_json"]),
        "budgets": _decode_json(record["budgets_json"]),
        "manifest": _decode_json(record["manifest_json"]),
        "createdAt": record["created_at"],
        "updatedAt": record["updated_at"],
    }
