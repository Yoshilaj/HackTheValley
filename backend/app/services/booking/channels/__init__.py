"""Booking channels (web → email → phone) behind one contract (see base.py).

    channels = default_channels()            # {"web": WebChannel(), "email": EmailChannel(), "phone": PhoneChannel()}
"""

from __future__ import annotations

from typing import Any

from app.services.booking.channels.base import (  # noqa: F401
    ALLOWED_FIELDS, CHANNEL_ORDER, BookingContext, Channel, ChannelMessage, ChannelResult, PatientInfo,
)


def default_channels() -> dict[str, Any]:
    from app.services.booking.channels.email import EmailChannel
    from app.services.booking.channels.phone import PhoneChannel
    from app.services.booking.channels.web import WebChannel

    return {"web": WebChannel(), "email": EmailChannel(), "phone": PhoneChannel()}
