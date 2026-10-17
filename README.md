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
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # fill in API keys as needed
uvicorn app.main:app --reload
```
Docs: http://localhost:8000/docs

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
See `docs/API.md`.
