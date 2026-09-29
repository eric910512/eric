"""Nursing assessments (api-design.md §9, database-design.md §6.J): SOAP assessments with
problem / goal / intervention / education / referral items.

- Drafts: only the assessing nurse edits or signs them. Signed assessments are locked
  (``422 RECORD_LOCKED``); a correction goes through ``amend``: a new draft version
  (``amends_id`` → the signed one). When that version is signed, the original becomes
  ``amended`` — every version stays readable, nothing is overwritten.
- Access: staff only (nurses of the patient; admin read); patients never get assessments here
  (the timeline shows them a neutral line for signed ones only).
- ``cycle_id`` / ``cycle_day`` are set once, from the cycle running on the assessment day.
Callers commit.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.api import APIError
from app.core.timeutil import MAX_CLOCK_SKEW, iso_utc, local_day_bounds_utc, patient_zone, to_local
from app.extensions import db
from app.models import Appointment, NursePatientAssignment, NursingAssessment, NursingAssessmentItem
from app.models.base import utcnow
from app.models.enums import (
    AssessmentItemStatus,
    AssessmentItemType,
    AssessmentType,
    ChemoReadiness,
    ObservationSource,
    OverallCondition,
    Priority,
    RecordStatus,
    RiskLevel,
    SignStatus,
)
from app.modules.chemotherapy.services import Fields, _invalid, _person
from app.services.treatment import active_plan_and_cycle

MAX_BACKDATE = timedelta(days=7)
SOAP = ("subjective", "objective", "assessment", "plan")
FIELDS = ("assessment_type", "assessed_at", "appointment_id", "ecog_status", "overall_condition", "risk_level",
          "chemo_readiness", *SOAP, "next_follow_up_at", "items")
TYPE_TEXT = {"initial": "初次評估", "pre_chemo": "化療前評估", "during_infusion": "輸注中評估", "post_chemo": "化療後評估",
             "follow_up": "追蹤評估", "phone_follow_up": "電話追蹤"}


def _locked():
    raise APIError(422, "RECORD_LOCKED", "已簽署的評估不能直接修改，請使用「修正」")


# ------------------------------------------------------------------ serialize


def item_payload(i):
    return {"id": i.id, "item_type": i.item_type, "code": i.code, "description": i.description, "priority": i.priority,
            "item_status": i.item_status, "resolved_at": iso_utc(i.resolved_at)}


def assessment_payload(a, *, summary=False):
    data = {
        "id": a.id, "patient_id": a.patient.public_id, "patient_code": a.patient.patient_code, "patient_name": a.patient.display_name,
        "assessment_type": a.assessment_type, "assessment_type_text": TYPE_TEXT.get(a.assessment_type, "護理評估"),
        "assessed_at": iso_utc(a.assessed_at), "assessed_by": _person(a.assessor),
        "sign_status": a.sign_status, "signed_at": iso_utc(a.signed_at), "record_status": a.record_status,
        "amends_id": a.amends_id, "amended_by_id": a.amendments[0].id if a.amendments else None,
        "risk_level": a.risk_level, "overall_condition": a.overall_condition, "chemo_readiness": a.chemo_readiness,
        "cycle_id": a.cycle_id, "cycle_day": a.cycle_day, "appointment_id": a.appointment_id,
    }
    if not summary:
        data.update({
            "ecog_status": a.ecog_status, **{k: getattr(a, k) for k in SOAP}, "next_follow_up_at": iso_utc(a.next_follow_up_at),
            "items": [item_payload(i) for i in a.items], "created_at": iso_utc(a.created_at), "updated_at": iso_utc(a.updated_at),
        })
    return data


def get_assessment(assessment_id):
    a = db.session.get(NursingAssessment, assessment_id)
    if a is None:
        raise APIError(404, "NOT_FOUND", "Nursing assessment not found")
    return a


def versions(a):
    """The whole correction chain of ``a``, oldest first."""
    first = a
    while first.amends is not None:
        first = first.amends
    chain, cur = [], first
    while cur is not None:
        chain.append(cur)
        cur = cur.amendments[0] if cur.amendments else None
    return chain


# ------------------------------------------------------------------ list


def _day(raw, field, details):
    from datetime import date
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        details.append({"field": field, "issue": "must be a date (YYYY-MM-DD)"})
        return None


def list_assessments(viewer, patient, args):
    """For one patient, or (no patient) the nurse's own assessments of current patients.
    ``sign_status``; ``from`` / ``to`` (local dates); ``include_history=true`` adds corrected versions."""
    details = []
    status = args.get("sign_status")
    if status and status not in SignStatus.ALL:
        details.append({"field": "sign_status", "issue": "must be draft or signed"})
    start, end = _day(args.get("from"), "from", details), _day(args.get("to"), "to", details)
    if details:
        _invalid(details, "查詢條件有誤")
    q = select(NursingAssessment)
    if patient is not None:
        q = q.where(NursingAssessment.patient_id == patient.id)
        zone = patient_zone(patient.timezone)
        if start:
            q = q.where(NursingAssessment.assessed_at >= local_day_bounds_utc(start, zone)[0])
        if end:
            q = q.where(NursingAssessment.assessed_at < local_day_bounds_utc(end, zone)[1])
    else:
        current = select(NursePatientAssignment.patient_id).where(NursePatientAssignment.nurse_id == viewer.id,
                                                                  NursePatientAssignment.ended_at.is_(None))
        q = q.where(NursingAssessment.assessed_by == viewer.id, NursingAssessment.patient_id.in_(current))
    if status:
        q = q.where(NursingAssessment.sign_status == status)
    if args.get("include_history") != "true":
        q = q.where(NursingAssessment.record_status == RecordStatus.FINAL)
    return db.session.execute(q.order_by(NursingAssessment.assessed_at.desc(), NursingAssessment.id.desc()).limit(200)).scalars().all()


# ------------------------------------------------------------------ parse


def _datetime(f, key, *, required=False):
    raw = f.body.get(key)
    if raw is None:
        if required:
            f.error(key, "is required (ISO 8601 datetime with timezone)")
        elif key in f.body:
            f.values[key] = None
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00")) if isinstance(raw, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        f.error(key, "must be an ISO 8601 datetime with timezone")
        return None
    at = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    at = at.replace(microsecond=at.microsecond // 1000 * 1000)
    f.values[key] = at
    return at


def _items(raw, f):
    if not isinstance(raw, list) or len(raw) > 30:
        f.error("items", "must be a list of at most 30 items")
        return []
    rows = []
    for n, item in enumerate(raw):
        at = f"items[{n}]"
        if not isinstance(item, dict):
            f.error(at, "must be an object")
            continue
        sub = Fields(item, "")
        sub.choice("item_type", AssessmentItemType.ALL, required=True)
        sub.text("description", 2000, required=True)
        sub.text("code", 50)
        sub.choice("priority", Priority.ALL, allow_null=True)
        sub.choice("item_status", AssessmentItemStatus.ALL)
        sub.unknown(("item_type", "description", "code", "priority", "item_status"))
        f.details += [{"field": f"{at}.{d['field']}", "issue": d["issue"]} for d in sub.details]
        rows.append({"item_status": AssessmentItemStatus.OPEN, **sub.values, "display_order": n + 1})
    return rows


def _values(patient, body, *, partial, window=True):
    f = Fields(body, "護理評估內容有誤")
    if not partial or f.has("assessment_type"):
        f.choice("assessment_type", AssessmentType.ALL, required=True)
    if f.has("assessed_at") or not partial:
        at = _datetime(f, "assessed_at")
        now = utcnow()
        if at is not None and (at > now + MAX_CLOCK_SKEW or (window and at < now - MAX_BACKDATE)):
            f.error("assessed_at", "must be within the last 7 days and not in the future")
    f.integer("ecog_status", 0, 5)
    for key, choices in (("overall_condition", OverallCondition.ALL), ("risk_level", RiskLevel.ALL), ("chemo_readiness", ChemoReadiness.ALL)):
        if f.has(key):
            f.choice(key, choices, allow_null=True)
    for key in SOAP:
        if f.has(key):
            f.text(key, 5000)
    if f.has("next_follow_up_at"):
        _datetime(f, "next_follow_up_at")
    if "appointment_id" in body:
        raw = body["appointment_id"]
        appt = db.session.get(Appointment, raw) if isinstance(raw, int) else None
        if raw is not None and (appt is None or appt.patient_id != patient.id or appt.deleted_at is not None):
            f.error("appointment_id", "must be one of this patient's appointments")
        else:
            f.values["appointment_id"] = raw
    items = _items(body["items"], f) if "items" in body else None
    return f, items


def _cycle_context(patient, at):
    _, cycle = active_plan_and_cycle(patient)
    if cycle is None:
        return None, None
    return cycle.id, cycle.cycle_day_on(to_local(at, patient_zone(patient.timezone)).date())


# ------------------------------------------------------------------ write


def create_draft(patient, nurse, body, *, amends=None):
    f, items = _values(patient, body, partial=False, window=amends is None)  # a correction keeps the original time
    f.unknown(("patient_id", *FIELDS))
    values = f.done()
    values.setdefault("assessed_at", utcnow())
    cycle_id, cycle_day = _cycle_context(patient, values["assessed_at"])
    a = NursingAssessment(patient=patient, assessed_by=nurse.id, sign_status=SignStatus.DRAFT, source=ObservationSource.NURSE,
                          record_status=RecordStatus.FINAL, cycle_id=cycle_id, cycle_day=cycle_day,
                          amends_id=amends.id if amends else None, **values)
    a.items = [NursingAssessmentItem(**r) for r in items or []]
    db.session.add(a)
    db.session.flush()
    return a


def _ensure_author(a, nurse):
    if a.assessed_by != nurse.id:
        raise APIError(403, "FORBIDDEN", "只有撰寫這份評估的護理師可以修改或簽署")


def update_draft(a, nurse, body):
    if a.sign_status == SignStatus.SIGNED or a.record_status != RecordStatus.FINAL:
        _locked()
    _ensure_author(a, nurse)
    f, items = _values(a.patient, body, partial=True)
    f.unknown(FIELDS)
    values = f.done()
    changed = [k for k, v in values.items() if getattr(a, k) != v]
    for k in changed:
        setattr(a, k, values[k])
    if "assessed_at" in changed:
        a.cycle_id, a.cycle_day = _cycle_context(a.patient, a.assessed_at)
    if items is not None:
        a.items = [NursingAssessmentItem(**r) for r in items]
        changed.append("items")
    return changed


def sign(a, nurse):
    """Draft → signed. A signed correction supersedes the version it corrects (→ ``amended``)."""
    if a.sign_status == SignStatus.SIGNED:
        raise APIError(409, "INVALID_STATE", "這份評估已簽署")
    if a.record_status != RecordStatus.FINAL:
        _locked()
    _ensure_author(a, nurse)
    if not any((getattr(a, k) or "").strip() for k in SOAP):
        _invalid([{"field": "subjective", "issue": "at least one of S / O / A / P is required before signing"}], "護理評估內容不足")
    a.sign_status = SignStatus.SIGNED
    a.signed_at = utcnow()
    if a.amends is not None:
        a.amends.record_status = RecordStatus.AMENDED
    return a.amends


def amend(original, nurse, body):
    """Correction of a signed assessment: a new draft version with the fields sent; everything
    else (incl. items) is copied. Returns (draft, reason)."""
    if original.sign_status != SignStatus.SIGNED:
        raise APIError(409, "INVALID_STATE", "草稿請直接修改；只有已簽署的評估需要修正")
    if original.record_status != RecordStatus.FINAL:
        raise APIError(409, "INVALID_STATE", "這份評估已被修正，請對最新的版本操作")
    if any(v.sign_status == SignStatus.DRAFT for v in original.amendments):
        raise APIError(409, "INVALID_STATE", "這份評估已有尚未簽署的修正版本")
    f = Fields(body, "修正內容有誤")
    f.text("amend_reason", 500, required=True)
    f.unknown(("amend_reason", *FIELDS))
    reason = f.done()["amend_reason"]
    base = {k: getattr(original, k) for k in ("assessment_type", "appointment_id", "ecog_status", "overall_condition", "risk_level",
                                                "chemo_readiness", *SOAP)}
    base["assessed_at"] = iso_utc(original.assessed_at)
    base["next_follow_up_at"] = iso_utc(original.next_follow_up_at)
    base["items"] = [{"item_type": i.item_type, "code": i.code, "description": i.description, "priority": i.priority,
                      "item_status": i.item_status} for i in original.items]
    merged = {**{k: v for k, v in base.items() if v is not None}, **{k: v for k, v in body.items() if k != "amend_reason"}}
    draft = create_draft(original.patient, nurse, merged, amends=original)
    return draft, reason


def update_item(a, item_id, body):
    """Item status (open / in_progress / resolved / done) — care follow-up, also after signing."""
    item = next((i for i in a.items if i.id == item_id), None)
    if item is None:
        raise APIError(404, "NOT_FOUND", "Assessment item not found")
    if a.record_status != RecordStatus.FINAL:
        raise APIError(409, "INVALID_STATE", "這份評估已被修正，請更新最新版本的項目")
    f = Fields(body, "項目資料有誤")
    f.choice("item_status", AssessmentItemStatus.ALL, required=True)
    f.unknown(("item_status",))
    status = f.done()["item_status"]
    item.item_status = status
    item.resolved_at = utcnow() if status in (AssessmentItemStatus.RESOLVED, AssessmentItemStatus.DONE) else None
    return item
