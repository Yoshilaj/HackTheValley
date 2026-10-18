from scripts.eval_answers import raw_markers, summarise


def test_raw_markers_keeps_out_of_range_numbers():
    assert raw_markers("A [1]. B [2, 7]. C [3-4]. D [x].") == [1, 2, 7, 3, 4]


def _row(**kw):
    base = {"expect_empty": False, "faithful": True, "citations_correct": True, "answers_question": True,
            "cites": True, "cites_expected": True, "term_in_answer": None, "bad_markers": [],
            "disclaimer": True, "admits_no_evidence": False, "llm_ms": 100}
    return {**base, **kw}


def test_summarise_splits_answerable_and_unanswerable():
    rows = [_row(), _row(faithful=False, term_in_answer=True, bad_markers=[9]),
            _row(expect_empty=True, admits_no_evidence=True, cites=False)]
    s = summarise(rows)
    assert (s["answerable"], s["unanswerable"]) == (2, 1)
    assert s["faithful"] == 0.5 and s["term_in_answer"] == 1.0
    assert s["answers_with_bad_markers"] == 1
    assert s["unanswerable_admits_no_evidence"] == 1.0
