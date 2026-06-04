# Amadeus Web

Python + React web version of the current Amadeus mobile prototype.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
npm install
```

Configure PostgreSQL in `.env`:

```powershell
Copy-Item .env.example .env
```

Set `DATABASE_URL` to your PostgreSQL database, for example:

```text
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/amadeus_web
```

Create the database before starting the API if it does not already exist:

```powershell
createdb amadeus_web
```

The API creates the required tables on startup. Chat conversations, messages, and user settings are stored in PostgreSQL. Generated voice files remain under `backend/runtime/audio` and are served through `/audio/...`. `AMADEUS_DATABASE_URL` can be used instead of `DATABASE_URL` if you prefer an app-specific variable name.

Start the local CosyVoice2 engine before enabling voice:

```powershell
cd E:\Amadeus\AmadeusVoiceEngine
.\start_engine.ps1
```

The web backend uses `AMADEUS_LOCAL_TTS_URL=http://127.0.0.1:8011` by default and requests CosyVoice2 with `stream=True`. The existing chat voice path still splits long model replies into sentence-sized chunks before sending them to the voice engine.

Start the API:

```powershell
.\.venv\Scripts\python -m uvicorn backend.amadeus_app.main:app --reload --host 127.0.0.1 --port 8000
```

Start the web app:

```powershell
npm run dev
```

Open http://127.0.0.1:5173.

## Notes

- The backend accepts OpenAI-compatible `/chat/completions` providers and applies the same fast/thinking mode parameters used by the Android version.
- Voice uses SiliconFlow `FunAudioLLM/CosyVoice2-0.5B`; the bundled Kurisu reference audio can be used for cloning.
- Live2D assets are served from `public/live2dmodels/steinsGateKurisuNew`.
- Future tools and agent execution can be registered through `backend/amadeus_app/agent_registry.py`.

## Agent Bridge

The backend exposes a simple Agent bridge for Web and HarmonyOS clients:

```http
POST /api/agent/invoke
```

Uploaded text files can be stored before invoking `file_reader`:

```http
POST /api/files/upload
Content-Type: multipart/form-data
```

Form fields:

- `file`: required text-like file.
- `device`: `host` or `mobile`, defaults to `host`.
- `overwrite`: optional boolean, defaults to `false`.

Files are stored as:

```text
agent_uploads/{host|mobile}/{YYYY-MM-DD}/{textType}/{originalFilename}
```

The upload endpoint preserves the uploaded basename for normal filenames. If the client sends a path-like or filesystem-invalid name, the backend strips path segments and replaces invalid characters. Duplicate filenames return `409` unless `overwrite=true`.

Example response:

```json
{
  "ok": true,
  "summary": "已上传 notes.md 到 agent_uploads/mobile/2026-06-04/markdown/notes.md，可交给 file_reader 读取。",
  "file": {
    "device": "mobile",
    "date": "2026-06-04",
    "textType": "markdown",
    "originalFilename": "notes.md",
    "filename": "notes.md",
    "path": "agent_uploads/mobile/2026-06-04/markdown/notes.md",
    "contentType": "text/markdown",
    "sizeBytes": 128,
    "uploadedAt": "2026-06-04T10:00:00+08:00",
    "readableByFileReader": true
  }
}
```

Request:

```json
{
  "action": "call_tool | call_agent | query_capabilities",
  "targetType": "tool | agent | auto",
  "target": "tool or agent name",
  "intent": "user task",
  "payload": {},
  "rawArguments": {},
  "client": "Amaduse HarmonyOS",
  "requestedAt": "2026-06-03T18:30:00+08:00"
}
```

Response:

```json
{
  "ok": true,
  "summary": "execution summary",
  "data": {}
}
```

Current safe tools are `get_current_time`, `calculate`, `echo`, `describe_capabilities`, `web_search`, `doc_writer`, `file_reader`, and `todo_task`. High-risk local tools such as shell, browser automation, and unrestricted file writes are intentionally disabled in the simple agent.

`web_search` is backed by a LangGraph search workflow:

```text
search_agent -> reader_agent -> critic_agent -> writer_agent
```

Example:

