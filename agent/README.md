# Camel Pet Agent

Python sidecar that hosts the CAMEL-AI `ChatAgent` for Camel Pet. Speaks to the Tauri shell over a local WebSocket.

## Setup

```bash
cd agent
poetry install
cp .env.example .env   # then put your ANTHROPIC_API_KEY in .env
poetry run camel-agent
```

The server binds to `127.0.0.1:8765` by default. Override with `CAMEL_PET_PORT`.

## Layout

- `camel_pet_agent/server.py` — FastAPI app, `/ws` endpoint.
- `camel_pet_agent/agent.py` — CAMEL `ChatAgent` wrapper with tools.
- `camel_pet_agent/memory.py` — SQLite chat history.
- `camel_pet_agent/tools/` — clipboard, timer, etc.
- `camel_pet_agent/scheduler.py` — proactive messages.
- `camel_pet_agent/personality.py` — system prompt.
- `build.py` — PyInstaller driver for release bundling.
