"""Chemotherapy API (api-design.md §5; appointments are a later sprint).

Reads follow the patient's access rule (admin all, patient self, nurse only currently assigned
patients; else 404). Writes on a patient's plan / cycles / medications: the assigned nurse.
Drug and regimen master data: nurses and admins read, admins write.
"""

from flask import g, request

from app.core.api import APIError, ok
from app.core.audit import record_data_event, record_patient_view
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.core.idempotency import run_idempotent
from app.extensions import db
from app.models import ChemoRegimen, Drug, MedicationRecord
from app.models.enums import AuditAction, RoleName
from app.modules.chemotherapy import appointments as ap
from app.modules.chemotherapy import chemotherapy_bp
from app.modules.chemotherapy import services as s

STAFF = (RoleName.NURSE, RoleName.ADMIN)


def _body():
    body = request.get_json(silent=True)
    if body is None and not request.get_data():
        return {}
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    return body


def _patient_of(obj):
    ensure_can_view_patient(obj.patient)  # 404 for anyone outside the patient's scope
    return obj.patient


def _flag(name):
    return request.args.get(name) == "true"


# ------------------------------------------------------------------ drugs


@chemotherapy_bp.get("/drugs")
@require_auth(*STAFF)
def list_drugs():
    """Query: q (generic / brand name), include_inactive=true."""
    return ok([s.drug_payload(d) for d in s.list_drugs(q=request.args.get("q") or None, include_inactive=_flag("include_inactive"))])


@chemotherapy_bp.post("/drugs")
@require_auth(RoleName.ADMIN)
def create_drug():
    drug = s.create_drug(_body())
    record_data_event(AuditAction.CREATE, "drugs", drug.id, changes={"generic_name": drug.generic_name})
    db.session.commit()
    return ok(s.drug_payload(drug), status=201)


@chemotherapy_bp.patch("/drugs/<int:drug_id>")
@require_auth(RoleName.ADMIN)
def update_drug(drug_id):
    drug = db.session.get(Drug, drug_id) or _missing("Drug")
    changed = s.update_drug(drug, _body())
    if changed:
        record_data_event(AuditAction.UPDATE, "drugs", drug.id, changes={"fields": changed})
    db.session.commit()
    return ok(s.drug_payload(drug))


def _missing(name):
    raise APIError(404, "NOT_FOUND", f"{name} not found")


# ------------------------------------------------------------------ regimens


@chemotherapy_bp.get("/regimens")
@require_auth(*STAFF)
def list_regimens():
    return ok([s.regimen_payload(r) for r in s.list_regimens(include_inactive=_flag("include_inactive"))])


@chemotherapy_bp.get("/regimens/<int:regimen_id>")
@require_auth(*STAFF)
def get_regimen(regimen_id):
    return ok(s.regimen_payload(db.session.get(ChemoRegimen, regimen_id) or _missing("Regimen")))


@chemotherapy_bp.post("/regimens")
@require_auth(RoleName.ADMIN)
def create_regimen():
    regimen = s.create_regimen(_body())
    record_data_event(AuditAction.CREATE, "chemo_regimens", regimen.id,
                      changes={"name": regimen.name, "drug_ids": [rd.drug_id for rd in regimen.regimen_drugs]})
    db.session.commit()
    return ok(s.regimen_payload(regimen), status=201)


@chemotherapy_bp.patch("/regimens/<int:regimen_id>")
@require_auth(RoleName.ADMIN)
def update_regimen(regimen_id):
    regimen = db.session.get(ChemoRegimen, regimen_id) or _missing("Regimen")
    changed = s.update_regimen(regimen, _body())
    if changed:
        record_data_event(AuditAction.UPDATE, "chemo_regimens", regimen.id, changes={"fields": changed})
    db.session.commit()
    return ok(s.regimen_payload(regimen))


# ------------------------------------------------------------------ plans


@chemotherapy_bp.get("/plans")
@require_auth()
def list_plans():
    """``?patient_id=`` (required; ``me`` for patients). Newest first, with cycles."""
    patient_id = request.args.get("patient_id")
    if not patient_id:
        raise APIError(400, "VALIDATION_ERROR", "patient_id is required", [{"field": "patient_id", "issue": "is required"}])
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    plans = s.list_plans(patient)
    record_patient_view(patient, resource_type="chemotherapy_plans")
    db.session.commit()
    return ok([s.plan_payload(p, g.current_user) for p in plans])


