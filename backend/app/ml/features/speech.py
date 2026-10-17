"""Speech text and timing features for the story-recall task.

Spec: ``docs/specs/phase-a-assessments.md`` §1.1 ("Secondary features", Table 1
of Skirrow et al. 2024): lexical (word count, idea density, pronoun/noun
ratio, noun rate, unique-word ratio), temporal (speech duration, speech rate,
pauses >200 ms, total pause time) and spectral (F0 mean/SD, HNR, MFCC 1-13
mean/SD, jitter, shimmer) features.

Tokenisation
------------
* Japanese: ``fugashi`` (MeCab) with ``unidic-lite``; surface forms, with
  punctuation/symbols (``補助記号``/``記号``/``空白``) dropped. UniDic POS is
  mapped to a coarse universal-style tag set (see ``_JA_POS``).
* English: lower-cased regex words. POS uses spaCy (``en_core_web_sm``) if it
  is importable; otherwise a small closed-class lexicon plus suffix heuristic
  (``_en_heuristic_tag``). **Limits of the heuristic**: closed classes
  (pronouns, determiners, prepositions, conjunctions, auxiliaries) are exact;
  open classes are guessed — verbs from a list of ~150 common verbs plus
  ``-ed``/``-ing``, adverbs from ``-ly`` and a list, adjectives from a short
  list and a few suffixes; every remaining word is a NOUN (or PROPN if it was
  capitalised mid-sentence). Noun rate is therefore biased upward and idea
  density downward relative to spaCy; values are only comparable within one
  tagger, so ``lexical_features`` reports the tagger it used.

Idea density
------------
Propositional-density proxy in the spirit of CPIDR (Brown et al. 2008): the
count of VERB + ADJ + ADV + ADP + CCONJ + SCONJ tokens divided by word count.
For Japanese, ADP = 格助詞 (case particles, the analogue of prepositions) and
SCONJ = 接続助詞; 形状詞 counts as ADJ. Because Japanese tokenisation yields
many more function morphemes per idea, the value is **not comparable across
languages** — compare a user only with their own baseline in one language.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any, Iterable, Sequence

import numpy as np

__all__ = [
    "tokenize",
    "pos_tags",
    "lexical_features",
    "temporal_features",
    "spectral_features",
    "energy_vad",
    "is_japanese",
    "normalize_language",
    "to_hiragana",
    "ja_readings",
    "split_sentences",
]

PAUSE_THRESHOLD_S = 0.2  # §1.1 "pauses >200 ms"

# --------------------------------------------------------------------------
# Language helpers
# --------------------------------------------------------------------------

_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")
_EN_WORD_RE = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")


def normalize_language(language: str | None) -> str:
    """Map ``ja``/``ja-JP``/``jpn``/``japanese`` → ``ja``; anything else → ``en``."""
    lang = (language or "en").lower()
    return "ja" if lang.startswith("ja") or lang in {"jpn", "japanese"} else "en"


def is_japanese(text: str) -> bool:
    """True if ``text`` contains kana or CJK ideographs."""
    return bool(_CJK_RE.search(text or ""))


def to_hiragana(text: str) -> str:
    """Convert full-width katakana to hiragana (NFKC first, so half-width works)."""
    text = unicodedata.normalize("NFKC", text or "")
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def split_sentences(text: str) -> list[str]:
    """Split on sentence-final punctuation (EN ``.!?``, JA ``。！？``) and newlines."""
    parts = re.split(r"(?<=[.!?。！？])\s*|\n+", text or "")
    return [p.strip() for p in parts if p and p.strip()]


# --------------------------------------------------------------------------
# Japanese (fugashi)
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _ja_tagger() -> Any:
    import fugashi  # lazy: heavy dictionary load

    return fugashi.Tagger()


_JA_SKIP_POS1 = {"補助記号", "記号", "空白"}


def _ja_pos(pos1: str, pos2: str) -> str:
    if pos1 == "名詞":
        if pos2 == "固有名詞":
            return "PROPN"
        if pos2 == "数詞":
            return "NUM"
        return "NOUN"
    if pos1 == "感動詞":
        return "INTJ"
    if pos1 == "助詞":
        if pos2 == "格助詞":
            return "ADP"
        if pos2 == "接続助詞":
            return "SCONJ"
        return "PART"
    return _JA_POS.get(pos1, "X")


_JA_POS = {
    "代名詞": "PRON",
    "動詞": "VERB",
    "形容詞": "ADJ",
    "形状詞": "ADJ",
    "副詞": "ADV",
    "連体詞": "DET",
    "接続詞": "CCONJ",
    "助動詞": "AUX",
    "接頭辞": "PART",
    "接尾辞": "PART",
}


def _ja_analyse(text: str) -> list[tuple[str, str, str, str]]:
    """Return (surface, coarse_pos, pos2, kana_reading) for non-punctuation morphemes."""
    out: list[tuple[str, str, str, str]] = []
    words = list(_ja_tagger()(unicodedata.normalize("NFKC", text or "")))
    for i, w in enumerate(words):
        f = w.feature
        pos1 = getattr(f, "pos1", "") or ""
        if pos1 in _JA_SKIP_POS1 or not w.surface.strip():
            continue
        pos2 = getattr(f, "pos2", "") or ""
        kana = getattr(f, "kana", None) or getattr(f, "pron", None) or w.surface
        coarse = _ja_pos(pos1, pos2)
        # unidic-lite tags あの/その as フィラー even directly before a noun ("あの店");
        # there it is a determiner (連体詞), so re-tag it.
        if pos2 == "フィラー" and w.surface in {"あの", "その", "この"} and i + 1 < len(words):
            if (getattr(words[i + 1].feature, "pos1", "") or "") in {"名詞", "代名詞"}:
                coarse, pos2 = "DET", "連体詞"
        out.append((w.surface, coarse, pos2, kana))
    return out


def ja_readings(text: str) -> list[tuple[str, str]]:
    """(surface, hiragana reading) per Japanese morpheme — used by word-recall scoring."""
    return [(s, to_hiragana(k)) for s, _, _, k in _ja_analyse(text)]


# --------------------------------------------------------------------------
# English heuristic tagger
# --------------------------------------------------------------------------

_EN_PRON = set(
    "i me my mine myself you your yours yourself yourselves he him his himself she her hers herself "
    "it its itself we us our ours ourselves they them their theirs themselves this that these those "
    "who whom whose which what someone somebody something anyone anybody anything everyone everybody "
    "everything nobody nothing one".split()
)
_EN_DET = set("a an the some any no every each either neither another such all both many much few several".split())
_EN_ADP = set(
    "about above across after against along among around at before behind below beneath beside between "
    "beyond by down during except for from in inside into near of off on onto out outside over past "
    "since through throughout to toward towards under until up upon with within without".split()
)
_EN_CCONJ = set("and but or nor so yet".split())
_EN_SCONJ = set("because although though while when whenever where if unless whether than as once".split())
_EN_AUX = set(
    "am is are was were be been being have has had having do does did will would shall should can could "
    "may might must 's 're 've 'd 'll".split()
)
_EN_INTJ = set("um uh er erm ah oh hmm mm uhm well yes no okay ok".split())
_EN_ADV = set(
    "not very too also just then there here now again still already soon always never often sometimes "
    "really quite almost finally instead back away home later today yesterday tomorrow once twice "
    "together slowly quickly maybe perhaps".split()
)
_EN_ADJ = set(
    "old new young big small little large long short good bad great best better first last next fresh "
    "ripe warm cold hot heavy white yellow red blue green grey gray black brown dark free happy sad "
    "favourite favorite surprised own whole several different same other sure safe strong".split()
)
_EN_VERB = set(
    "go goes went gone going come came comes take took taken takes make made makes get got gets give "
    "gave given gives see saw seen sees say said says tell told tells know knew known think thought "
    "want wanted find found leave left bring brought buy bought sell sold eat ate eaten drink drank "
    "run ran sit sat stand stood walk walked meet met pay paid put keep kept hold held carry carried "
    "call called ask asked try tried use used feel felt become became look looked help helped start "
    "began begin begun visit visited travel travelled traveled arrive arrived wait waited drive drove "
    "driven ride rode bake baked cook cooked cut chop chopped cover covered water watered notice "
    "noticed fetch fetched climb climbed reach reached return returned decide decided join joined "
    "print printed hang hung phone phoned win won vote voted taste tasted thank thanked laugh laughed "
    "realise realised realize realized hurry hurried choose chose chosen blow blew turn turned "
    "remember remembered forget forgot hear heard read write wrote written like liked love loved "
    "live lived work worked open opened close closed stop stopped offer offered fall fell".split()
)
_EN_ADJ_SUFFIX = ("ful", "ous", "ive", "able", "ible", "less", "ish")


def _en_heuristic_tag(word: str, capitalised_mid: bool) -> str:
    w = word.lower()
    for tag, lex in (
        ("PRON", _EN_PRON),
        ("DET", _EN_DET),
        ("AUX", _EN_AUX),
        ("ADP", _EN_ADP),
        ("CCONJ", _EN_CCONJ),
        ("SCONJ", _EN_SCONJ),
        ("INTJ", _EN_INTJ),
        ("ADV", _EN_ADV),
        ("ADJ", _EN_ADJ),
        ("VERB", _EN_VERB),
    ):
        if w in lex:
            return tag
    if w.isdigit():
        return "NUM"
    if capitalised_mid:
        return "PROPN"
    if w.endswith("ly") and len(w) > 4:
        return "ADV"
    if (w.endswith("ed") or w.endswith("ing")) and len(w) > 4:
        return "VERB"
    if w.endswith(_EN_ADJ_SUFFIX) and len(w) > 5:
        return "ADJ"
    return "NOUN"


@lru_cache(maxsize=1)
def _spacy_en() -> Any | None:
    try:  # optional upgrade path named in §1.1; not a hard dependency
        import spacy

        return spacy.load("en_core_web_sm", disable=["ner", "parser", "lemmatizer"])
    except Exception:
        return None


_EN_RAW_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?|[.!?]")


def _en_tags(text: str) -> tuple[list[tuple[str, str]], str]:
    nlp = _spacy_en()
    if nlp is not None:
        doc = nlp(text or "")
        return [(t.text.lower(), t.pos_) for t in doc if not t.is_punct and not t.is_space], "spacy"
    out: list[tuple[str, str]] = []
    sentence_start = True
    for m in _EN_RAW_RE.finditer(text or ""):
        tok = m.group(0)
        if tok in ".!?":
            sentence_start = True
            continue
        cap_mid = tok[:1].isupper() and not sentence_start and tok.lower() != "i"
        out.append((tok.lower().replace("’", "'"), _en_heuristic_tag(tok, cap_mid)))
        sentence_start = False
    return out, "heuristic"


# --------------------------------------------------------------------------
# Public tokenisation API
# --------------------------------------------------------------------------

def tokenize(text: str, language: str) -> list[str]:
    """Tokenise ``text``: EN lower-case regex words; JA fugashi surface forms (no punctuation)."""
    if normalize_language(language) == "ja":
        return [s for s, _, _, _ in _ja_analyse(text)]
    return [w.replace("’", "'") for w in _EN_WORD_RE.findall((text or "").lower())]


def pos_tags(text: str, language: str) -> list[tuple[str, str]]:
    """(token, coarse POS) pairs; tags: NOUN PROPN PRON VERB AUX ADJ ADV ADP CCONJ SCONJ DET PART INTJ NUM X."""
    if normalize_language(language) == "ja":
        return [(s, p) for s, p, _, _ in _ja_analyse(text)]
    return _en_tags(text)[0]


# --------------------------------------------------------------------------
# Lexical features (§1.1)
# --------------------------------------------------------------------------

EN_FILLERS = {"um", "uh", "er", "erm", "ah", "hmm", "mm", "uhm", "umm", "uhh"}
# "あの"/"その" are excluded: they are usually determiners (連体詞); UniDic tags filler uses as フィラー.
JA_FILLERS = {"えー", "えーと", "えっと", "ええと", "あー", "あのー", "うーん", "んー", "まあ", "えと"}
EN_VAGUE = {"thing", "things", "stuff", "something", "somewhere", "someone", "somebody", "whatever", "whatsit", "whatchamacallit"}
JA_VAGUE = {"あれ", "それ", "これ", "なんか", "何か", "どこか", "誰か", "あそこ", "そこ", "なんとか", "何とか"}
_IDEA_TAGS = {"VERB", "ADJ", "ADV", "ADP", "CCONJ", "SCONJ"}


def lexical_features(text: str, language: str) -> dict[str, Any]:
    """Lexical feature set (§1.1, Table 1 of Skirrow et al. 2024) plus filler/vague-word rates.

    Returns ``word_count``, ``unique_word_ratio``, ``pronoun_noun_ratio``,
    ``noun_rate``, ``idea_density`` (proxy; see module docstring),
    ``filler_rate``, ``vague_word_rate`` and the ``tagger`` used. JA adds
    ``char_count`` (non-space characters). JA fillers are those tagged
    ``感動詞-フィラー`` by UniDic or in ``JA_FILLERS``; note that ASR (Whisper)
    tends to drop fillers, so ``filler_rate`` is a lower bound.
    """
    lang = normalize_language(language)
    if lang == "ja":
        analysed = _ja_analyse(text)
        tagged = [(s, p) for s, p, _, _ in analysed]
        fillers = sum(1 for s, _, p2, _ in analysed if p2 == "フィラー" or s in JA_FILLERS)
        vague_lex, tagger = JA_VAGUE, "fugashi-unidic"
    else:
        tagged, tagger = _en_tags(text)
        fillers = sum(1 for t, _ in tagged if t in EN_FILLERS)
        vague_lex = EN_VAGUE
    n = len(tagged)
    feats: dict[str, Any] = {"language": lang, "tagger": tagger, "word_count": n}
    if lang == "ja":
        feats["char_count"] = len(re.sub(r"\s", "", text or ""))
    if n == 0:
        feats.update(
            unique_word_ratio=0.0, pronoun_noun_ratio=0.0, noun_rate=0.0,
            idea_density=0.0, filler_rate=0.0, vague_word_rate=0.0,
        )
        return feats
    tags = [p for _, p in tagged]
    nouns = sum(1 for p in tags if p in {"NOUN", "PROPN"})
    prons = sum(1 for p in tags if p == "PRON")
    feats.update(
        unique_word_ratio=len({t for t, _ in tagged}) / n,
        pronoun_noun_ratio=(prons / nouns) if nouns else float(prons),
        noun_rate=nouns / n,
        idea_density=sum(1 for p in tags if p in _IDEA_TAGS) / n,
        filler_rate=fillers / n,
        vague_word_rate=sum(1 for t, _ in tagged if t in vague_lex) / n,
    )
    return feats


# --------------------------------------------------------------------------
# Temporal features (§1.1)
# --------------------------------------------------------------------------

def energy_vad(
    y: np.ndarray,
    sr: int,
    frame_s: float = 0.025,
    hop_s: float = 0.010,
    rel_db: float = -35.0,
    min_voiced_s: float = 0.05,
) -> list[tuple[float, float]]:
    """Energy-based voice-activity detection with librosa RMS.

    A frame is speech when its RMS is within ``rel_db`` of the recording peak
    and ≥6 dB above the 10th-percentile noise floor. Runs shorter than
    ``min_voiced_s`` are dropped. Returns ``[(start_s, end_s), ...]``.
    """
    import librosa

    y = np.asarray(y, dtype=np.float32)
    if y.ndim > 1:
        y = y.mean(axis=0 if y.shape[0] < y.shape[-1] else 1)
    if y.size == 0 or float(np.max(np.abs(y))) == 0.0:
        return []
    frame, hop = max(1, int(frame_s * sr)), max(1, int(hop_s * sr))
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=hop, center=True)[0]
    db = 20.0 * np.log10(np.maximum(rms, 1e-10))
    floor = float(np.percentile(db, 10))
    voiced = (db >= db.max() + rel_db) & (db >= floor + 6.0)
    runs: list[tuple[float, float]] = []
    start: int | None = None
    for i, v in enumerate(np.append(voiced, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            s, e = start * hop / sr, i * hop / sr
            if e - s >= min_voiced_s:
                runs.append((s, e))
            start = None
    return runs


def _intervals_from_timing(timing: Sequence[dict[str, Any]]) -> tuple[list[tuple[float, float, str]], str]:
    items: list[tuple[float, float, str]] = []
    kind = "segments"
    for t in timing or []:
        try:
            s, e = float(t["start"]), float(t["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if "word" in t:
            kind = "word_timestamps"
        items.append((s, max(s, e), str(t.get("word", t.get("text", "")))))
    items.sort(key=lambda x: x[0])
    return items, kind


def temporal_features(
    timing: Sequence[dict[str, Any]] | None,
    audio_duration_s: float | None,
    y: np.ndarray | None = None,
    sr: int | None = None,
    *,
    language: str = "en",
    text: str | None = None,
) -> dict[str, Any]:
    """Temporal feature set (§1.1): duration, rate, pauses >200 ms, pause time.

    Source of timing, in order of preference: ``timing`` with word
    timestamps (``[{word,start,end}]``), else energy VAD on ``y``/``sr``, else
    ``timing`` as segments (``[{start,end,text}]``, coarse: only gaps between
    segments count as pauses).

    Definitions: ``speech_span_s`` = first speech onset → last offset;
    a pause is a silent gap > 200 ms inside the span; ``speech_duration_s`` =
    span − total pause time (phonation time); ``pause_ratio`` = pause / span;
    ``speech_rate`` = units per minute over the span and ``articulation_rate``
    = units per minute over phonation time, where units are words (EN,
    ``speech_rate_wpm``) or characters (JA, ``speech_rate_cpm``; also exposed
    per second as ``speech_rate_cps``) — Japanese has no word boundary in ASR
    output, so characters are the stable unit.
    """
    lang = normalize_language(language)
    items, kind = _intervals_from_timing(timing or [])
    intervals: list[tuple[float, float]]
    if kind == "word_timestamps" and items:
        source = "word_timestamps"
        intervals = [(s, e) for s, e, _ in items]
        if text is None:
            text = ("" if lang == "ja" else " ").join(w for _, _, w in items)
    elif y is not None and sr:
        source = "energy_vad"
        intervals = energy_vad(y, int(sr))
        if audio_duration_s is None:
            audio_duration_s = len(np.asarray(y).reshape(-1)) / float(sr)
    elif items:
        source = "segments"
        intervals = [(s, e) for s, e, _ in items]
        if text is None:
            text = ("" if lang == "ja" else " ").join(w for _, _, w in items)
    else:
        source = "none"
        intervals = []

    if lang == "ja":
        units = len(re.sub(r"[\s、。，．,.!?！？「」『』（）()・…ー]", "", text or "")) if text else 0
    else:
        units = len(tokenize(text or "", "en"))

    out: dict[str, Any] = {
        "timing_source": source,
        "audio_duration_s": float(audio_duration_s) if audio_duration_s is not None else None,
        "rate_unit": "chars" if lang == "ja" else "words",
        "unit_count": units,
    }
    if not intervals:
        out.update(
            speech_span_s=0.0, speech_duration_s=0.0, n_pauses_gt_200ms=0, total_pause_s=0.0,
            pause_ratio=0.0, speech_rate_wpm=None, speech_rate_cpm=None, speech_rate_cps=None,
            articulation_rate=None, mean_pause_s=0.0,
        )
        return out

    # merge overlaps, then gaps between consecutive speech runs
    merged: list[list[float]] = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    gaps = [merged[i + 1][0] - merged[i][1] for i in range(len(merged) - 1)]
    pauses = [g for g in gaps if g > PAUSE_THRESHOLD_S]
    span = merged[-1][1] - merged[0][0]
    total_pause = float(sum(pauses))
    phonation = max(span - total_pause, 1e-6)
    rate_per_min = units / (span / 60.0) if span > 0 and units else None
    artic = units / (phonation / 60.0) if units else None
    out.update(
        speech_span_s=float(span),
        speech_duration_s=float(phonation if span > 0 else 0.0),
        n_pauses_gt_200ms=len(pauses),
        total_pause_s=total_pause,
        mean_pause_s=total_pause / len(pauses) if pauses else 0.0,
        pause_ratio=total_pause / span if span > 0 else 0.0,
        speech_rate_wpm=rate_per_min if lang == "en" else None,
        speech_rate_cpm=rate_per_min if lang == "ja" else None,
        speech_rate_cps=(rate_per_min / 60.0) if (rate_per_min and lang == "ja") else None,
        articulation_rate=artic,
    )
    return out


# --------------------------------------------------------------------------
# Spectral features (§1.1) — low reliability on uncontrolled microphones
# --------------------------------------------------------------------------

def _praat_voice(y: np.ndarray, sr: int, fmin: float, fmax: float) -> dict[str, float] | None:
    try:
        import parselmouth
        from parselmouth.praat import call
    except Exception:
        return None
    try:
        snd = parselmouth.Sound(np.asarray(y, dtype=np.float64), sampling_frequency=float(sr))
        pp = call(snd, "To PointProcess (periodic, cc)", fmin, fmax)
        jitter = call(pp, "Get jitter (local)", 0, 0, 0.0001, 0.02, 1.3)
        shimmer = call([snd, pp], "Get shimmer (local)", 0, 0, 0.0001, 0.02, 1.3, 1.6)
        harm = call(snd, "To Harmonicity (cc)", 0.01, fmin, 0.1, 1.0)
        hnr = call(harm, "Get mean", 0, 0)
        vals = {"jitter_local": float(jitter), "shimmer_local": float(shimmer), "hnr": float(hnr)}
        return {k: (v if np.isfinite(v) else float("nan")) for k, v in vals.items()}
    except Exception:
        return None


def _approx_voice(y: np.ndarray, sr: int, f0: np.ndarray, voiced: np.ndarray, hop: int) -> dict[str, float]:
    """Frame-level approximations when parselmouth is unavailable (coarser than Praat's cycle-level)."""
    jit = shim = hnr = float("nan")
    periods = 1.0 / f0[voiced & np.isfinite(f0)] if np.any(voiced) else np.array([])
    if periods.size > 2:
        jit = float(np.mean(np.abs(np.diff(periods))) / np.mean(periods))
    import librosa

    amp = librosa.feature.rms(y=y, frame_length=hop * 4, hop_length=hop)[0][: len(voiced)]
    a = amp[voiced[: len(amp)]]
    if a.size > 2 and np.mean(a) > 0:
        shim = float(np.mean(np.abs(np.diff(a))) / np.mean(a))
    rs: list[float] = []
    frame = hop * 4
    for i in np.flatnonzero(voiced)[:400]:
        seg = y[i * hop : i * hop + frame]
        if len(seg) < frame or not np.isfinite(f0[i]):
            continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, "full")[frame - 1 :]
        lag = int(round(sr / f0[i]))
        if 0 < lag < len(ac) and ac[0] > 0:
            rs.append(float(np.clip(ac[lag] / ac[0], 1e-4, 0.9999)))
    if rs:
        r = float(np.median(rs))
        hnr = 10.0 * np.log10(r / (1.0 - r))
    return {"jitter_local": jit, "shimmer_local": shim, "hnr": hnr}


