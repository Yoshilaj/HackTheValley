"""Evaluate the ANSWER step of RAG chat (what the user reads), not just retrieval.

    .venv/bin/python -m scripts.eval_answers [--tags fact,lay,near_miss] [--limit N]
        [--json out.json] [--verbose]

Each question from tests/fixtures/rag_eval.json goes through the same path as chat:
``infer_pathway`` → ``rag_service.retrieve`` → ``answer_with_citations`` (the real LLM from
``settings``, normally Gemini) → ``output_guard``, with an empty health-context block. Every answer
is then scored:

- deterministic: ``bad_markers`` (a ``[n]`` with no matching passage), ``cites`` (≥ 1 valid marker),
  ``cites_expected`` (a cited passage comes from an acceptable source), ``term_in_answer``
  (an expected fact such as "135" appears in the answer), ``disclaimer`` present;
- LLM judge (structured output, same provider): ``faithful`` (every health claim is supported by
  the numbered passages or is generic safety advice), ``citations_correct`` (each marker points at a
  passage that backs its sentence), ``answers_question``, and for unanswerable questions
  ``admits_no_evidence`` (says the library does not cover it instead of answering from memory).

The judge is the same model family as the answerer, so treat its verdicts as a screen: read the
``unsupported_claims`` it lists (``--verbose``) before trusting a rate. Costs two LLM calls per
question; nothing is written to the database.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_rag import DEFAULT_CASES, load_cases  # noqa: E402

DEFAULT_TAGS = ("fact", "lay", "cross", "near_miss", "off_topic")
_MARKER = re.compile(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]")

JUDGE_SYSTEM = (
    "You audit answers written by a health-information assistant for older adults. The assistant was "
    "given numbered research excerpts and must only state health facts those excerpts support, citing "
    "them as [n]. Judge strictly but fairly: generic safety advice (see a doctor, call emergency "
    "services, bring your readings or logs to an appointment, consumer devices can be inaccurate), "
    "units of measurement and restating the question need no citation. Paraphrase is fine; a number, "
    "threshold, drug, recommendation or how-to step that is not in the excerpts is unsupported. When no "
    "excerpts are given, any substantive answer to the question is unsupported."
)

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "faithful": {"type": "boolean",
                     "description": "Every health claim in the answer is supported by the excerpts."},
        "unsupported_claims": {"type": "array", "items": {"type": "string"},
                               "description": "Claims not supported by any excerpt (empty if none)."},
        "citations_correct": {"type": "boolean",
                              "description": "Each [n] marker points at an excerpt that supports its sentence "
                                             "(true when there are no markers and no claims)."},
        "answers_question": {"type": "boolean", "description": "The answer addresses what was asked."},
        "admits_no_evidence": {"type": "boolean",
                               "description": "The answer says the library/excerpts do not cover the question."},
    },
    "required": ["faithful", "unsupported_claims", "citations_correct", "answers_question", "admits_no_evidence"],
}


def raw_markers(answer: str) -> list[int]:
    nums: list[int] = []
    for grp in _MARKER.findall(answer or ""):
        for part in re.split(r"\s*[,\-–]\s*", grp):
            if part.isdigit():
                nums.append(int(part))
    return nums


def judge_prompt(question: str, excerpts: str, answer: str) -> str:
    return (f"<question>\n{question}\n</question>\n\n<research_excerpts>\n{excerpts}\n</research_excerpts>\n\n"
            f"<answer>\n{answer}\n</answer>")


def _with_retry(fn, tries: int = 4):
    from app.providers.llm import LlmApiError

    import httpx

    for i in range(tries):
        try:
            return fn()
        except (LlmApiError, httpx.TransportError) as exc:  # rate limits / dropped connections: back off
            if i == tries - 1 or (isinstance(exc, LlmApiError) and exc.code not in (429, 500, 503)):
                raise
            time.sleep(5 * 2 ** i)


def run_case(db, llm, case: dict[str, Any], provider, reranker) -> dict[str, Any]:
    from app.prompts.chat_system import format_excerpts
    from app.services import rag_service
    from app.services.guardrails import DISCLAIMER_EN, output_guard

    q = case["question"]
    pathway = rag_service.infer_pathway(q)
    hits = rag_service.retrieve(db, q, k=6, pathway=pathway, embedding_provider=provider, reranker=reranker)
    t0 = time.perf_counter()
    res = _with_retry(lambda: rag_service.answer_with_citations(db, llm, q, hits, "", "en"))
    ms = (time.perf_counter() - t0) * 1000
    answer = output_guard(res["answer"], "en")
    cited = rag_service.cited_numbers(answer, len(hits))
    expected = set(case.get("sources") or [])
    terms = case.get("terms") or []
    verdict = _with_retry(lambda: llm.extract(JUDGE_SYSTEM, judge_prompt(q, format_excerpts(hits), answer),
                                             JUDGE_SCHEMA, tool_name="judge_answer", purpose="eval_judge"))
    return {
        "id": case["id"], "tags": case.get("tags") or [], "question": q, "expect_empty": bool(case.get("expect_empty")),
        "n_passages": len(hits), "answer": answer, "llm_ms": round(ms),
        "bad_markers": sorted({m for m in raw_markers(answer) if not 1 <= m <= len(hits)}),
        "cites": bool(cited), "cited_sources": sorted({hits[n - 1].source_id for n in cited}),
        "cites_expected": bool(expected & {hits[n - 1].source_id for n in cited}),
        "term_in_answer": (any(t.lower() in answer.lower() for t in terms) if terms else None),
        "disclaimer": DISCLAIMER_EN in answer,
        **{k: verdict.get(k) for k in JUDGE_SCHEMA["properties"]},
    }


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    vals = [bool(r[key]) for r in rows if r.get(key) is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [r for r in rows if not r["expect_empty"]]
    neg = [r for r in rows if r["expect_empty"]]
    return {
        "answerable": len(pos), "unanswerable": len(neg),
        "faithful": _rate(pos, "faithful"), "citations_correct": _rate(pos, "citations_correct"),
        "answers_question": _rate(pos, "answers_question"), "cites": _rate(pos, "cites"),
        "cites_expected": _rate(pos, "cites_expected"), "term_in_answer": _rate(pos, "term_in_answer"),
        "answers_with_bad_markers": sum(bool(r["bad_markers"]) for r in rows),
        "disclaimer": _rate(rows, "disclaimer"),
        "unanswerable_admits_no_evidence": _rate(neg, "admits_no_evidence"),
        "unanswerable_faithful": _rate(neg, "faithful"),
        "llm_ms_p50": round(statistics.median(r["llm_ms"] for r in rows)) if rows else 0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tags", default=",".join(DEFAULT_TAGS), help="comma-separated case tags to include")
    ap.add_argument("--limit", type=int, default=None, help="at most N cases (in file order)")
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--json", default=None, help="write the full report to this path")
    ap.add_argument("--verbose", action="store_true", help="print every answer and unsupported claim")
    args = ap.parse_args(argv)

    from app.db import session as db_session
    from app.providers.embeddings import get_embedding_provider
    from app.providers.llm import get_llm_provider
    from app.rag.rerank import get_reranker

    tags = {t.strip() for t in args.tags.split(",") if t.strip()}
    cases = [c for c in load_cases(args.cases) if tags & set(c.get("tags") or [])][: args.limit]
    llm = get_llm_provider()
    provider = get_embedding_provider("local")
    reranker = get_reranker(provider)
    db = db_session.SessionLocal()
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    try:
        for c in cases:
            try:
                r = run_case(db, llm, c, provider, reranker)
            except Exception as exc:  # keep going; the summary counts errors separately
                errors.append({"id": c["id"], "error": f"{type(exc).__name__}: {exc}"[:300]})
                print(f"ERR {c['id']:<30} {errors[-1]['error']}", flush=True)
                continue
            rows.append(r)
            flags = [k for k in ("faithful", "citations_correct", "answers_question") if r[k] is False]
            if r["expect_empty"]:
                flags = [] if r["admits_no_evidence"] else ["answered_without_evidence"]
            if r["bad_markers"]:
                flags.append(f"bad_markers={r['bad_markers']}")
            print(f"{'OK ' if not flags else 'BAD'} {r['id']:<30} n={r['n_passages']} cited={r['cited_sources']} "
                  f"{' '.join(flags)}", flush=True)
            if args.verbose or flags:
                for claim in r["unsupported_claims"] or []:
                    print(f"      unsupported: {claim[:160]}")
            if args.verbose:
                print("      " + r["answer"].replace("\n", "\n      ")[:1200])
    finally:
        db.close()
    report = {"summary": {**summarise(rows), "errors": len(errors)}, "errors": errors, "cases": rows, "model": getattr(llm, "model_default", None)}
    print(json.dumps(report["summary"], indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
