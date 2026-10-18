"""Chat conversations: each user's threads are stored separately and only ever visible to them."""

import uuid

import pytest
from sqlalchemy import select

from app.api.routes import chat as chat_routes
from app.core.auth import create_access_token
from app.db.models import ChatConversation, ChatMessage, Profile, User
from app.providers.embeddings import FakeEmbeddings
from app.providers.llm import FakeLlm
from app.services import chat_service


def _user(db):
    u = User(email=f"conv-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.flush()
    db.add(Profile(user_id=u.id, language="en", timezone="America/Toronto"))
    db.commit()
    return u


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture()
def fake_llm(app):
    app.dependency_overrides[chat_routes.get_llm] = lambda: FakeLlm(canned_answer="Noted.")
    app.dependency_overrides[chat_routes.get_embeddings] = lambda: FakeEmbeddings()
    yield
    for dep in (chat_routes.get_llm, chat_routes.get_embeddings):
        app.dependency_overrides.pop(dep, None)


def _send(client, headers, message, **extra):
    res = client.post("/api/chat", json={"message": message, **extra}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_new_conversation_is_created_and_continued(client, db, fake_llm):
    alice = _user(db)
    h = _headers(alice)
    first = _send(client, h, "I have been sleeping badly for a week, waking at 3am most nights", new_conversation=True)
    cid = first["conversation_id"]
    assert cid
    again = _send(client, h, "Is that worth mentioning to my doctor?", conversation_id=cid)
    assert again["conversation_id"] == cid

    convs = client.get("/api/chat/conversations", headers=h).json()
    assert [c["id"] for c in convs] == [cid]
    assert convs[0]["message_count"] == 4  # two user turns + two replies
    assert convs[0]["title"].startswith("I have been sleeping badly") and len(convs[0]["title"]) <= 61

    msgs = client.get(f"/api/chat/conversations/{cid}/messages", headers=h).json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert all(m["conversation_id"] == cid for m in msgs)


def test_users_only_see_their_own_conversations(client, db, fake_llm):
    alice, bob = _user(db), _user(db)
    a_cid = _send(client, _headers(alice), "Alice's question", new_conversation=True)["conversation_id"]
    b_cid = _send(client, _headers(bob), "Bob's question", new_conversation=True)["conversation_id"]

    assert [c["id"] for c in client.get("/api/chat/conversations", headers=_headers(alice)).json()] == [a_cid]
    assert [c["id"] for c in client.get("/api/chat/conversations", headers=_headers(bob)).json()] == [b_cid]

    # Bob can't read, post into or delete Alice's conversation; it looks like it doesn't exist.
    assert client.get(f"/api/chat/conversations/{a_cid}/messages", headers=_headers(bob)).status_code == 404
    res = client.post("/api/chat", json={"message": "sneaky", "conversation_id": a_cid}, headers=_headers(bob))
    assert res.status_code == 404
    assert client.delete(f"/api/chat/conversations/{a_cid}", headers=_headers(bob)).status_code == 404
    db.expire_all()
    assert db.get(ChatConversation, a_cid) is not None
    assert not db.scalars(select(ChatMessage).where(ChatMessage.user_id == bob.id,
                                                    ChatMessage.content == "sneaky")).all()


def test_conversations_require_sign_in(client):
    assert client.get("/api/chat/conversations").status_code == 401
    assert client.get("/api/chat/conversations/x/messages").status_code == 401


def test_delete_removes_the_conversation_and_its_messages(client, db, fake_llm):
    alice = _user(db)
    h = _headers(alice)
    keep = _send(client, h, "Keep this one", new_conversation=True)["conversation_id"]
    drop = _send(client, h, "Delete this one", new_conversation=True)["conversation_id"]
    assert client.delete(f"/api/chat/conversations/{drop}", headers=h).status_code == 204
    assert [c["id"] for c in client.get("/api/chat/conversations", headers=h).json()] == [keep]
    db.expire_all()
    assert not db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == drop)).all()
    assert db.scalars(select(ChatMessage).where(ChatMessage.conversation_id == keep)).all()


def test_model_history_stays_inside_one_conversation(client, db, fake_llm):
    alice = _user(db)
    h = _headers(alice)
    a = _send(client, h, "Topic A: my knee hurts", new_conversation=True)["conversation_id"]
    b = _send(client, h, "Topic B: I feel anxious", new_conversation=True)["conversation_id"]
    hist_a = chat_service._recent_history(db, alice.id, a)
    hist_b = chat_service._recent_history(db, alice.id, b)
    assert any("knee" in m["content"] for m in hist_a) and not any("anxious" in m["content"] for m in hist_a)
    assert any("anxious" in m["content"] for m in hist_b) and not any("knee" in m["content"] for m in hist_b)


def test_without_a_conversation_the_old_single_stream_still_works(client, db, fake_llm):
    alice = _user(db)
    body = _send(client, _headers(alice), "A message from the iOS Shortcut")
    assert body["conversation_id"] is None
    assert client.get("/api/chat/conversations", headers=_headers(alice)).json() == []
    assert [m["content"] for m in client.get("/api/chat/history", headers=_headers(alice)).json()][:1] == [
        "A message from the iOS Shortcut"]


def test_conversation_title_is_short_and_clean():
    assert chat_service.conversation_title("  Hello   there ") == "Hello there"
    long = "word " * 40
    title = chat_service.conversation_title(long)
    assert title.endswith("…") and len(title) <= 61
    assert chat_service.conversation_title("") == "New chat"
