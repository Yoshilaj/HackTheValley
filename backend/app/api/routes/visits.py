"""Clinic directory, follow-up recommendations and self-booked visits (logic in app/services/visit_service.py).

GET /api/providers[?lat=&lng=&specialty=]   clinics nearest first, with open times
GET /api/recommendations[?lat=&lng=]        a clinic per pathway with a finding, with the reason
GET/POST /api/bookings, DELETE /api/bookings/{id}
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.visits import BookingCreate, BookingOut, ProviderOut, RecommendationOut
from app.services import visit_service as svc

router = APIRouter(tags=["visits"])


@router.get("/providers", response_model=list[ProviderOut])
def providers(lat: float | None = Query(None, ge=-90, le=90), lng: float | None = Query(None, ge=-180, le=180),
              specialty: str | None = Query(None, max_length=40),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Clinics near you (or your profile address), nearest first."""
    return svc.list_providers(db, user, lat, lng, specialty)


@router.get("/recommendations", response_model=list[RecommendationOut])
def recommendations(lat: float | None = Query(None, ge=-90, le=90), lng: float | None = Query(None, ge=-180, le=180),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The nearest clinic for each of your follow-up findings, with why."""
    return svc.recommendations(db, user, lat, lng)


@router.get("/bookings", response_model=list[BookingOut])
def list_bookings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Visits you booked that are still upcoming (not cancelled)."""
    return svc.list_bookings(db, user)


@router.post("/bookings", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def book(req: BookingCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Book a visit with a provider at a time (or "next available" without one)."""
    try:
        return svc.book(db, user, req.provider_id, req.slot, req.note, req.provider)
    except svc.ProviderNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider not found") from exc


@router.delete("/bookings/{booking_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel(booking_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        svc.cancel_booking(db, user, booking_id)
    except svc.BookingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found") from exc
