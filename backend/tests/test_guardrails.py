"""Red-flag detector, emergency text and output guard."""

import pytest

from app.services.guardrails import (
    DISCLAIMER_EN, DISCLAIMER_JA, RED_FLAG_LABELS, detect_red_flags, emergency_response, output_guard,
)


@pytest.mark.parametrize("text,expected", [
    ("I have chest pain right now", ["chest_pain"]),
    ("There's a tightness in my chest", ["chest_pressure"]),
    ("My face is drooping and my speech is slurred", ["speech_difficulty", "face_droop"]),
    ("Suddenly I can't lift my arm", ["arm_weakness"]),
    ("I passed out in the kitchen", ["fainting"]),
    ("worst headache of my life, it came on suddenly", ["severe_sudden_headache"]),
    ("I don't want to live anymore", ["suicidal_ideation"]),
    ("I'm not sure but my chest hurts", ["chest_pain"]),
    ("胸が締め付けられるように痛い", ["chest_pain", "chest_pressure"]),
    ("ろれつが回らない", ["speech_difficulty"]),
    ("右腕に力が入らない", ["arm_weakness"]),
    ("顔の片側がゆがんでいる", ["face_droop"]),
    ("昨日失神しました", ["fainting"]),
    ("突然激しい頭痛がした", ["severe_sudden_headache"]),
    ("死にたい", ["suicidal_ideation"]),
    ("急に目が見えなくなった", ["vision_disturbance"]),
])
def test_detects(text, expected):
    assert detect_red_flags(text, "en") == expected


@pytest.mark.parametrize("text", [
    "no chest pain today", "I never had chest pain", "I slept badly and feel tired",
    "胸は痛くないです", "失神はしていない", "少し頭痛がします", "What is atrial fibrillation?",
    "My blood pressure was 146/92 this morning",
])
def test_no_false_positive(text):
    assert detect_red_flags(text, "ja") == []


def test_labels_are_known():
    assert set(detect_red_flags("chest pain, fainted, 死にたい")) <= set(RED_FLAG_LABELS)


def test_emergency_response_canada_en_default_and_ja():
    en = emergency_response("en", ["fainting"], "911")
    assert "call 911" in en and "fainting" in en and "book" in en
    assert "811" in en and "registered nurse" in en
    default = emergency_response(None, ["chest_pain", "face_droop"])  # no language, no number → English, 911
    assert default.startswith("EMERGENCY") and "call 911" in default and "FAST" in default
    assert "119" not in default and "#7119" not in default
    s = emergency_response("en", ["suicidal_ideation"], "911")
    assert "9-8-8" in s and "call or text" in s and "Canada" in s
    ja = emergency_response("ja", ["chest_pain", "face_droop"])
    assert "911" in ja and "811" in ja and "胸の痛み" in ja and "脳卒中" in ja and "#7119" not in ja
    assert "9-8-8" in emergency_response("ja", ["suicidal_ideation"])


@pytest.mark.parametrize("text,expected", [
    ("the pain is radiating to my left arm", ["chest_pain"]),
    ("chest discomfort since noon", ["chest_pressure"]),
    ("I keeled over at the store", ["fainting"]),
    ("there is no reason to go on", ["suicidal_ideation"]),
    ("I took an overdose", ["suicidal_ideation"]),
])
def test_detects_extra_english_phrasings(text, expected):
    assert detect_red_flags(text) == expected


