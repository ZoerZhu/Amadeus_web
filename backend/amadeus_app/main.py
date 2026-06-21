"""Amadeus Web - FastAPI application factory.

All business-route logic has been moved to backend/amadeus_app/routers/.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ._common import require_storage
from .code_tasks.opencode_runner import stop_all_opencode_processes
from .code_tasks.task_hub import code_task_hub
from .complex_agent.mcp_client import mcp_manager
from .complex_agent.tool_registry import sync_builtin_tools
from .logging_config import configure_logging, get_logger
from .memory.jobs import start_memory_worker
from .memory.tree_store import MemoryTreeStore
from .routers import chat, code_tasks, complex_agent, conversations, files, memories, mobile, settings, voice
from .security import RateLimiterMiddleware, SecurityHeadersMiddleware
from .storage import SQLiteStorage

_log = get_logger(__name__)

# ---------------- paths / env ----------------


ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT_DIR / "dist"
load_dotenv(ROOT_DIR / ".env")

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/octet-stream", ".moc3")

SQLITE_PATH_VALUE = os.getenv("AMADEUS_SQLITE_PATH", "").strip()
if SQLITE_PATH_VALUE == ":memory:":
    SQLITE_PATH = Path(":memory:")
elif SQLITE_PATH_VALUE:
    SQLITE_PATH = Path(SQLITE_PATH_VALUE)
    if not SQLITE_PATH.is_absolute():
        SQLITE_PATH = ROOT_DIR / SQLITE_PATH
else:
    SQLITE_PATH = ROOT_DIR / "agent_state" / "amadeus_web.sqlite3"

# ---------------- app ----------------


app = FastAPI(title="Amadeus Web", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("AMADEUS_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4173,http://localhost:4173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers (X-Frame-Options, CSP, etc.)
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiting per client IP (configurable via env vars)
app.add_middleware(RateLimiterMiddleware)

# ---------------- storage singleton ----------------


_global_storage = SQLiteStorage(SQLITE_PATH)
_global_memory_tree: MemoryTreeStore | None = None
_memory_worker = None


@app.on_event("startup")
async def startup() -> None:
    global _global_memory_tree, _memory_worker
    configure_logging()
    _log.info("Amadeus Web starting up...")
    await _global_storage.connect()
    _log.info("SQLite storage connected at %s.", SQLITE_PATH)

    # Sync builtin tools into the unified tool registry (complex agent)
    try:
        sync_builtin_tools()
        _log.info("Unified tool registry synced with builtin tools.")
    except Exception as error:
        _log.warning("Failed to sync unified tool registry: %s", error)

    # Auto-connect enabled MCP servers
    try:
        from .complex_agent import agent_storage
        from .complex_agent.domain import McpServerConfig
        from .storage import DEFAULT_USER_ID

        servers = await agent_storage.list_mcp_servers(_global_storage, DEFAULT_USER_ID)
        for server in servers:
            if not server.get("enabled", True):
                continue
            try:
                config = McpServerConfig.model_validate(server)
                await mcp_manager.connect_server(config)
                _log.info("MCP server %s connected.", server["id"])
            except Exception as error:
                _log.warning("MCP server %s failed to connect: %s", server["id"], error)
    except Exception as error:
        _log.warning("MCP auto-connect failed: %s", error)

    # 初始化记忆树 + 启动异步 worker
    if os.getenv("AMADEUS_MEMORY_ENABLED", "1").lower() not in ("0", "false", "no"):
        _global_memory_tree = MemoryTreeStore(_global_storage)
        await _global_memory_tree.ensure_default_tree()
        _memory_worker = await start_memory_worker(_global_storage, _global_memory_tree)
        _log.info("Memory system initialized.")

        # 注册记忆工具到 LLM tool_registry
        try:
            from .memory.memory_tools import register_memory_tools
            register_memory_tools()
            _log.info("Memory tools registered.")
        except Exception as error:
            _log.warning("Failed to register memory tools: %s", error)

        # Re-sync builtin tools into the unified registry so memory tools
        # (registered above) are available to the complex agent.
        try:
            sync_builtin_tools()
            _log.info("Unified tool registry re-synced after memory tools registration.")
        except Exception as error:
            _log.warning("Failed to re-sync unified tool registry: %s", error)


@app.on_event("shutdown")
async def shutdown() -> None:
    global _memory_worker
    _log.info("Amadeus Web shutting down...")
    if _memory_worker:
        await _memory_worker.stop()
    await code_task_hub.cancel_background_tasks()
    from .complex_agent.task_hub import agent_task_hub
    await agent_task_hub.cancel_all()
    await mcp_manager.disconnect_all()
    await stop_all_opencode_processes()
    await _global_storage.close()
    _log.info("Shutdown complete.")


# ---------------- static mounts ----------------


from .voice_service import AUDIO_DIR
from .voice_input_service import USER_VOICE_DIR

app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")
app.mount("/user-voice", StaticFiles(directory=USER_VOICE_DIR), name="user_voice")


# ---------------- health / storage status ----------------


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/storage")
async def storage_status() -> dict:
    return {
        "enabled": _global_storage.enabled,
        "connected": _global_storage.connected,
        "type": "sqlite",
        "path": str(_global_storage.database_path),
    }


# ---------------- routers ----------------


app.include_router(chat.router)
app.include_router(code_tasks.router)
app.include_router(complex_agent.router)
app.include_router(conversations.router)
app.include_router(files.router)
app.include_router(memories.router)
app.include_router(mobile.router)
app.include_router(settings.router)
app.include_router(voice.router)


# ---------------- frontend ----------------


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