@chemotherapy_bp.post("/plans")
@require_auth(RoleName.NURSE)
def create_plan():
    """New plan for an assigned patient; ``generate_cycles`` (default true) schedules every cycle
    from ``start_date`` by the regimen's cycle length."""
    body = _body()
    patient_id = body.get("patient_id")
    if not isinstance(patient_id, str) or not patient_id:
        raise APIError(400, "VALIDATION_ERROR", "療程資料有誤", [{"field": "patient_id", "issue": "is required"}])
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    plan = s.create_plan(patient, g.current_user, body)
    appointments = [a for c in s._cycles(plan) for a in c.appointments]
    record_data_event(AuditAction.CREATE, "chemotherapy_plans", plan.id, patient=patient,
                      changes={"regimen_id": plan.regimen_id, "total_cycles": plan.total_cycles,
                               "generated_cycles": len(s._cycles(plan)), "appointment_ids": [a.id for a in appointments]})
    for a in appointments:
        record_data_event(AuditAction.CREATE, "appointments", a.id, patient=patient,
                          changes={"appointment_type": a.appointment_type, "scheduled_at": a.scheduled_at.isoformat(),
                                   "cycle_id": a.cycle_id, "plan_id": plan.id})
    db.session.commit()
    return ok(s.plan_payload(plan, g.current_user), status=201)


@chemotherapy_bp.get("/plans/<int:plan_id>")
@require_auth()
def get_plan(plan_id):
    plan = s.get_plan(plan_id)
    patient = _patient_of(plan)
    record_patient_view(patient, resource_type="chemotherapy_plans", resource_id=str(plan.id))
    db.session.commit()
    return ok(s.plan_payload(plan, g.current_user))


@chemotherapy_bp.patch("/plans/<int:plan_id>")
@require_auth(RoleName.NURSE)
def update_plan(plan_id):
    plan = s.get_plan(plan_id)
    patient = _patient_of(plan)
    changed = s.update_plan(plan, _body())
    if changed:
        record_data_event(AuditAction.UPDATE, "chemotherapy_plans", plan.id, patient=patient, changes={"fields": changed})
    db.session.commit()
    return ok(s.plan_payload(plan, g.current_user))


@chemotherapy_bp.post("/plans/<int:plan_id>/discontinue")
@require_auth(RoleName.NURSE)
def discontinue_plan(plan_id):
    plan = s.get_plan(plan_id)
    patient = _patient_of(plan)
    s.discontinue_plan(plan, _body())
    record_data_event(AuditAction.UPDATE, "chemotherapy_plans", plan.id, patient=patient,
                      changes={"status": plan.status, "discontinue_reason": plan.discontinue_reason})
    db.session.commit()
    return ok(s.plan_payload(plan, g.current_user))


# ------------------------------------------------------------------ cycles


@chemotherapy_bp.get("/plans/<int:plan_id>/cycles")
@require_auth()
def list_cycles(plan_id):
    plan = s.get_plan(plan_id)
    patient = _patient_of(plan)
    today = s.local_today(patient)
    return ok([s.cycle_payload(c, g.current_user, today) for c in s._cycles(plan)])


@chemotherapy_bp.post("/plans/<int:plan_id>/cycles")
@require_auth(RoleName.NURSE)
def add_cycle(plan_id):
    plan = s.get_plan(plan_id)
    patient = _patient_of(plan)
    cycle = s.add_cycle(plan, _body())
    record_data_event(AuditAction.CREATE, "chemotherapy_cycles", cycle.id, patient=patient,
                      changes={"plan_id": plan.id, "cycle_number": cycle.cycle_number})
    db.session.commit()
    return ok(s.cycle_payload(cycle, g.current_user, s.local_today(patient)), status=201)


@chemotherapy_bp.get("/cycles/<int:cycle_id>")
@require_auth()
def get_cycle(cycle_id):
    cycle = s.get_cycle(cycle_id)
    patient = _patient_of(cycle)
    return ok(s.cycle_payload(cycle, g.current_user, s.local_today(patient)))


@chemotherapy_bp.patch("/cycles/<int:cycle_id>")
@require_auth(RoleName.NURSE)
def update_cycle(cycle_id):
    cycle = s.get_cycle(cycle_id)
    patient = _patient_of(cycle)
    changed = s.update_cycle(cycle, _body())
    if changed:
        record_data_event(AuditAction.UPDATE, "chemotherapy_cycles", cycle.id, patient=patient, changes={"fields": changed})
    db.session.commit()
    return ok(s.cycle_payload(cycle, g.current_user, s.local_today(patient)))


