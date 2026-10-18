"""Lay → clinical query expansion for research retrieval.

Users type everyday words ("blood thinners", "my top number", "my hands shake") while the corpus is
written in clinical language ("anticoagulants", "systolic", "tremor"). The small cross-encoder scores
such pairs low (e.g. "blood thinners" vs a DOAC recommendation: −5.1; "anticoagulants": +1.1), so
:func:`expand_query` appends the clinical terms in parentheses before embedding, lexical search and
reranking. The user's own words are kept; nothing is appended when no pattern matches or when the
clinical term is already in the query. Measured with ``scripts/eval_rag.py`` (``lay`` tag).
"""

from __future__ import annotations

import re

#: (pattern on the lower-cased query, clinical terms appended). Patterns match at word starts.
LAY_TERMS: tuple[tuple[str, str], ...] = (
    # heart and blood pressure
    (r"\bblood thinn", "anticoagulants"),
    (r"\btop number|\bupper number", "systolic blood pressure"),
    (r"\bbottom number|\blower number", "diastolic blood pressure"),
    (r"\b(?:bring|get|keep)\b.{0,30}\b(?:blood pressure|bp)\b.{0,10}\bdown\b|\blower(?:ing)? (?:my |your )?(?:blood pressure|bp)\b",
     "blood pressure lowering treatment lifestyle"),
    (r"\bheart (?:is )?(?:flutter|racing|races|pounding|skipping|skips)|\bfluttering\b|\bskipped beats?\b",
     "palpitations arrhythmia"),
    (r"\bafib\b|\ba-fib\b", "atrial fibrillation"),
    (r"\bheart attack", "myocardial infarction"),
    (r"\bmini[- ]?stroke", "transient ischemic attack"),
    (r"\bwater pill", "diuretic"),
    (r"\bbad cholesterol\b", "LDL cholesterol"),
    (r"\bcholesterol (?:number|level|reading)s?\b", "LDL cholesterol target"),
    # memory
    (r"\bforget|\bforgetful|\bmemory (?:is )?(?:slipping|going|getting worse)|\bmemory (?:loss|problems?|issues?|checked)|\brepeat(?:s|ing)? (?:the same|herself|himself|themselves)",
     "memory complaints cognitive impairment assessment"),
    (r"\b(?:brain|mind) (?:sharp|healthy|health)|\bkeep (?:my |your )?(?:brain|mind)", "cognitive decline dementia risk reduction"),
    # movement and Parkinson's
    (r"\bshak(?:e|es|y|ing)\b|\btrembl", "tremor"),
    (r"\bshuffl", "gait parkinsonism"),
    (r"\bstiff", "rigidity"),
    (r"\bslow(?:er|ness)? (?:movements?|moving)|\bmoving (?:more )?slow", "bradykinesia"),
    (r"\b(?:soft|quiet|weak|hoarse) voice\b|\bvoice (?:has )?(?:gone|got|gotten|is) (?:soft|quiet|weak|hoarse)", "hypophonia dysphonia"),
    # activity and sleep
    (r"\bsit(?:ting)? (?:at a desk |down )?all day|\bsit(?:ting)? too much", "sedentary behaviour"),
    (r"\bworking out\b|\bwork out\b", "physical activity exercise"),
)

_COMPILED = tuple((re.compile(p), terms) for p, terms in LAY_TERMS)


def expand_query(query: str) -> str:
    """``query`` plus ``" (clinical; terms)"`` for every lay pattern it contains, else unchanged."""
    q = query or ""
    low = q.lower()
    extra: list[str] = []
    for rx, terms in _COMPILED:
        if rx.search(low) and terms.lower() not in low and terms not in extra:
            extra.append(terms)
    return f"{q} ({'; '.join(extra)})" if extra else q
