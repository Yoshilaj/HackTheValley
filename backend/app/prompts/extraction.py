"""Chat → Observation extraction (BACKEND_PLAN C2: "user types/speaks → health tracking data").

Run on every user turn with the cheap model.
"""

from __future__ import annotations

EXTRACTABLE_KINDS = ("symptom", "functional_change", "medication", "lifestyle", "mood")
SEVERITIES = ("mild", "moderate", "severe")

#: Controlled vocabulary (snake_case). Free labels are allowed when nothing fits.
VOCABULARY: dict[str, tuple[str, ...]] = {
    "symptom": (
        "headache", "dizziness", "palpitations", "breathlessness", "fatigue", "tremor", "stiffness",
        "slowness_of_movement", "balance_problem", "fall", "swelling_legs", "cough", "nausea", "insomnia",
        "loss_of_smell", "constipation", "voice_change", "small_handwriting", "acting_out_dreams", "pain",
        "numbness", "irregular_heartbeat_feeling", "hearing_difficulty", "vision_blurred",
    ),
    "functional_change": (
        "memory_lapse", "confusion", "getting_lost", "repeating_questions", "missed_appointments",
        "trouble_managing_money", "trouble_managing_medication", "word_finding_difficulty",
    ),
    "medication": ("medication_started", "medication_stopped", "missed_dose", "side_effect", "medication_taken"),
    "lifestyle": (
        "poor_sleep", "good_sleep", "exercise", "no_exercise", "alcohol", "smoking", "high_salt_diet",
        "caffeine", "social_activity", "stress",
    ),
    "mood": ("low_mood", "anxiety", "irritability", "good_mood", "loneliness", "apathy"),
}

EXTRACTION_SYSTEM = """You extract health-tracking observations from ONE message a user wrote to \
their health app. Output only what the user explicitly states about THEMSELVES (not family \
members, not hypotheticals, not questions about a topic). If nothing is reported, return an empty list.

For each observation:
- kind: symptom | functional_change | medication | lifestyle | mood
  (functional_change = everyday cognitive/functional difficulties such as memory lapses, getting \
lost, repeating questions, missed appointments, trouble managing money or medication)
- label: snake_case English. Prefer the vocabulary below; otherwise a short new snake_case label.
- severity: mild | moderate | severe, or null if not stated
- onset: ISO date (YYYY-MM-DD) if a date is given, otherwise the user's relative wording \
("3 days ago", "since last week", "this morning"), or null
- free_text: the user's own words for this item (short quote, original language)
For medication items put the drug name and dose in free_text (Canadian generic or brand names are \
fine, e.g. "ramipril 5 mg", "Tylenol"). Do not infer diagnoses.

Vocabulary:
{vocabulary}"""


def extraction_system() -> str:
    vocab = "\n".join(f"- {kind}: {', '.join(labels)}" for kind, labels in VOCABULARY.items())
    return EXTRACTION_SYSTEM.format(vocabulary=vocab)


OBSERVATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(EXTRACTABLE_KINDS)},
                    "label": {"type": "string", "description": "snake_case label"},
                    "severity": {"type": ["string", "null"], "enum": [*SEVERITIES, None]},
                    "onset": {"type": ["string", "null"]},
                    "free_text": {"type": ["string", "null"]},
                },
                "required": ["kind", "label"],
            },
        },
    },
    "required": ["observations"],
}