def _transition(cycle_id, action):
    cycle = s.get_cycle(cycle_id)
    patient = _patient_of(cycle)
    body = _body()
    if action == "start":
        changes = {"transition": "start", "actual_start_date": s.start_cycle(cycle, body).isoformat()}
    elif action == "complete":
        changes = {"transition": "complete", "actual_end_date": s.complete_cycle(cycle, body).isoformat(),
                   "plan_status": cycle.plan.status}
    else:
        days, moved = s.delay_cycle(cycle, body, g.current_user)
        changes = {"transition": "delay", "delay_days": days, "scheduled_date": cycle.scheduled_date.isoformat(),
                   "delay_reason": cycle.delay_reason, "rescheduled_appointments": [[old.id, new.id] for old, new in moved]}
        for old, new in moved:
            record_data_event(AuditAction.UPDATE, "appointments", old.id, patient=patient,
                              changes={"status": old.status, "rescheduled_to_id": new.id, "reason": "cycle delayed"})
            record_data_event(AuditAction.CREATE, "appointments", new.id, patient=patient,
                              changes={"rescheduled_from_id": old.id, "scheduled_at": new.scheduled_at.isoformat()})
    record_data_event(AuditAction.UPDATE, "chemotherapy_cycles", cycle.id, patient=patient, changes=changes)
    db.session.commit()
    return ok(s.cycle_payload(cycle, g.current_user, s.local_today(patient)))


@chemotherapy_bp.post("/cycles/<int:cycle_id>/start")
@require_auth(RoleName.NURSE)
def start_cycle(cycle_id):
    """Day 1 = ``start_date`` (default today). Earlier records keep their cycle_id / cycle_day."""
    return _transition(cycle_id, "start")


@chemotherapy_bp.post("/cycles/<int:cycle_id>/complete")
@require_auth(RoleName.NURSE)
def complete_cycle(cycle_id):
    return _transition(cycle_id, "complete")


@chemotherapy_bp.post("/cycles/<int:cycle_id>/delay")
@require_auth(RoleName.NURSE)
def delay_cycle(cycle_id):
    """``{new_scheduled_date, delay_reason}`` for a cycle not yet started."""
    return _transition(cycle_id, "delay")


# ------------------------------------------------------------------ medication records


@chemotherapy_bp.get("/cycles/<int:cycle_id>/medications")
@require_auth()
def list_cycle_medications(cycle_id):
    cycle = s.get_cycle(cycle_id)
    _patient_of(cycle)
    return ok([s.medication_payload(m, g.current_user) for m in s.list_medications(g.current_user, cycle=cycle)])


@chemotherapy_bp.get("/medications")
@require_auth()
def list_patient_medications():
    """``?patient_id=`` — every cycle's records, newest first (staff: incl. corrected / erroneous)."""
    patient_id = request.args.get("patient_id")
    if not patient_id:
        raise APIError(400, "VALIDATION_ERROR", "patient_id is required", [{"field": "patient_id", "issue": "is required"}])
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    record_patient_view(patient, resource_type="medication_records")
    db.session.commit()
    return ok([s.medication_payload(m, g.current_user) for m in s.list_medications(g.current_user, patient=patient)])


def _replay_medication(resource_id):
    return {"data": s.medication_payload(db.session.get(MedicationRecord, int(resource_id)), g.current_user)}


@chemotherapy_bp.post("/cycles/<int:cycle_id>/medications")
@require_auth(RoleName.NURSE)
def create_medication(cycle_id):
    """Record an administration. Idempotency-Key: a retried submission never adds a second row."""
    cycle = s.get_cycle(cycle_id)
    patient = _patient_of(cycle)
    body = _body()

    def do_create():
        record = s.create_medication(cycle, g.current_user, body)
        record_data_event(AuditAction.CREATE, "medication_records", record.id, patient=patient,
                          changes={"cycle_id": cycle.id, "drug_id": record.drug_id,
                                   "administration_status": record.administration_status})
        return record.id, {"data": s.medication_payload(record, g.current_user)}, 201

    return run_idempotent(resource_type="medication_records", body=body, create=do_create, replay=_replay_medication, required=False)


@chemotherapy_bp.post("/medications/<int:record_id>/amend")
@require_auth(RoleName.NURSE)
def amend_medication(record_id):
    """``{amend_reason, …corrected fields}`` → a new record (``amends_id``); the original becomes ``amended``."""
    original = s.get_medication(record_id)
    patient = _patient_of(original)
    body = _body()

    def do_amend():
        record, reason, changed = s.amend_medication(original, g.current_user, body)
        record_data_event(AuditAction.AMEND, "medication_records", original.id, patient=patient,
                          changes={"new_record_id": record.id, "amend_reason": reason, "fields": changed})
        return record.id, {"data": s.medication_payload(record, g.current_user)}, 201

    return run_idempotent(resource_type="medication_records", body=body, create=do_amend, replay=_replay_medication, required=False)


