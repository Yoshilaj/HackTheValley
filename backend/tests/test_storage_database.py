"""DatabaseStorage: uploads kept in the stored_files table (STORAGE_BACKEND=database)."""

import io
import uuid

import pytest

from app.core.config import settings
from app.db.models import StoredFile, User
from app.providers.storage import DatabaseStorage, LocalStorage, get_storage


@pytest.fixture()
def user(db):
    u = User(email=f"store-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def storage(tmp_path):
    return DatabaseStorage(LocalStorage(tmp_path))


def test_save_open_and_local_path(storage, user, db, tmp_path):
    url = storage.save(user.id, "document", "lab results.pdf", b"%PDF-1.4 hello")
    assert url.startswith("db://")
    assert storage.open(url) == b"%PDF-1.4 hello"
    row = db.get(StoredFile, url[5:])
    assert row.user_id == user.id and row.filename == "lab_results.pdf" and row.size == 14
    path = storage.local_path(url)  # copied to a cache for code that needs a real file
    assert path.read_bytes() == b"%PDF-1.4 hello" and path.is_relative_to(tmp_path)


def test_streams_and_big_imports(storage, user):
    url = storage.save_stream(user.id, "bp_photo", "monitor.jpg", io.BytesIO(b"jpegbytes"))
    assert url.startswith("db://") and storage.open(url) == b"jpegbytes"
    # Apple Health exports stay on disk: parsed right away, too big for the database.
    local = storage.save_stream(user.id, "apple_health", "export.zip", io.BytesIO(b"zipbytes"))
    assert not local.startswith("db://") and storage.open(local) == b"zipbytes"


def test_delete_user_files_removes_database_and_disk_files(storage, user):
    storage.save(user.id, "document", "a.pdf", b"a")
    storage.save(user.id, "document", "b.pdf", b"b")
    storage.save(user.id, "apple_health", "export.zip", b"z")
    assert storage.delete_user_files(user.id) == 3


def test_missing_file_and_unsafe_segments(storage, user):
    with pytest.raises(FileNotFoundError):
        storage.open("db://does-not-exist")
    with pytest.raises(ValueError):
        storage.save("../escape", "document", "x.pdf", b"x")


def test_setting_selects_the_backend(monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "database")
    assert isinstance(get_storage(), DatabaseStorage)
    monkeypatch.setattr(settings, "storage_backend", "local")
    assert isinstance(get_storage(), LocalStorage)
