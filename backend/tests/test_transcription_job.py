"""process_pending_uploads job: audio documents → transcript (BACKEND_PLAN C1/C5)."""

import uuid

from app.db.models import UploadedDocument, User
from app.jobs import JOBS
from app.providers.storage import LocalStorage
from app.providers.stt import FakeStt
from app.services.transcription_jobs import process_pending, transcribe_document


class BrokenStt:
    name = "broken"

    def transcribe(self, audio, language, filename=None):
        raise RuntimeError("asr down")


def _doc(db, storage, data=b"audio-bytes"):
    u = User(email=f"tr-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.flush()
    url = storage.save(u.id, "audio", "memo.m4a", data)
    d = UploadedDocument(user_id=u.id, kind="audio", file_url=url, status="pending_transcription",
                         extracted={"filename": "memo.m4a", "media_type": "audio/mp4"})
    db.add(d)
    db.commit()
    return d


def test_job_registered():
    assert JOBS["process_pending_uploads"].interval_minutes == 10


def test_transcribe_document(db, tmp_path):
    st = LocalStorage(tmp_path)
    d = _doc(db, st)
    transcribe_document(db, d, FakeStt(text="My blood pressure was a bit high"), st)
    assert d.status == "transcribed"
    assert d.extracted["transcript"]["text"] == "My blood pressure was a bit high"
    assert d.extracted["transcript"]["language"] == "en"  # default language (Canada, English)
    assert d.extracted["filename"] == "memo.m4a"


def test_transcribe_failure_marks_failed(db, tmp_path):
    st = LocalStorage(tmp_path)
    d = _doc(db, st)
    transcribe_document(db, d, BrokenStt(), st)
    assert d.status == "failed" and "asr down" in d.extracted["error"]


def test_process_pending_batch(db, tmp_path):
    st = LocalStorage(tmp_path)
    docs = [_doc(db, st) for _ in range(2)]
    counts = process_pending(db, FakeStt(text="hello"), st)
    assert counts["transcribed"] >= 2
    db.expire_all()
    assert all(db.get(UploadedDocument, d.id).status == "transcribed" for d in docs)
    assert process_pending(db, FakeStt(text="x"), st)["pending"] == 0
