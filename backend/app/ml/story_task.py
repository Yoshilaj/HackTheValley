"""Story-recall session state machine and 3-word recall (spec §1.1 battery, §1.4).

Battery (ADNI4 structure, adapted from the published protocol):

    story1_listen → story1_recall → story2_listen → story2_recall
      → fluency_60s ("name as many animals as you can", distractor)
      → delay (≈3-5 min since story 1 recall; logged) → story1_delayed_recall

Pure Python: callers persist :class:`StorySession` (``to_dict``/``from_dict``)
and drive it with :func:`next_step`. Instruction wording ("retell in as much
detail as you can remember") is in :data:`INSTRUCTIONS`. Max 3 min per
retelling (enforced client-side; recorded duration is checked here).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.content.loader import ContentError, Story, get_story, get_story_by_id, get_word_set, get_word_set_by_id
from app.ml.features.speech import ja_readings, normalize_language, to_hiragana, tokenize
from app.ml.recall import PROTOCOL_VERSION, RecallSessionScore, score_session
from app.providers.embeddings import EmbeddingProvider
from app.providers.stt import Transcript

__all__ = [
    "STEPS",
    "RECALL_STEPS",
    "INSTRUCTIONS",
    "MAX_RECALL_S",
    "DEFAULT_MIN_DELAY_S",
    "StepOrderError",
    "DelayNotElapsedError",
    "StorySession",
    "new_session",
    "next_step",
    "finalize",
    "WordRecallTrial",
    "new_word_recall",
    "score_word_recall",
]

STEPS: tuple[str, ...] = (
    "story1_listen",
    "story1_recall",
    "story2_listen",
    "story2_recall",
    "fluency_60s",
    "delay",
    "story1_delayed_recall",
)
RECALL_STEPS: tuple[str, ...] = ("story1_recall", "story2_recall", "story1_delayed_recall")
MAX_RECALL_S = 180.0
FLUENCY_S = 60.0
DEFAULT_MIN_DELAY_S = 180.0  # §1.1: delay ≈3-5 min (papers do not report it; we log the actual value)

INSTRUCTIONS: dict[str, dict[str, str]] = {
    "en": {
        "listen": "Listen carefully to the story. You will be asked to retell it.",
        "recall": "Please retell the story in as much detail as you can remember. Press stop when you have finished.",
        "fluency_60s": "For the next minute, name as many animals as you can.",
        "delay": "Please wait a moment.",
        "delayed_recall": "Earlier you heard a story. Please retell that first story in as much detail as you can remember.",
    },
    "ja": {
        "listen": "これからお話を流します。よく聞いてください。あとで、そのお話をしていただきます。",
        "recall": "今のお話を、覚えている限りできるだけ詳しく話してください。話し終わったら停止ボタンを押してください。",
        "fluency_60s": "これから1分間、動物の名前をできるだけたくさん言ってください。",
        "delay": "少しお待ちください。",
        "delayed_recall": "先ほど、最初に聞いたお話を、覚えている限りできるだけ詳しく話してください。",
    },
}


class StepOrderError(ValueError):
    """``completed_step`` is not the session's current step, or the session is finished."""


