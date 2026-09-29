from flask import g, request

from app.core.api import APIError, ok, query_choice
from app.core.audit import record_patient_view
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.extensions import db
from app.models.enums import RoleName
from app.modules.dashboard import dashboard_bp
from app.modules.dashboard.layouts import patient_home_layout
from app.modules.dashboard.services import build_nurse_caseload, build_patient_dashboard

TREND_DAYS_DEFAULT = 14
TREND_DAYS_MAX = 90


@dashboard_bp.get("/patient/<patient_id>")
@require_auth()
def patient_dashboard(patient_id):
    """All Phase 1 dashboard widgets for one patient in a single response.

    ``patient_id`` is the patient's public_id (UUID) or ``me`` (patient accounts).
    Query: ``trend_days`` (1–90, default 14) for the symptom trend window.
    """
    raw = request.args.get("trend_days", str(TREND_DAYS_DEFAULT))
    trend_days = int(raw) if raw.isdigit() else None
    if trend_days is None or not 1 <= trend_days <= TREND_DAYS_MAX:
        raise APIError(
            400,
            "VALIDATION_ERROR",
            "Invalid query parameter",
            [{"field": "trend_days", "issue": f"must be an integer between 1 and {TREND_DAYS_MAX}"}],
        )

    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)

    data = build_patient_dashboard(patient, trend_days, viewer_role=g.current_user.role_name)

    record_patient_view(patient, resource_type="dashboard")
    db.session.commit()
    return ok(data, meta={"timezone": patient.timezone})


@dashboard_bp.get("/widgets/caseload/data")
@require_auth(RoleName.NURSE)
def caseload():
    """caseload widget (api-design.md §8.2): the signed-in nurse's assigned patients.
    Query: sort=risk (default, highest risk first) | last_report | name.
    """
    sort = query_choice(request.args, "sort", "risk", ("risk", "last_report", "name"))
    items, meta = build_nurse_caseload(g.current_user, sort)
    return ok(items, meta=meta)


@dashboard_bp.get("/widgets/today-appointments/data")
@require_auth(RoleName.NURSE)
def today_appointments():
    """today-appointments widget (api-design.md §8.2): today's appointments of the nurse's current
    patients, soonest first (cancelled / rescheduled excluded)."""
    from app.modules.chemotherapy.appointments import nurse_today
    items = nurse_today(g.current_user)
    return ok(items, meta={"total": len(items)})


@dashboard_bp.get("/widgets/pending-symptom-reviews/data")
@require_auth(RoleName.NURSE)
def pending_symptom_reviews():
    """pending-symptom-reviews widget (api-design.md §8.2): submitted (not yet reviewed) reports of
    the nurse's current patients, oldest first (the longest waiting at the top)."""
    from app.modules.dashboard.services import build_pending_reviews
    items = build_pending_reviews(g.current_user)
    return ok(items, meta={"total": len(items)})


@dashboard_bp.get("/layout")
@require_auth()
def layout():
    """Dashboard layout JSON (api-design.md §8.1). Query: ``context=overview|patient``
    (default overview), ``patient_id`` (public id or ``me``, with ``context=patient``).

    Phase 1 serves the patient home layout — the only screen rendered from a layout. Nurse and
    admin screens are fixed views, so their layouts are 404 until Phase 2.
    """
    context = query_choice(request.args, "context", "overview", ("overview", "patient"))
    if g.current_user.role_name != RoleName.PATIENT:
        raise APIError(404, "NOT_FOUND", "此角色目前沒有可設定的版面（護理端與管理端畫面為固定版面）")
    if context == "patient" and request.args.get("patient_id"):
        ensure_can_view_patient(resolve_patient(request.args["patient_id"]))  # a patient: only their own
    return ok(patient_home_layout(), meta={"role": RoleName.PATIENT, "context": context})
