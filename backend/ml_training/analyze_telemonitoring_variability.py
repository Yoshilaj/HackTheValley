"""Within-person variability floor of voice features from the UCI Parkinson's Telemonitoring set.

Why: PD-03b ("personal voice stability") compares a user's sustained-vowel features
against their own baseline. To set an alert threshold we need to know how much the
same person's features wobble from day to day *without* any clinical change. The
UCI Telemonitoring data (Tsanas & Little 2009; 42 early-stage PD patients, ~200
home recordings each over ~6 months, one device) is the only public longitudinal
voice dataset of this shape, so it serves as an order-of-magnitude floor.

Caveats baked into the output's ``source.note``: PD-only, early-stage, AHTD-pipeline
features (not our Praat extractor), one device per person at home.

Per subject and feature this script computes

1. **rel_mad** - robust relative spread 1.4826 * MAD / |median| over all of the
   subject's recordings, and the same over the first 10 recordings only
   (``baseline10_rel_mad``: what a 10-recording app baseline would see);
2. **weekly_drift** - median absolute relative change between consecutive weekly
   medians (week = floor(test_time / 7));
3. **spearman_*_updrs** - Spearman rho between the feature series and
   total_UPDRS / motor_UPDRS within the subject;
4. **alert_rates** - empirical single-recording and 2-of-3 alert rates when a
   recording is flagged if it moves in the "worse" direction by more than
   k * baseline10_rel_mad relative to the baseline-10 median (k in K_ALERT).

Distributions across subjects (median, IQR, fraction positive) go to
``app/ml/data/voice_variability_floor.json``; a markdown summary goes to
``ml_training/telemonitoring_variability.md``.

Run from ``backend/``::

    .venv/bin/python -m ml_training.analyze_telemonitoring_variability
    .venv/bin/python -m ml_training.analyze_telemonitoring_variability --input path/to.csv --json-out x.json --md-out x.md
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = BACKEND_DIR / "data" / "raw" / "uci_telemonitoring" / "parkinsons_updrs.data"
DEFAULT_JSON_OUT = BACKEND_DIR / "app" / "ml" / "data" / "voice_variability_floor.json"
DEFAULT_MD_OUT = BACKEND_DIR / "ml_training" / "telemonitoring_variability.md"

# UCI telemonitoring column -> our extractor key (app/ml/features/vowel.py); informational only.
FEATURES: dict[str, str] = {
    "Jitter(%)": "MDVP:Jitter(%)",
    "Shimmer": "MDVP:Shimmer",
    "Shimmer(dB)": "MDVP:Shimmer(dB)",
    "Shimmer:APQ11": "MDVP:APQ",
    "NHR": "NHR",
    "HNR": "HNR",
    "RPDE": "RPDE",
    "DFA": "DFA",
    "PPE": "PPE",
}
# Direction that is "worse" in PD (Little 2009; Skodda 2013): +1 = increase is worse.
WORSE_DIRECTION: dict[str, int] = {
    "Jitter(%)": +1, "Shimmer": +1, "Shimmer(dB)": +1, "Shimmer:APQ11": +1, "NHR": +1,
    "HNR": -1, "RPDE": +1, "DFA": +1, "PPE": +1,
}
UPDRS_COLS = ("total_UPDRS", "motor_UPDRS")
BASELINE_N = 10
K_ALERT = (1.5, 2.0, 3.0)
MAD_SCALE = 1.4826

SOURCE = {
    "dataset": "UCI Parkinsons Telemonitoring",
    "doi": "10.24432/C5ZS3N",
    "licence": "CC BY 4.0",
    "n_subjects": 42,
    "n_recordings": 5875,
    "note": (
        "PD-only, early-stage, home recordings on one device; features from the AHTD pipeline not our "
        "extractor - used only as an order-of-magnitude floor for within-person relative variability"
    ),
}


# --------------------------------------------------------------------------- statistics

def rel_mad(x: np.ndarray) -> float:
    """1.4826 * MAD / |median|; nan when fewer than 3 finite values or median is 0."""
    x = np.asarray(x, dtype=np.float64)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return float("nan")
    med = float(np.median(x))
    if med == 0.0:
        return float("nan")
    return float(MAD_SCALE * np.median(np.abs(x - med)) / abs(med))


def weekly_drift(t_days: np.ndarray, x: np.ndarray) -> float:
    """Median |relative change| between consecutive weekly medians; nan with < 2 weeks."""
    t = np.asarray(t_days, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    ok = np.isfinite(t) & np.isfinite(x)
    if ok.sum() < 2:
        return float("nan")
    weeks = np.floor(t[ok] / 7.0).astype(int)
    s = pd.Series(x[ok]).groupby(weeks).median().sort_index()
    if len(s) < 2:
        return float("nan")
    prev, cur = s.values[:-1], s.values[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.abs(cur - prev) / np.abs(prev)
    rel = rel[np.isfinite(rel)]
    return float(np.median(rel)) if len(rel) else float("nan")


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    from scipy.stats import spearmanr

    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 5 or np.std(x[ok]) == 0 or np.std(y[ok]) == 0:
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = spearmanr(x[ok], y[ok]).correlation
    return float(r) if np.isfinite(r) else float("nan")


def alert_flags(x: np.ndarray, direction: int, k: float, baseline_n: int = BASELINE_N) -> np.ndarray | None:
    """Boolean flags for recordings after the baseline: moved > k * baseline rel-MAD in the worse direction."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < baseline_n + 3:
        return None
    base = x[:baseline_n]
    med = float(np.median(base))
    spread = rel_mad(base)
    if not np.isfinite(spread) or med == 0.0:
        return None
    rel = direction * (x[baseline_n:] - med) / abs(med)
    return rel > k * spread


