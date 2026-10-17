# ml_training

Offline training scripts. Run from `backend/` with the project venv.

## `train_pd_vowel.py` — experimental Parkinson's sustained-vowel classifier

Spec: `docs/specs/phase-a-assessments.md` §5.4, `docs/BACKEND_PLAN.md` A4.

```bash
.venv/bin/python -m ml_training.train_pd_vowel                 # full run (~15 s)
.venv/bin/python -m ml_training.train_pd_vowel --fast --splits 2 --repeats 0   # smoke run
HF_TOKEN=... .venv/bin/python -m ml_training.train_pd_vowel --push-to-hub hackthevalley/pd-vowel-experimental-v1
```

Without `HF_TOKEN`, `--push-to-hub` only prints what it would upload.

Input: `data/raw/uci_parkinsons/parkinsons.data` (loaded with `datasets.load_dataset("csv")`,
pandas fallback). Subject = `name` minus the trailing `_N` (gives 32 IDs; the
dataset documentation says 31 subjects).

Protocol: outer `GroupKFold(5)` by subject; inside each outer training fold a
subject-grouped `GridSearchCV` (LogReg: `SelectKBest` k and C; XGBoost: trees ≤ 300,
depth ≤ 3) followed by sigmoid `CalibratedClassifierCV` on grouped inner splits;
10 shuffled grouped-CV repeats; subject-cluster bootstrap CI; threshold at 80%
specificity from pooled out-of-fold probabilities.

Outputs in `models/pd_vowel_experimental_v1/`:

| file | contents |
|---|---|
| `model.joblib` | calibrated sklearn estimator (input: DataFrame with `feature_order`) |
| `metrics.json` | all CV metrics, `threshold`, `chosen_model`, per-subject summary |
| `feature_order.json` | `features` (UCI column order) and `train_medians` (for imputation) |
| `feature_notes.json` | serving-vs-training feature differences (`app/ml/features/vowel.py::FEATURE_NOTES`) |
| `README.md` | model card, filled from `ml_training/model_card_pd_vowel.md` |

Serving: `app/ml/pd_vowel_model.py::PdVowelModel.load()`.
