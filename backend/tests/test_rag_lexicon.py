import pytest

from app.rag.lexicon import expand_query


@pytest.mark.parametrize("query, term", [
    ("What are blood thinners for?", "anticoagulants"),
    ("My top number is 142", "systolic blood pressure"),
    ("Will walking help bring my blood pressure down?", "blood pressure lowering"),
    ("my heart is fluttering at night", "palpitations"),
    ("Do I have afib?", "atrial fibrillation"),
    ("Dad had a mini-stroke", "transient ischemic attack"),
    ("I keep forgetting names", "memory complaints"),
    ("Mom repeats the same questions", "memory complaints"),
    ("How do I keep my brain sharp?", "dementia risk reduction"),
    ("My hands shake at rest", "tremor"),
    ("He has started shuffling", "gait"),
    ("my voice has gone quiet", "hypophonia"),
    ("I sit at a desk all day", "sedentary behaviour"),
])
def test_lay_words_gain_clinical_terms(query, term):
    out = expand_query(query)
    assert out.startswith(query) and term in out


@pytest.mark.parametrize("query", [
    "Which anticoagulants are recommended for atrial fibrillation?",  # already clinical
    "What is the CCS CHADS-65 algorithm?",
    "What's a good recipe for banana bread?",
    "",
])
def test_unchanged_without_lay_words(query):
    assert expand_query(query) == query


def test_terms_not_repeated():
    out = expand_query("blood thinners and blood thinner doses")
    assert out.count("anticoagulants") == 1
