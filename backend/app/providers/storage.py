"""File storage for uploads (BACKEND_PLAN C1, Security: private storage).

`LocalStorage` writes to `<settings.storage_dir>/<user_id>/<kind>/<uuid>_<safe_name>` and
returns that path as the `file_url`. `SupabaseStorage` is a stub for the cloud bucket.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Protocol

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
    def delete_user_files(self, user_id: str) -> int: ...


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

    def delete_user_files(self, user_id: str) -> int:
        """Remove every stored file of the user; returns the number of files deleted."""
        if not _SAFE_SEGMENT.match(user_id):
            raise ValueError("unsafe user id")
        d = self.root / user_id
        if not d.exists():
            return 0
        n = sum(1 for p in d.rglob("*") if p.is_file())
        shutil.rmtree(d)
        return n


class SupabaseStorage:
    """TODO(deploy): Supabase Storage private bucket + signed URLs (BACKEND_PLAN Security)."""

    def __init__(self, *_, **__):
        raise NotImplementedError("SupabaseStorage is not implemented yet; use LocalStorage")


def get_storage() -> Storage:
    return LocalStorage(settings.storage_dir)
