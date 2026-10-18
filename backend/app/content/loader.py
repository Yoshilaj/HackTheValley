"""Content banks: story-recall stories (§1.1), 3-word recall sets (§1.4).

All files are validated on first load; a malformed bank raises
:class:`ContentError` instead of serving a bad stimulus.

Story length rule (§1.1 "110-125 words, 7-9 sentences")
-------------------------------------------------------
* EN: 110-125 words (regex words; contractions count as one).
* JA: **270-330 characters**, counted as every non-whitespace character
  including punctuation (the 原稿用紙 convention). Japanese has no word count,
  so the rule matches the EN stories on two measures: (1) content — our
  parallel stories carry the same propositions at ≈2.3-2.5 characters per
  English word (110-125 words ≈ 255-310 chars); (2) duration — at a calm
  read-aloud pace of ≈330-360 characters/min (NHK news is ≈300/min; TTS
  default is faster) 270-330 characters take ≈45-60 s, the same as 110-125
  words at 140 wpm (47-54 s).
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "STORY_BANK_VERSION",
    "ContentError",
    "Story",
    "WordSet",
    "get_story",
    "get_story_by_id",
    "list_stories",
    "get_word_set",
    "get_word_set_by_id",
    "get_voice_stability_text",
    "count_en_words",
    "count_ja_chars",
    "count_sentences",
    "EN_WORD_RANGE",
    "JA_CHAR_RANGE",
    "SENTENCE_RANGE",
    "ENTITY_RANGE",
    "CONTENT_UNIT_RANGE",
]

STORY_BANK_VERSION = "2026.10.1"
CONTENT_DIR = Path(__file__).resolve().parent
LANGUAGES = ("en", "ja")
EN_WORD_RANGE = (110, 125)
JA_CHAR_RANGE = (270, 330)
SENTENCE_RANGE = (7, 9)
ENTITY_RANGE = (4, 6)
CONTENT_UNIT_RANGE = (8, 12)


class ContentError(ValueError):
    """A content file is missing, malformed or violates the §1.1 constraints, or a lookup failed."""


def count_en_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", text))


def count_ja_chars(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def count_sentences(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?。！？])\s*", text) if s.strip()])


@dataclass(frozen=True)
class Story:
    id: str
    language: str
    title: str
    text: str
    sentence_count: int
    entities: tuple[str, ...]
    content_units: tuple[str, ...]
    theme: str
    version: str
    word_count: int | None = None
    char_count: int | None = None

    def to_dict(self, include_text: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id, "language": self.language, "title": self.title,
            "sentence_count": self.sentence_count, "entities": list(self.entities),
            "content_units": list(self.content_units), "theme": self.theme, "version": self.version,
            "word_count": self.word_count, "char_count": self.char_count,
        }
        if include_text:
            d["text"] = self.text
        return d


@dataclass(frozen=True)
class WordSet:
    id: str
    language: str
    words: tuple[str, ...]
    variants: dict[str, list[str]] = field(default_factory=dict)



def _lang(language: str) -> str:
    lang = (language or "").lower().split("-")[0]
    if lang not in LANGUAGES:
        raise ContentError(f"unsupported language {language!r}; expected one of {LANGUAGES}")
    return lang


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ContentError(f"cannot load {path}: {e}") from e


def _validate_story(d: dict[str, Any], lang: str) -> Story:
    sid = d.get("id", "?")
    required = ("id", "language", "title", "text", "sentence_count", "entities", "content_units", "theme", "version")
    missing = [k for k in required if k not in d]
    if missing:
        raise ContentError(f"story {sid}: missing fields {missing}")
    if d["language"] != lang:
        raise ContentError(f"story {sid}: language {d['language']!r} in {lang} bank")
    text = d["text"]
    errs: list[str] = []
    if lang == "en":
        n = count_en_words(text)
        if d.get("word_count") != n:
            errs.append(f"word_count {d.get('word_count')} != counted {n}")
        if not EN_WORD_RANGE[0] <= n <= EN_WORD_RANGE[1]:
            errs.append(f"{n} words outside {EN_WORD_RANGE}")
    else:
        n = count_ja_chars(text)
        if d.get("char_count") != n:
            errs.append(f"char_count {d.get('char_count')} != counted {n}")
        if not JA_CHAR_RANGE[0] <= n <= JA_CHAR_RANGE[1]:
            errs.append(f"{n} chars outside {JA_CHAR_RANGE}")
    sc = count_sentences(text)
    if d["sentence_count"] != sc or not SENTENCE_RANGE[0] <= sc <= SENTENCE_RANGE[1]:
        errs.append(f"sentence_count {d['sentence_count']} (counted {sc}) outside {SENTENCE_RANGE}")
    if not ENTITY_RANGE[0] <= len(d["entities"]) <= ENTITY_RANGE[1]:
        errs.append(f"{len(d['entities'])} entities outside {ENTITY_RANGE}")
    if not CONTENT_UNIT_RANGE[0] <= len(d["content_units"]) <= CONTENT_UNIT_RANGE[1]:
        errs.append(f"{len(d['content_units'])} content units outside {CONTENT_UNIT_RANGE}")
    if d["version"] != STORY_BANK_VERSION:
        errs.append(f"version {d['version']} != bank {STORY_BANK_VERSION}")
    if errs:
        raise ContentError(f"story {sid}: " + "; ".join(errs))
    return Story(
        id=d["id"], language=lang, title=d["title"], text=text, sentence_count=sc,
        entities=tuple(d["entities"]), content_units=tuple(d["content_units"]), theme=d["theme"],
        version=d["version"], word_count=d.get("word_count"), char_count=d.get("char_count"),
    )


@lru_cache(maxsize=None)
def _stories(lang: str) -> tuple[Story, ...]:
    doc = _read(CONTENT_DIR / "stories" / f"{lang}.json")
    if doc.get("bank_version") != STORY_BANK_VERSION:
        raise ContentError(f"{lang} bank_version {doc.get('bank_version')} != {STORY_BANK_VERSION}")
    stories = tuple(_validate_story(s, lang) for s in doc.get("stories", []))
    ids = [s.id for s in stories]
    if len(set(ids)) != len(ids):
        raise ContentError(f"{lang} bank has duplicate story ids")
    if len(stories) < 6:
        raise ContentError(f"{lang} bank has {len(stories)} stories; §1.1 requires ≥6")
    return stories


def list_stories(language: str) -> list[Story]:
    """All validated stories for a language."""
    return list(_stories(_lang(language)))


def get_story(language: str, exclude_ids: Iterable[str] = (), rng: random.Random | None = None) -> Story:
    """Random unused story (§1.1: a story is never reused for the same user).

    Raises :class:`ContentError` when every story has been used.
    """
    excluded = set(exclude_ids)
    pool = [s for s in _stories(_lang(language)) if s.id not in excluded]
    if not pool:
        raise ContentError(f"story bank exhausted for language {language!r}")
    return (rng or random).choice(pool)


def get_story_by_id(story_id: str) -> Story:
    for lang in LANGUAGES:
        for s in _stories(lang):
            if s.id == story_id:
                return s
    raise ContentError(f"unknown story id {story_id!r}")


@lru_cache(maxsize=1)
def _word_sets() -> dict[str, Any]:
    doc = _read(CONTENT_DIR / "word_recall.json")
    sets = doc.get("sets", [])
    if len(sets) < 10:
        raise ContentError("word_recall.json needs ≥10 sets")
    for s in sets:
        for lang in LANGUAGES:
            words = s.get(lang)
            if not isinstance(words, list) or len(words) != 3 or len(set(words)) != 3:
                raise ContentError(f"word set {s.get('id')}: {lang} must be 3 distinct words")
    return doc


def get_word_set(language: str, exclude_ids: Iterable[str] = (), rng: random.Random | None = None) -> WordSet:
    """Random 3-word set for §1.4; cycles through all sets if every one was excluded."""
    lang = _lang(language)
    sets = _word_sets()["sets"]
    excluded = set(exclude_ids)
    pool = [s for s in sets if s["id"] not in excluded] or sets
    s = (rng or random).choice(pool)
    return WordSet(id=s["id"], language=lang, words=tuple(s[lang]), variants=dict(s.get("variants", {}).get(lang, {})))


def get_word_set_by_id(set_id: str, language: str) -> WordSet:
    lang = _lang(language)
    for s in _word_sets()["sets"]:
        if s["id"] == set_id:
            return WordSet(id=s["id"], language=lang, words=tuple(s[lang]), variants=dict(s.get("variants", {}).get(lang, {})))
    raise ContentError(f"unknown word set {set_id!r}")



VOICE_STABILITY_KEYS = ("message", "fired_summary", "report_limitation", "baseline_note", "device_change_note")


@lru_cache(maxsize=1)
def get_voice_stability_text() -> dict[str, Any]:
    """PD-03B user-facing text (spec §5.5), EN + JA: ``message{lang:{title, body}}``,
    ``fired_summary{lang}``, ``report_limitation{lang}``, ``baseline_note{lang}`` (``{needed}``/``{n}``
    placeholders), ``device_change_note{lang}``. Validated so a missing translation never reaches a user."""
    doc = _read(CONTENT_DIR / "voice_stability.json")
    for key in VOICE_STABILITY_KEYS:
        block = doc.get(key)
        if not isinstance(block, dict) or not all(block.get(lang) for lang in LANGUAGES):
            raise ContentError(f"voice_stability.{key} must carry {LANGUAGES}")
    for lang in LANGUAGES:
        m = doc["message"][lang]
        if not (isinstance(m, dict) and m.get("title") and m.get("body")):
            raise ContentError(f"voice_stability.message.{lang} needs title and body")
    return doc
