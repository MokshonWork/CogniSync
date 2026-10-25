# CogniSync

CogniSync is a local web app that lets you ask a question to several LLMs at once instead of picking a single provider. It sends your query through OpenRouter to a configurable group of models — a "council" — shows the individual responses side by side, has each model anonymously rank its peers, and then synthesizes a final answer with a designated chairman model.

## How it works

1. **Stage 1: First opinions**. The user query is given to all LLMs individually, and the responses are collected. The individual responses are shown in a "tab view", so that the user can inspect them all one by one.
2. **Stage 2: Review**. Each individual LLM is given the responses of the other LLMs. Under the hood, the LLM identities are anonymized so that the LLM can't play favorites when judging their outputs. The LLM is asked to rank them in accuracy and insight.
3. **Stage 3: Final response**. The designated Chairman of the LLM Council takes all of the model's responses and compiles them into a single final answer that is presented to the user.

## Features

- **Multi-model council** with models selected from the live OpenRouter catalog — in default *free-testing mode* only catalog-verified free models (zero prompt/completion pricing, `:free` variants) are used, and paid model ids are rejected with an explanatory error instead of silently billing.
- **Anonymous peer review** with aggregate rankings and de-anonymized evaluations in the UI.
- **DuckDuckGo Search** — optional server-side grounding via the `ddgs` library (no API key), with cited results shown next to the answer.
- **GitHub integration** — read-only official REST API: search repositories, read files, list issues. Set `GITHUB_TOKEN` server-side.
- **Notion integration** — read-only official API: search shared pages, read page content. Set `NOTION_TOKEN` (internal integration secret) and share pages with the integration.
- **Document attachments** — attach files to a message; text is extracted from `.docx`, `.pdf`, images, CSV and text and fed to the council.
- **Persistent composer** — the chat composer stays visible and selectable during inference; follow-up messages queued while the council runs are submitted automatically when the run finishes, in the same conversation with prior-turn context.


## Setup

### 1. Install Dependencies

The project uses [uv](https://docs.astral.sh/uv/) for project management.

**Backend:**
```bash
uv sync
```

**Frontend:**
```bash
cd frontend
npm install
cd ..
```

### 2. Configure API Key

Create a `.env` file in the project root:

```bash
OPENROUTER_API_KEY=sk-or-v1-...
```

Get your API key at [openrouter.ai](https://openrouter.ai/). Make sure to purchase the credits you need, or sign up for automatic top up.

### 3. Configure Models (Optional)

Council members are resolved from the live free-model catalog; choose them in
the Models view. To force a chairman, set `CHAIRMAN_MODEL` in `.env`. Paid
model ids are rejected unless `FREE_TESTING_MODE=false`.

## Running the Application

**Option 1: Use the start script**
```bash
./start.sh
```

**Option 2: Run manually**

Terminal 1 (Backend):
```bash
uv run python -m backend.main
```

Terminal 2 (Frontend):
```bash
cd frontend
npm run dev
```

Then open http://localhost:5173 in your browser.

## Tech Stack

- **Backend:** FastAPI (Python 3.10+), async httpx, OpenRouter API
- **Frontend:** React + Vite, react-markdown for rendering
- **Storage:** JSON files in `data/conversations/`
- **Package Management:** uv for Python, npm for JavaScript

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | yes | OpenRouter API key used for all model queries |
| `FREE_TESTING_MODE` | no | `true` (default): council uses only catalog-verified free models; paid ids are rejected |
| `COUNCIL_MODEL_COUNT` | no | How many council members to assemble (default 4) |
| `CHAIRMAN_MODEL` | no | Model id for the synthesizing chairman |
| `MODEL_CATALOG_TTL_SECONDS` | no | Server-side catalog cache lifetime (default 600); `POST /api/models/refresh` forces a re-fetch |
| `TITLE_MODEL` | no | Model used to generate conversation titles (defaults to the first free fallback model) |
| `OPENROUTER_MAX_CONCURRENCY` | no | Maximum concurrent OpenRouter requests (default 5) |
| `RATE_LIMIT_MAX_ATTEMPTS` | no | Total attempts per request, including the first (default 3) |
| `RATE_LIMIT_BACKOFF_CAP_SECONDS` | no | Exponential backoff cap in seconds (default 30) |
| `RATE_LIMIT_TOTAL_TIMEOUT` | no | Whole-request retry budget in seconds (default 90) |
| `DDG_MAX_RESULTS` | no | Max DuckDuckGo results used for grounding (default 8) |
| `DDG_TIMEOUT` | no | DuckDuckGo search timeout in seconds (default 20) |
| `GITHUB_TOKEN` | no | GitHub integration token (read-only official REST API; without it, public repos at lower rate limits) |
| `NOTION_TOKEN` | no | Notion internal integration secret; share pages with the integration in Notion |
| `VITE_API_URL` | no | Frontend override for the backend URL (default `http://localhost:8001`); set in `frontend/.env` |

See `.env.example` for backend placeholders and `frontend/.env.example` for the
frontend override. `.env` files are gitignored and never committed.

## API Endpoints (backend, port 8001)

- `GET /` — health check (reports provider configuration, never secrets)
- `GET /api/models` — free-eligible model catalog (cached server-side)
- `POST /api/models/refresh` — force catalog re-fetch
- `GET /api/conversations` — list conversations
- `POST /api/conversations` — create a conversation
- `POST /api/conversations/{id}/message` — run the 3-stage council on a message (persists the user message and result even on failure)
- `POST /api/conversations/{id}/message/stream` — multipart variant with file attachments
- `GET /api/conversations/{id}` — conversation history
- `POST /api/query` — one-shot council query by model id
- `GET /api/tools/status` — integration configuration state (Web Search / GitHub / Notion)
- `POST /api/tools/test` — live connection test for one integration
- `POST /api/tools/github/search|file|issues` — read-only GitHub operations
- `POST /api/tools/notion/search|page` — read-only Notion operations

## Tests

Backend tests live in `backend/tests/`:

```bash
uv run python -m pytest backend/tests -q
```

Frontend production build:

```bash
cd frontend
npm run build
```

## Known Limitations

- Deleting `data/conversations/` while running requires a restart to recreate it.
- Council members are configured in `backend/config.py`; there is no per-request model selection UI for the chairman.
- Reasoning-effort-heavy models may take noticeably longer in Stage 2.
