"""Output guard and disclaimers."""

from app.services.guardrails import DISCLAIMER_EN, DISCLAIMER_JA, output_guard


def test_disclaimer_has_no_emergency_number():
    assert "911" not in DISCLAIMER_EN and "911" not in DISCLAIMER_JA


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
