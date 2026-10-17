"""Place ONE sandbox test call through the phone booking channel (ElevenLabs Agents + Twilio).

    cd backend && .venv/bin/python -m scripts.test_phone_call

Thin wrapper kept for compatibility: the same as `python -m scripts.test_booking --channel phone`
(see scripts/test_booking.py for what it does and its safety guards: sandbox only, seed clinics
only, nothing is sent before you type `yes`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.test_booking import main as booking_main  # noqa: E402


def main() -> int:
    return booking_main(["--channel", "phone"])


if __name__ == "__main__":
    sys.exit(main())
