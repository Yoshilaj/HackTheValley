# Phase A detailed spec — Assessment tasks, calculators, rules

Companion to `docs/BACKEND_PLAN.md` Phase A. Decisions (2026-10-17): own story-recall prototype following the Novoic Storyteller / ASRT protocol; Suita score as the long-term cardio calculator (superseded 2026-10-17 for Canada: Framingham Risk Score / CCS, §3a); one trained model (UCI sustained-vowel PD classifier, experimental); ADReSS and mPower are access-gated extras.

---

## 1. Story Recall Task (cognitive) — adapted from Storyteller / ASRT

> Wording rule: say **"adapted from the published protocol"**. We reproduce the task structure and instruction; our stories, TTS, ASR, embedding model and scoring are our own, so the published materials, administration and scoring are **not** reproduced.

Sources: Skirrow et al., *Alzheimer's & Dementia* 2024 (PMID 39234647, "Storyteller in ADNI4"); Skirrow et al., *JMIR Aging* 2022 e37090 (ASRT validation, AMYPRED).

### 1.1 Protocol we adapt (ADNI4 battery structure)
| Step | What the published protocol did | Our implementation |
|---|---|---|
| Story material | 36 prerecorded stories; short form ≈119 words, long ≈224 words; read at ≈140 wpm; themes balanced for linguistic/discourse metrics; each story used once per participant after day 1 | Author ≥6 short-form stories per language (JA, EN), 110–125 words, 7–9 sentences, concrete everyday narrative, 4–6 named entities, a 3–4 step event sequence. TTS at ≈140 wpm (fixed voice per language). Story bank versioned (`protocol_version`). A story is never reused for the same user. |
| Battery | Immediate recall of story 1 → immediate recall of story 2 → animal category fluency distractor → delayed recall of story 1 | Identical. Distractor = 60 s "name as many animals as you can". Delay ≈ 3–5 min (the papers do not report the exact delay; we log it). |
| Instruction | "retell these in as much detail as they can remember" | Same wording, in JA and EN. |
| Delivery | Audio playback; participant records retelling and stops recording manually; unsupervised, browser or smartphone | Browser: play story audio (no text shown), then record; user presses stop. Max 3 min per retelling. |
| Transcription | Google Speech-to-Text; WER ≈0.10–0.11 | Whisper (large-v3 via API) with language forced to the user's language; store transcript + ASR confidence; WER not evaluated for JA — stated limitation. |
| Primary score | **G-match**: weighted sum of cosine similarity between pretrained multilingual language-model embeddings of the original story and the transcribed retelling; 0–100; averaged across the three recalls | `semantic_recall` = **experimental semantic-recall score**, not G-match: cosine similarity of multilingual sentence embeddings (`voyage-3` or `multilingual-e5`) between story text and transcript, scaled 0–100, unweighted mean over the 3 recalls. Novoic's weights and model are unpublished, so equivalence is not claimed. Per-recall scores stored (immediate 1, immediate 2, delayed). |
| Secondary features (Table 1 of the paper) | Lexical: word count, idea density, pronoun/noun ratio, noun rate, unique-word ratio. Temporal: speech duration, speech rate, pauses >200 ms count, total pause time. Spectral: F0 mean/SD, HNR, MFCC 1–13 mean/SD, jitter, shimmer | Compute the lexical and temporal set (JA tokenisation via `fugashi`/MeCab; EN via spaCy); spectral set via `parselmouth`/`librosa`, stored but flagged low-reliability on uncontrolled microphones (the paper's own limitation). |
| Exclusions | No audible speech; transcription or feature-extraction failure (4% excluded) | Same → `invalid_measurement` with re-record prompt. Add: speech duration <5 s, clipping >1%, SNR <10 dB. |

### 1.2 Published performance (attributed background only — never presented as our app's performance, never used as referral thresholds)
- G-match AUC: 0.84 (AMYPRED, CV, n=101), 0.73 (ADNI4 in-clinic, n=57), 0.75 (ADNI4 remote, n=426). Lexical 0.79/0.79/0.71; temporal 0.70/0.68/0.69; spectral 0.61/0.63/0.56.
- At 80% sensitivity: G-match threshold 59.3 (in-clinic) / 60.1 (remote), specificity 0.68 / 0.62, NPV 0.91 / 0.95.
- Parallel-form reliability (triplets): rho 0.83 immediate, 0.85 delayed. Validity: r 0.65–0.66 with PACC5, 0.54–0.56 with Logical Memory.
- Aβ+ MCI vs CU (AMYPRED): AUC 0.83 in-clinic, 0.92 remote.
- **Limitations we inherit and state**: English only; remote labels self-reported; practice effects shift thresholds; mic variability limits spectral features; our embedding model and stories differ from Novoic's, so their thresholds do not transfer.

### 1.3 What we output and claim
- Per session: `semantic_recall_mean`, per-recall scores, lexical/temporal features, `recording_quality`, `language`, `protocol_version`, `story_ids`.
- Personal baseline: median and MAD of `semantic_recall_mean` over the first 5 valid sessions (≥3 days apart).
- Rules:
  - `COG-01` baseline incomplete (<5 valid sessions) → `insufficient_data`.
  - `COG-02` last 3 of 5 sessions each ≥1.5 MAD below personal baseline → `watch` ("performance on your story task has been lower than your usual range").
  - `COG-03` user- or family-reported functional change (missed appointments, getting lost, repeating questions, trouble managing money/medication) in `Observation`s within 30 days → `followup_recommended` regardless of task score.
  - `COG-04` single session far below (>3 MAD) with good recording quality → re-test prompt, not a flag.
- Claim: "a story-recall task adapted from a published remote screening protocol, scored with an experimental semantic-recall score tracked against your own baseline. Not a diagnosis; no disease thresholds are used."

### 1.4 3-word delayed recall (Linus-informed, optional in daily check-in)
Present 3 unrelated words (TTS), ask again after the check-in questions (~2 min). Score 0–3 plus response latency. Stored as `AssessmentTask(word_recall)`; feeds `COG-02`-style baseline only, no standalone rule in v1.

---

## 2. Cardio module 2 — Home BP protocol (Hypertension Canada 2020 primary; JSH 2019 secondary)

> Canada (2026-10-17, verified in Hypertension Canada's 2020–22 Highlights guide, `data/raw/research/hypertension_canada_2020_highlights.pdf`; full guideline Rabi et al., *Can J Cardiol* 2020;36:596-624): home BP series = duplicate readings morning and evening for 7 days, discard day 1, average the rest; **HBPM series mean ≥135/85 = hypertension** (diagnostic algorithm); office mean ≥180/110 = hypertension at once. Our 135/85 thresholds are unchanged. The 145/90 single-window cut-off (BP-02), morning hypertension (BP-03) and the morning–evening difference (BP-04) come from JSH 2019 and stay as secondary sources. Rule sources: `src_hypertension_canada_2020`, `src_jsh2019`.

> JSH 2025 (高血圧管理・治療ガイドライン2025) has been published. Before the BP rules are enabled, check JSH 2025 for changes to the home classification, thresholds and targets and record the differences here.
>
> JSH 2025 check (2026-10-17, secondary sources, primary text not yet read): **treatment targets** changed to a single target regardless of age — office <130/80, home <125/75 (JSH 2019 had <140/90 office / <135/85 home for ≥75). The term 厳格降圧 was dropped; targets are individualised for adverse events. Home measurement method unchanged: morning within 1 h of waking, before breakfast and before medication; evening just before bed; mean of 2 readings. Whether the **135/85 home diagnostic threshold** changed could not be confirmed from secondary sources — verify in the JSH 2025 text before enabling BP-01/02. Keep three things separate: **diagnostic thresholds** (BP-01/02), **treatment targets** (report context only, not rules), **emergency criteria** (BP-05).

Source: JSH 2019 guideline (Umemura et al., *Hypertens Res* 2019;42:1235). Verified from the text: upper-arm cuff; **two readings per occasion, use the mean** (three allowed; ≥4 discouraged); **record every reading**; evaluate on the **mean of morning and evening values over 7 days (minimum 5 days)**; **home BP ≥135/85 defines hypertension** and **home-based diagnosis takes priority over office BP**; morning hypertension = morning home mean ≥135/85; **morning minus bedtime ≥15 mmHg systolic is an independent risk factor**.

Home classification (JSH 2019 Table 2-5 — **UNVERIFIED, table disabled in code until checked against JSH 2019/2025**; rules BP-01/02 use only the verified 135/85 and 145/90 cutoffs):
| Category | Systolic | | Diastolic |
|---|---|---|---|
| Normal | <115 | and | <75 |
| High-normal | 115–124 | and | <75 |
| Elevated | 125–134 | and/or | 75–84 |
| Grade I | 135–144 | and/or | 85–89 |
| Grade II | 145–159 | and/or | 90–99 |
| Grade III | ≥160 | and/or | ≥100 |

Measurement conditions to show the user (JSH Table 2-3, standard wording): morning within 1 h of waking, after urination, before breakfast and before medication, seated, 1–2 min rest; evening before going to bed, seated, 1–2 min rest.

Data model: `BpReading(session: morning|evening, reading_no 1..3, systolic, diastolic, pulse, irregular_pulse_flag, measured_at, valid: bool, invalid_reason)`. Occasion value = mean of readings; day value = mean of morning and evening occasions; 7-day value = mean over the last 7 calendar days with ≥5 days present.

Rules:
- `BP-00` <5 days with at least one valid occasion in the last 7 → `insufficient_data` (check-in asks for readings).
- `BP-01` 7-day mean in Grade I (≥135 or ≥85, below Grade II) → `watch`.
- `BP-02` 7-day mean ≥145 or ≥90 (Grade II+), **or** Grade I sustained over two consecutive 7-day windows → `followup_recommended`.
- `BP-03` morning 7-day mean ≥135/85 while evening mean <135/85 (morning hypertension) → `watch`, noted in report.
- `BP-04` morning − evening systolic ≥15 mmHg on 7-day means → `watch`, noted in report.
- `BP-05` any occasion ≥180/110 with symptoms (headache, chest pain, dyspnoea, neuro signs) → `emergency`; without symptoms → re-measure after 5 min, if persistent → `followup_recommended` (urgent wording).
- Validity: discard a reading if systolic ≤ diastolic, systolic <70 or >260, pulse <30 or >200, or user marks "error".

---

## 3a. Cardio module 3 (Canada) — Framingham Risk Score (10-year general CVD risk; context only)

Source: D'Agostino RB Sr et al., *Circulation* 2008;117:743-753 (`src_framingham2008`); Canadian use: Pearson GJ et al., 2021 CCS dyslipidemia guideline, *Can J Cardiol* 2021;37:1129-1150 (`src_ccs_lipids_2021`). Implemented in `app/ml/framingham.py`.

- Equation: risk = 1 − S0(10)^exp(Σβx − mean). Women: ln age 2.32888, ln TC 1.20904, ln HDL −0.70833, ln SBP untreated 2.76157 / treated 2.82263, smoker 0.52873, diabetes 0.69154, S0 0.95012, mean 26.1931. Men: 3.06117, 1.12370, −0.93263, 1.93303 / 1.99881, 0.65451, 0.57367, S0 0.88936, mean 23.9802 (checked against the Framingham Heart Study risk-function page; tests reproduce the FHS calculator spreadsheet to 1e-9 and the 10.48% / 15.62% worked examples).
- Cholesterol enters in mg/dL (as in the paper); Profile stores mg/dL, the API shows mmol/L (÷ 38.67). Lab uploads in mmol/L are converted.
- CCS 2021: the FRS percent is doubled for a first-degree relative with premature CVD (men ≤55, women ≤65) at age 30–59 without diabetes; categories low <10%, intermediate 10–19.9%, high ≥20%; reassess every 5 years at 40–75.
- Inputs: age (30–74), sex, smoker, diabetes, 7-day home SBP mean (used as-is in place of office SBP), BP treated (from the medication list), TC, HDL. Any missing → `insufficient_data` with `missing_inputs`; never imputed. Known CHD → `not_applicable`.
- `FRS-01` (informational): ≥10% (CCS intermediate/high) → "discuss at the visit"; never changes the level. What-if: stop smoking; SBP 120 mmHg; TC 5.2 mmol/L; HDL 1.3 mmol/L.
- Limitation: US Framingham cohort; home rather than office SBP.

## 3. Cardio module 3 (Japan only, kept in code) — Suita score (10-year **coronary heart disease** risk calculator; not stroke, not general heart disease, not an early-warning signal)

Source: Nishimura et al., *J Atheroscler Thromb* 2014;21:784-798. Cohort: 5,521 Japanese adults without CHD, Suita city, mean follow-up 11.8 y, 213 CHD events. CHD = definite/probable acute MI (MONICA criteria), sudden cardiac death within 24 h, or CHD followed by CABG/PCI. C-statistic 0.831 (LDL model with CKD). Framingham overestimated risk in this cohort.

Implement the **LDL Suita Score with CKD** score sheet (Table 5b) exactly:

| Factor | Category | Points |
|---|---|---|
| Age | 35–44 | 30 |
| | 45–54 | 38 |
| | 55–64 | 45 |
| | 65–69 | 51 |
| | ≥70 | 53 |
| Sex | Female | −7 |
| Smoking | Current smoker | 5 |
| Diabetes | Yes | 6 |
| Blood pressure | Optimal (<120/80) | −7 |
| | Normal and high-normal (120–139 / 80–89) | 0 |
| | Stage 1 hypertension (140–159 / 90–99) | 4 |
| | Stage 2 hypertension (≥160 / ≥100) | 6 |
| LDL-C (mg/dL) | <100 | 0 |
| | 100–139 | 5 |
| | 140–159 | 7 |
| | 160–179 | 10 |
| | ≥180 | 11 |
| HDL-C (mg/dL) | <40 | 0 |
| | 40–59 | −5 |
| | ≥60 | −6 |
| CKD | eGFR >60 | 0 |
| | Stage 3 (eGFR 30–60) | 3 |
| | Stage 4–5 (eGFR <30) | 14 |

Total → 10-year CHD probability: ≤35: <1% · 36–40: 1% · 41–45: 2% · 46–50: 3% · 51–55: 5% · 56–60: 9% · 61–65: 14% · 66–70: 22% · ≥71: >28%.

The TC variant (Table 5a: age 36–45=30, 46–55=39, 56–65=46, >65=58; female −6; smoker 5; DM 5; BP −7/0/3/6; TC <160=−11, 160–239=0, 240–279=5, ≥280=9; HDL <35=6, 36–49=0, 50–59=−5, ≥60=−5; CKD 0/3/15; total ≤30 <1%, 31–35 1%, 36–40 2%, 41–45 4%, 46–50 6%, 51–55 10%, 56–60 15%, ≥61 24%) is used when only total cholesterol is known.

Implementation notes:
- The Suita BP categories are office-style. We feed the 7-day home mean as-is and state this; adding 5 mmHg to approximate office BP is not justified by the paper.
- Eligibility: age 35–79, no known CHD. Outside → `not_applicable`. Missing LDL/HDL/eGFR → `insufficient_data` for this module; **never impute normal values** (report says "lipids/kidney values needed; ask your doctor").
- Output is **context only** (shown in the doctor report with the citation and the cohort description). It never fires a follow-up rule by itself. `SUITA-01`: ≥9% → noted as "elevated long-term risk, discuss at the visit". Registered only for region `JP` (`Rule.regions`); with `DEFAULT_COUNTRY=CA` the engine uses §3a.
- Limitation stated: Japanese urban cohort, baseline 1989–1994 era, categories not individualised, no wearable inputs.

---

## 4. Cardio module 1 — Device rhythm results (Apple / OMRON)

- `DeviceRhythmResult.label` enum: `sinus_rhythm | possible_af | afib | high_hr | low_hr | inconclusive | poor_recording | irregular_rhythm_notification | other`. Source enum: `apple_irn | apple_ecg | omron_connect | manual_irregular_pulse`.
- Apple ECG labels map 1:1 (Sinus rhythm, AFib, High HR, Low HR, Inconclusive, Poor recording). Apple IRN → `irregular_rhythm_notification`. OMRON connect 「心房細動の可能性」 → `possible_af`; the other five OMRON patterns are mapped when we see the PDF (store raw label string always).
- Rules:
  - `RHY-01` any `possible_af | afib | irregular_rhythm_notification` and no known AF diagnosis in profile → `followup_recommended` (mirrors Apple's and OMRON's instruction "talk to your doctor"). No symptom requirement.
  - `RHY-02` ≥2 `inconclusive | poor_recording` in 7 days and no classified result → `invalid_measurement` with recording tips.
  - `RHY-03` `high_hr` (>120 at rest) or `low_hr` (<50) on ≥2 occasions → `watch`.
  - Known AF diagnosis → results are logged for the report; `RHY-01` does not fire.

---

## 5. Parkinson's pathway

### 5.1 Screener (StrivePD-style + prodromal items)
Items (yes/no, last 3 months): new shaking of a hand at rest; stiffness or slowness; clumsiness / smaller handwriting; softer voice; reduced sense of smell; acting out dreams / shouting in sleep; constipation; a fall or near-fall. Motor items = first four. `PD-01`: ≥2 motor items, or 1 motor + ≥1 non-motor → `watch` and schedule vowel + tapping tasks. This item list is a custom, unvalidated questionnaire (not Tanner 1990, Goetz/MDS-UPDRS or PD-NMS), the PD-01 cut-offs are unvalidated conventions, and "a fall or near-fall" is a late PD sign rather than an MDS prodromal marker; `app/ml/screener.py::SCREENER_NOTE` carries this caveat for the API and reports.

### 5.2 Passive mobility (Apple Health)
Metrics: walking speed, step length, double-support %, walking asymmetry, walking steadiness. Baseline = 30-day median/MAD, requires ≥14 days. `PD-02`: walking speed or step length ≥1.5 MAD below baseline on ≥5 of last 7 days, or double-support/asymmetry ≥1.5 MAD above → `watch`.

### 5.3 Finger tapping — keyboard BRAIN tap test (decided 2026-10-17: keyboard only)
**Protocol** (BRAIN = Bradykinesia Akinesia Incoordination test; Giovannoni et al. 1999, Noyce et al. 2014). Seated at a PC keyboard, the user alternately presses the **`s`** and **`;`** keys with the **index finger of one hand** as fast and as accurately as possible for **30 s**, then repeats with the other hand (either hand first). The client records every keydown / keyup of every key (`performance.now()`-relative ms; `up_ms` null for a key still held at the end) and submits both hands in one `POST /api/tasks/tapping` (`input_method: "keyboard"`, `duration_s` 5–60, default 30, ≤4000 events per hand). The paper does not prescribe the finger; "index finger" is our instruction so repeat sessions are comparable.

**Keyboard only (user decision).** Mouse / trackpad targets involve wrist and arm movement and vary with pointer hardware, acceleration and DPI, so they do not measure distal bradykinesia and would not be comparable across sessions; touch was dropped because the web app runs on a PC. Any other `input_method` is refused (422 "keyboard tapping only") and never stored, so the PD-04 baseline is a single-input-method series. `device = "keyboard"` is stored with the task (`recording_quality.device`, as the voice tasks store their device for PD-03B) and `context_builder.tapping_series` keeps only sessions on the device of the latest valid session — a change of input method restarts the baseline, as in §5.5.

**Metrics** (per hand, `app/ml/features/tapping.py`; paper names in brackets):
| Feature | Definition | PD direction |
|---|---|---|
| `tap_count` (**KS30**, Kinesia Score) | number of presses of the two target keys in the trial; `kinesia_score` = `tap_count × 30 / duration_s` (equal to the count at 30 s) | ↓ |
| `mean_iti_ms`, `iti_cv`, `decrement_slope_ms_per_tap` | inter-tap (keydown→keydown) interval mean, CV and linear slope, after the median/MAD (3.5 × scaled MAD) outlier rule; `n_outliers` reported | ↑ ↑ ↑ |
| `n_freezes` | intervals > 3 × the median interval (pauses / freezes); reported, excluded from the interval statistics, **kept in `tap_count`** | ↑ |
| `dwell_mean_ms`, `dwell_cv` (**AT30**, Akinesia Time) | keydown→keyup hold time per press, mean (ms) and CV; unreleased presses (`n_unreleased`) carry no dwell | ↑ |
| `travel_time_var_ms2` (**IS30**, Incoordination Score) | variance (ms²) of the travelling time between alternate presses = keyup of one key → keydown of the other key, raw (pauses included, as in the paper; negative when the next key goes down before the previous one is released) | ↑ |
| `travel_time_cv` | CV of the same travelling times after the MAD outlier rule (stable enough for a daily baseline — the raw IS30 has a high test-retest CV because pauses dominate the variance) | ↑ |
| `alternation_error_rate` | (consecutive same-key presses `n_same_key_repeats` + presses of other keys `error_presses`) / all presses; the paper's dysmetria score (DS30) did not separate PD from controls and is not scored | ↑ |
| `asymmetry`, `iti_asymmetry`, `dwell_asymmetry` | \|L − R\| / max(L, R) of tap count / mean interval / mean dwell across hands | — |

**Validity** per hand (both hands must pass): ≥ 10 target-key presses (`fewer_than_10_taps`), `duration_s` ≥ 20 (`duration_below_20_s`), no inter-tap interval < 30 ms (`key_autorepeat_detected` — physically impossible for one finger, so the trial is rejected as held-key OS auto-repeat; `n_autorepeat` reported), and ≥ 9 clean intervals (`too_many_outlier_intervals`). The response is 201 with `valid=false` and `reason="<hand>:<code>"`; features that could be computed are still returned.

**Reference values** (Noyce 2014, both hands averaged, for orientation only — never a threshold): KS30 controls 60.3 vs PD 44.2 taps; AT30 controls median 97 ms vs PD 139 ms; IS30 controls median 6758 vs PD 13813 ms²; test-retest CV in controls KS30 6 %, AT30 7 %, with a learning effect of +1.2 taps per attempt. Session features: flat `tap_count`, `mean_iti_ms`, `iti_cv`, `decrement_slope_ms_per_tap`, `dwell_mean_ms`, `dwell_cv`, `travel_time_var_ms2`, `travel_time_cv`, `kinesia_score`, `alternation_error_rate` = mean of both hands when the session is valid.

**Baseline rule `PD-04`** as in 5.2 on the daily series `tap_count` ↓, `tap_interval_mean` ↑, `tap_interval_cv` ↑, `tap_dwell_mean` ↑ (AT30) and `tap_travel_cv` ↑ (`PD04_METRICS`; the §6 detector is metric-agnostic, so the two keyboard metrics needed no further change). No trained model unless mPower access arrives. Protocol version `tapping-2`; the earlier pointer-timestamp contract (`left_ms` / `right_ms`, 15 s) is removed.

Sources: Giovannoni G, van Schalkwyk J, Fritz V, Lees A. Bradykinesia akinesia inco-ordination test (BRAIN TEST): an objective computerised assessment of upper limb motor function. *J Neurol Neurosurg Psychiatry* 1999;67:624–629 (PMC1736603; keystroke count, cumulative key-depression time, incoordination = variance of the inter-keystroke interval). Noyce AJ, Nagy A, Acharya S, et al. Bradykinesia-Akinesia Incoordination Test: validating an online keyboard test of upper limb function. *PLoS One* 2014;9(4):e96260 (PMC4004565; `s` / `;` keys, 30 s per hand, KS30 / AT30 / IS30 / DS30 definitions, PD vs control values above). Tests: `tests/test_tapping.py`, `tests/test_tasks_tapping.py`, `tests/test_seed_demo.py`.

### 5.4 Sustained-vowel classifier (the trained model, experimental)
- Task: say "ahh" steadily for 5 s at comfortable pitch and loudness, 3 repetitions, phone 10–15 cm from mouth, quiet room. Matches the UCI protocol (sustained phonations).
- Dataset: UCI Parkinson's (Little et al. 2008): 195 recordings, 31 subjects (23 PD), 22 features. **Not combined** with Telemonitoring (42 PD-only; optional separate UPDRS regression).
- Features (serving, `app/ml/features/vowel.py` via `parselmouth`): F0 mean/max/min, jitter (local, abs, RAP, PPQ5, DDP), shimmer (local, dB, APQ3, APQ5, APQ11, DDA), NHR, HNR; RPDE, DFA, PPE, spread1/2, D2 reimplemented from the papers' definitions with a written note of differences from the original extraction.
- Training (`ml_training/train_pd_vowel.py`): `datasets.load_dataset("csv")` → `GroupKFold` by subject (5 folds); within-fold feature selection + `CalibratedClassifierCV`; `xgboost` or `LogisticRegression` (report both); metrics: grouped-CV AUC with bootstrap CI, sensitivity at 80% specificity. `push_to_hub("hackthevalley/pd-vowel-experimental-v1")` with model card: 31 subjects, English speakers, sustained vowel only, no external validation, experimental.
- Serving: median probability over the 3 repetitions; `recording_quality` gate (duration ≥3 s voiced, SNR ≥15 dB, no clipping) else `invalid_measurement`. Serving-feature caveat: the Praat/reimplemented features are unvalidated against the MDVP training features (spread2, D2 and NHR are on a different scale; no real-audio validation exists), and the dataset's 31 people map to 32 name-derived IDs used for grouping, so app probabilities are experimental, display-only values (`CALIBRATION_NOTE`, model-card "Serving caveat").
- Rule `PD-03`: probability ≥ calibrated threshold on ≥2 sessions ≥7 days apart **and** at least one of PD-01/02/04 fired → `watch`. Voice alone never raises above `no_finding` with a note.

### 5.5 Personal voice stability — `PD-03B` (experimental, decided 2026-10-17)
**What it is.** A within-person *change detector* on acoustic measures, not a classifier: it compares each new recording with the user's own baseline recordings and says whether the voice has shifted from its usual range. It is called "voice stability" on purpose: in UCI Parkinson's Telemonitoring (42 people with PD, 6 months at home, one device; `app/ml/data/voice_variability_floor.json`, `ml_training/telemonitoring_variability.md`) **within-person trends in every feature did not track UPDRS** (within-subject Spearman ≈ 0, no feature with a consistent sign in ≥70 % of subjects), so PD-03B cannot claim progression detection, and the user-facing text says so.

**Inputs.** `TaskSessionIn.features` (`AssessmentTask.features`), `device`, `recording_quality` (`app/ml/context.py`; mapped in `context_builder.map_task`). Vowel repetitions on one calendar day are one session (per-key median of the features). Trend features and PD direction:
| Task | Feature (key) | Direction | Floor key | K |
|---|---|---|---|---|
| sustained_vowel | shimmer (`MDVP:Shimmer` = Praat local shimmer; chosen over APQ11 because it needs fewer periods and every floor source carries it) | ↑ | `Shimmer` | 3.0 |
| sustained_vowel | `NHR` | ↑ | `NHR` | 3.0 |
| sustained_vowel | `CPPS` (dB) | ↓ | `CPPS` | 2.5 (placeholder, no telemonitoring data) |
| story_recall | speech rate (median over the three recalls of `temporal[label].speech_rate_wpm`, `speech_rate_cpm` for JA) | ↓ | `speech_rate` | 2.0 (placeholder) |
| story_recall | pause ratio (median of `temporal[label].pause_ratio`) | ↑ | `pause_ratio` | 2.0 (placeholder) |
Display-only, never scored: `MDVP:Jitter(%)`, `HNR`, `MDVP:Fo(Hz)`, and the v1 `pd_vowel` model score (§5.4).

**Baseline (`VOICE_BASELINE_SESSIONS = 10`).** Per task series, the first 10 valid sessions (recording-quality gate passed, ≥1 trend feature present) **on the current device** — the device of the latest session (`task_service.device_label`: explicit client `device` field, else the User-Agent platform family, e.g. `iPhone`, `Android Pixel 7`, `Mac`). Sessions on another device are dropped and the note "Recording device changed; the voice baseline restarts on the current device" is shown; the baseline is simply the first 10 sessions on the current device. Sessions are not required to be days apart. Noise gate: the reference noise floor is the median `recording_quality.noise_floor_dbfs` of the first 10 candidate sessions; a session more than **10 dB** noisier is excluded (noted). Fewer than 10 baseline sessions in both series → `insufficient` with `missing_inputs=["voice_baseline_sessions"]`; this is an optional input, never a gate (§8: Parkinson's is `insufficient_data` only when every PD rule lacks input). A feature is judged only when ≥7 of the baseline sessions carry it.

**Statistics.** Per feature: median and scaled MAD (×1.4826) over the baseline (`app/ml/baseline.robust_center_scale`), with the scale floored at `rel_mad_median × |median|` from `voice_variability_floor.json` (UCI Telemonitoring within-person relative MAD: Shimmer 0.34, NHR 0.57, …; built-in placeholders when the file is absent: Shimmer 0.25, NHR 0.35, CPPS 0.08, speech rate 0.10, pause ratio 0.25) so a tiny baseline MAD cannot trigger. A session is **adverse** when it deviates in the PD direction by ≥ K × scale (K per feature from the table above, `VOICE_K_BY_FEATURE`). A feature is **shifted** when ≥3 of the last 5 valid sessions of its series are adverse; the recent window may overlap the baseline tail while fewer than 15 sessions exist (robust statistics tolerate ≤3 shifted baseline sessions), which is why 12 sessions suffice.

**Fire condition.** ≥2 of the 5 trend features shifted (vowel and story features on their own session series). Alone → `not_fired` with evidence, the i18n message (`app/content/voice_stability.json`, surfaced as `data_quality.informational[].message`) and `display_only=true`. With PD-01 / PD-02 / PD-04 fired in the same evaluation → `fired`, `watch`, summary "Voice measurements have shifted from your usual range alongside another finding — a change worth checking". Never `followup_recommended`.

**Expected false-alert rate.** With no true change, at K=3 a single telemonitoring feature trips ~6–9 % of recordings and a "2-of-3 features" window ~2–3 % per evaluation (`alert_rates` in the JSON: Shimmer 2.4 %, NHR 3.1 %); the 2-of-5-features / 3-of-5-sessions rule here is expected at or below that (~≤3 % per evaluation; the CPPS / speech placeholders are uncalibrated). The rule exposes the per-feature rates in `data.no_change_alert_rates` and the report prints them next to each shifted measure.

**User-facing text** (EN/JA, `voice_stability.json`): "Change in voice measurements — some measurements of your voice have changed compared with your previous recordings. These changes can occur for many reasons (temporary illness, speaking conditions, vocal health). This experimental feature cannot determine whether you have Parkinson's disease. If you have persistent voice changes or other new symptoms, consider discussing them with a healthcare professional." plus the stability-not-progression sentence. **Report**: the PD-03B evidence renders as a table (measure, baseline median / scaled MAD, threshold with K, last 5 values, adverse count, status, no-change alert rate) with citations and a fixed limitation sentence; the v1 score stays in the experimental-model section with its caveat. `data_quality` carries `voice_baseline_sessions`, `voice_valid_sessions`, `voice_baseline_needed`, `voice_device`, `voice_sessions_excluded_noise`.

Sources: Skodda 2013 (PMC3872441, longitudinal voice change in PD — shimmer / NHR direction), Wright & Aharonson 2025 (PMC11939921, smartphone voice monitoring), Bottalico 2018 (PMC6529301, recording conditions), Barsties 2025 (PMID 41037430, CPPS reliability), UCI Telemonitoring (variability floor). Tests: `tests/test_rules_pd.py` (fires / notes / insufficient / device restart / floor / noise exclusion), `test_rules_engine.py`, `test_seed_demo.py` (demo → parkinsons `watch` `[PD-04, PD-03B]`), `test_report.py`.

---

## 6. Personal-baseline change detector (HearO-informed)
`app/ml/baseline.py`: for any numeric series, robust baseline = median/MAD of the trailing 30 days excluding the last 7; flag when ≥5 of the last 7 daily values deviate ≥1.5 × scaled MAD (MAD × 1.4826, a robust σ) in the adverse direction. Requires ≥14 baseline days else `insufficient_data`. Used by COG-02, PD-02, PD-04, and general sleep/fatigue (`GEN-01`: sleep duration or self-rated fatigue). Output text: "change from your usual range worth checking", never a disease name.

---

## 7. Emergency path
`EMG-01` red-flag symptom extracted from chat or daily log: chest pain/pressure, sudden face droop / arm weakness / speech difficulty, fainting, severe sudden headache, suicidal ideation. `EMG-02` = BP-05. Action: immediate emergency guidance in the reply (Canada: call 911; if unsure, the 811 nurse line — Health Link / Health811; for suicidal ideation 9-8-8, call or text), notify emergency contact if consented, create `RiskAssessment(level=emergency)`; **no booking agent**.

---

## 8. Rule engine contract
```
Rule = {id, pathway, version, inputs: [field names], condition(ctx) -> bool,
        level, rationale, sources: [ResearchSource ids], review_status}
evaluate(user) -> for each pathway: {level, fired: [rule ids], insufficient: [inputs], evidence: [...]}
```
Level per pathway = max over fired rules by order `no_finding < watch < followup_recommended < emergency`; `insufficient_data` and `invalid_measurement` are reported alongside, and are the level when nothing fired and a **gate** rule's inputs are missing/invalid (cardio `BP-00`/`RHY-02`, cognitive `COG-01`, general `GEN-01`; Parkinson's only when no screener, mobility, tapping or voice input is usable) — other rules' missing inputs are listed in `data_quality.missing_optional_inputs` and do not change the level.

Tests (pytest): for every rule, three synthetic cases (fires / does not fire / insufficient). Replay harness over the seeded demo user must produce `[BP-02, RHY-01] → followup_recommended`.

## 9. Dataset access to request on day 0 (gated extras)
- DementiaBank / ADReSS (TalkBank membership; academic use; no redistribution) — would enable a picture-description model; requires adding the Cookie Theft task (English).
- mPower (Sage Bionetworks, Synapse data-use agreement) — would enable a tapping classifier.
- UCI Parkinson's: direct download, no approval.

---

## 10. Assessment output contract (store separately, never collapse into one number)
Every assessment run writes, per pathway:
1. **Measured result / device finding** — `AssessmentTask.features` + score, `DeviceRhythmResult.label`, `BpWindow` means, Framingham 10-year CVD % and CCS category (Suita points / CHD % for JP).
2. **Model score + version** where a model exists — `model_scores{name, version, probability, threshold, calibration_note}`.
3. **Data quality and missing inputs** — `recording_quality`, `valid` flags, `missing_inputs[]`, `baseline_n_days`.
4. **Rule that suggested follow-up** — `fired_rules[]` with rule version.
5. **Supporting observations and references** — `evidence[]` (row ids) and `sources[]` (ResearchSource ids).
The user-facing level and the booking trigger come only from (4). Items (1)–(3) are displayed with their own meaning; (5) is used by the report and the RAG explanation. The emergency and insufficient-data paths (sections 7, 8) must be implemented and tested **before** the booking agent is connected to `followup_recommended`.

---

## 11. Risk percentage UI — "Follow-up priority" score (decided 2026-10-17)

The dashboard shows one percentage per pathway. It is a **follow-up priority score (0–100)** derived from the fired rules, labelled on screen as *"Follow-up priority — not the probability of having a disease."* Where a real probability exists (Framingham 10-year CVD risk in Canada; Suita CHD risk for JP) it is shown as a second, separately labelled number.

### 11.1 Computation (`app/ml/priority.py`)
Deterministic, from the rule engine output only.
| Level | Base | Within-band additions (capped at band top) |
|---|---|---|
| `no_finding` | 0 | +5 per rule that fired at `watch` level in the previous 30 days but has since cleared (shows "recently resolved") → max 15 |
| `watch` | 25 | +10 per additional `watch` rule fired; +10 if the same rule fired in ≥2 consecutive weekly windows; max 55 |
| `followup_recommended` | 60 | +10 per additional `followup_recommended` rule; +10 if any `watch` rule also fired; +5 if persistent ≥2 windows; max 85 |
| `emergency` | 100 | — (UI shows emergency guidance, no gauge) |
| `insufficient_data` / `invalid_measurement` | null | UI shows "—" with "N more days of X needed" / "re-record" |

Examples: BP-02 alone → 60. BP-02 + RHY-01 → 70. BP-02 + RHY-01 + BP-04 (watch) → 80. PD-01 alone → 25; PD-01 + PD-02 → 35; PD-01 + PD-02 persistent → 45.

Booking trigger is unchanged: `followup_recommended` level (score ≥60 by construction), after consent check. The score never triggers anything by itself.

### 11.2 Real probabilities shown alongside (separately labelled)
- Cardio card: **"10-year cardiovascular disease risk: X% (CCS low/intermediate/high)"** (Framingham, §3a) in `long_term_risk` and `probabilities[]`, with `inputs` (mmol/L), `inputs_used`, `missing_inputs`, and a **what-if** list (stop smoking; SBP 120; TC 5.2 mmol/L; HDL 1.3 mmol/L). Shown only when eligible and inputs complete; otherwise "needs cholesterol values / BP-treatment status". (JP: Suita CHD risk, what-if stop smoking; BP <120/80; LDL <100; HDL ≥60.)
- Parkinson's card: vowel classifier probability shown only in the detail view as *"experimental voice-model score"* with the model card link; never on the main gauge.

### 11.3 Preventive measures
Per pathway, the card lists 2–4 measures tied to the fired rules' modifiable inputs, each with a RAG citation and the framing "to discuss with your doctor": BP (Hypertension Canada: less salt / DASH-style diet, alcohol <2 drinks/day, 30–60 min moderate activity 4–7 days/week, weight, medication adherence), long-term CVD (smoking, lipids, diabetes control; CCS 2021), sleep/activity (sleep regularity, daytime activity), cognitive and PD (exercise, hearing/vision check, social activity, sleep — framed as general brain-health guidance, not as lowering a disease percentage).

### 11.4 API shape (`GET /api/dashboard` → `pathways[]`, also `GET /api/risk/latest`)
```json
{
  "pathway": "cardio",
  "level": "followup_recommended",
  "priority_score": 70,
  "score_kind": "follow_up_priority",
  "score_label": "Follow-up priority (not a disease probability)",
  "headline": "Two findings suggest a check-up is worth arranging",
  "drivers": [
    {"rule_id": "BP-02", "text": "7-day home blood pressure averaged 146/92", "evidence_ids": ["bpw_…"]},
    {"rule_id": "RHY-01", "text": "OMRON recorded a possible atrial-fibrillation result on Oct 14", "evidence_ids": ["drr_…"]}
  ],
  "missing_inputs": [],
  "data_quality": {"valid_bp_days": 7, "invalid_readings": 1},
  "trend": [{"week": "2026-09-21", "priority_score": 0}, {"week": "2026-09-28", "priority_score": 25}, {"week": "2026-10-05", "priority_score": 60}, {"week": "2026-10-12", "priority_score": 70}],
  "probabilities": [
    {"name": "10-year cardiovascular disease risk (Framingham Risk Score)", "calculator": "framingham_2008",
     "value_pct": 49.8, "value_display": "49.8", "category": "high", "kind": "population_estimate",
     "inputs_used": ["age", "sex", "smoker", "diabetes", "bp", "bp_treated", "total_chol", "hdl"], "missing_inputs": [],
     "what_if": [{"change": "stop smoking", "value_pct": 30.1, "value_display": "30.1", "category": "high"},
                 {"change": "systolic BP 120 mmHg", "value_pct": 37.5, "value_display": "37.5", "category": "high"}],
     "source_id": "src_framingham2008", "guideline_source_id": "src_ccs_lipids_2021"}
  ],
  "long_term_risk": {"calculator": "framingham_2008", "status": "ok", "percent_10y": 49.8, "display": "49.8",
     "category": "high", "inputs": {"age": 68, "sex": "male", "smoker": true, "diabetes": false,
       "systolic_bp_mmhg": 146.4, "bp_treated": false, "total_chol_mmol_l": 5.8, "hdl_mmol_l": 1.15,
       "family_history_premature_cvd": false},
     "inputs_used": ["…"], "missing_inputs": [], "what_if": [{"change": "stop smoking", "percent_10y": 30.1, "display": "30.1", "category": "high"}],
     "source_id": "src_framingham2008", "guideline_source_id": "src_ccs_lipids_2021", "note": "BP input is the 7-day home systolic mean …"},
  "preventive_measures": [
    {"text": "Eat less salt and follow a DASH-style diet (more vegetables, fruit and fibre, less saturated fat) (to discuss with your doctor)", "source_id": "src_hypertension_canada_2020"},
    {"text": "Bring your device's rhythm recordings to the visit (to discuss with your doctor)", "source_id": "src_ccs_af_2020"}
  ],
  "next_action": {"type": "appointment_requested", "appointment_id": "apt_…"},
  "disclaimer": "This is not a diagnosis. The priority score reflects which follow-up rules fired, not the chance that you have a condition."
}
```
UI rules: gauge colour by level (grey / amber / red / emergency banner); the `score_label` text is always rendered under the number; `probabilities[]` render in their own labelled block with the source; `insufficient_data` renders a progress indicator ("3 of 5 BP days recorded") instead of a number.
