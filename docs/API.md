# API contract

Base URL (dev): `http://localhost:8000` — from the frontend just call `/api/...`.

| Method | Path          | Request body                 | Response                                  |
|--------|---------------|------------------------------|-------------------------------------------|
| GET    | /api/health   | –                            | `{ "status": "ok" }`                      |
| POST   | /api/chat     | `{ "message": str }`         | `{ "answer": str, "sources": [str] }`     |
| POST   | /api/ingest   | `{ "text": str, "source": str }` | `{ "chunks_added": int }`             |

Add new endpoints here *before* building them so both sides agree on shapes.
