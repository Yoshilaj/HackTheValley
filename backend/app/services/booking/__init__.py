"""Booking agent v2 (docs/specs/booking-v2.md).

    discovery.py   Places (New) Text Search / Geocoding near the patient, seed fallback, ranking
    slots.py       FreeBusy ∩ clinic opening hours (clinic timezone), 3 spread proposals
    sandbox.py     where outbound actions really go (sandbox by default)
    channels/      web → email → phone behind one contract (base.py); detect.py classifies websites
    orchestrator.py  planning, approval gate, execution, outcomes, confirm/cancel
    inbound.py     Resend inbound webhook (Svix) + reply classification
    followups.py   daily follow-up job logic

`app.services.booking_agent` re-exports the public API for existing imports.
"""
