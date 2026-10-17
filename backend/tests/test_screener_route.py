"""PD screener routes (spec phase-a §5.1)."""
from sqlalchemy import select

from app.db.models import ScreenerResponse


def test_get_items_by_language(client, auth_headers):
    en = client.get("/api/screener/pd", headers=auth_headers).json()  # demo user language = en
    assert en["language"] == "en" and len(en["items"]) == 8 and en["items"][0]["type"] == "motor"
    assert en["items"][0]["text"].startswith("New shaking") and en["period"].startswith("In the last 3 months")
    ja = client.get("/api/screener/pd?language=ja", headers=auth_headers).json()
    assert ja["language"] == "ja" and len(ja["items"]) == 8


def test_post_fires_pd01(client, auth_headers, db, demo_user):
    r = client.post("/api/screener/pd", headers=auth_headers,
                    json={"answers": {"tremor_rest": True, "smell_loss": True, "falls": False}})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pd01_would_fire"] is True and body["next_tasks"] == ["sustained_vowel", "finger_tap"]
    assert body["motor_count"] == 1 and body["nonmotor_count"] == 1 and body["score"] == 2
    row = db.scalar(select(ScreenerResponse).where(ScreenerResponse.id == body["id"]))
    assert row.answers == {"tremor_rest": True, "smell_loss": True, "falls": False}


def test_post_all_no(client, auth_headers):
    r = client.post("/api/screener/pd", headers=auth_headers, json={"answers": {"tremor_rest": False}})
    assert r.json()["pd01_would_fire"] is False and r.json()["next_tasks"] == []


def test_post_unknown_item(client, auth_headers):
    assert client.post("/api/screener/pd", headers=auth_headers, json={"answers": {"nope": True}}).status_code == 422


def test_requires_auth(client):
    assert client.get("/api/screener/pd").status_code == 401


def test_post_accepts_legacy_ids(client, auth_headers, db):
    r = client.post("/api/screener/pd", headers=auth_headers, json={"answers": {"pd_rest_tremor": True, "pd_soft_voice": True}})
    assert r.status_code == 201 and r.json()["pd01_would_fire"] is True
    row = db.scalar(select(ScreenerResponse).where(ScreenerResponse.id == r.json()["id"]))
    assert row.answers == {"tremor_rest": True, "soft_voice": True}
