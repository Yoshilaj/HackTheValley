from collections.abc import Iterator
from pathlib import Path

import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.db.types import is_postgres

log = logging.getLogger(__name__)


def _normalise_url(url: str) -> str:
    # Render/Heroku style "postgres://" -> SQLAlchemy + psycopg3 driver.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(url: str) -> Engine:
    url = _normalise_url(url)
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # timeout=30: wait up to 30 s for another process's write lock (uvicorn + a script such as
        # scripts.test_booking share data/dev.db) instead of failing after the 5 s default.
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        file_db = bool(path) and path != ":memory:"

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):  # enforce ON DELETE CASCADE in SQLite
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            if file_db:
                # WAL: readers never block the writer and vice versa (several local processes).
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return eng
    # Postgres (Neon). Neon's pooled endpoint (the "-pooler" host) is PgBouncer in transaction mode,
    # which can't keep psycopg's server-side prepared statements between transactions, so they are
    # off (prepare_threshold=None). Neon's free tier also suspends after a few idle minutes:
    # pre_ping drops dead connections and pool_recycle retires any older than 5 minutes. A small
    # pool suits the single uvicorn worker and Neon's connection limit.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=5,
        connect_args={"prepare_threshold": None},
    )


engine: Engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def configure(url: str) -> None:
    """Rebind engine/SessionLocal to another URL (used by tests and scripts)."""
    global engine
    engine = make_engine(url)
    SessionLocal.configure(bind=engine)


def init_db(bind: Engine | None = None) -> None:
    """Create all tables. Hackathon: no Alembic, create_all at startup.
    TODO(post-hackathon): replace with Alembic migrations."""
    import app.db.models  # noqa: F401  (register models)

    bind = bind or engine
    if is_postgres(bind):
        with bind.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind)
    add_missing_columns(bind)
    add_missing_indexes(bind)


def add_missing_indexes(bind: Engine | None = None) -> list[str]:
    """``create_all`` only creates a model's indexes together with a NEW table, so an index added to
    an existing table later (e.g. ``ix_chat_messages_conversation_time``) is created here.
    Idempotent (checkfirst). Returns the names of the indexes created."""
    import app.db.models  # noqa: F401

    bind = bind or engine
    insp = inspect(bind)
    existing_tables = set(insp.get_table_names())
    created: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {ix["name"] for ix in insp.get_indexes(table.name)}
        for index in table.indexes:
            if index.name and index.name not in have:
                index.create(bind, checkfirst=True)
                created.append(index.name)
    if created:
        log.info("added missing indexes: %s", ", ".join(created))
    return created


def add_missing_columns(bind: Engine | None = None) -> list[str]:
    """Tiny idempotent migration: ``create_all`` creates missing TABLES but never adds COLUMNS to an
    existing table, so a model column added later (e.g. the onboarding fields on ``profiles``) is
    added here with ``ALTER TABLE … ADD COLUMN``. Only safe additions are made: nullable, non-key
    columns without a foreign key / unique constraint / server default — existing rows get NULL.
    Anything else is logged and left for a real migration. SQLite + Postgres (``IF NOT EXISTS`` on
    Postgres so concurrent workers cannot race). Returns ["table.column", …] that were added."""
    import app.db.models  # noqa: F401

    bind = bind or engine
    insp = inspect(bind)
    existing_tables = set(insp.get_table_names())
    added: list[str] = []
    pg = is_postgres(bind)
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            if not col.nullable or col.primary_key or col.foreign_keys or col.unique or col.server_default is not None:
                log.warning("schema drift: cannot auto-add %s.%s (not a plain nullable column)", table.name, col.name)
                continue
            prep = bind.dialect.identifier_preparer
            ddl = (f"ALTER TABLE {prep.quote(table.name)} ADD COLUMN {'IF NOT EXISTS ' if pg else ''}"
                   f"{prep.quote(col.name)} {col.type.compile(dialect=bind.dialect)}")
            with bind.begin() as conn:
                conn.execute(text(ddl))
            added.append(f"{table.name}.{col.name}")
    if added:
        log.info("added missing columns: %s", ", ".join(added))
    return added


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