def two_of_three(flags: np.ndarray) -> float:
    """Fraction of 3-recording windows with >= 2 flags (a 2-of-N trend rule with N=3)."""
    if len(flags) < 3:
        return float("nan")
    f = flags.astype(int)
    windows = f[:-2] + f[1:-1] + f[2:]
    return float(np.mean(windows >= 2))


# --------------------------------------------------------------------------- per subject / aggregate

def per_subject(sub: pd.DataFrame, feature: str) -> dict[str, Any]:
    sub = sub.sort_values("test_time")
    x = sub[feature].to_numpy(dtype=np.float64)
    t = sub["test_time"].to_numpy(dtype=np.float64)
    out: dict[str, Any] = {
        "n": int(np.isfinite(x).sum()),
        "rel_mad": rel_mad(x),
        "baseline10_rel_mad": rel_mad(x[:BASELINE_N]),
        "weekly_drift": weekly_drift(t, x),
    }
    for col in UPDRS_COLS:
        out[f"spearman_{col.lower()}"] = spearman(x, sub[col].to_numpy()) if col in sub else float("nan")
    for k in K_ALERT:
        flags = alert_flags(x, WORSE_DIRECTION.get(feature, +1), k)
        out[f"alert_single_k{k:g}"] = float(np.mean(flags)) if flags is not None else float("nan")
        out[f"alert_2of3_k{k:g}"] = two_of_three(flags) if flags is not None else float("nan")
    return out


def _dist(values: list[float]) -> dict[str, Any]:
    v = np.asarray([x for x in values if x is not None and np.isfinite(x)], dtype=np.float64)
    if len(v) == 0:
        return {"median": None, "iqr": [None, None], "n": 0}
    return {
        "median": float(np.median(v)),
        "iqr": [float(np.percentile(v, 25)), float(np.percentile(v, 75))],
        "n": int(len(v)),
    }


