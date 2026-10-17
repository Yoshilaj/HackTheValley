"""Evaluate research retrieval against the labelled question set (tests/fixtures/rag_eval.json).

    .venv/bin/python -m scripts.eval_rag [--reranker cross_encoder|identity] [--floor X]
        [--calibrate] [--k 6] [--json out.json] [--cases PATH] [--verbose]

Runs every question through the same path as chat (``infer_pathway`` → ``hybrid_retrieve``) on
the DB in DATABASE_URL (the dev corpus by default) with the LOCAL embedder, and reports:

- answerable questions: hit@k (an expected source among the packed passages), hit@1 (the first
  passage is from an expected source), MRR, term recall (an expected string such as "135/85"
  appears in the packed text), mean passages / tokens;
- unanswerable questions (``expect_empty``): abstain rate (retrieval returned nothing);
- latency p50 / p95 (warm; the first query loads the models and is excluded).

``--calibrate`` runs once with no floor and sweeps cross-encoder thresholds: for each one it
counts answerable questions that keep an expected passage and unanswerable ones that would
return nothing, and suggests the middle of the range of thresholds that maximise both (the widest
margin on either side). Set it as ``RAG_MIN_RERANK_SCORE``.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "rag_eval.json"
NO_FLOOR = -1e9


def load_cases(path: str | Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]


def run_case(db, case: dict[str, Any], provider, reranker, k: int, min_score: float | None) -> dict[str, Any]:
    from app.rag import retrieval
    from app.services.rag_service import infer_pathway

    pathway = infer_pathway(case["question"])
    t0 = time.perf_counter()
    hits = retrieval.hybrid_retrieve(db, case["question"], k=k, pathway=pathway, embedding_provider=provider,
                                     reranker=reranker, min_score=min_score, use_cache=False)
    ms = (time.perf_counter() - t0) * 1000
    expected = set(case.get("sources") or [])
    ranks = [i for i, h in enumerate(hits, start=1) if h.source_id in expected]
    text = "\n".join(h.text for h in hits).lower()
    terms = case.get("terms") or []
    return {
        "id": case["id"], "question": case["question"], "pathway": pathway, "ms": round(ms, 1),
        "expect_empty": bool(case.get("expect_empty")),
        "n": len(hits), "tokens": sum(int(h.meta.get("tokens", 0)) for h in hits),
        "hit": bool(ranks), "hit1": bool(ranks) and ranks[0] == 1, "rr": 1 / ranks[0] if ranks else 0.0,
        "term_ok": (any(t.lower() in text for t in terms) if terms else None),
        "best_expected_score": max((h.score for h in hits if h.source_id in expected), default=None),
        "top_score": hits[0].score if hits else None,
        "fallback": retrieval.last_trace.pathway_fallback,
        "passages": [{"n": i, "source": h.source_id, "section": h.meta.get("section"), "score": h.score}
                     for i, h in enumerate(hits, start=1)],
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [r for r in rows if not r["expect_empty"]]
    neg = [r for r in rows if r["expect_empty"]]
    termed = [r for r in pos if r["term_ok"] is not None]
    warm = sorted(r["ms"] for r in rows[1:]) or [0.0]
    pct = lambda xs, q: xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]  # noqa: E731
    return {
        "answerable": len(pos), "unanswerable": len(neg),
        "hit_at_k": round(sum(r["hit"] for r in pos) / max(1, len(pos)), 3),
        "hit_at_1": round(sum(r["hit1"] for r in pos) / max(1, len(pos)), 3),
        "mrr": round(sum(r["rr"] for r in pos) / max(1, len(pos)), 3),
        "term_recall": round(sum(r["term_ok"] for r in termed) / max(1, len(termed)), 3),
        "abstain_rate": round(sum(r["n"] == 0 for r in neg) / max(1, len(neg)), 3),
        "empty_on_answerable": sum(r["n"] == 0 for r in pos),
        "mean_passages": round(statistics.fmean(r["n"] for r in pos), 2) if pos else 0,
        "mean_tokens": round(statistics.fmean(r["tokens"] for r in pos)) if pos else 0,
        "latency_ms_p50": round(pct(warm, 0.5), 1), "latency_ms_p95": round(pct(warm, 0.95), 1),
    }


def evaluate(db, cases, provider, reranker, k: int = 6, min_score: float | None = None) -> dict[str, Any]:
    rows = [run_case(db, c, provider, reranker, k, min_score) for c in cases]
    return {"summary": summarise(rows), "cases": rows}


def calibrate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Sweep floors over rows produced with NO floor; returns the table and the suggested floor."""
    pos = [r for r in rows if not r["expect_empty"]]
    neg = [r for r in rows if r["expect_empty"]]
    table = []
    for t in [x / 2 for x in range(-20, 11)]:  # -10.0 … 5.0 in steps of 0.5
        kept = sum(1 for r in pos if r["best_expected_score"] is not None and r["best_expected_score"] >= t)
        abstained = sum(1 for r in neg if r["top_score"] is None or r["top_score"] < t)
        table.append({"floor": t, "answerable_kept": kept, "unanswerable_abstained": abstained,
                      "score": kept + abstained})
    top = max(row["score"] for row in table)
    plateau = [row["floor"] for row in table if row["score"] == top]
    suggested = plateau[len(plateau) // 2]  # middle of the best range = the widest margin both ways
    return {"table": table, "suggested_floor": suggested, "answerable": len(pos), "unanswerable": len(neg),
            "negative_top_scores": {r["id"]: r["top_score"] for r in neg},
            "positive_best_scores": {r["id"]: r["best_expected_score"] for r in pos}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reranker", choices=("cross_encoder", "identity"), default="cross_encoder")
    ap.add_argument("--floor", type=float, default=None, help="relevance floor (default: RAG_MIN_RERANK_SCORE)")
    ap.add_argument("--calibrate", action="store_true", help="sweep floors and suggest one")
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--provider", default="local", help="embedding provider (must match the ingested corpus)")
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--json", default=None, help="write the full report to this path")
    ap.add_argument("--verbose", action="store_true", help="print the passages of every case")
    args = ap.parse_args(argv)

    from app.db import session as db_session
    from app.providers.embeddings import get_embedding_provider
    from app.rag.rerank import get_reranker

    provider = get_embedding_provider(args.provider)
    reranker = get_reranker(provider, name=args.reranker)
    cases = load_cases(args.cases)
    db = db_session.SessionLocal()
    try:
        report = evaluate(db, cases, provider, reranker, k=args.k,
                          min_score=NO_FLOOR if args.calibrate else args.floor)
    finally:
        db.close()
    report["config"] = {"provider": provider.name, "reranker": reranker.name, "k": args.k,
                        "floor": "none" if args.calibrate else args.floor}

    for r in report["cases"]:
        mark = ("ABSTAIN" if r["n"] == 0 else "LEAK") if r["expect_empty"] else (
            ("HIT@1" if r["hit1"] else "hit") if r["hit"] else "MISS")
        term = "" if r["term_ok"] is None else (" term✓" if r["term_ok"] else " term✗")
        top = "" if r["top_score"] is None else f" top={r['top_score']:.2f}"
        print(f"{mark:<8}{r['id']:<24} n={r['n']} {r['ms']:>6.0f}ms pathway={r['pathway']}{top}{term}")
        if args.verbose or mark in ("MISS", "LEAK"):
            for p in r["passages"]:
                print(f"          [{p['n']}] {p['score']:7.3f} {p['source']} | {(p['section'] or '')[:50]}")
    print(json.dumps(report["summary"], indent=1))
    if args.calibrate:
        cal = calibrate(report["cases"])
        report["calibration"] = cal
        print("floor  answerable_kept  unanswerable_abstained")
        for row in cal["table"]:
            print(f"{row['floor']:>5}  {row['answerable_kept']:>3}/{cal['answerable']:<12}"
                  f"{row['unanswerable_abstained']:>3}/{cal['unanswerable']}")
        print(f"suggested RAG_MIN_RERANK_SCORE={cal['suggested_floor']}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
