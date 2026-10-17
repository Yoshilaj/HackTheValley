# Phase A detailed spec — Assessment tasks, calculators, rules

Companion to `docs/BACKEND_PLAN.md` Phase A. Decisions (2026-10-17): own story-recall prototype following the Novoic Storyteller / ASRT protocol; Suita score as the long-term cardio calculator; one trained model (UCI sustained-vowel PD classifier, experimental); ADReSS and mPower are access-gated extras.

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

## 2. Cardio module 2 — Home BP protocol (JSH 2019, to be reconciled with JSH 2025)

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

## 3. Cardio module 3 — Suita score (10-year **coronary heart disease** risk calculator; not stroke, not general heart disease, not an early-warning signal)

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
- Output is **context only** (shown in the doctor report with the citation and the cohort description). It never fires a follow-up rule by itself. `SUITA-01`: ≥9% → noted as "elevated long-term risk, discuss at the visit".
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
Items (yes/no, last 3 months): new shaking of a hand at rest; stiffness or slowness; clumsiness / smaller handwriting; softer voice; reduced sense of smell; acting out dreams / shouting in sleep; constipation; a fall or near-fall. Motor items = first four. `PD-01`: ≥2 motor items, or 1 motor + ≥1 non-motor → `watch` and schedule vowel + tapping tasks.

### 5.2 Passive mobility (Apple Health)
Metrics: walking speed, step length, double-support %, walking asymmetry, walking steadiness. Baseline = 30-day median/MAD, requires ≥14 days. `PD-02`: walking speed or step length ≥1.5 MAD below baseline on ≥5 of last 7 days, or double-support/asymmetry ≥1.5 MAD above → `watch`.

### 5.3 Finger tapping
15 s per hand, alternate index-finger taps on two on-screen targets; record pointer timestamps. Features: tap count, mean inter-tap interval, interval CV, decrement (linear slope of interval over time), left/right asymmetry of count. Baseline rule `PD-04` as in 5.2. No trained model unless mPower access arrives.

### 5.4 Sustained-vowel classifier (the trained model, experimental)
- Task: say "ahh" steadily for 5 s at comfortable pitch and loudness, 3 repetitions, phone 10–15 cm from mouth, quiet room. Matches the UCI protocol (sustained phonations).
- Dataset: UCI Parkinson's (Little et al. 2008): 195 recordings, 31 subjects (23 PD), 22 features. **Not combined** with Telemonitoring (42 PD-only; optional separate UPDRS regression).
- Features (serving, `app/ml/features/vowel.py` via `parselmouth`): F0 mean/max/min, jitter (local, abs, RAP, PPQ5, DDP), shimmer (local, dB, APQ3, APQ5, APQ11, DDA), NHR, HNR; RPDE, DFA, PPE, spread1/2, D2 reimplemented from the papers' definitions with a written note of differences from the original extraction.
- Training (`ml_training/train_pd_vowel.py`): `datasets.load_dataset("csv")` → `GroupKFold` by subject (5 folds); within-fold feature selection + `CalibratedClassifierCV`; `xgboost` or `LogisticRegression` (report both); metrics: grouped-CV AUC with bootstrap CI, sensitivity at 80% specificity. `push_to_hub("hackthevalley/pd-vowel-experimental-v1")` with model card: 31 subjects, English speakers, sustained vowel only, no external validation, experimental.
- Serving: median probability over the 3 repetitions; `recording_quality` gate (duration ≥3 s voiced, SNR ≥15 dB, no clipping) else `invalid_measurement`.
- Rule `PD-03`: probability ≥ calibrated threshold on ≥2 sessions ≥7 days apart **and** at least one of PD-01/02/04 fired → `watch`. Voice alone never raises above `no_finding` with a note.

---

## 6. Personal-baseline change detector (HearO-informed)
`app/ml/baseline.py`: for any numeric series, robust baseline = median/MAD of the trailing 30 days excluding the last 7; flag when ≥5 of the last 7 daily values deviate ≥1.5 MAD in the adverse direction. Requires ≥14 baseline days else `insufficient_data`. Used by COG-02, PD-02, PD-04, and general sleep/fatigue (`GEN-01`: sleep duration or self-rated fatigue). Output text: "change from your usual range worth checking", never a disease name.

---

## 7. Emergency path
`EMG-01` red-flag symptom extracted from chat or daily log: chest pain/pressure, sudden face droop / arm weakness / speech difficulty, fainting, severe sudden headache, suicidal ideation. `EMG-02` = BP-05. Action: immediate emergency guidance in the reply (call 119 in Japan / local number by profile), notify emergency contact if consented, create `RiskAssessment(level=emergency)`; **no booking agent**.

