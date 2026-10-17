# HackTheValley

Hackathon project. Two independent halves:

| Folder      | Stack                          | Owner    | Runs on                 |
|-------------|--------------------------------|----------|-------------------------|
| `frontend/` | React (Vite), HTML, CSS        | frontend | http://localhost:5173   |
| `backend/`  | Python, FastAPI, RAG / ML      | backend  | http://localhost:8000   |

The frontend dev server proxies any request to `/api/*` to the backend, so the
frontend code never needs to hardcode the backend URL.

## Quick start

### Backend
Python 3.12 venv managed with [uv](https://docs.astral.sh/uv/) (`backend/.python-version` pins 3.12).
Everything runs offline with deterministic fake providers; no API key is needed for the demo.
```bash
cd backend
uv venv --python 3.12 .venv                                   # once
uv pip install --python .venv/bin/python -r requirements.txt  # once / when requirements change
cp .env.example .env                                          # defaults = fake providers, SQLite, DEV_MODE=true
.venv/bin/python -m scripts.seed_demo                         # demo user demo@example.com / demo1234 + 35-day planted trend
.venv/bin/python -m scripts.ingest_corpus                     # research corpus → RAG (fake embeddings unless EMBEDDING_PROVIDER=voyage)
.venv/bin/uvicorn app.main:app --reload                       # http://localhost:8000, Swagger at /docs
.venv/bin/python -m scripts.demo_smoke                        # in a 2nd terminal: live demo checklist against BASE_URL (default :8000)
.venv/bin/pytest -q                                           # full test suite (~15 s), incl. tests/test_e2e_demo.py
```
- `python -m scripts.seed` creates only the demo user and prints a device token for the iOS Shortcut
  ([docs/shortcut.md](docs/shortcut.md)). `POST /api/admin/seed-demo` does the same as `seed_demo` over HTTP (dev mode or `X-Admin-Secret`).
- DB: SQLite at `backend/data/dev.db` by default; `DATABASE_URL=postgresql://...` for Postgres + pgvector.
  Tables are created at startup (no Alembic). Delete `data/dev.db` to start over.
- Auth: `Authorization: Bearer <jwt>` from `POST /api/auth/login`; the iOS Shortcut uses `X-Device-Token`;
  while `DEV_MODE=true` the frontend may send `X-User-Id: <user id>` instead.
- `GET /api/health` shows which provider each integration resolves to, the DB kind and the scheduler state.
- Deploy: `backend/Dockerfile` (1 uvicorn worker, so the in-process scheduler runs once) and the Render
  Blueprint `render.yaml` at the repo root (`rootDir: backend`). Never deployed yet.

- Region: **Canada only, English only.** Emergency guidance is 911, the 811 nurse line (Health Link /
  Health811) and 9-8-8 (Suicide Crisis Helpline); default timezone `America/Toronto`; long-term cardio
  context is the Framingham Risk Score as used by the CCS (Suita kept in code, used only for `DEFAULT_COUNTRY=JP`);
  home-BP thresholds follow Hypertension Canada (≥135/85). Cholesterol is stored in mg/dL and shown in mmol/L.
  Check that your local `.env` does not still say `SCHEDULER_TIMEZONE=Asia/Tokyo`.

#### Providers and keys (all in `backend/.env`)

| Integration | Switch | Works with fakes | Needs for the real thing |
|---|---|---|---|
| LLM (chat answers, extraction, report summary, booking tool loop, BP-photo vision) | `LLM_PROVIDER=fake\|anthropic` | yes: canned answer, regex extraction, deterministic booking | `ANTHROPIC_API_KEY` |
| Embeddings (RAG retrieval, story-recall scoring) | `EMBEDDING_PROVIDER=fake\|voyage` | yes: hashed bag-of-words vectors | `VOYAGE_API_KEY`; re-run `scripts.ingest_corpus --force` after switching |
| Speech-to-text (voice chat, story task) | `STT_PROVIDER=fake\|openai` | yes: empty / fixed transcript | `OPENAI_API_KEY` |
| Text-to-speech (story audio) | `TTS_PROVIDER=fake\|openai` | yes: silent WAV | `OPENAI_API_KEY` |
| E-mail (clinic booking request, user notifications) | `EMAIL_PROVIDER=auto\|resend\|fake` | yes: logged, not sent | `RESEND_API_KEY` + `RESEND_FROM_EMAIL` (verified domain); `CLINIC_FALLBACK_EMAIL` = demo inbox |
| Google Calendar (FreeBusy, tentative event) | automatic | yes: default slots, no event | `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET`, redirect `BASE_URL/api/integrations/google/callback` |
| Clinic search | automatic | yes: seeded (fictional) Toronto clinics | `GOOGLE_MAPS_API_KEY` (Places API) |
| PD vowel model | – | local `backend/models/pd_vowel_experimental_v1` | `HF_TOKEN` only to push it to the Hub |

A real provider selected without its key does not crash startup; the first request that needs it returns
`503 {"error": "provider_not_configured", "detail": "... KEY is not set"}`.

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Repo layout
```
frontend/
  index.html            # single HTML entry
  src/
    main.jsx            # React mount point
    App.jsx             # root component / routing
    api/client.js       # all fetch calls to the backend live here
    components/         # reusable UI pieces
    pages/              # page-level components
    styles/             # plain CSS
backend/
  app/
    main.py             # FastAPI app, CORS, router wiring
    core/config.py      # settings loaded from .env
    api/routes/         # one file per endpoint group
    schemas/            # pydantic request/response models
    rag/                # ingest -> chunk -> embed -> retrieve
    ml/                 # any non-RAG models / inference
  data/raw              # source documents (gitignored)
  data/processed        # chunks / vector index (gitignored)
  tests/
docs/                   # design notes, pitch, API contract
```

## API contract (keep in sync between teams)
See [docs/API.md](docs/API.md). Backend status: [docs/BACKEND_PLAN.md](docs/BACKEND_PLAN.md) (top section); morning hand-off: [docs/HANDOFF.md](docs/HANDOFF.md).
