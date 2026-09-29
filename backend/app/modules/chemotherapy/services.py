"""Chemotherapy (api-design.md §5, database-design.md §6.E): drugs, regimens, plans, cycles and
medication records.

- Access control is the existing one (app.core.auth): everything hangs off a patient, so a
  plan / cycle / medication is visible only to whoever may view its patient (else 404).
- ``cycle_day`` is computed once, when a record is written, from the cycle it belongs to.
  Starting / completing / delaying a cycle never recalculates existing records: records made
  after a new cycle starts get that cycle's id and day (``active_plan_and_cycle``).
- Medication records are observations (append-only): a correction is a new row with
  ``amends_id`` → the original, which becomes ``amended``; a wrong entry becomes
  ``entered_in_error``. The reason is kept in the audit log. Callers commit.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select

from app.core.api import APIError
from app.core.timeutil import MAX_CLOCK_SKEW, iso_date, iso_utc, patient_zone, to_local
from app.extensions import db
from app.models import (
    CancerDiagnosis,
    ChemoRegimen,
    ChemotherapyCycle,
    ChemotherapyPlan,
    Drug,
    MedicationRecord,
    RegimenDrug,
)
from app.models.base import utcnow
from app.models.enums import (
    AdministrationStatus,
    CycleStatus,
    DrugRoute,
    EmetogenicRisk,
    MedicationType,
    ObservationSource,
    PlanIntent,
    PlanStatus,
    RecordStatus,
    RoleName,
)
from app.services.treatment import cycle_nadir

MAX_TOTAL_CYCLES = 30
MAX_START_BACKDATE = timedelta(days=30)
OPEN_PLAN = (PlanStatus.PLANNED, PlanStatus.ACTIVE)
NOT_STARTED = (CycleStatus.SCHEDULED, CycleStatus.DELAYED)
STARTED = (CycleStatus.IN_PROGRESS, CycleStatus.COMPLETED)
FINISHED = (CycleStatus.COMPLETED, CycleStatus.CANCELLED)


def _invalid(details, message="資料內容有誤"):
    raise APIError(400, "VALIDATION_ERROR", message, details)


def _conflict(code, message):
    raise APIError(409, code, message)


def _person(user):
    return {"id": user.public_id, "display_name": user.display_name} if user else None


def is_staff(user):
    return user.role_name in (RoleName.NURSE, RoleName.ADMIN)


def local_today(patient):
    return to_local(utcnow(), patient_zone(patient.timezone)).date()


# ------------------------------------------------------------------ field parsing


class Fields:
    """Collects per-field errors while reading a JSON body (400 with every problem at once)."""

    def __init__(self, body, message):
        self.body = body
        self.message = message
        self.details = []
        self.values = {}

    def has(self, key):
        return key in self.body

    def error(self, field, issue):
        self.details.append({"field": field, "issue": issue})

    def text(self, key, limit, *, required=False, target=None):
        if key not in self.body and not required:
            return
        v = self.body.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            if required:
                self.error(key, f"is required (at most {limit} characters)")
            else:
                self.values[target or key] = None
            return
        if not isinstance(v, str) or len(v.strip()) > limit:
            self.error(key, f"must be text of at most {limit} characters")
        else:
            self.values[target or key] = v.strip()

    def choice(self, key, choices, *, required=False, allow_null=False):
        if key not in self.body:
            if required:
                self.error(key, f"is required (one of: {', '.join(choices)})")
            return
        v = self.body[key]
        if v is None and allow_null and not required:
            self.values[key] = None
        elif v not in choices:
            self.error(key, f"must be one of: {', '.join(choices)}")
        else:
            self.values[key] = v

    def integer(self, key, lo, hi, *, required=False, allow_null=True):
        v = self.body.get(key)
        if v is None:
            if required:
                self.error(key, f"is required (integer {lo}–{hi})")
            elif key in self.body and allow_null:
                self.values[key] = None
            return
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            self.error(key, f"must be an integer from {lo} to {hi}")
        else:
            self.values[key] = v

    def decimal(self, key, lo, hi, *, places=2, required=False):
        v = self.body.get(key)
        if v is None:
            if required:
                self.error(key, f"is required (number {lo}–{hi})")
            elif key in self.body:
                self.values[key] = None
            return
        try:
            d = Decimal(str(v)) if not isinstance(v, bool) and isinstance(v, (int, float, str)) else None
        except InvalidOperation:
            d = None
        if d is None or not d.is_finite() or not Decimal(str(lo)) <= d <= Decimal(str(hi)):
            self.error(key, f"must be a number from {lo} to {hi}")
        else:
            self.values[key] = d.quantize(Decimal(1).scaleb(-places))

    def day(self, key, *, required=False):
        v = self.body.get(key)
        if v is None:
            if required:
                self.error(key, "is required (YYYY-MM-DD)")
            elif key in self.body:
                self.values[key] = None
            return None
        try:
            parsed = date.fromisoformat(v) if isinstance(v, str) and len(v) == 10 else None
        except ValueError:
            parsed = None
        if parsed is None:
            self.error(key, "must be a date (YYYY-MM-DD)")
            return None
        self.values[key] = parsed
        return parsed

    def boolean(self, key, default=None):
        v = self.body.get(key, default)
        if not isinstance(v, bool):
            self.error(key, "must be true or false")
            return default
        return v

    def unknown(self, allowed):
        for key in sorted(set(self.body) - set(allowed)):
            self.error(key, "is not a supported field")

    def done(self):
        if self.details:
            _invalid(self.details, self.message)
        return self.values


# ------------------------------------------------------------------ drugs


def drug_payload(d):
    return {"id": d.id, "generic_name": d.generic_name, "brand_name": d.brand_name, "drug_class": d.drug_class,
            "default_route": d.default_route, "is_active": bool(d.is_active)}


def list_drugs(*, q=None, include_inactive=False):
    conds = [] if include_inactive else [Drug.is_active.is_(True)]
    if q:
        like = f"%{q.strip()}%"
        conds.append(Drug.generic_name.ilike(like) | Drug.brand_name.ilike(like))
    return db.session.execute(select(Drug).where(*conds).order_by(Drug.generic_name)).scalars().all()


def _drug_values(body, partial):
    f = Fields(body, "藥物資料有誤")
    if not partial or f.has("generic_name"):
        f.text("generic_name", 100, required=True)
    for key, limit in (("brand_name", 100), ("drug_class", 50)):
        if f.has(key):
            f.text(key, limit)
    if f.has("default_route"):
        f.choice("default_route", DrugRoute.ALL, allow_null=True)
    if f.has("is_active"):
        f.values["is_active"] = f.boolean("is_active")
    f.unknown(("generic_name", "brand_name", "drug_class", "default_route", "is_active"))
    values = f.done()
    name = values.get("generic_name")
    if name and db.session.execute(select(Drug.id).where(func.lower(Drug.generic_name) == name.lower())).first():
        raise APIError(409, "CONFLICT", "這個藥物已存在", [{"field": "generic_name", "issue": "is already registered"}])
    return values


def create_drug(body):
    drug = Drug(is_active=True, **_drug_values(body, partial=False))
    db.session.add(drug)
    db.session.flush()
    return drug


def update_drug(drug, body):
    values = _drug_values({k: v for k, v in body.items() if not (k == "generic_name" and v == drug.generic_name)}, partial=True)
    changed = [k for k, v in values.items() if getattr(drug, k) != v]
    for k in changed:
        setattr(drug, k, values[k])
    return changed


# ------------------------------------------------------------------ regimens


def regimen_payload(r, full=True):
    data = {"id": r.id, "name": r.name, "cycle_length_days": r.cycle_length_days,
            "default_total_cycles": r.default_total_cycles, "emetogenic_risk": r.emetogenic_risk}
    if full:
        data.update({
            "description": r.description, "is_active": bool(r.is_active),
            "drugs": [{"id": rd.id, "drug": {"id": rd.drug.id, "generic_name": rd.drug.generic_name},
                       "dose_value": float(rd.dose_value) if rd.dose_value is not None else None,
                       "dose_unit": rd.dose_unit, "route": rd.route, "day_of_cycle": rd.day_of_cycle,
                       "sequence": rd.sequence} for rd in r.regimen_drugs],
        })
    return data


def list_regimens(include_inactive=False):
    conds = [] if include_inactive else [ChemoRegimen.is_active.is_(True)]
    return db.session.execute(select(ChemoRegimen).where(*conds).order_by(ChemoRegimen.name)).scalars().all()


def _regimen_drugs(raw):
    if not isinstance(raw, list) or len(raw) > 20:
        _invalid([{"field": "drugs", "issue": "must be a list of at most 20 drugs"}], "處方資料有誤")
    rows, details, seen = [], [], set()
    for i, item in enumerate(raw):
        at = f"drugs[{i}]"
        if not isinstance(item, dict):
            details.append({"field": at, "issue": "must be an object"})
            continue
        drug = db.session.get(Drug, item.get("drug_id")) if isinstance(item.get("drug_id"), int) else None
        if drug is None or not drug.is_active:
            details.append({"field": f"{at}.drug_id", "issue": "must be an active drug id"})
            continue
        f = Fields(item, "")
        f.decimal("dose_value", 0.01, 100000)
        f.text("dose_unit", 20)
        f.choice("route", DrugRoute.ALL, allow_null=True)
        f.text("day_of_cycle", 20)
        f.integer("sequence", 1, 99)
        details += [{"field": f"{at}.{d['field']}", "issue": d["issue"]} for d in f.details]
        key = (drug.id, f.values.get("day_of_cycle"))
        if key in seen:
            details.append({"field": at, "issue": "the same drug is listed twice for the same day"})
        seen.add(key)
        rows.append(RegimenDrug(drug_id=drug.id, sequence=f.values.get("sequence", i + 1),
                                **{k: f.values.get(k) for k in ("dose_value", "dose_unit", "route", "day_of_cycle")}))
    if details:
        _invalid(details, "處方資料有誤")
    return rows


def _regimen_values(body, partial, current=None):
    f = Fields(body, "處方資料有誤")
    if not partial or f.has("name"):
        f.text("name", 50, required=True)
    if f.has("description"):
        f.text("description", 2000)
    f.integer("cycle_length_days", 1, 365, required=not partial)
    f.integer("default_total_cycles", 1, MAX_TOTAL_CYCLES)
    if f.has("emetogenic_risk"):
        f.choice("emetogenic_risk", EmetogenicRisk.ALL, allow_null=True)
    if f.has("is_active"):
        f.values["is_active"] = f.boolean("is_active")
    f.unknown(("name", "description", "cycle_length_days", "default_total_cycles", "emetogenic_risk", "is_active", "drugs"))
    values = f.done()
    name = values.get("name")
    if name and (current is None or name != current.name) and db.session.execute(
            select(ChemoRegimen.id).where(func.lower(ChemoRegimen.name) == name.lower())).first():
        raise APIError(409, "CONFLICT", "這個處方名稱已存在", [{"field": "name", "issue": "is already registered"}])
    return values


def create_regimen(body):
    values = _regimen_values(body, partial=False)
    drugs = _regimen_drugs(body.get("drugs", []))
    regimen = ChemoRegimen(is_active=True, **values)
    regimen.regimen_drugs = drugs
    db.session.add(regimen)
    db.session.flush()
    return regimen


def update_regimen(regimen, body):
    values = _regimen_values(body, partial=True, current=regimen)
    changed = [k for k, v in values.items() if getattr(regimen, k) != v]
    for k in changed:
        setattr(regimen, k, values[k])
    if "drugs" in body:
        regimen.regimen_drugs = _regimen_drugs(body["drugs"])  # template only: records reference drugs, not these rows
        db.session.flush()
        changed.append("drugs")
    return changed


# ------------------------------------------------------------------ plans / cycles: serialize


def cycle_payload(c, viewer, today):
    started = c.status in STARTED and c.actual_start_date is not None
    data = {
        "id": c.id, "plan_id": c.plan_id, "cycle_number": c.cycle_number,
        "scheduled_date": iso_date(c.scheduled_date), "actual_start_date": iso_date(c.actual_start_date),
        "actual_end_date": iso_date(c.actual_end_date), "status": c.status,
        "delay_days": c.delay_days, "delay_reason": c.delay_reason,
        "dose_modification_pct": c.dose_modification_pct,
        "nadir_start_day": c.nadir_start_day, "nadir_end_day": c.nadir_end_day,
        "cycle_day": c.cycle_day_on(today) if c.status == CycleStatus.IN_PROGRESS else None,
        "in_nadir": cycle_nadir(c, today)[0] if c.status == CycleStatus.IN_PROGRESS else False,
        "medication_count": sum(m.record_status == RecordStatus.FINAL for m in c.medication_records) if started else 0,
        "appointment_id": _cycle_appointment_id(c),
    }
    if is_staff(viewer):
        data.update({"weight_kg": float(c.weight_kg) if c.weight_kg is not None else None,
                     "bsa_m2": float(c.bsa_m2) if c.bsa_m2 is not None else None, "notes": c.notes})
    return data


def _cycle_appointment_id(c):
    from app.modules.chemotherapy.appointments import cycle_appointment_id
    return cycle_appointment_id(c)


def _cycles(plan):
    return [c for c in plan.cycles if c.deleted_at is None]


def plan_payload(plan, viewer, with_cycles=True):
    today = local_today(plan.patient)
    cycles = _cycles(plan)
    current = next((c for c in cycles if c.status == CycleStatus.IN_PROGRESS), None)
    dx = plan.diagnosis
    data = {
        "id": plan.id, "patient_id": plan.patient.public_id,
        "diagnosis": {"id": dx.id, "cancer_type_code": dx.cancer_type.code, "name_zh": dx.cancer_type.name_zh, "stage": dx.stage} if dx else None,
        "regimen": regimen_payload(plan.regimen, full=False) if plan.regimen else None,
        "plan_name": plan.plan_name, "intent": plan.intent, "line_of_therapy": plan.line_of_therapy,
        "total_cycles": plan.total_cycles, "start_date": iso_date(plan.start_date), "end_date": iso_date(plan.end_date),
        "status": plan.status, "discontinue_reason": plan.discontinue_reason,
        "attending_physician_name": plan.attending_physician_name,
        "progress": {
            "completed_cycles": sum(c.status == CycleStatus.COMPLETED for c in cycles),
            "current_cycle_number": current.cycle_number if current else None,
            "current_cycle_day": current.cycle_day_on(today) if current else None,
        },
        "created_at": iso_utc(plan.created_at),
    }
    if with_cycles:
        data["cycles"] = [cycle_payload(c, viewer, today) for c in cycles]
    if is_staff(viewer):
        data["created_by"] = _person(plan.creator)
    return data


def list_plans(patient):
    return db.session.execute(
        select(ChemotherapyPlan).filter_by(patient_id=patient.id, deleted_at=None)
        .order_by(ChemotherapyPlan.start_date.desc(), ChemotherapyPlan.id.desc())
    ).scalars().all()


def get_plan(plan_id):
    plan = db.session.get(ChemotherapyPlan, plan_id)
    if plan is None or plan.deleted_at is not None:
        raise APIError(404, "NOT_FOUND", "Chemotherapy plan not found")
    return plan


def get_cycle(cycle_id):
    cycle = db.session.get(ChemotherapyCycle, cycle_id)
    if cycle is None or cycle.deleted_at is not None or cycle.plan.deleted_at is not None:
        raise APIError(404, "NOT_FOUND", "Cycle not found")
    return cycle


def get_medication(record_id):
    record = db.session.get(MedicationRecord, record_id)
    if record is None:
        raise APIError(404, "NOT_FOUND", "Medication record not found")
    return record


# ------------------------------------------------------------------ plans: write


def _plan_fields(f, partial):
    if not partial or f.has("plan_name"):
        f.text("plan_name", 100, required=not partial)
    if f.has("intent") or not partial:
        f.choice("intent", PlanIntent.ALL, allow_null=True)
    f.integer("line_of_therapy", 1, 20)
    f.integer("total_cycles", 1, MAX_TOTAL_CYCLES, required=not partial, allow_null=False)
    if f.has("attending_physician_name"):
        f.text("attending_physician_name", 100)


def create_plan(patient, user, body):
    """New plan (``planned``); by default also its cycles, scheduled every regimen cycle length."""
    f = Fields(body, "療程資料有誤")
    _plan_fields(f, partial=False)
    start = f.day("start_date", required=True)
    generate = f.boolean("generate_cycles", True)
    dx = db.session.get(CancerDiagnosis, body.get("diagnosis_id")) if isinstance(body.get("diagnosis_id"), int) else None
    if dx is None or dx.patient_id != patient.id or dx.deleted_at is not None:
        f.error("diagnosis_id", "must be one of this patient's diagnoses")
    regimen = None
    if body.get("regimen_id") is not None:
        regimen = db.session.get(ChemoRegimen, body["regimen_id"]) if isinstance(body["regimen_id"], int) else None
        if regimen is None or not regimen.is_active:
            f.error("regimen_id", "must be an active regimen id")
    if generate and not f.details and not (regimen and regimen.cycle_length_days):
        f.error("generate_cycles", "needs a regimen with cycle_length_days (or send false and add cycles one by one)")
    from app.modules.chemotherapy.appointments import create_infusions, infusion_options
    infusions = infusion_options(body.get("generate_infusion_appointments"))
    if infusions and not generate:
        f.error("generate_infusion_appointments", "needs generate_cycles")
    f.unknown(("patient_id", "diagnosis_id", "regimen_id", "plan_name", "intent", "line_of_therapy", "total_cycles",
               "start_date", "attending_physician_name", "generate_cycles", "generate_infusion_appointments"))
    values = f.done()
    values.pop("start_date", None)
    if any(p.status in OPEN_PLAN for p in list_plans(patient)):
        _conflict("CONFLICT", "這位病人已有進行中或預定的療程，請先完成或停止原療程")
    plan = ChemotherapyPlan(patient_id=patient.id, diagnosis_id=dx.id, regimen_id=regimen.id if regimen else None,
                            start_date=start, status=PlanStatus.PLANNED, created_by=user.id, **values)
    db.session.add(plan)
    db.session.flush()
    if generate:
        for n in range(1, plan.total_cycles + 1):
            db.session.add(ChemotherapyCycle(plan_id=plan.id, patient_id=patient.id, cycle_number=n,
                                             scheduled_date=start + timedelta(days=(n - 1) * regimen.cycle_length_days),
                                             status=CycleStatus.SCHEDULED, dose_modification_pct=100))
        db.session.flush()
        db.session.refresh(plan)
        if infusions:
            create_infusions(plan, user, infusions)
    return plan


def _ensure_open(plan):
    if plan.status not in OPEN_PLAN:
        _conflict("INVALID_STATE", "這個療程已結束，不能再修改")


def update_plan(plan, body):
    _ensure_open(plan)
    f = Fields(body, "療程資料有誤")
    _plan_fields(f, partial=True)
    f.unknown(("plan_name", "intent", "line_of_therapy", "total_cycles", "attending_physician_name"))
    values = f.done()
    if "total_cycles" in values and values["total_cycles"] < max((c.cycle_number for c in _cycles(plan)), default=0):
        _invalid([{"field": "total_cycles", "issue": "cannot be less than the number of existing cycles"}], "療程資料有誤")
    changed = [k for k, v in values.items() if getattr(plan, k) != v]
    for k in changed:
        setattr(plan, k, values[k])
    return changed


def discontinue_plan(plan, body):
    """Stop a plan: cycles not started are cancelled; a running cycle ends today (it did happen)."""
    _ensure_open(plan)
    f = Fields(body, "停止療程資料有誤")
    f.text("discontinue_reason", 2000, required=True)
    f.unknown(("discontinue_reason",))
    reason = f.done()["discontinue_reason"]
    today = local_today(plan.patient)
    for c in _cycles(plan):
        if c.status in NOT_STARTED:
            c.status = CycleStatus.CANCELLED
        elif c.status == CycleStatus.IN_PROGRESS:
            c.status = CycleStatus.COMPLETED
            c.actual_end_date = max(today, c.actual_start_date)
    plan.status = PlanStatus.DISCONTINUED
    plan.discontinue_reason = reason
    plan.end_date = today


# ------------------------------------------------------------------ cycles: write


def _cycle_fields(f):
    f.decimal("weight_kg", 20, 300, places=1)
    f.decimal("bsa_m2", 0.5, 3.5)
    f.integer("dose_modification_pct", 1, 150, allow_null=False)
    f.integer("nadir_start_day", 1, 60)
    f.integer("nadir_end_day", 1, 60)
    if f.has("notes"):
        f.text("notes", 2000)


def _check_nadir(values, current=None):
    start = values.get("nadir_start_day", getattr(current, "nadir_start_day", None))
    end = values.get("nadir_end_day", getattr(current, "nadir_end_day", None))
    if start is not None and end is not None and end < start:
        _invalid([{"field": "nadir_end_day", "issue": "must not be before nadir_start_day"}], "Cycle 資料有誤")


def add_cycle(plan, body):
    _ensure_open(plan)
    f = Fields(body, "Cycle 資料有誤")
    f.day("scheduled_date", required=True)
    _cycle_fields(f)
    f.unknown(("scheduled_date", "weight_kg", "bsa_m2", "dose_modification_pct", "nadir_start_day", "nadir_end_day", "notes"))
    values = f.done()
    _check_nadir(values)
    number = max((c.cycle_number for c in plan.cycles), default=0) + 1  # incl. deleted rows (UQ plan_id, cycle_number)
    if number > (plan.total_cycles or 0):
        _conflict("CONFLICT", f"已達療程的 {plan.total_cycles} 個 Cycle；請先修改療程的總 Cycle 數")
    cycle = ChemotherapyCycle(plan_id=plan.id, patient_id=plan.patient_id, cycle_number=number,
                              status=CycleStatus.SCHEDULED, **{"dose_modification_pct": 100, **values})
    db.session.add(cycle)
    db.session.flush()
    return cycle


def update_cycle(cycle, body):
    _ensure_open(cycle.plan)
    f = Fields(body, "Cycle 資料有誤")
    if f.has("scheduled_date"):
        f.day("scheduled_date", required=True)
    _cycle_fields(f)
    for key in ("actual_start_date", "actual_end_date", "status", "cycle_number"):
        if key in body:
            f.error(key, "use start / complete / delay (dates of started cycles are not edited)")
    f.unknown(("scheduled_date", "weight_kg", "bsa_m2", "dose_modification_pct", "nadir_start_day", "nadir_end_day", "notes",
               "actual_start_date", "actual_end_date", "status", "cycle_number"))
    values = f.done()
    if "scheduled_date" in values and cycle.status not in NOT_STARTED:
        _conflict("INVALID_STATE", "已開始的 Cycle 不能修改預定日期")
    if cycle.status in FINISHED and values:
        _conflict("INVALID_STATE", "已結束的 Cycle 不能修改")
    _check_nadir(values, cycle)
    changed = [k for k, v in values.items() if getattr(cycle, k) != v]
    for k in changed:
        setattr(cycle, k, values[k])
    return changed


def start_cycle(cycle, body):
    """Day 1 = ``start_date`` (default today, patient's timezone). Existing records keep their
    cycle_id / cycle_day; only records written from now on belong to this cycle."""
    plan = cycle.plan
    _ensure_open(plan)
    if cycle.status not in NOT_STARTED:
        _conflict("INVALID_STATE", "這個 Cycle 已經開始或已結束")
    f = Fields(body, "開始 Cycle 資料有誤")
    today = local_today(plan.patient)
    start = f.day("start_date") or today
    _cycle_fields(f)
    f.unknown(("start_date", "weight_kg", "bsa_m2", "dose_modification_pct", "nadir_start_day", "nadir_end_day", "notes"))
    if start > today:
        f.error("start_date", "cannot be in the future")
    elif start < today - MAX_START_BACKDATE:
        f.error("start_date", "cannot be more than 30 days ago")
    values = f.done()
    values.pop("start_date", None)
    running = db.session.execute(
        select(ChemotherapyCycle.id).filter_by(patient_id=cycle.patient_id, status=CycleStatus.IN_PROGRESS, deleted_at=None)
    ).first()
    if running:
        _conflict("INVALID_STATE", "這位病人還有進行中的 Cycle，請先完成該 Cycle")
    earlier = [c for c in _cycles(plan) if c.cycle_number < cycle.cycle_number]
    if any(c.status not in FINISHED for c in earlier):
        _conflict("INVALID_STATE", "前面的 Cycle 尚未完成或取消")
    last_start = max((c.actual_start_date for c in earlier if c.actual_start_date), default=None)
    last_end = max((c.actual_end_date for c in earlier if c.actual_end_date), default=None)
    if (last_end and start < last_end) or (last_start and start <= last_start):
        _invalid([{"field": "start_date", "issue": "must be after the previous cycle"}], "開始 Cycle 資料有誤")
    previous = max((c for c in earlier if c.status == CycleStatus.COMPLETED), key=lambda c: c.cycle_number, default=None)
    if previous is not None:  # carry the nadir window of the previous cycle unless given
        values.setdefault("nadir_start_day", cycle.nadir_start_day or previous.nadir_start_day)
        values.setdefault("nadir_end_day", cycle.nadir_end_day or previous.nadir_end_day)
    _check_nadir(values, cycle)
    for k, v in values.items():
        setattr(cycle, k, v)
    cycle.actual_start_date = start
    cycle.status = CycleStatus.IN_PROGRESS
    if plan.status == PlanStatus.PLANNED:
        plan.status = PlanStatus.ACTIVE
    return start


def complete_cycle(cycle, body):
    plan = cycle.plan
    _ensure_open(plan)
    if cycle.status != CycleStatus.IN_PROGRESS:
        _conflict("INVALID_STATE", "只有進行中的 Cycle 可以完成")
    f = Fields(body, "完成 Cycle 資料有誤")
    today = local_today(plan.patient)
    end = f.day("end_date") or today
    if f.has("notes"):
        f.text("notes", 2000)
    f.unknown(("end_date", "notes"))
    if end > today:
        f.error("end_date", "cannot be in the future")
    elif end < cycle.actual_start_date:
        f.error("end_date", "cannot be before the cycle start")
    values = f.done()
    if "notes" in values:
        cycle.notes = values["notes"]
    cycle.actual_end_date = end
    cycle.status = CycleStatus.COMPLETED
    cycles = _cycles(plan)
    if len(cycles) >= (plan.total_cycles or 0) and all(c.status in FINISHED for c in cycles):
        plan.status = PlanStatus.COMPLETED
        plan.end_date = end
    return end


def delay_cycle(cycle, body, user):
    """Returns (days, [(old, new) appointments moved when ``reschedule_appointments``])."""
    _ensure_open(cycle.plan)
    if cycle.status not in NOT_STARTED:
        _conflict("INVALID_STATE", "只有尚未開始的 Cycle 可以延後")
    f = Fields(body, "延後 Cycle 資料有誤")
    new_date = f.day("new_scheduled_date", required=True)
    f.text("delay_reason", 500, required=True)
    move = f.boolean("reschedule_appointments", False)
    f.unknown(("new_scheduled_date", "delay_reason", "reschedule_appointments"))
    if new_date and new_date <= cycle.scheduled_date:
        f.error("new_scheduled_date", "must be later than the current scheduled date")
    values = f.done()
    days = (new_date - cycle.scheduled_date).days
    cycle.delay_days = (cycle.delay_days or 0) + days
    cycle.delay_reason = values["delay_reason"]
    cycle.scheduled_date = new_date
    cycle.status = CycleStatus.DELAYED
    moved = []
    if move:
        from app.modules.chemotherapy.appointments import move_cycle_appointments
        moved = move_cycle_appointments(cycle, user, days)
    return days, moved


# ------------------------------------------------------------------ medication records


def medication_payload(m, viewer):
    data = {
        "id": m.id, "cycle_id": m.cycle_id, "cycle_number": m.cycle.cycle_number, "cycle_day": m.cycle_day,
        "drug": {"id": m.drug.id, "generic_name": m.drug.generic_name},
        "medication_type": m.medication_type, "dose_value": float(m.dose_value), "dose_unit": m.dose_unit,
        "route": m.route, "administered_at": iso_utc(m.administered_at), "infusion_duration_min": m.infusion_duration_min,
        "administration_status": m.administration_status, "record_status": m.record_status,
    }
    if is_staff(viewer):  # internal: who gave it, reactions, correction chain
        newer = next((a for a in m.amendments), None)
        data.update({
            "reaction_notes": m.reaction_notes, "administered_by": _person(m.administrator), "source": m.source,
            "amends_id": m.amends_id, "amended_by_id": newer.id if newer else None, "created_at": iso_utc(m.created_at),
        })
    return data


def list_medications(viewer, *, cycle=None, patient=None):
    """Staff: every record incl. amended / entered-in-error (history). Patient: final records only."""
    q = select(MedicationRecord)
    q = q.filter_by(cycle_id=cycle.id) if cycle is not None else q.filter_by(patient_id=patient.id)
    if not is_staff(viewer):
        q = q.filter_by(record_status=RecordStatus.FINAL)
    return db.session.execute(q.order_by(MedicationRecord.administered_at.desc(), MedicationRecord.id.desc())).scalars().all()


def _administered_at(f, cycle):
    raw = f.body.get("administered_at")
    if not isinstance(raw, str):
        f.error("administered_at", "is required (ISO 8601 datetime with timezone)")
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        f.error("administered_at", "must be an ISO 8601 datetime with timezone")
        return None
    at = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    at = at.replace(microsecond=at.microsecond // 1000 * 1000)
    day = to_local(at, patient_zone(cycle.patient.timezone)).date()
    if at > utcnow() + MAX_CLOCK_SKEW:
        f.error("administered_at", "cannot be in the future")
    elif day < cycle.actual_start_date or (cycle.actual_end_date and day > cycle.actual_end_date):
        f.error("administered_at", "must be within this cycle (from its start to its end)")
    f.values["administered_at"] = at
    return at


MED_FIELDS = ("drug_id", "medication_type", "dose_value", "dose_unit", "route", "administered_at",
              "infusion_duration_min", "administration_status", "reaction_notes")


def _medication_values(body, cycle, base=None):
    """Values of a new record; ``base`` (an amended record) supplies fields not sent."""
    merged = {**({k: _raw(base, k) for k in MED_FIELDS} if base else {}), **{k: v for k, v in body.items() if k in MED_FIELDS}}
    f = Fields(merged, "給藥紀錄內容有誤")
    drug = db.session.get(Drug, merged.get("drug_id")) if isinstance(merged.get("drug_id"), int) else None
    if drug is None or (not drug.is_active and (base is None or drug.id != base.drug_id)):
        f.error("drug_id", "must be an active drug id")
    f.choice("medication_type", MedicationType.ALL, required=True)
    f.decimal("dose_value", 0.01, 100000, required=True)
    f.text("dose_unit", 20, required=True)
    f.choice("route", DrugRoute.ALL, allow_null=True)
    _administered_at(f, cycle)
    f.integer("infusion_duration_min", 1, 1440)
    f.choice("administration_status", AdministrationStatus.ALL, required=True)
    f.text("reaction_notes", 2000)
    values = f.done()
    values["drug_id"] = drug.id
    if values.get("route") is None and "route" not in merged:
        values["route"] = drug.default_route
    return values


def _raw(record, key):
    v = getattr(record, key)
    if key == "dose_value":
        return str(v)
    if key == "administered_at":
        return iso_utc(v)
    return v


def _cycle_day(cycle, at):
    return cycle.cycle_day_on(to_local(at, patient_zone(cycle.patient.timezone)).date())


def create_medication(cycle, user, body):
    if cycle.status not in STARTED or cycle.actual_start_date is None:
        _conflict("CYCLE_NOT_STARTED", "這個 Cycle 還沒開始，請先開始 Cycle 再登錄給藥")
    extra = set(body) - set(MED_FIELDS)
    if extra:
        _invalid([{"field": k, "issue": "is not a supported field"} for k in sorted(extra)], "給藥紀錄內容有誤")
    values = _medication_values(body, cycle)
    record = MedicationRecord(patient_id=cycle.patient_id, cycle_id=cycle.id, cycle_day=_cycle_day(cycle, values["administered_at"]),
                              source=ObservationSource.NURSE, record_status=RecordStatus.FINAL, administered_by=user.id, **values)
    db.session.add(record)
    db.session.flush()
    return record


def _ensure_final(record):
    if record.record_status != RecordStatus.FINAL:
        _conflict("INVALID_STATE", "這筆紀錄已被更正或標示為錯誤，請對最新的紀錄操作")


def amend_medication(original, user, body):
    """Correction: a new final record (``amends_id`` → original); the original becomes ``amended``."""
    _ensure_final(original)
    f = Fields(body, "更正內容有誤")
    f.text("amend_reason", 500, required=True)
    f.unknown(("amend_reason", *MED_FIELDS))
    reason = f.done()["amend_reason"]
    cycle = original.cycle
    values = _medication_values({k: v for k, v in body.items() if k != "amend_reason"}, cycle, base=original)
    if all(getattr(original, k) == v for k, v in values.items()):
        _invalid([{"field": "amend_reason", "issue": "nothing changed: send the corrected fields"}], "更正內容有誤")
    record = MedicationRecord(patient_id=original.patient_id, cycle_id=cycle.id, cycle_day=_cycle_day(cycle, values["administered_at"]),
                              source=ObservationSource.NURSE, record_status=RecordStatus.FINAL, amends_id=original.id,
                              administered_by=original.administered_by, **values)
    original.record_status = RecordStatus.AMENDED
    db.session.add(record)
    db.session.flush()
    changed = sorted(k for k, v in values.items() if getattr(original, k) != v)
    return record, reason, changed


def mark_medication_error(record, body):
    _ensure_final(record)
    f = Fields(body, "標示錯誤資料有誤")
    f.text("reason", 500, required=True)
    f.unknown(("reason",))
    reason = f.done()["reason"]
    record.record_status = RecordStatus.ENTERED_IN_ERROR
    return reason
