"""Document ingestion (BACKEND_PLAN C1, A5 §6): doctor notes, prescriptions, lab sheets,
BP-monitor photos (OMRON) and ECG PDFs (OMRON connect / Apple) → structured data.

Flow: store the file → get text (pypdf for PDFs) or use vision for images / scanned PDFs →
`llm.extract` / `llm.vision_extract` with a per-kind JSON schema → write results:
- doctor_note | prescription | lab: medications → Observation(kind='medication',
  source='document') + Profile.medications; diagnoses → Profile known_* flags via a small
  matcher (hypertension, AF/心房細動, coronary disease, Parkinson's, dementia, diabetes);
  LDL/HDL/total cholesterol/eGFR lab values → Profile (Framingham inputs; Canadian mmol/L cholesterol
  is converted to the stored mg/dL);
  everything (incl. follow_ups) kept in UploadedDocument.extracted.
- bp_photo: {systolic, diastolic, pulse, irregular_icon, measured_at} → add_bp_readings
  (source 'omron_photo'; irregular icon → manual_irregular_pulse rhythm result).
- ecg_pdf: {device, classification, recorded_at} → add_rhythm_result (omron_connect, or
  apple_ecg when the device is an Apple Watch).
- audio: stored, status 'pending_transcription' (STT owned by app/providers/stt.py).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Observation, Profile, UploadedDocument
from app.providers.llm import VISION_MEDIA_TYPES, LlmProvider
from app.providers.storage import Storage, get_storage
from app.schemas.metrics import BpIn
from app.services import llm_audit_hook
from app.services.ingest_service import add_bp_readings, add_rhythm_result

log = get_logger(__name__)

DOCUMENT_KINDS = ("doctor_note", "prescription", "lab", "bp_photo", "ecg_pdf", "audio")
TEXT_KINDS = ("doctor_note", "prescription", "lab")

MEDICAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "diagnoses": {"type": "array", "items": {"type": "string"},
                      "description": "Diagnoses stated as established (not suspected/ruled out)"},
        "medications": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "dose": {"type": ["string", "null"]},
                           "schedule": {"type": ["string", "null"]}},
            "required": ["name"]}},
        "follow_ups": {"type": "array", "items": {"type": "string"}},
        "lab_values": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "value": {"type": ["number", "null"]},
                           "unit": {"type": ["string", "null"]}, "date": {"type": ["string", "null"]}},
            "required": ["name"]}},
        "summary": {"type": ["string", "null"]},
    },
    "required": ["diagnoses", "medications", "follow_ups", "lab_values", "summary"],
}
BP_PHOTO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "systolic": {"type": ["integer", "null"]},
        "diastolic": {"type": ["integer", "null"]},
        "pulse": {"type": ["integer", "null"]},
        "irregular_icon": {"type": "boolean", "description": "Irregular heartbeat / IHB icon shown"},
        "measured_at": {"type": ["string", "null"], "description": "ISO 8601 if the display shows date/time"},
    },
    "required": ["systolic", "diastolic", "pulse", "irregular_icon"],
}
ECG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device": {"type": ["string", "null"]},
        "classification": {"type": ["string", "null"],
                           "description": "The device's own result label, verbatim (e.g. 心房細動の可能性)"},
        "recorded_at": {"type": ["string", "null"], "description": "ISO 8601"},
    },
    "required": ["device", "classification", "recorded_at"],
}

SYSTEM_MEDICAL = (
    "You extract structured data from a patient's medical document (doctor's note, prescription "
    "or lab report; usually Canadian English, sometimes French or Japanese). Copy names, doses, values "
    "and units as written (Canadian labs report cholesterol in mmol/L). Only list a "
    "diagnosis if the document states it as established; skip suspected or ruled-out ones. "
    "Do not interpret or give advice."
)
SYSTEM_BP_PHOTO = (
    "You read a photo of a home blood-pressure monitor display. Report the systolic (upper, SYS) "
    "and diastolic (lower, DIA) values in mmHg, the pulse (/min), whether the irregular-heartbeat "
    "icon is visible, and the measurement time if shown. Use null for anything not legible."
)
SYSTEM_ECG = (
    "You read an ECG report (OMRON connect or Apple Watch ECG PDF/screenshot). Report the device, "
    "the device's own classification label verbatim, and the recording time."
)

# ---------------------------------------------------------------- diagnosis matcher

DIAGNOSIS_PATTERNS: dict[str, re.Pattern[str]] = {
    "known_hypertension": re.compile(r"hypertension|high blood pressure|高血圧", re.I),
    "known_af": re.compile(r"atrial fibrillation|\ba-?fib\b|\bAF\b|心房細動", re.I),
    "known_chd": re.compile(r"coronary|angina|myocardial infarction|heart attack|狭心症|心筋梗塞|冠動脈", re.I),
    "known_pd": re.compile(r"parkinson|パーキンソン", re.I),
    "known_dementia": re.compile(r"dementia|alzheimer|認知症|アルツハイマー", re.I),
    "diabetes": re.compile(r"diabetes|糖尿病", re.I),
}
_UNCERTAIN = re.compile(r"^\s*(no|not|without|denies|negative for|rule[sd]? out|r/o|suspected|possible|probable|\?)\b"
                        r"|\b(suspected|ruled out|rule out)\b|疑い|否定|なし|可能性", re.I)


def match_diagnoses(diagnoses: list[str]) -> dict[str, bool]:
    """Profile flags implied by established diagnoses (negated/suspected ones are skipped)."""
    flags: dict[str, bool] = {}
    for d in diagnoses or []:
        if not isinstance(d, str) or _UNCERTAIN.search(d):
            continue
        for fld, pat in DIAGNOSIS_PATTERNS.items():
            if pat.search(d):
                flags[fld] = True
    return flags


_LAB_FIELDS = {
    "ldl_mg_dl": re.compile(r"\bLDL", re.I),
    "hdl_mg_dl": re.compile(r"\bHDL", re.I),
    "total_chol_mg_dl": re.compile(r"total cholesterol|\bT-?CHO|\bTC\b|総コレステロール", re.I),
    "egfr": re.compile(r"eGFR", re.I),
}


_CHOL_FIELDS = ("ldl_mg_dl", "hdl_mg_dl", "total_chol_mg_dl")
#: Cholesterol below this with no unit is taken as mmol/L (Canadian default; mg/dL values are ≥ ~20).
_MMOL_GUESS_MAX = 20.0


def _chol_to_mg_dl(value: float, unit: str | None) -> float:
    """Profile stores cholesterol in mg/dL; Canadian reports use mmol/L (× 38.67, app.ml.framingham)."""
    from app.ml.framingham import mmol_l_to_mg_dl

    u = (unit or "").lower().replace(" ", "")
    if "mmol" in u or (not u and value < _MMOL_GUESS_MAX):
        return round(mmol_l_to_mg_dl(value), 1)
    return value


def match_labs(lab_values: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for lv in lab_values or []:
        name, value = str(lv.get("name") or ""), lv.get("value")
        if not isinstance(value, (int, float)):
            continue
        for fld, pat in _LAB_FIELDS.items():
            if pat.search(name) and fld not in out:
                v = float(value)
                out[fld] = _chol_to_mg_dl(v, lv.get("unit")) if fld in _CHOL_FIELDS else v
                break
    return out


def _parse_dt(s: Any) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip().replace("/", "-")
    for cand in (s, s.replace(" ", "T")):
        try:
            return datetime.fromisoformat(cand)
        except ValueError:
            continue
    return None


def _media_kind(media_type: str, data: bytes) -> str:
    mt = (media_type or "").lower()
    if data[:5] == b"%PDF-" or mt == "application/pdf":
        return "pdf"
    if mt.startswith("image/"):
        return "image"
    if mt.startswith("audio/"):
        return "audio"
    if mt.startswith("text/"):
        return "text"
    return "unknown"


# ---------------------------------------------------------------- main entry

def ingest_document(db: Session, user_id: str, kind: str, filename: str, data: bytes, media_type: str,
                    llm: LlmProvider, storage: Storage | None = None) -> UploadedDocument:
    """Store + extract one document. Flushes; caller commits. Failures set status='failed'
    with the error in `extracted` instead of raising (the file is kept)."""
    if kind not in DOCUMENT_KINDS:
        raise ValueError(f"unknown document kind {kind!r}; expected one of {DOCUMENT_KINDS}")
    storage = storage or get_storage()
    mkind = _media_kind(media_type, data)
    file_url = storage.save(user_id, kind, filename, data)
    doc = UploadedDocument(user_id=user_id, kind=kind, file_url=file_url, status="processing",
                           extracted={"filename": filename, "media_type": media_type})
    db.add(doc)
    db.flush()

    if kind == "audio" or mkind == "audio":
        doc.status = "pending_transcription"
        db.flush()
        return doc

    hook = llm_audit_hook(db, user_id, resource=f"uploaded_document:{doc.id}")
    try:
        if kind == "bp_photo":
            result = _extract(llm, mkind, data, media_type, SYSTEM_BP_PHOTO, BP_PHOTO_SCHEMA, hook, "bp_photo")
            created = _apply_bp_photo(db, user_id, doc, result)
        elif kind == "ecg_pdf":
            result = _extract(llm, mkind, data, media_type, SYSTEM_ECG, ECG_SCHEMA, hook, "ecg_pdf")
            created = _apply_ecg(db, user_id, doc, result)
        else:
            result = _extract(llm, mkind, data, media_type, SYSTEM_MEDICAL, MEDICAL_SCHEMA, hook, kind)
            created = _apply_medical(db, user_id, doc, result)
        doc.extracted = {**(doc.extracted or {}), "data": result, "created": created}
        doc.status = "done"
    except Exception as e:  # keep the file; surface the error to the user
        log.warning("document %s extraction failed: %s", doc.id, e)
        doc.extracted = {**(doc.extracted or {}), "error": f"{type(e).__name__}: {e}"}
        doc.status = "failed"
    db.flush()
    return doc


def _extract(llm: LlmProvider, mkind: str, data: bytes, media_type: str, system: str, schema: dict[str, Any],
             hook, purpose: str) -> dict[str, Any]:
    purpose = f"document_extract:{purpose}"
    if mkind == "image":
        if media_type not in VISION_MEDIA_TYPES:
            raise ValueError(f"unsupported image type {media_type}; use JPEG, PNG, GIF or WebP")
        return llm.vision_extract(data, media_type, system, schema, purpose=purpose, audit_hook=hook)
    if mkind in ("pdf", "text"):
        text = llm.transcribe_document(data)
        if text.strip():
            return llm.extract(system, f"<document>\n{text}\n</document>", schema, model=llm.model_default,
                               purpose=purpose, audit_hook=hook)
        if mkind == "pdf":  # scanned PDF without a text layer → let the model read the pages
            return llm.vision_extract(data, "application/pdf", system, schema, purpose=purpose, audit_hook=hook)
        raise ValueError("document is empty")
    raise ValueError(f"unsupported media type {media_type!r}")


def _apply_medical(db: Session, user_id: str, doc: UploadedDocument, result: dict[str, Any]) -> dict[str, Any]:
    meds = [m for m in (result.get("medications") or []) if isinstance(m, dict) and m.get("name")]
    obs_ids = []
    for m in meds:
        detail = " ".join(str(x) for x in (m.get("dose"), m.get("schedule")) if x) or None
        o = Observation(user_id=user_id, kind="medication", label=str(m["name"])[:200], free_text=detail,
                        source="document", source_id=doc.id)
        db.add(o)
        db.flush()
        obs_ids.append(o.id)

    profile = db.query(Profile).filter(Profile.user_id == user_id).one_or_none()
    flags = match_diagnoses([d for d in (result.get("diagnoses") or []) if isinstance(d, str)])
    labs = match_labs(result.get("lab_values") or [])
    if profile is not None:
        for fld in flags:
            setattr(profile, fld, True)
        for fld, value in labs.items():
            setattr(profile, fld, value)
        if meds:
            current = list(profile.medications or [])
            names = {str(c.get("name", "")).lower() for c in current if isinstance(c, dict)}
            for m in meds:
                if str(m["name"]).lower() not in names:
                    current.append({"name": m["name"], "dose": m.get("dose"), "schedule": m.get("schedule"),
                                    "source_document_id": doc.id})
            profile.medications = current
    return {"medication_observation_ids": obs_ids, "profile_flags": flags, "profile_labs": labs}


def _apply_bp_photo(db: Session, user_id: str, doc: UploadedDocument, result: dict[str, Any]) -> dict[str, Any]:
    sys_, dia = result.get("systolic"), result.get("diastolic")
    if not isinstance(sys_, (int, float)) or not isinstance(dia, (int, float)):
        raise ValueError("could not read systolic/diastolic from the photo")
    pulse = result.get("pulse")
    measured = _parse_dt(result.get("measured_at")) or datetime.now(timezone.utc)
    rows = add_bp_readings(db, user_id, [BpIn(
        systolic=int(sys_), diastolic=int(dia), pulse=int(pulse) if isinstance(pulse, (int, float)) else None,
        measured_at=measured, irregular_pulse_flag=bool(result.get("irregular_icon")), source="omron_photo",
    )])
    return {"bp_reading_ids": [r.id for r in rows]}


def _apply_ecg(db: Session, user_id: str, doc: UploadedDocument, result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("classification")
    if not raw:
        raise ValueError("could not read the ECG classification")
    device = str(result.get("device") or "")
    source = "apple_ecg" if re.search(r"apple|watch", device, re.I) else "omron_connect"
    recorded = _parse_dt(result.get("recorded_at")) or datetime.now(timezone.utc)
    row = add_rhythm_result(db, user_id, source, str(raw), recorded, file_url=doc.file_url,
                            meta={"device": device or None, "document_id": doc.id})
    return {"rhythm_result_id": row.id, "label": row.label}
