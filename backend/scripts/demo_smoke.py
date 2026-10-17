"""Live smoke test of the demo flow against a RUNNING server (starts nothing itself).

    .venv/bin/uvicorn app.main:app --port 8000          # in another terminal (DEV_MODE=true)
    .venv/bin/python -m scripts.demo_smoke                # BASE_URL=http://127.0.0.1:8000 by default
    BASE_URL=https://api.example.com ADMIN_SECRET=... .venv/bin/python -m scripts.demo_smoke

Steps: health → seed demo user + planted trend (POST /api/admin/seed-demo; dev mode or
ADMIN_SECRET) → login as the demo user (scripts/seed.py credentials) → risk run (cardio
follow-up) → approve the booking plan (POST /api/appointments/{id}/approve; the agent stops at
awaiting_approval; with the web channel against the mock clinic it confirms directly) → dashboard
(next action) → report share link (/r/{token}) → clinic confirmation (already confirmed by the web
channel, else play the clinic via the confirm link) → dashboard shows the confirmation → ingest
the research corpus → chat with citations. Each check prints its elapsed time.
Prints a checklist; exit code 1 if any step failed. Uses only httpx.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.seed import DEMO_EMAIL, DEMO_PASSWORD  # noqa: E402

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")

results: list[tuple[bool, str, str]] = []
_last = [time.time()]


def check(ok: bool, name: str, detail: str = "") -> bool:
    now = time.time()
    dt, _last[0] = now - _last[0], now
    results.append((bool(ok), name, detail))
    print(f"[{'x' if ok else ' '}] {name} ({dt:.1f}s){(' — ' + detail) if detail else ''}", flush=True)
    return bool(ok)


def local(url: str) -> str:
    """Server-built absolute links use BASE_URL of the server; re-target them at our BASE_URL."""
    parts = urlsplit(url)
    return f"{BASE_URL}{parts.path}" + (f"?{parts.query}" if parts.query else "")


def cardio(cards: dict[str, Any]) -> dict[str, Any]:
    return next((c for c in cards.get("pathways", []) if c["pathway"] == "cardio"), {})


def main() -> int:
    t0 = time.time()
    admin = {"X-Admin-Secret": ADMIN_SECRET} if ADMIN_SECRET else {}
    with httpx.Client(base_url=BASE_URL, timeout=60) as c:
        try:
            h = c.get("/api/health").json()
        except httpx.HTTPError as exc:
            check(False, "server reachable", f"{BASE_URL}: {exc}")
            return 1
        check(h.get("status") == "ok", "health", f"v{h.get('version')} db={h.get('db')} "
              f"scheduler={h.get('scheduler')} providers={h.get('providers')}")

        r = c.post("/api/admin/seed-demo", headers=admin)
        check(r.status_code == 200, "seed demo user + planted trend",
              str(r.json().get("seed")) if r.status_code == 200 else f"{r.status_code} {r.text[:200]}")

        r = c.post("/api/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        if not check(r.status_code == 200, "login", DEMO_EMAIL if r.status_code == 200 else r.text[:200]):
            return 1
        auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = c.post("/api/risk/run", headers=auth)
        rows = {a["pathway"]: a for a in r.json().get("assessments", [])} if r.status_code == 200 else {}
        card = rows.get("cardio", {})
        fired = sorted(f["id"] for f in card.get("fired_rules") or [])
        check(card.get("level") == "followup_recommended" and {"BP-02", "RHY-01"} <= set(fired),
              "risk run: cardio follow-up", f"level={card.get('level')} score={card.get('priority_score')} "
                                            f"fired={fired}")

        pending = c.get("/api/appointments?status=awaiting_approval", headers=auth).json()
        if pending:
            plan = (pending[0].get("approval") or {}).get("channel_plan") or []
            r = c.post(f"/api/appointments/{pending[0]['id']}/approve", headers=auth)
            body = r.json() if r.status_code == 200 else {}
            check(r.status_code == 200 and body.get("status") in ("requested", "confirmed", "awaiting_approval"),
                  "approve booking plan",
                  f"{(pending[0].get('clinic') or {}).get('name')}: plan "
                  f"{[p['channel'] for p in plan if p.get('available')]} → {body.get('status')} via "
                  f"{body.get('channel')}" if body else f"{r.status_code} {r.text[:200]}")
        else:
            check(any(a.get("status") in ("requested", "confirmed") for a in
                      c.get("/api/appointments", headers=auth).json()),
                  "approve booking plan", "no plan awaiting approval (already approved?)")

        cards = c.get("/api/dashboard?language=en", headers=auth).json()
        na = cardio(cards).get("next_action", {})
        check(na.get("type") in ("appointment_requested", "appointment_confirmed"), "dashboard next action",
              f"{na} | unread notifications={cards.get('notifications', {}).get('unread')}")

        reports = c.get("/api/reports", headers=auth).json()
        rep = next((x for x in reports if x.get("pathway") in (None, "cardio")), reports[0] if reports else None)
        if rep:
            page = c.get(local(rep["share_url"]))
            check(page.status_code == 200 and "<html" in page.text.lower(), "report share link",
                  f"{urlsplit(rep['share_url']).path} ({len(page.text)} bytes)")
        else:
            check(False, "report share link", "no report generated")

        appts = [a for a in c.get("/api/appointments", headers=auth).json()
                 if a.get("status") in ("requested", "needs_user_info", "confirmed")]
        appt = appts[0] if appts else None
        if appt:
            clinic = (appt.get("clinic") or {}).get("name")
            if appt.get("status") == "confirmed":  # web channel booked it on the (mock) clinic site
                check(True, "clinic confirms appointment",
                      f"{clinic}: already confirmed via {appt.get('channel')} "
                      f"(ref {appt.get('external_ref')}, slot {(appt.get('chosen_slot') or {}).get('start')})")
            else:
                links = c.get(f"/api/admin/appointments/{appt['id']}/confirm-links", headers=admin).json()
                r = c.post(local(links["links"][0]["url"])) if links.get("links") else None  # GET = page only
                ok = r is not None and r.status_code == 200
                status = c.get(f"/api/appointments/{appt['id']}", headers=auth).json().get("status")
                check(ok and status == "confirmed", "clinic confirms appointment",
                      f"{clinic}: {len(appt.get('proposed_slots') or [])} slots → {status} (confirm link)")
            na = cardio(c.get("/api/dashboard?language=en", headers=auth).json()).get("next_action", {})
            check(na.get("type") == "appointment_confirmed", "dashboard shows confirmation", str(na))
        else:
            check(False, "clinic confirms appointment", "no appointment (share_with_clinic consent?)")

        r = c.post("/api/ingest/corpus?limit_chunks_per_source=20", headers=admin)
        check(r.status_code == 200, "ingest research corpus",
              str({k: r.json().get(k) for k in ("sources", "ingested", "skipped", "failed")})
              if r.status_code == 200 else f"{r.status_code} {r.text[:200]}")

        r = c.post("/api/chat", headers=auth, json={"message": "How should I measure my blood pressure at home?",
                                                     "language": "en"})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        answered = not (body.get("answer") or "").startswith("Sorry — I can't generate")  # LLM error fallback
        check(r.status_code == 200 and bool(body.get("sources")) and answered, "chat with citations",
              f"{len(body.get('sources') or [])} sources, answer: {(body.get('answer') or '')[:70]!r}"
              if r.status_code == 200 else f"{r.status_code} {body or r.text[:200]}")

    passed = sum(ok for ok, *_ in results)
    print(f"\n{passed}/{len(results)} checks passed in {time.time() - t0:.1f}s against {BASE_URL}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
