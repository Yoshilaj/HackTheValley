import uuid

from sqlalchemy import select

from app.core.auth import create_access_token
from app.db.models import AuditLog, Consent, Notification, Profile, User
from app.providers.email import FakeEmail
from app.services import notify as svc


def make_user(db, consents=(), language="en", timezone="America/Toronto", contact_email="contact@example.com"):
    user = User(email=f"u-{uuid.uuid4().hex[:10]}@example.com")
    db.add(user)
    db.flush()
    user.profile = Profile(
        user_id=user.id, display_name="Test User", language=language, timezone=timezone,
        emergency_contact={"name": "C", "email": contact_email} if contact_email else None,
    )
    for purpose in consents:
        db.add(Consent(user_id=user.id, purpose=purpose))
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------- notify()

def test_notify_creates_row_and_audit(db):
    user = make_user(db)
    row = svc.notify(db, user.id, svc.KIND_DATA_NEEDED, "Need BP", "Please log BP", payload={"x": 1})
    assert row.id and row.read_at is None and row.payload == {"x": 1}
    assert db.get(Notification, row.id).kind == "data_needed"
    assert db.scalar(select(AuditLog).where(AuditLog.resource == f"notification:{row.id}")) is not None


def test_notify_email_requires_consent(db):
    fake = FakeEmail()
    user = make_user(db, consents=())
    row = svc.notify(db, user.id, svc.KIND_RISK_LEVEL_CHANGE, "t", "b", email=True, email_provider=fake)
    assert fake.outbox == []
    assert row.payload["email"] == {"sent": False, "reason": "no_consent"}


def test_notify_email_sent_with_consent(db):
    fake = FakeEmail()
    user = make_user(db, consents=("data_processing",))
    row = svc.notify(db, user.id, svc.KIND_APPOINTMENT_CONFIRMED, "Booked", "Line1\nLine2", email=True,
                     email_provider=fake)
    assert len(fake.outbox) == 1
    msg = fake.outbox[0]
    assert msg.to == [user.email] and msg.subject == "Booked" and "This is not a diagnosis" in msg.html
    assert row.payload["email"]["sent"] is True and row.payload["email"]["result"]["provider"] == "fake"


def test_notify_email_revoked_consent(db):
    from datetime import datetime, timezone

    fake = FakeEmail()
    user = make_user(db, consents=("data_processing",))
    for c in user.consents:
        c.revoked_at = datetime.now(timezone.utc)
    db.commit()
    svc.notify(db, user.id, svc.KIND_IMPORT_FINISHED, "t", "b", email=True, email_provider=fake)
    assert fake.outbox == []


# ---------------------------------------------------------------- service queries

def test_list_unread_and_mark(db):
    user = make_user(db)
    a = svc.notify(db, user.id, svc.KIND_DATA_NEEDED, "a", None)
    svc.notify(db, user.id, svc.KIND_DATA_NEEDED, "b", None)
    assert svc.unread_count(db, user.id) == 2
    assert svc.mark_read(db, user.id, a.id).read_at is not None
    assert [n.title for n in svc.list_notifications(db, user.id, unread_only=True, limit=10)] == ["b"]
    other = make_user(db)
    assert svc.mark_read(db, other.id, a.id) is None
    assert svc.mark_all_read(db, user.id) == 1 and svc.unread_count(db, user.id) == 0


# ---------------------------------------------------------------- endpoints

def test_notification_endpoints(client, db):
    user = make_user(db)
    headers = {"Authorization": f"Bearer {create_access_token(user.id)}"}
    first = svc.notify(db, user.id, svc.KIND_FOLLOWUP_RECOMMENDED, "first", "b1")
    svc.notify(db, user.id, svc.KIND_WEEKLY_SUMMARY, "second", "b2")

    r = client.get("/api/notifications", headers=headers)
    assert r.status_code == 200 and len(r.json()) == 2
    assert client.get("/api/notifications/unread-count", headers=headers).json() == {"unread": 2}

    r = client.post(f"/api/notifications/{first.id}/read", headers=headers)
    assert r.status_code == 200 and r.json()["read_at"] is not None
    r = client.get("/api/notifications?unread=true&limit=50", headers=headers)
    assert [n["title"] for n in r.json()] == ["second"]

    assert client.post("/api/notifications/read-all", headers=headers).json() == {"updated": 1}
    assert client.get("/api/notifications/unread-count", headers=headers).json() == {"unread": 0}
    assert client.post("/api/notifications/nope/read", headers=headers).status_code == 404


def test_notification_endpoints_scoped_and_auth(client, db, auth_headers):
    other = make_user(db)
    n = svc.notify(db, other.id, svc.KIND_DATA_NEEDED, "secret", None)
    assert client.get("/api/notifications").status_code == 401
    assert client.post(f"/api/notifications/{n.id}/read", headers=auth_headers).status_code == 404
    titles = [x["title"] for x in client.get("/api/notifications", headers=auth_headers).json()]
    assert "secret" not in titles
