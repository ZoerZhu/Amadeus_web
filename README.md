# Amadeus

Desktop/web runtime migrated from `E:\Amadeus\Amadeus_web` and refactored back into a full backend capability set.

The current backend keeps normal streaming chat, persona prompts, Live2D emotion feedback, segmented voice output, visual attachments, desktop assistant APIs, Orchestrator tasks, Tools, MCP, Skills, OpenCode delegation, and Memory. The mobile bridge remains intentionally excluded.

## Run

```powershell
npm install
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Start the FastAPI backend:

```powershell
.\.venv\Scripts\python -m uvicorn backend.amadeus_app.main:app --reload --host 127.0.0.1 --port 8000
```

Start the Vite frontend:

```powershell
npm run dev
```

Open <http://127.0.0.1:5173>.

## Backend Architecture

- `chat_service` handles OpenAI-compatible streaming chat, persona prompts, emotion tags, vision enrichment, tool calling, Memory injection, conversation persistence, and segmented TTS. When a user explicitly asks for a complex task, chat creates an Orchestrator task and emits an `orchestrator_task` stream event.
- `orchestrator` owns the task runtime, planner, capability gateway, permission queue, task ledger, artifacts, and OpenCode routing decisions. New task work should use `/api/orchestrator/*` and the `orchestrator_tasks` tables.
- `orchestrator_integrations` owns MCP servers, MCP resources, Skills, and the unified integration registry. `agent_integrations` is only a compatibility import layer.
- `builtin_tool_registry` exposes builtin tool schemas for explicit invoke-style calls. `agent_registry` remains as a compatibility import layer.
- `complex_agent` is retired as a runtime. Its remaining modules either re-export Orchestrator types or raise errors that direct callers to `/api/orchestrator/tasks`.
- `memory` keeps the tree + FTS5 design with optional sqlite-vec acceleration. If sqlite-vec is unavailable, Memory falls back to FTS.
- `code_tasks` keeps OpenCode isolated as an optional adapter. Orchestrator calls it only when the planner and OpenCode routing rules decide the task needs a stronger coding agent.

Mounted API groups include chat, settings, conversations, files, voice, Orchestrator tasks, legacy Agent compatibility routes, MCP, Skills, artifacts, permissions, Memory, projects, and code tasks. `/api/mobile/*` is not mounted.

## Legacy Migration

Dry-run counts from the legacy project:

```powershell
npm run migrate:legacy:dry
```

Run the explicit migration:

```powershell
npm run migrate:legacy
```

The migration backs up the target SQLite files, copies `agent_state/skills`, migrates settings, conversations, messages, Memory, MCP, Skills, legacy Agent task tables, permissions, and artifacts, then rewrites skill paths to the new project and rebuilds Memory FTS. Secrets in the legacy database are migrated as-is.

## Memory Embedding

The migrated local embedding model is stored at:

```powershell
models\embedding\Qwen3-Embedding-0.6B-Q8_0.gguf
```

Default desktop configuration uses Ollama's OpenAI-compatible embedding endpoint:

```powershell
npm run memory:embedding:ollama
```

Then restart the backend. `.env` defaults:

```powershell
AMADEUS_MEMORY_EMBEDDING_BACKEND=ollama
AMADEUS_MEMORY_EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1
AMADEUS_MEMORY_EMBEDDING_MODEL=amadeus-qwen3-embedding:latest
AMADEUS_MEMORY_EMBEDDING_DIM=1024
```

The vector tables are dimension-aware. If `AMADEUS_MEMORY_EMBEDDING_DIM` changes, the local sqlite-vec tables are recreated and should be rebuilt from the Memory panel.

For direct GGUF loading without Ollama, set `AMADEUS_MEMORY_EMBEDDING_BACKEND=local_gguf` and install the optional dependency:

```powershell
python -m pip install ".[local-embedding]"
```

On NVIDIA GPUs, install a CUDA llama-cpp-python wheel and offload layers:

```powershell
python -m pip install --prefer-binary --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu130 "llama-cpp-python>=0.3.0"
AMADEUS_MEMORY_LLAMA_N_GPU_LAYERS=-1
```

Use a CUDA wheel matching the local driver/runtime capability (`cu121`, `cu122`, `cu123`, `cu124`, `cu125`, `cu130`, or `cu132`). CPU-only mode uses `AMADEUS_MEMORY_LLAMA_N_GPU_LAYERS=0`.

## Checks

```powershell
npm run build
python -m compileall backend backend_launcher.py scripts\migrate_legacy_state.py
python -c "import backend_launcher; import backend.amadeus_app.main as m; print(m.app.title)"
```

For offline smoke tests without connecting migrated MCP servers:

```powershell
$env:AMADEUS_MCP_AUTO_CONNECT = "0"
python -m uvicorn backend.amadeus_app.main:app --host 127.0.0.1 --port 8876
```

Then request `/api/health`, `/api/storage`, `/api/mcp/servers`, `/api/skills`, and `/api/memory/tree`.
