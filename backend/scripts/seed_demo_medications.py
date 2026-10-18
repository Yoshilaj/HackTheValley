"""Give the demo user (demo@example.com) a medication list for the web app's Medications page.

    .venv/bin/python -m scripts.seed_demo_medications

Adds Metformin, Vitamin D and Omega-3 only when the demo user has no medications yet. No
blood-pressure drug on purpose: the planted demo (scripts.seed_demo) is UNTREATED high blood
pressure, and Profile.medications feeds the Framingham "BP treated" input. Re-running
scripts.seed_demo refreshes the demo profile and clears this list; run this again afterwards.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.models import User  # noqa: E402
from app.db.session import SessionLocal, init_db  # noqa: E402
from app.services import medication_service  # noqa: E402

DEMO_EMAIL = "demo@example.com"

MEDICATIONS = [
    {"name": "Metformin", "dose": "500 mg", "frequency": "Twice daily with meals", "purpose": "Blood sugar",
     "prescriber": "Dr. Priya Nair", "refills": 1, "times": ["8:00 AM", "6:00 PM"], "days_left": 9, "glyph": "M",
     "take_with": "Breakfast and dinner, to avoid an upset stomach",
     "side_effects": ["Upset stomach or diarrhea, usually easing after a few weeks", "Metallic taste"],
     "interactions": ["Heavy alcohol use", "Contrast dye for scans: tell the imaging team"]},
    {"name": "Vitamin D", "dose": "2000 IU", "frequency": "Once daily", "purpose": "Bone health",
     "prescriber": "Dr. Lucas Chen", "refills": 5, "times": ["8:00 PM"], "days_left": 63, "glyph": "D",
     "take_with": "A meal with some fat, for better absorption",
     "side_effects": ["Rare at this dose"], "interactions": []},
    {"name": "Omega-3", "dose": "1000 mg", "frequency": "Once daily", "purpose": "Heart health",
     "prescriber": "Dr. Amira Patel", "refills": 0, "times": ["8:00 PM"], "days_left": 4, "glyph": "O",
     "take_with": "Food", "side_effects": ["Fishy aftertaste", "Mild indigestion"],
     "interactions": ["Blood thinners such as warfarin: ask your doctor"]},
]


def main() -> None:
    init_db()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            sys.exit(f"{DEMO_EMAIL} not found; run `python -m scripts.seed_demo` first")
        existing = medication_service.list_medications(db, user)
        if existing:
            print(f"{DEMO_EMAIL} already has {len(existing)} medication(s); nothing added")
            return
        for med in MEDICATIONS:
            medication_service.add_medication(db, user, med)
        print(f"added {len(MEDICATIONS)} medications for {DEMO_EMAIL}")


if __name__ == "__main__":
    main()
