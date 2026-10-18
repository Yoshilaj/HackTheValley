"""Medical-AI safety guardrails (BACKEND_PLAN Phase E).

Region: Canada, English first. The Japanese branch is kept for structure (users may still write
in Japanese).

- :func:`output_guard` — post-processes LLM answers: rewrites a few diagnostic /
  treatment-instruction phrasings into advocacy phrasing and appends the disclaimer.

Known limits (deliberately simple, documented for review):
- ``output_guard`` rewrites only the listed sentence patterns; it cannot detect implied
  diagnoses. The system prompt is the primary control, this is a backstop.
"""

from __future__ import annotations

import re

__all__ = ["DISCLAIMER_JA", "DISCLAIMER_EN", "disclaimer_for", "output_guard"]

DISCLAIMER_EN = ("This is not a diagnosis. I'm a health advocate, not a doctor — please discuss these findings with a "
                 "healthcare professional.")
DISCLAIMER_JA = ("※これは診断ではありません。私は医師ではなく、あなたの健康の代弁者(アドボケート)として情報を整理しています。"
                 "気になる点は医師にご相談ください。")


def _lang(language: str | None) -> str:
    """English unless Japanese is explicitly requested (product default: English, Canada)."""
    return "ja" if (language or "").lower().startswith("ja") else "en"


def disclaimer_for(language: str | None) -> str:
    return DISCLAIMER_JA if _lang(language) == "ja" else DISCLAIMER_EN


# ---------------------------------------------------------------- output guard

_COND = (r"(?:disease|disorder|dementia|alzheimer'?s?|parkinson'?s?|atrial\s+fibrillation|afib|a-?fib|"
         r"hypertension|high\s+blood\s+pressure|stroke|cancer|diabetes|syndrome|arrhythmia|heart\s+failure|"
         r"cognitive\s+impairment|mci|depression|infection)")
_EN_REWRITES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\byou\s+(?:definitely\s+|probably\s+|likely\s+|clearly\s+|most\s+likely\s+)?(?:have|has|are\s+suffering\s+from|suffer\s+from|are\s+diagnosed\s+with)\s+(?P<cond>[^.,;!?\n]{{0,40}}?\b{_COND}\b)",
                re.I),
     r"it may be worth asking your doctor about \g<cond>"),
    (re.compile(rf"\b(?:this|these\s+results?|your\s+data|it)\s+(?:confirms?|proves?|means?)\s+(?:that\s+)?you\s+have\s+(?P<cond>[^.,;!?\n]{{0,40}}?\b{_COND}\b)",
                re.I),
     r"these results may be worth discussing with your doctor in relation to \g<cond>"),
    (re.compile(r"\byou\s+(?:should|must|need\s+to)\s+(?P<verb>start|stop|increase|decrease|double|halve|reduce)\s+(?:taking\s+)?(?P<x>[^.;!?\n]{1,60})",
                re.I),
     r"you could ask your doctor whether to \g<verb> \g<x>"),
    (re.compile(r"\b(?:take|start\s+taking)\s+(?P<dose>\d+(?:\.\d+)?\s?(?:mg|mcg|µg|g|ml|tablets?|pills?))\b", re.I),
     r"ask your doctor about the right dose (\g<dose> was mentioned)"),
]
_JA_COND = r"(?:病|症|障害|細動|高血圧|認知症|パーキンソン|脳卒中|不整脈|心不全|がん|糖尿病)"
_JA_REWRITES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?:あなたは)?(?P<cond>[^\s。、]{{0,15}}{_JA_COND})(?:です|でしょう|と診断されます|に間違いありません|にかかっています)"),
     r"\g<cond>の可能性について医師に相談する価値があります"),
    (re.compile(r"(?P<x>[^\s。、]{1,20})(?:を|の)(?:服用を)?(?:やめて|中止して|増やして|減らして)ください"),
     r"\g<x>の服用については医師に相談してください"),
]


def rewrite_phrasing(text: str) -> str:
    """Rewrite diagnostic / treatment-instruction phrasing (simple regex list). No pattern crosses a
    sentence end, so streamed text can be rewritten one complete sentence at a time."""
    for pat, repl in _EN_REWRITES + _JA_REWRITES:
        text = pat.sub(repl, text)
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)


def output_guard(answer: str, language: str | None = None) -> str:
    """`rewrite_phrasing` and append the disclaimer in ``language`` if no disclaimer is already present."""
    text = rewrite_phrasing((answer or "").strip())
    disc = disclaimer_for(language)
    if DISCLAIMER_EN not in text and DISCLAIMER_JA not in text:
        text = f"{text}\n\n{disc}" if text else disc
    return text
