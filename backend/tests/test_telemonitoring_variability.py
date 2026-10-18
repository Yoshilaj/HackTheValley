"""Within-person variability analysis on a tiny synthetic CSV in the UCI telemonitoring layout."""

import json
import math

import numpy as np
import pandas as pd
import pytest

from ml_training import analyze_telemonitoring_variability as mod

COLS = [
    "subject#", "age", "sex", "test_time", "motor_UPDRS", "total_UPDRS",
    "Jitter(%)", "Jitter(Abs)", "Jitter:RAP", "Jitter:PPQ5", "Jitter:DDP",
    "Shimmer", "Shimmer(dB)", "Shimmer:APQ3", "Shimmer:APQ5", "Shimmer:APQ11", "Shimmer:DDA",
    "NHR", "HNR", "RPDE", "DFA", "PPE",
]


def synthetic_frame(n_subjects: int = 3, n_rec: int = 40, seed: int = 0) -> pd.DataFrame:
    """Each subject: 40 recordings over ~120 days; Shimmer drifts up with UPDRS, HNR is flat noise."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(1, n_subjects + 1):
        t = np.sort(rng.uniform(-3, 120, n_rec))
        updrs = 20 + 0.05 * t + rng.normal(0, 0.5, n_rec)
        base = {c: 0.01 for c in COLS[6:]}
        for i in range(n_rec):
            r = {"subject#": s, "age": 60 + s, "sex": s % 2, "test_time": float(t[i]),
                 "motor_UPDRS": float(updrs[i] - 5), "total_UPDRS": float(updrs[i])}
            r.update({c: base[c] * (1 + 0.1 * rng.standard_normal()) for c in COLS[6:]})
            r["Shimmer"] = 0.03 * (1 + 0.004 * t[i]) * (1 + 0.05 * rng.standard_normal())  # tracks UPDRS
            r["HNR"] = 21.0 + rng.normal(0, 0.3)
            rows.append(r)
    return pd.DataFrame(rows, columns=COLS)


@pytest.fixture(scope="module")
def result():
    return mod.analyze(synthetic_frame())


def test_statistics_helpers():
    assert mod.rel_mad(np.array([1.0, 1.0, 1.0, 1.0])) == 0.0
    assert math.isnan(mod.rel_mad(np.array([1.0, 2.0])))
    assert math.isnan(mod.rel_mad(np.array([-1.0, 0.0, 1.0])))  # median 0
    x = np.array([10, 10, 10, 20, 20, 20], dtype=float)
    assert mod.weekly_drift(np.array([0, 1, 2, 7, 8, 9]), x) == pytest.approx(1.0)
    assert math.isnan(mod.weekly_drift(np.array([0, 1, 2]), x[:3]))
    assert mod.spearman(np.arange(10), np.arange(10) ** 2) == pytest.approx(1.0)
    assert math.isnan(mod.spearman(np.ones(10), np.arange(10)))
    flags = mod.alert_flags(np.array([1.0] * 10 + [1.0, 1.0, 5.0, 5.0, 1.0]), +1, 2.0)
    # baseline rel-MAD is 0, so any increase trips; decreases/equal do not
    assert flags.tolist() == [False, False, True, True, False]
    assert mod.two_of_three(flags) == pytest.approx(2 / 3)  # windows: FFT, FTT, TTF
    assert mod.alert_flags(np.ones(5), +1, 2.0) is None


def test_schema_and_values(result):
    assert set(result) == {"source", "method", "features"}
    src = result["source"]
    assert src["dataset"] == "UCI Parkinsons Telemonitoring" and src["doi"] == "10.24432/C5ZS3N"
    assert src["licence"] == "CC BY 4.0" and src["n_subjects"] == 3 and src["n_recordings"] == 120
    assert "AHTD" in src["note"]
    assert set(result["features"]) == set(mod.FEATURES)
    for f, e in result["features"].items():
        assert {"rel_mad_median", "rel_mad_iqr", "baseline10_rel_mad_median", "weekly_drift_median",
                "spearman_total_updrs", "spearman_motor_updrs", "n_subjects", "alert_rates"} <= set(e)
        assert e["n_subjects"] == 3 and len(e["rel_mad_iqr"]) == 2
        assert 0 <= e["rel_mad_iqr"][0] <= e["rel_mad_median"] <= e["rel_mad_iqr"][1]
        assert 0 <= e["baseline10_rel_mad_median"] and e["weekly_drift_median"] >= 0
        st = e["spearman_total_updrs"]
        assert {"median", "iqr", "frac_positive", "n_subjects"} <= set(st) and -1 <= st["median"] <= 1
        assert 0 <= st["frac_positive"] <= 1
        for k in ("k1.5", "k2", "k3"):
            ar = e["alert_rates"][k]
            assert 0 <= ar["single"] <= 1 and 0 <= ar["two_of_three"] <= 1
    # Shimmer was built to rise with UPDRS; HNR is pure noise with a tiny relative spread
    sh, hnr = result["features"]["Shimmer"], result["features"]["HNR"]
    assert sh["spearman_total_updrs"]["median"] > 0.8 and sh["spearman_total_updrs"]["frac_positive"] == 1.0
    assert abs(hnr["spearman_total_updrs"]["median"]) < 0.5
    assert hnr["rel_mad_median"] < 0.05 < result["features"]["Jitter(%)"]["rel_mad_median"]
    # a rising feature trips the k=2 alert far more often than a flat one
    assert sh["alert_rates"]["k2"]["single"] > hnr["alert_rates"]["k2"]["single"]
    json.dumps(result)  # JSON-serialisable (no numpy scalars / nan)
    assert "NaN" not in json.dumps(result)


def test_markdown_and_cli(tmp_path, result):
    md = mod.render_markdown(result)
    assert md.startswith("# Within-person") and "| Shimmer |" in md and "## Interpretation" in md
    assert len([ln for ln in md.splitlines() if ln[:2] in {"1.", "2.", "3.", "4.", "5."}]) == 5

    csv = tmp_path / "tele.csv"
    synthetic_frame().to_csv(csv, index=False)
    jout, mout = tmp_path / "out" / "floor.json", tmp_path / "out" / "floor.md"
    assert mod.main(["--input", str(csv), "--json-out", str(jout), "--md-out", str(mout)]) == 0
    data = json.loads(jout.read_text())
    assert data["features"]["Shimmer"]["rel_mad_median"] == pytest.approx(result["features"]["Shimmer"]["rel_mad_median"])
    assert mout.read_text() == md
    assert mod.main(["--input", str(tmp_path / "missing.csv"), "--json-out", str(jout), "--md-out", str(mout)]) == 2


def test_missing_columns_rejected():
    with pytest.raises(ValueError, match="missing columns"):
        mod.analyze(pd.DataFrame({"subject#": [1], "test_time": [0.0]}))


def test_tracked_floor_json_matches_schema():
    """The committed app/ml/data/voice_variability_floor.json must carry the full-dataset schema."""
    p = mod.DEFAULT_JSON_OUT
    assert p.exists(), "run `python -m ml_training.analyze_telemonitoring_variability`"
    data = json.loads(p.read_text())
    assert data["source"]["n_subjects"] == 42 and data["source"]["n_recordings"] == 5875
    assert set(data["features"]) == set(mod.FEATURES)
    for e in data["features"].values():
        assert e["n_subjects"] == 42 and e["rel_mad_median"] > 0
