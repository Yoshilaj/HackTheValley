"""Ingest the curated research corpus (data/raw/research/sources.json) into the DB.

    .venv/bin/python -m scripts.ingest_corpus [--force] [--provider local|fake|voyage]
        [--sources PATH] [--limit N] [--only src_ccs_af_2020,src_ccs_lipids_2021] [--no-prune]

Corpus v3: sections → ResearchParent rows, ~380-word children (embedded) → ResearchChunk rows.
Idempotent: a source is skipped when its chunks were built by the same provider and
RAG_CORPUS_VERSION; otherwise (or with --force) its parents + chunks are rebuilt.
A full run (no --only) first deletes curated sources that are no longer in sources.json
(ad-hoc POST /api/ingest sources are kept); --no-prune skips that.
Production: EMBEDDING_PROVIDER=local (nomic-embed-text-v1.5-Q, 768-d, downloaded on first use).
Uses DATABASE_URL from the environment / .env (SQLite dev DB by default).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import session as db_session  # noqa: E402
from app.providers.embeddings import get_embedding_provider  # noqa: E402
from app.rag.ingest import DEFAULT_SOURCES_PATH, ingest_corpus  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-chunk and re-embed sources that already have chunks")
    ap.add_argument("--provider", choices=("local", "fake", "voyage"), default=None,
                    help="embedding provider (default: EMBEDDING_PROVIDER env)")
    ap.add_argument("--sources", default=str(DEFAULT_SOURCES_PATH), help="path to sources.json")
    ap.add_argument("--limit", type=int, default=None, help="max chunks per source")
    ap.add_argument("--only", default=None, help="comma-separated source ids")
    ap.add_argument("--no-prune", action="store_true",
                    help="keep sources that were removed from sources.json (default: delete them)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    provider = get_embedding_provider(args.provider)
    db_session.init_db(db_session.engine)
    t0 = time.time()
    db = db_session.SessionLocal()
    try:
        res = ingest_corpus(db, provider, args.sources, limit_chunks_per_source=args.limit, force=args.force,
                            only=args.only.split(",") if args.only else None,
                            prune=not args.no_prune and not args.only)
    finally:
        db.close()
    for sid in res["pruned"]:
        print(f"{sid:<30} pruned")
    for r in res["per_source"]:
        print(f"{r['id']:<30} {r['status']:<9} {r.get('loader', ''):<6} parents={r.get('parents', 0):<4}"
              f" chunks={r.get('chunks', 0)}"
              + (f"  error={r['error']}" if r.get("error") else ""))
    print(json.dumps({k: v for k, v in res.items() if k not in ("per_source", "pruned")}), f"provider={provider.name}",
          f"elapsed={time.time() - t0:.1f}s")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
