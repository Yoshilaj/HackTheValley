"""Content banks: schema, counts and §1.1 length bounds; loader behaviour."""

import random

import pytest

from app.content import STORY_BANK_VERSION, ContentError, get_story, get_story_by_id, get_word_set, list_stories
from app.content.loader import (
    CONTENT_UNIT_RANGE, ENTITY_RANGE, EN_WORD_RANGE, JA_CHAR_RANGE, SENTENCE_RANGE,
    count_en_words, count_ja_chars, count_sentences,
)

ALL = [(lang, s) for lang in ("en", "ja") for s in list_stories(lang)]


def test_six_stories_per_language():
    assert len(list_stories("en")) == 6
    assert len(list_stories("ja")) == 6
    assert len({s.id for _, s in ALL}) == 12


@pytest.mark.parametrize("lang,story", ALL, ids=[s.id for _, s in ALL])
def test_story_bounds(lang, story):
    assert story.language == lang
    assert story.version == STORY_BANK_VERSION
    if lang == "en":
        assert story.word_count == count_en_words(story.text)
        assert EN_WORD_RANGE[0] <= story.word_count <= EN_WORD_RANGE[1]
    else:
        assert story.char_count == count_ja_chars(story.text)
        assert JA_CHAR_RANGE[0] <= story.char_count <= JA_CHAR_RANGE[1]
    assert story.sentence_count == count_sentences(story.text)
    assert SENTENCE_RANGE[0] <= story.sentence_count <= SENTENCE_RANGE[1]
    assert ENTITY_RANGE[0] <= len(story.entities) <= ENTITY_RANGE[1]
    assert CONTENT_UNIT_RANGE[0] <= len(story.content_units) <= CONTENT_UNIT_RANGE[1]
    assert story.title and story.theme
    # every named entity's head appears in the text
    for ent in story.entities:
        head = ent.replace("the ", "").split()[0] if lang == "en" else ent[:2]
        assert head in story.text, ent


def test_themes_balanced():
    for lang in ("en", "ja"):
        themes = [s.theme for s in list_stories(lang)]
        assert len(set(themes)) == len(themes)


def test_loader_excludes_used_ids():
    used = [s.id for s in list_stories("en")][:5]
    for seed in range(10):
        s = get_story("en", exclude_ids=used, rng=random.Random(seed))
        assert s.id not in used
    with pytest.raises(ContentError):
        get_story("en", exclude_ids=[s.id for s in list_stories("en")])


def test_get_story_by_id_and_unknown():
    assert get_story_by_id("ja-04-neko").language == "ja"
    with pytest.raises(ContentError):
        get_story_by_id("nope")
    with pytest.raises(ContentError):
        get_story("fr")


def test_word_sets():
    for lang in ("en", "ja"):
        ws = get_word_set(lang, rng=random.Random(1))
        assert len(ws.words) == 3 and len(set(ws.words)) == 3
    ws = get_word_set("ja", exclude_ids=[f"wr-{i:02d}" for i in range(1, 10)])
    assert ws.id == "wr-10"