@pytest.mark.parametrize("text,expected", [
    # breathing
    ("I can't breathe", ["shortness_of_breath"]),
    ("I cant breathe", ["shortness_of_breath"]),
    ("can't catch my breath", ["shortness_of_breath"]),
    ("I'm short of breath and it's getting worse", ["shortness_of_breath"]),
    ("息ができない", ["shortness_of_breath"]),
    ("息苦しい", ["shortness_of_breath"]),
    # sudden confusion (BE-FAST)
    ("sudden confusion", ["sudden_confusion"]),
    ("my husband is suddenly confused", ["sudden_confusion"]),
    ("she became confused all of a sudden", ["sudden_confusion"]),
    ("急に混乱している", ["sudden_confusion"]),
    # suicidal ideation / overdose
    ("thinking of ending my life", ["suicidal_ideation"]),
    ("I just want to end it all", ["suicidal_ideation"]),
    ("I don't want to be alive", ["suicidal_ideation"]),
    ("I took too many pills", ["suicidal_ideation"]),
    ("I think I overdosed", ["suicidal_ideation"]),
    ("薬を飲みすぎた", ["suicidal_ideation"]),
    # BE-FAST balance / eyes
    ("sudden dizziness", ["loss_of_balance"]),
    ("I lost my balance out of nowhere", ["loss_of_balance"]),
    ("the room was spinning and I fell", ["loss_of_balance"]),
    ("sudden loss of vision", ["vision_disturbance"]),
    ("suddenly I can't see", ["vision_disturbance"]),
    ("急にめまいがした", ["loss_of_balance"]),
    ("めまいがして倒れた", ["fainting", "loss_of_balance"]),
])
def test_detects_breathing_confusion_balance_and_crisis(text, expected):
    assert detect_red_flags(text) == expected


@pytest.mark.parametrize("text", [
    "I don't have chest pain",
    "I'm confused about the app",               # confusion only fires on sudden onset
    "I don't know where the clinic is",
    "no shortness of breath today",
    "I am not short of breath",
    "I felt out of breath after the stairs",    # exertional, not an emergency phrase
    "I never lost my balance",
    "I was a bit dizzy after standing up",      # plain dizziness is a daily-log symptom, not a red flag
    "sudden loss of appetite",
    "I took my pills at 8",
    "息苦しくない", "めまいはない", "急に混乱はしていない", "混乱しています",
])
def test_no_false_positive_new_labels(text):
    assert detect_red_flags(text) == []


def test_new_labels_are_listed_and_have_emergency_text():
    for lbl in ("shortness_of_breath", "sudden_confusion", "loss_of_balance"):
        assert lbl in RED_FLAG_LABELS
        en = emergency_response("en", [lbl])
        ja = emergency_response("ja", [lbl])
        assert "EMERGENCY" in en and "911" in en and "【緊急】" in ja and "911" in ja
    # BE-FAST stroke hint for balance / confusion, none for breathing alone
    assert "FAST" in emergency_response("en", ["loss_of_balance"]) and "FAST" in emergency_response("en", ["sudden_confusion"])
    assert "FAST" not in emergency_response("en", ["shortness_of_breath"])
    assert "trouble breathing" in emergency_response("en", ["shortness_of_breath"])


def test_disclaimer_names_canadian_emergency_number():
    assert "911" in DISCLAIMER_EN and "119" not in DISCLAIMER_EN


def test_output_guard_appends_disclaimer_once():
    out = output_guard("Home BP is best measured twice in the morning [1].", "en")
    assert out.endswith(DISCLAIMER_EN)
    assert output_guard(out, "en").count(DISCLAIMER_EN) == 1
    assert output_guard("家庭血圧は朝晩測ります[1]。", "ja").endswith(DISCLAIMER_JA)


def test_output_guard_rewrites_diagnostic_phrasing():
    out = output_guard("Based on this you probably have atrial fibrillation. You should stop taking aspirin.", "en")
    assert "you probably have" not in out.lower()
    assert "it may be worth asking your doctor about atrial fibrillation" in out
    assert "You could ask your doctor whether to stop aspirin" in out
    ja = output_guard("あなたは高血圧です。", "ja")
    assert "高血圧の可能性について医師に相談する価値があります" in ja
    # benign "you have" is left alone
    assert "you have 7 days of readings" in output_guard("Great, you have 7 days of readings.", "en")
