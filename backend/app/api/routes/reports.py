"""Clinician report endpoints (thin; logic in app/services/report_service.py).

Authenticated: GET /api/reports, GET /api/reports/{id}, GET /api/reports/{id}/html,
POST /api/reports/{assessment_id}/regenerate.   Public: GET /r/{share_token} (`public_router`).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.security import audit
from app.db.models import Report, RiskAssessment, User
from app.db.session import get_db
from app.providers.llm import get_llm_provider
from app.schemas.reports import ReportOut, ReportSummaryOut
from app.services import llm_audit_hook
from app.services import report_service as svc

router = APIRouter(prefix="/reports", tags=["reports"])
public_router = APIRouter(tags=["reports"])

_HTML_HEADERS = {"X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}


def _summary(db: Session, r: Report) -> dict:
    a = db.get(RiskAssessment, r.assessment_id) if r.assessment_id else None
    return {"id": r.id, "assessment_id": r.assessment_id, "pathway": a.pathway if a else None,
            "level": a.level if a else None, "share_url": svc.share_url(r), "html_url": f"/api/reports/{r.id}/html",
            "pdf_url": r.pdf_url, "created_at": r.created_at}


@router.get("", response_model=list[ReportSummaryOut])
def list_reports(limit: int = Query(50, ge=1, le=200), user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """The current user's reports, newest first."""
    return [_summary(db, r) for r in svc.list_reports(db, user.id, limit)]


def _own(db: Session, user: User, report_id: str) -> Report:
    row = svc.get_report(db, user.id, report_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    audit(db, user.id, "report.read", f"report:{row.id}", commit=True)
    return row


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _own(db, user, report_id)
    return _summary(db, row) | {"markdown": row.markdown or ""}


@router.get("/{report_id}/html", response_class=HTMLResponse)
def get_report_html(report_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Print-ready HTML (use the browser's Print → Save as PDF)."""
    return HTMLResponse(_own(db, user, report_id).html or "", headers=_HTML_HEADERS)


@router.post("/{assessment_id}/regenerate", response_model=ReportOut)
def regenerate(assessment_id: str, language: str | None = Query(None, pattern="^(ja|en)$"),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Re-render the latest report for an assessment (share link kept), or create one."""
    assessment = db.get(RiskAssessment, assessment_id)
    if assessment is None or assessment.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found")
    llm = get_llm_provider(audit_hook=llm_audit_hook(db, user.id, f"risk_assessment:{assessment.id}"))
    row = svc.regenerate_report(db, user, assessment, llm, language)
    return _summary(db, row) | {"markdown": row.markdown or ""}


@public_router.get("/r/{share_token}", response_class=HTMLResponse, include_in_schema=True)
def shared_report(share_token: str, db: Session = Depends(get_db)):
    """Public, unauthenticated report view for the clinic (unguessable token)."""
    row = svc.get_shared_report(db, share_token)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return HTMLResponse(svc.shared_html(row), headers=_HTML_HEADERS)
