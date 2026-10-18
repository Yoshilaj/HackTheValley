"""File storage for uploads (BACKEND_PLAN C1, Security: private storage).

`LocalStorage` writes to `<settings.storage_dir>/<user_id>/<kind>/<uuid>_<safe_name>` and
returns that path as the `file_url`. `DatabaseStorage` (STORAGE_BACKEND=database) keeps the bytes in
the `stored_files` table instead, for hosts with a temporary disk. `SupabaseStorage` is a stub.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from app.core.config import settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_name(filename: str | None, default: str = "file") -> str:
    """Basename only, unsafe characters replaced, max 100 chars."""
    name = Path(filename or "").name.strip() or default
    name = _SAFE.sub("_", name).strip("._") or default
    return name[-100:]


class Storage(Protocol):
    def save(self, user_id: str, kind: str, filename: str, data: bytes) -> str: ...
    def save_stream(self, user_id: str, kind: str, filename: str, fileobj: BinaryIO) -> str: ...
    def open(self, file_url: str) -> bytes: ...
    def local_path(self, file_url: str) -> Path: ...
    def delete_user_files(self, user_id: str, db: Any = None) -> int: ...


class LocalStorage:
    """Local-disk storage. `file_url` is the absolute path string."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or settings.storage_dir).resolve()

    def _dir(self, user_id: str, kind: str) -> Path:
        for seg in (user_id, kind):
            if not _SAFE_SEGMENT.match(seg):
                raise ValueError(f"unsafe storage path segment: {seg!r}")
        d = self.root / user_id / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _new_path(self, user_id: str, kind: str, filename: str) -> Path:
        return self._dir(user_id, kind) / f"{uuid.uuid4().hex}_{safe_name(filename)}"

    def save(self, user_id: str, kind: str, filename: str, data: bytes) -> str:
        path = self._new_path(user_id, kind, filename)
        path.write_bytes(data)
        return str(path)

    def save_stream(self, user_id: str, kind: str, filename: str, fileobj: BinaryIO) -> str:
        """Copy a (possibly large) file object to disk in chunks."""
        path = self._new_path(user_id, kind, filename)
        try:
            with path.open("wb") as out:
                shutil.copyfileobj(fileobj, out, length=1024 * 1024)
        except BaseException:  # e.g. upload size limit exceeded mid-stream → no partial file left
            path.unlink(missing_ok=True)
            raise
        return str(path)

    def local_path(self, file_url: str) -> Path:
        path = Path(file_url).resolve()
        if not path.is_relative_to(self.root):
            raise PermissionError("file_url outside storage root")
        return path

    def open(self, file_url: str) -> bytes:
        return self.local_path(file_url).read_bytes()

    def delete_user_files(self, user_id: str, db: Any = None) -> int:
        """Remove every stored file of the user; returns the number of files deleted. (``db`` is
        accepted for the Storage interface and unused: these files are on disk.)"""
        if not _SAFE_SEGMENT.match(user_id):
            raise ValueError("unsafe user id")
        d = self.root / user_id
        if not d.exists():
            return 0
        n = sum(1 for p in d.rglob("*") if p.is_file())
        shutil.rmtree(d)
        return n


class DatabaseStorage:
    """Files in the database (``stored_files``), for hosts whose disk is wiped on every redeploy
    (Render's free plan). ``file_url`` is ``db://<id>``. Kinds in ``LOCAL_KINDS`` (multi-GB Apple
    Health exports, parsed straight after upload and not kept) still go to local disk, and paths
    from before the switch keep working, so ``open`` / ``local_path`` accept both."""

    PREFIX = "db://"
    LOCAL_KINDS = frozenset({"apple_health"})

    def __init__(self, local: LocalStorage | None = None):
        self.local = local or LocalStorage(settings.storage_dir)

    @staticmethod
    def _session():
        from app.db.session import SessionLocal  # late import: storage is imported by db-less code

        return SessionLocal()

    def save(self, user_id: str, kind: str, filename: str, data: bytes) -> str:
        if kind in self.LOCAL_KINDS:
            return self.local.save(user_id, kind, filename, data)
        for seg in (user_id, kind):
            if not _SAFE_SEGMENT.match(seg):
                raise ValueError(f"unsafe storage path segment: {seg!r}")
        from app.db.models import StoredFile

        with self._session() as db:
            row = StoredFile(user_id=user_id, kind=kind, filename=safe_name(filename), size=len(data), content=data)
            db.add(row)
            db.commit()
            return f"{self.PREFIX}{row.id}"

    def save_stream(self, user_id: str, kind: str, filename: str, fileobj: BinaryIO) -> str:
        if kind in self.LOCAL_KINDS:
            return self.local.save_stream(user_id, kind, filename, fileobj)
        # Everything else is small (documents are capped at 20 MB by the upload route).
        return self.save(user_id, kind, filename, fileobj.read())

    def _row(self, file_url: str):
        from app.db.models import StoredFile

        with self._session() as db:
            row = db.get(StoredFile, file_url[len(self.PREFIX):])
            if row is None:
                raise FileNotFoundError(file_url)
            db.expunge(row)
            return row

    def open(self, file_url: str) -> bytes:
        if not file_url.startswith(self.PREFIX):
            return self.local.open(file_url)
        return self._row(file_url).content

    def local_path(self, file_url: str) -> Path:
        """A real file for code that needs a path: database files are copied to a cache on disk."""
        if not file_url.startswith(self.PREFIX):
            return self.local.local_path(file_url)
        row = self._row(file_url)
        cache = self.local.root / "_db_cache"
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"{row.id}_{row.filename}"
        if not path.exists():
            path.write_bytes(row.content)
        return path

    def delete_user_files(self, user_id: str, db: Any = None) -> int:
        """Delete the user's stored files. Pass the caller's session when it is mid-transaction on
        this user (DELETE /api/me): the rows are then deleted in that transaction, uncommitted. A
        second connection would wait on that transaction's row locks while it waits on us."""
        from sqlalchemy import delete

        from app.db.models import StoredFile

        stmt = delete(StoredFile).where(StoredFile.user_id == user_id)
        if db is not None:
            n = db.execute(stmt).rowcount or 0
        else:
            with self._session() as own:
                n = own.execute(stmt).rowcount or 0
                own.commit()
        return n + self.local.delete_user_files(user_id)


class SupabaseStorage:
    """TODO(deploy): Supabase Storage private bucket + signed URLs (BACKEND_PLAN Security)."""

    def __init__(self, *_, **__):
        raise NotImplementedError("SupabaseStorage is not implemented yet; use LocalStorage")


def get_storage() -> Storage:
    if settings.storage_backend == "database":
        return DatabaseStorage(LocalStorage(settings.storage_dir))
    return LocalStorage(settings.storage_dir)
