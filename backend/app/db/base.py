import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import UTCDateTime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    # Every `Mapped[datetime]` column is a tz-aware UTC datetime.
    type_annotation_map = {datetime: UTCDateTime()}


class IdMixin:
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id, sort_order=-100)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