---

## 8. Rule engine contract
```
Rule = {id, pathway, version, inputs: [field names], condition(ctx) -> bool,
        level, rationale, sources: [ResearchSource ids], review_status}
evaluate(user) -> for each pathway: {level, fired: [rule ids], insufficient: [inputs], evidence: [...]}
```
Level per pathway = max over fired rules by order `no_finding < watch < followup_recommended < emergency`; `insufficient_data` and `invalid_measurement` are reported alongside, and are the level when nothing fired and inputs are missing/invalid.

Tests (pytest): for every rule, three synthetic cases (fires / does not fire / insufficient). Replay harness over the seeded demo user must produce `[BP-02, RHY-01] → followup_recommended`.

## 9. Dataset access to request on day 0 (gated extras)
- DementiaBank / ADReSS (TalkBank membership; academic use; no redistribution) — would enable a picture-description model; requires adding the Cookie Theft task (English).
- mPower (Sage Bionetworks, Synapse data-use agreement) — would enable a tapping classifier.
- UCI Parkinson's: direct download, no approval.

---

## 10. Assessment output contract (store separately, never collapse into one number)
Every assessment run writes, per pathway:
1. **Measured result / device finding** — `AssessmentTask.features` + score, `DeviceRhythmResult.label`, `BpWindow` means, Suita total points and 10-year CHD %.
2. **Model score + version** where a model exists — `model_scores{name, version, probability, threshold, calibration_note}`.
3. **Data quality and missing inputs** — `recording_quality`, `valid` flags, `missing_inputs[]`, `baseline_n_days`.
4. **Rule that suggested follow-up** — `fired_rules[]` with rule version.
5. **Supporting observations and references** — `evidence[]` (row ids) and `sources[]` (ResearchSource ids).
The user-facing level and the booking trigger come only from (4). Items (1)–(3) are displayed with their own meaning; (5) is used by the report and the RAG explanation. The emergency and insufficient-data paths (sections 7, 8) must be implemented and tested **before** the booking agent is connected to `followup_recommended`.

---

## 11. Risk percentage UI — "Follow-up priority" score (decided 2026-10-17)

The dashboard shows one percentage per pathway. It is a **follow-up priority score (0–100)** derived from the fired rules, labelled on screen as *"Follow-up priority — not the probability of having a disease."* Where a real probability exists (Suita 10-year CHD risk) it is shown as a second, separately labelled number.

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
- Cardio card: **"10-year coronary heart disease risk: X%"** (Suita, LDL or TC sheet), with `inputs_used`, `missing_inputs`, and a **what-if** list: recomputed X% for each modifiable change (stop smoking; BP to <120/80; LDL to <100; HDL ≥60). Shown only when eligible and inputs complete; otherwise "needs lipid / kidney values".
- Parkinson's card: vowel classifier probability shown only in the detail view as *"experimental voice-model score"* with the model card link; never on the main gauge.

### 11.3 Preventive measures
Per pathway, the card lists 2–4 measures tied to the fired rules' modifiable inputs, each with a RAG citation and the framing "to discuss with your doctor": BP (salt <6 g/day, alcohol, exercise, weight, medication adherence), CHD (smoking, lipids, diabetes control), sleep/activity (sleep regularity, daytime activity), cognitive and PD (exercise, hearing/vision check, social activity, sleep — framed as general brain-health guidance, not as lowering a disease percentage).

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
    {"name": "10-year coronary heart disease risk (Suita score)", "value_pct": 9, "kind": "population_estimate",
     "inputs_used": ["age", "sex", "bp", "ldl", "hdl", "smoking", "diabetes", "egfr"], "missing_inputs": [],
     "what_if": [{"change": "stop smoking", "value_pct": 5}, {"change": "BP < 120/80", "value_pct": 3}],
     "source_id": "src_suita2014"}
  ],
  "preventive_measures": [
    {"text": "Reduce salt to under 6 g/day", "source_id": "src_jsh2019"},
    {"text": "Keep taking blood-pressure medication at the same time daily", "source_id": "src_jsh2019"}
  ],
  "next_action": {"type": "appointment_requested", "appointment_id": "apt_…"},
  "disclaimer": "This is not a diagnosis. The priority score reflects which follow-up rules fired, not the chance that you have a condition."
}
```
UI rules: gauge colour by level (grey / amber / red / emergency banner); the `score_label` text is always rendered under the number; `probabilities[]` render in their own labelled block with the source; `insufficient_data` renders a progress indicator ("3 of 5 BP days recorded") instead of a number.