def spectral_features(y: np.ndarray, sr: int, fmin: float = 65.0, fmax: float = 400.0) -> dict[str, Any]:
    """Spectral/voice-quality set (§1.1): F0 mean/SD (pyin), HNR, MFCC 1-13 mean/SD, jitter, shimmer.

    Jitter/shimmer/HNR come from Praat via ``parselmouth`` when importable
    (``voice_backend='parselmouth'``), else frame-level approximations
    (``'approx'``). Always tagged ``reliability='low_on_uncontrolled_mics'``:
    the source paper reports spectral features as the weakest set (AUC
    0.56-0.63), attributed to microphone variability.
    """
    import librosa

    y = np.asarray(y, dtype=np.float32).reshape(-1)
    out: dict[str, Any] = {"reliability": "low_on_uncontrolled_mics"}
    if y.size < int(0.5 * sr) or float(np.max(np.abs(y))) == 0.0:
        nan = float("nan")
        out.update(f0_mean=nan, f0_sd=nan, hnr=nan, jitter_local=nan, shimmer_local=nan,
                   mfcc_mean=[nan] * 13, mfcc_sd=[nan] * 13, voiced_fraction=0.0, voice_backend="none")
        return out
    hop = 512 if sr >= 32000 else 256
    f0, voiced, _ = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=sr, hop_length=hop, frame_length=hop * 4)
    voiced = np.asarray(voiced, dtype=bool)
    vf0 = f0[voiced & np.isfinite(f0)]
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    out.update(
        f0_mean=float(np.mean(vf0)) if vf0.size else float("nan"),
        f0_sd=float(np.std(vf0)) if vf0.size else float("nan"),
        mfcc_mean=[float(v) for v in mfcc.mean(axis=1)],
        mfcc_sd=[float(v) for v in mfcc.std(axis=1)],
        voiced_fraction=float(voiced.mean()) if voiced.size else 0.0,
    )
    praat = _praat_voice(y, sr, fmin, fmax)
    if praat is not None:
        out.update(praat, voice_backend="parselmouth")
    else:
        out.update(_approx_voice(y, sr, f0, voiced, hop), voice_backend="approx")
    return out


def concat_words(words: Iterable[str], language: str) -> str:
    """Join ASR word tokens into text (no spaces for JA)."""
    return ("" if normalize_language(language) == "ja" else " ").join(w.strip() for w in words)
