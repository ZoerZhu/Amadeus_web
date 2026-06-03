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

Current safe tools are `get_current_time`, `calculate`, `echo`, `describe_capabilities`, and `web_search`. High-risk local tools such as shell, browser automation, and file writes are intentionally disabled in the simple agent.

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

See `docs/web_search_design.md` for the planned self-hosted Agent web search design.