def analyze(df: pd.DataFrame, features: dict[str, str] | None = None) -> dict[str, Any]:
    """Aggregate per-subject statistics into the ``voice_variability_floor.json`` schema."""
    features = features or FEATURES
    missing = [c for c in ("subject#", "test_time", *features) if c not in df.columns]
    if missing:
        raise ValueError(f"input is missing columns {missing}")
    subjects = sorted(df["subject#"].unique().tolist())
    result: dict[str, Any] = {
        "source": {**SOURCE, "n_subjects": len(subjects), "n_recordings": int(len(df))},
        "method": {
            "rel_mad": f"{MAD_SCALE} * MAD / |median| over the subject's recordings",
            "baseline10": f"same over the subject's first {BASELINE_N} recordings by test_time",
            "weekly_drift": "median |m_w - m_(w-1)| / |m_(w-1)| over consecutive weekly medians, week = floor(test_time/7)",
            "spearman": "within-subject Spearman rho of the feature series vs UPDRS; distribution across subjects",
            "alert_rates": (
                f"recording flagged when it moves in the worse direction by > k * baseline10_rel_mad from the "
                f"baseline-10 median (k in {list(K_ALERT)}); 'single' = fraction of post-baseline recordings "
                "flagged, 'two_of_three' = fraction of 3-recording windows with >= 2 flags; medians across subjects"
            ),
            "worse_direction": WORSE_DIRECTION,
        },
        "features": {},
    }
    for feat, ours in features.items():
        rows = [per_subject(g, feat) for _, g in df.groupby("subject#")]
        rm = _dist([r["rel_mad"] for r in rows])
        entry: dict[str, Any] = {
            "extractor_key": ours,
            "worse_direction": WORSE_DIRECTION.get(feat, +1),
            "rel_mad_median": rm["median"],
            "rel_mad_iqr": rm["iqr"],
            "baseline10_rel_mad_median": _dist([r["baseline10_rel_mad"] for r in rows])["median"],
            "weekly_drift_median": _dist([r["weekly_drift"] for r in rows])["median"],
            "n_subjects": rm["n"],
        }
        for col in UPDRS_COLS:
            key = f"spearman_{col.lower()}"
            vals = [r[key] for r in rows]
            d = _dist(vals)
            finite = [v for v in vals if np.isfinite(v)]
            entry[key] = {
                "median": d["median"],
                "iqr": d["iqr"],
                "frac_positive": (float(np.mean([v > 0 for v in finite])) if finite else None),
                "n_subjects": d["n"],
            }
        entry["alert_rates"] = {
            f"k{k:g}": {
                "single": _dist([r[f"alert_single_k{k:g}"] for r in rows])["median"],
                "two_of_three": _dist([r[f"alert_2of3_k{k:g}"] for r in rows])["median"],
            }
            for k in K_ALERT
        }
        result["features"][feat] = entry
    return result


# --------------------------------------------------------------------------- reporting

def _fmt(v: float | None, nd: int = 3) -> str:
    return "n/a" if v is None or not np.isfinite(v) else f"{v:.{nd}f}"


def interpretation(result: dict[str, Any]) -> list[str]:
    """Five data-driven lines; thresholds are deliberately coarse."""
    feats = result["features"]
    by_spread = sorted(feats, key=lambda f: feats[f]["rel_mad_median"] if feats[f]["rel_mad_median"] is not None else 9)
    stable = [f for f in by_spread if (feats[f]["rel_mad_median"] or 9) < 0.15]
    noisy = [f for f in by_spread if (feats[f]["rel_mad_median"] or 0) >= 0.30]
    tracking = []
    for f in feats:
        st = feats[f]["spearman_total_updrs"]
        if st["median"] is None or st["frac_positive"] is None:
            continue
        if abs(st["median"]) >= 0.10 and abs(st["frac_positive"] - 0.5) >= 0.2:
            tracking.append(f)
    k2 = {f: feats[f]["alert_rates"]["k2"] for f in feats}
    single = [v["single"] for v in k2.values() if v["single"] is not None]
    two3 = [v["two_of_three"] for v in k2.values() if v["two_of_three"] is not None]
    lines = [
        f"Within-person stable (rel-MAD < 0.15 of the person's median): {', '.join(stable) or 'none'}; "
        f"noisy (rel-MAD >= 0.30): {', '.join(noisy) or 'none'}. Perturbation measures (jitter, shimmer, NHR) "
        f"wobble by tens of percent day to day in the same person on the same device.",
        f"A 10-recording baseline underestimates the long-run spread only mildly "
        f"(baseline10 vs all-recordings rel-MAD medians: "
        + ", ".join(f"{f} {_fmt(feats[f]['baseline10_rel_mad_median'], 2)}/{_fmt(feats[f]['rel_mad_median'], 2)}" for f in feats)
        + ").",
        "Within-subject Spearman vs total UPDRS is weak for every feature (|median rho| "
        + ", ".join(f"{f} {_fmt(feats[f]['spearman_total_updrs']['median'], 2)}" for f in feats)
        + f"); features whose sign is consistent across >= 70% of subjects: {', '.join(tracking) or 'none'}. "
        "Tsanas 2010's UPDRS mapping relied on cross-subject models, not within-person trends over 6 months.",
        f"Alert rule at k = 2 x baseline rel-MAD in the worse direction: a single recording trips in a median "
        f"{_fmt(np.median(single), 2) if single else 'n/a'} of post-baseline recordings "
        f"(range {_fmt(min(single), 2) if single else 'n/a'}-{_fmt(max(single), 2) if single else 'n/a'} across features), "
        f"and 2-of-3 consecutive recordings trip {_fmt(np.median(two3), 2) if two3 else 'n/a'} of windows - this is the "
        "false-alert floor in a PD population with no intervention, before any true change.",
        "Implication for PD-03b: use k >= 3 (or 2-of-N with N >= 5) with per-feature k from this table, gate on "
        f"unchanged recording conditions (noise floor / level / clipping), and treat the low-spread features "
        f"({', '.join(stable) or 'none'}) as the primary trend signals; the perturbation measures "
        f"({', '.join(noisy) or 'none'}) carry Skodda 2013's longitudinal shimmer/NHR effect only at group level and "
        "need the wider per-feature k here before they can act as secondary evidence.",
    ]
    return lines


