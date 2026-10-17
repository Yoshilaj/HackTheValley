# API contract

Base URL (dev): `http://localhost:8000` — from the frontend just call `/api/...`.

| Method | Path          | Request body                 | Response                                  |
|--------|---------------|------------------------------|-------------------------------------------|
| GET    | /api/health   | –                            | `{ "status": "ok" }`                      |
| POST   | /api/chat     | `{ "message": str }`         | `{ "answer": str, "sources": [str] }`     |
| POST   | /api/ingest   | `{ "text": str, "source": str }` | `{ "chunks_added": int }`             |

Add new endpoints here *before* building them so both sides agree on shapes.

## Dashboard / risk (planned — see docs/specs/phase-a-assessments.md §11 for the full JSON)

| Method | Path | Response |
|--------|------|----------|
| GET | /api/dashboard | `{ pathways: PathwayCard[], metrics_summary, appointments, notifications }` |
| GET | /api/risk/latest | `PathwayCard[]` |
| GET | /api/risk/history?weeks=8 | `[{ week, pathway, level, priority_score }]` |

`PathwayCard` = `{ pathway, level, priority_score (0–100 | null), score_kind, score_label, headline, drivers[], missing_inputs[], data_quality, trend[], probabilities[], preventive_measures[], next_action, disclaimer }`.
`level` ∈ `insufficient_data | invalid_measurement | no_finding | watch | followup_recommended | emergency`.