@chemotherapy_bp.post("/medications/<int:record_id>/mark-error")
@require_auth(RoleName.NURSE)
def mark_medication_error(record_id):
    """``{reason}`` → ``entered_in_error`` (kept, no longer counted or shown to the patient)."""
    record = s.get_medication(record_id)
    patient = _patient_of(record)
    reason = s.mark_medication_error(record, _body())
    record_data_event(AuditAction.MARK_ERROR, "medication_records", record.id, patient=patient, changes={"reason": reason})
    db.session.commit()
    return ok(s.medication_payload(record, g.current_user))


# ------------------------------------------------------------------ appointments (treatment schedule)


@chemotherapy_bp.get("/appointments")
@require_auth()
def list_appointments():
    """``?patient_id=`` (required; ``me``) and ``date`` or ``from`` / ``to`` (local dates), ``status``."""
    patient_id = request.args.get("patient_id")
    if not patient_id:
        raise APIError(400, "VALIDATION_ERROR", "patient_id is required", [{"field": "patient_id", "issue": "is required"}])
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    rows = ap.list_appointments(patient, request.args)
    return ok([ap.appointment_payload(a, g.current_user) for a in rows], meta={"timezone": patient.timezone})


@chemotherapy_bp.post("/appointments")
@require_auth(RoleName.NURSE)
def create_appointment():
    body = _body()
    patient_id = body.get("patient_id")
    if not isinstance(patient_id, str) or not patient_id:
        raise APIError(400, "VALIDATION_ERROR", "行程資料有誤", [{"field": "patient_id", "issue": "is required"}])
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    a = ap.create_appointment(patient, g.current_user, body)
    record_data_event(AuditAction.CREATE, "appointments", a.id, patient=patient,
                      changes={"appointment_type": a.appointment_type, "scheduled_at": a.scheduled_at.isoformat(), "cycle_id": a.cycle_id})
    db.session.commit()
    return ok(ap.appointment_payload(a, g.current_user), status=201)


@chemotherapy_bp.get("/appointments/<int:appointment_id>")
@require_auth()
def get_appointment(appointment_id):
    a = ap.get_appointment(appointment_id)
    _patient_of(a)
    return ok(ap.appointment_payload(a, g.current_user))


@chemotherapy_bp.patch("/appointments/<int:appointment_id>")
@require_auth(RoleName.NURSE)
def update_appointment(appointment_id):
    """Details and preparation steps (``instructions`` replaces the list). Time: use reschedule."""
    a = ap.get_appointment(appointment_id)
    patient = _patient_of(a)
    changed = ap.update_appointment(a, _body())
    if changed:
        record_data_event(AuditAction.UPDATE, "appointments", a.id, patient=patient, changes={"fields": changed})
    db.session.commit()
    return ok(ap.appointment_payload(a, g.current_user))


def _appointment_step(appointment_id, step):
    a = ap.get_appointment(appointment_id)
    patient = _patient_of(a)
    body = _body()
    changes = {"status": None}
    if step == "check-in":
        ap.check_in(a)
    elif step == "complete":
        ap.complete(a)
    else:
        changes["reason"] = ap.cancel(a, body)
    changes["status"] = a.status
    record_data_event(AuditAction.UPDATE, "appointments", a.id, patient=patient, changes=changes)
    db.session.commit()
    return ok(ap.appointment_payload(a, g.current_user))


@chemotherapy_bp.post("/appointments/<int:appointment_id>/check-in")
@require_auth(RoleName.NURSE)
def check_in_appointment(appointment_id):
    return _appointment_step(appointment_id, "check-in")


@chemotherapy_bp.post("/appointments/<int:appointment_id>/complete")
@require_auth(RoleName.NURSE)
def complete_appointment(appointment_id):
    return _appointment_step(appointment_id, "complete")


@chemotherapy_bp.post("/appointments/<int:appointment_id>/cancel")
@require_auth(RoleName.NURSE)
def cancel_appointment(appointment_id):
    """``{reason}`` (required)."""
    return _appointment_step(appointment_id, "cancel")


@chemotherapy_bp.post("/appointments/<int:appointment_id>/reschedule")
@require_auth(RoleName.NURSE)
def reschedule_appointment(appointment_id):
    """``{scheduled_at, reason}`` → 201 with the new appointment; the original becomes ``rescheduled``."""
    a = ap.get_appointment(appointment_id)
    patient = _patient_of(a)
    new, reason = ap.reschedule(a, g.current_user, _body())
    record_data_event(AuditAction.UPDATE, "appointments", a.id, patient=patient,
                      changes={"status": a.status, "rescheduled_to_id": new.id, "reason": reason})
    record_data_event(AuditAction.CREATE, "appointments", new.id, patient=patient,
                      changes={"rescheduled_from_id": a.id, "scheduled_at": new.scheduled_at.isoformat()})
    db.session.commit()
    return ok(ap.appointment_payload(new, g.current_user), status=201)