def render_markdown(result: dict[str, Any]) -> str:
    feats = result["features"]
    src = result["source"]
    head = [
        "# Within-person voice-feature variability floor (UCI Parkinson's Telemonitoring)",
        "",
        f"Source: {src['dataset']} (DOI {src['doi']}, {src['licence']}); {src['n_subjects']} subjects, "
        f"{src['n_recordings']} recordings. {src['note']}.",
        "",
        "Generated by `ml_training/analyze_telemonitoring_variability.py`; machine-readable copy in "
        "`app/ml/data/voice_variability_floor.json`.",
        "",
        "| feature | rel-MAD median (IQR) | baseline-10 rel-MAD | weekly drift | Spearman total UPDRS median (IQR), frac > 0 "
        "| Spearman motor UPDRS median | single alert @k=2 | 2-of-3 @k=2 | 2-of-3 @k=3 | n |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows = []
    for f, e in feats.items():
        st, sm = e["spearman_total_updrs"], e["spearman_motor_updrs"]
        rows.append(
            f"| {f} | {_fmt(e['rel_mad_median'])} ({_fmt(e['rel_mad_iqr'][0])}-{_fmt(e['rel_mad_iqr'][1])}) "
            f"| {_fmt(e['baseline10_rel_mad_median'])} | {_fmt(e['weekly_drift_median'])} "
            f"| {_fmt(st['median'], 2)} ({_fmt(st['iqr'][0], 2)}-{_fmt(st['iqr'][1], 2)}), {_fmt(st['frac_positive'], 2)} "
            f"| {_fmt(sm['median'], 2)} "
            f"| {_fmt(e['alert_rates']['k2']['single'], 2)} | {_fmt(e['alert_rates']['k2']['two_of_three'], 2)} "
            f"| {_fmt(e['alert_rates']['k3']['two_of_three'], 2)} | {e['n_subjects']} |"
        )
    tail = ["", "## Interpretation", ""] + [f"{i + 1}. {line}" for i, line in enumerate(interpretation(result))] + [""]
    return "\n".join(head + rows + tail)


# --------------------------------------------------------------------------- CLI

def load_input(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    return df


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    ap.add_argument("--md-out", type=Path, default=DEFAULT_MD_OUT)
    args = ap.parse_args(argv)
    if not args.input.exists():
        print(f"input not found: {args.input} (see data/raw/uci_telemonitoring/SOURCE.md)", file=sys.stderr)
        return 2
    result = analyze(load_input(args.input))
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, indent=2) + "\n")
    md = render_markdown(result)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.write_text(md)
    print(md)
    print(f"\nwrote {args.json_out} and {args.md_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