class DelayNotElapsedError(ValueError):
    """The delayed recall was requested before ``min_delay_s`` had passed since story 1 recall."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@dataclass
class StorySession:
    """One story-recall battery. ``current_index`` points at the next step to perform."""

    session_id: str
    protocol_version: str
    language: str
    steps: list[str]
    story_ids: list[str]
    created_at: str
    current_index: int = 0
    min_delay_s: float = DEFAULT_MIN_DELAY_S
    step_log: list[dict[str, Any]] = field(default_factory=list)
    transcripts: dict[str, dict[str, Any]] = field(default_factory=dict)
    delay_s: float | None = None
    finished: bool = False

    @property
    def current_step(self) -> str | None:
        return None if self.finished or self.current_index >= len(self.steps) else self.steps[self.current_index]

    def instruction(self, step: str | None = None) -> str:
        """Instruction text for ``step`` (default: current step) in the session language."""
        step = step or self.current_step or ""
        key = (
            "listen" if step.endswith("_listen")
            else "delayed_recall" if step == "story1_delayed_recall"
            else "recall" if step.endswith("_recall")
            else step
        )
        return INSTRUCTIONS[self.language].get(key, "")

    def story_for_step(self, step: str | None = None) -> Story | None:
        """The story played/recalled at ``step`` (story 1 for the delayed recall)."""
        step = step or self.current_step or ""
        if step.startswith("story1"):
            return get_story_by_id(self.story_ids[0])
        if step.startswith("story2"):
            return get_story_by_id(self.story_ids[1])
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "StorySession":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def new_session(
    user_language: str,
    used_story_ids: Iterable[str] = (),
    *,
    min_delay_s: float = DEFAULT_MIN_DELAY_S,
    rng: random.Random | None = None,
    now: datetime | None = None,
) -> StorySession:
    """Create a battery with two stories the user has never heard (§1.1: no reuse).

    Raises :class:`app.content.loader.ContentError` if fewer than two unused
    stories remain.
    """
    lang = normalize_language(user_language)
    used = set(used_story_ids)
    s1 = get_story(lang, exclude_ids=used, rng=rng)
    s2 = get_story(lang, exclude_ids=used | {s1.id}, rng=rng)
    return StorySession(
        session_id=f"srs_{uuid.uuid4().hex[:16]}",
        protocol_version=PROTOCOL_VERSION,
        language=lang,
        steps=list(STEPS),
        story_ids=[s1.id, s2.id],
        created_at=_iso(now or _now()),
        min_delay_s=min_delay_s,
    )


def _transcript_from(payload: Mapping[str, Any]) -> Transcript | None:
    t = payload.get("transcript")
    if t is None:
        return None
    if isinstance(t, Transcript):
        return t
    if isinstance(t, Mapping):
        return Transcript.from_dict(dict(t))
    raise TypeError("payload['transcript'] must be a Transcript or dict")


def _completed_at(session: StorySession, step: str) -> datetime | None:
    for e in session.step_log:
        if e["step"] == step:
            return datetime.fromisoformat(e["completed_at"])
    return None


def next_step(
    session: StorySession,
    completed_step: str,
    payload: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> str | None:
    """Record ``completed_step`` and advance; returns the new current step (``None`` when done).

    Payload by step: recall steps and ``fluency_60s`` take ``{"transcript":
    Transcript | dict}``; listen steps may pass ``{"played_s": float}``;
    ``delay`` may pass ``{"force": True}`` (demo only; logged). The session
    is mutated in place.

    Raises :class:`StepOrderError` for out-of-order steps and
    :class:`DelayNotElapsedError` if ``delay`` is completed before
    ``min_delay_s`` has passed since ``story1_recall``.
    """
    payload = payload or {}
    t = now or _now()
    if session.current_step is None:
        raise StepOrderError(f"session {session.session_id} is already finished")
    if completed_step != session.current_step:
        raise StepOrderError(f"expected step {session.current_step!r}, got {completed_step!r}")
    entry: dict[str, Any] = {"step": completed_step, "completed_at": _iso(t)}
    if completed_step == "delay":
        start = _completed_at(session, "story1_recall")
        elapsed = (t - start).total_seconds() if start else 0.0
        forced = bool(payload.get("force"))
        if elapsed < session.min_delay_s and not forced:
            raise DelayNotElapsedError(f"{elapsed:.0f}s elapsed; need {session.min_delay_s:.0f}s since story 1 recall")
        session.delay_s = elapsed
        entry.update(delay_s=elapsed, forced=forced)
    if completed_step in RECALL_STEPS or completed_step == "fluency_60s":
        tr = _transcript_from(payload)
        if tr is not None:
            session.transcripts[completed_step] = tr.to_dict()
            if tr.duration_s is not None and tr.duration_s > MAX_RECALL_S + 5:
                entry["over_max_duration"] = True
    if "played_s" in payload:
        entry["played_s"] = float(payload["played_s"])
    session.step_log.append(entry)
    session.current_index += 1
    if session.current_index >= len(session.steps):
        session.finished = True
        return None
    return session.steps[session.current_index]


def _fluency_count(tr: Transcript | None, language: str) -> int | None:
    """Unique tokens in the fluency response — a rough distractor-engagement count, not a scored test."""
    if tr is None:
        return None
    from app.ml.features.speech import pos_tags

    return len({w for w, p in pos_tags(tr.text, language) if p in {"NOUN", "PROPN"}})


def finalize(
    session: StorySession,
    transcripts: Mapping[str, Transcript | Mapping[str, Any]] | Sequence[Transcript | None] | None,
    provider: EmbeddingProvider,
    *,
    audio: Sequence[Any] | None = None,
    with_spectral: bool = False,
) -> RecallSessionScore:
    """Score the session (§1.1). ``transcripts``: step→Transcript mapping, a 3-list
    ``[story1_recall, story2_recall, story1_delayed_recall]``, or ``None`` to use
    the transcripts recorded via :func:`next_step`.

    Adds ``incomplete_session`` to the invalid reasons if the battery did not
    reach the end, and records the logged delay and fluency count in
    ``temporal['session']``.
    """
    if transcripts is None:
        transcripts = session.transcripts
    if isinstance(transcripts, Mapping):
        ordered: list[Transcript | None] = []
        for step in RECALL_STEPS:
            v = transcripts.get(step)
            ordered.append(v if isinstance(v, Transcript) or v is None else Transcript.from_dict(dict(v)))
        flu = transcripts.get("fluency_60s")
        fluency = flu if isinstance(flu, Transcript) or flu is None else Transcript.from_dict(dict(flu))
    else:
        ordered = list(transcripts)
        f = session.transcripts.get("fluency_60s")
        fluency = Transcript.from_dict(f) if f else None
    stories = [get_story_by_id(sid) for sid in session.story_ids]
    result = score_session(stories, ordered, provider, audio=audio, with_spectral=with_spectral)
    result.protocol_version = session.protocol_version
    if not session.finished:
        result.invalid_reasons.append("incomplete_session")
        result.valid = False
        result.status = "invalid_measurement"
    result.temporal["session"] = {
        "session_id": session.session_id,
        "delay_s": session.delay_s,
        "fluency_unique_nouns": _fluency_count(fluency, session.language),
    }
    return result


# --------------------------------------------------------------------------
# §1.4 3-word delayed recall (Linus-informed)
# --------------------------------------------------------------------------

@dataclass
class WordRecallTrial:
    set_id: str
    language: str
    words: list[str]
    presented_at: str
    instruction: str


_WR_INSTR = {
    "en": "Please remember these three words. I will ask you for them again in a few minutes.",
    "ja": "これから言う三つの言葉を覚えてください。数分後にもう一度お聞きします。",
}


def new_word_recall(language: str, exclude_ids: Iterable[str] = (), rng: random.Random | None = None,
                    now: datetime | None = None) -> WordRecallTrial:
    """Pick a 3-word set (§1.4) not in ``exclude_ids`` (cycles when all were used)."""
    lang = normalize_language(language)
    ws = get_word_set(lang, exclude_ids=exclude_ids, rng=rng)
    return WordRecallTrial(ws.id, lang, list(ws.words), _iso(now or _now()), _WR_INSTR[lang])


def _variants_for(target_words: Sequence[str], language: str) -> dict[str, list[str]]:
    from app.content.loader import _word_sets  # same package-level content; cached

    out: dict[str, list[str]] = {}
    try:
        for s in _word_sets()["sets"]:
            for w, vs in s.get("variants", {}).get(language, {}).items():
                if w in target_words:
                    out[w] = list(vs)
    except ContentError:
        pass
    return out


def _en_match(target: str, variants: list[str], tokens: set[str]) -> bool:
    forms = {target.lower(), *[v.lower() for v in variants]}
    for f in list(forms):
        forms.update({f + "s", f + "es", f[:-1] + "ies" if f.endswith("y") else f})
    return bool(forms & tokens)


def _ja_match(target: str, variants: list[str], response: str, morphs: list[tuple[str, str]]) -> bool:
    keys = {to_hiragana(target), *[to_hiragana(v) for v in variants]}
    surfaces = {to_hiragana(s) for s, _ in morphs}
    readings = {r for _, r in morphs}
    if keys & (surfaces | readings):
        return True
    joined_read = "".join(r for _, r in morphs)
    norm = to_hiragana(response)
    # substring fallback for multi-morpheme splits; only for ≥3-kana keys to avoid false hits (かさ ⊂ たかさ)
    return any(len(k) >= 3 and (k in norm or k in joined_read) for k in keys)


def score_word_recall(
    target_words: Sequence[str],
    response_text: str,
    latency_ms: float | None,
    language: str,
) -> dict[str, Any]:
    """Score a §1.4 response: 0-3 words recalled (any order) plus response latency.

    EN: token match incl. simple plurals and listed variants (e.g. bike).
    JA: morpheme surface/reading match against the target in hiragana and its
    listed kanji/katakana variants (林檎 → りんご), with a substring fallback
    for targets of ≥3 kana. Feeds the cognitive baseline only (no standalone rule).
    """
    lang = normalize_language(language)
    variants = _variants_for(target_words, lang)
    recalled: list[str] = []
    if lang == "ja":
        morphs = ja_readings(response_text or "")
        for w in target_words:
            if _ja_match(w, variants.get(w, []), response_text or "", morphs):
                recalled.append(w)
    else:
        tokens = set(tokenize(response_text or "", "en"))
        for w in target_words:
            if _en_match(w, variants.get(w, []), tokens):
                recalled.append(w)
    return {
        "score": len(recalled),
        "max_score": len(target_words),
        "recalled": recalled,
        "missed": [w for w in target_words if w not in recalled],
        "latency_ms": float(latency_ms) if latency_ms is not None else None,
        "language": lang,
        "valid": bool((response_text or "").strip()) or latency_ms is not None,
    }
