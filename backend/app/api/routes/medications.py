"""Medications, refill requests and daily dose tick-offs (logic in app/services/medication_service.py).

GET/POST /api/medications, PATCH/DELETE /api/medications/{id}
GET/POST /api/refills, DELETE /api/refills/{medication_id}
GET/PUT  /api/doses/{day}   (day = YYYY-MM-DD)
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.medications import (
    DosesIn, DosesOut, MedicationIn, MedicationOut, MedicationUpdate, RefillCreate, RefillOut,
)
from app.services import medication_service as svc

router = APIRouter(tags=["medications"])


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


@router.get("/medications", response_model=list[MedicationOut])
def list_medications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Your medication list (the same list the risk assessment and chat use)."""
    return svc.list_medications(db, user)


@router.post("/medications", response_model=MedicationOut, status_code=status.HTTP_201_CREATED)
def add_medication(req: MedicationIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.add_medication(db, user, req.model_dump())


@router.patch("/medications/{medication_id}", response_model=MedicationOut)
def update_medication(medication_id: str, req: MedicationUpdate, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    try:
        return svc.update_medication(db, user, medication_id, req.model_dump(exclude_unset=True))
    except svc.MedicationNotFound as exc:
        raise _not_found("Medication") from exc


@router.delete("/medications/{medication_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_medication(medication_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        svc.delete_medication(db, user, medication_id)
    except svc.MedicationNotFound as exc:
        raise _not_found("Medication") from exc


@router.get("/refills", response_model=list[RefillOut])
def list_refills(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Your open refill requests, newest first."""
    return svc.list_refills(db, user)


@router.post("/refills", response_model=list[RefillOut], status_code=status.HTTP_201_CREATED)
def request_refills(req: RefillCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Ask for refills of one or more of your medications (an open request is never duplicated)."""
    try:
        return svc.request_refills(db, user, req.medication_ids, req.pharmacy_id, req.method, req.note)
    except svc.MedicationNotFound as exc:
        raise _not_found("Medication") from exc


@router.delete("/refills/{medication_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_refill(medication_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Withdraw the open refill request for one medication."""
    try:
        svc.cancel_refill(db, user, medication_id)
    except svc.MedicationNotFound as exc:
        raise _not_found("Open refill request") from exc


@router.get("/doses/{day}", response_model=DosesOut)
def get_doses(day: date, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Doses ticked off on ``day``."""
    return DosesOut(day=day, taken=svc.doses_for(db, user, day))


@router.put("/doses/{day}", response_model=DosesOut)
def set_doses(day: date, req: DosesIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Replace the doses ticked off on ``day``."""
    return DosesOut(day=day, taken=svc.set_doses(db, user, day, req.taken))