```json
{
  "action": "call_tool",
  "targetType": "tool",
  "target": "web_search",
  "intent": "查询 LangGraph Python checkpoint 持久化方案",
  "payload": {
    "query": "LangGraph Python checkpoint persistence",
    "domains": ["docs.langchain.com"],
    "maxResults": 5,
    "fetchContent": true
  },
  "rawArguments": {},
  "client": "Amaduse HarmonyOS",
  "requestedAt": "2026-06-03T18:30:00+08:00"
}
```

For stable production search, run a SearXNG instance and set `AMADEUS_SEARXNG_URL`. If it is empty, the simple agent falls back to DuckDuckGo HTML search.

`doc_writer` is backed by a LangGraph document workflow:

```text
planner_agent -> research_agent -> outline_agent -> markdown_writer_agent -> file_writer_agent
```

The basic version writes Markdown only. Output is restricted to the workspace and defaults to `generated_docs`.

Example:

```json
{
  "action": "call_agent",
  "targetType": "agent",
  "target": "doc_writer_agent",
  "intent": "写一份 Amadeus Agent 系统设计 Markdown 文档",
  "payload": {
    "title": "Amadeus Agent 系统设计",
    "format": "md",
    "audience": "开发者",
    "sections": ["目标", "架构", "接口", "安全边界", "后续工作"],
    "keyPoints": ["区分 local_agent 与 mobile_agent", "工具写入必须限制在工作区", "需要可追踪来源"],
    "useWebSearch": false,
    "outputPath": "generated_docs",
    "fileName": "amadeus-agent-system-design.md",
    "save": true
  },
  "rawArguments": {},
  "client": "Amaduse HarmonyOS",
  "requestedAt": "2026-06-04T10:00:00+08:00"
}
```

`file_reader` is a safe read-only workspace tool. It supports:

- `read`: read a text-like file with optional `startLine`, `endLine`, `maxBytes`, and `includeLineNumbers`.
- `list`: list a workspace directory with optional `recursive`, `includeHidden`, and `maxEntries`.
- `stat`: return file or directory metadata.

It blocks path traversal and restricted paths such as `.env`, `.git`, `.venv`, `node_modules`, `dist`, key/certificate files, local databases, and Python bytecode.

Example:

```json
{
  "action": "call_tool",
  "targetType": "tool",
  "target": "file_reader",
  "intent": "读取 README 的 Agent Bridge 部分",
  "payload": {
    "action": "read",
    "path": "README.md",
    "startLine": 64,
    "endLine": 160,
    "includeLineNumbers": true
  },
  "rawArguments": {},
  "client": "Amaduse HarmonyOS",
  "requestedAt": "2026-06-04T10:00:00+08:00"
}
```

`todo_task` is backed by a LangGraph task workflow:

```text
planner_agent -> task_store_agent -> writer_agent
```

It persists task state to `agent_state/todo_tasks.json` by default. The store path can be changed with `AMADEUS_TODO_TASK_STORE`, but it must remain a non-secret `.json` file inside the workspace. Supported actions are:

- `create`: create one or multiple tasks.
- `plan`: create a parent task and child subtasks from `steps`, `tasks`, bullet lines, or a default execution checklist.
- `list`, `get`, `summary`: inspect current tasks.
- `update`, `start`, `complete`, `block`, `archive`, `add_note`: mutate task state.
- `delete`: hard delete only when `confirmDelete=true`.

Example:

```json
{
  "action": "call_agent",
  "targetType": "agent",
  "target": "todo_task_agent",
  "intent": "拆解并记录 todo task agent 的实现任务",
  "payload": {
    "action": "plan",
    "title": "完成 todo task agent",
    "project": "Amadeus Agent",
    "priority": "high",
    "steps": [
      "设计任务数据结构和状态流转",
      "实现 LangGraph todo_task_agent",
      "注册后端工具和移动端桥接能力",
      "补充 README 和环境变量说明",
      "运行后端编译和接口验证"
    ]
  },
  "rawArguments": {},
  "client": "Amaduse HarmonyOS",
  "requestedAt": "2026-06-04T10:00:00+08:00"
}
```

See `docs/web_search_design.md` for the planned self-hosted Agent web search design.
