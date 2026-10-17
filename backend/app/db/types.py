"""Portable column types."""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, MetaData, event, text
from sqlalchemy.types import TypeDecorator

EMBEDDING_DIM = 768  # nomic-embed-text-v1.5


def is_postgres(engine_or_dialect) -> bool:
    """Accepts an Engine, Connection or Dialect."""
    dialect = getattr(engine_or_dialect, "dialect", engine_or_dialect)
    return getattr(dialect, "name", "") == "postgresql"


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite drops tzinfo otherwise).
    Naive datetimes passed in are assumed to be UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value if dialect.name == "postgresql" else value.replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class VectorType(TypeDecorator):
    """pgvector `vector(dim)` on Postgres; JSON list[float] elsewhere (SQLite dev/tests).

    On SQLite similarity search must be done in Python (load rows, compute cosine).
    On Postgres use the pgvector comparators, e.g.
    `ResearchChunk.embedding.cosine_distance(q)` — available because the column's
    dialect impl is pgvector's Vector.
    """

    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = EMBEDDING_DIM):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]


# ---------------------------------------------------------------- Postgres search indexes
# Runs after every ``metadata.create_all`` (i.e. ``init_db``), not only when the table is new, so an
# existing deployment gets the index too. SQLite: no-op (BM25 is computed in-process).
POSTGRES_SEARCH_INDEXES = (
    # Lexical side of hybrid RAG retrieval (app/rag/lexical.py uses the same expression).
    "CREATE INDEX IF NOT EXISTS ix_research_chunks_fts ON research_chunks "
    "USING gin (to_tsvector('english', text))",
)


@event.listens_for(MetaData, "after_create")
def _create_search_indexes(target, connection, **kw):
    if connection.dialect.name != "postgresql" or "research_chunks" not in target.tables:
        return
    for ddl in POSTGRES_SEARCH_INDEXES:
        connection.execute(text(ddl))


__all__ = ["VectorType", "UTCDateTime", "is_postgres", "EMBEDDING_DIM", "datetime"]
