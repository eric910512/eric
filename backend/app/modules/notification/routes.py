from flask import g, request

from sqlalchemy import select

from app.core.api import APIError, ok, query_choice, query_int, required_text
from app.core.audit import record_data_event
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.core.idempotency import run_idempotent
from app.extensions import db
from app.models.enums import AlertSeverity, AuditAction, NotificationType, RoleName
from app.models import AlertRule, Notification
from app.modules.admin import services as admin_services
from app.modules.notification import notification_bp
from app.modules.notification import delivery, reminders
from app.modules.notification.services import (
    STATUS_FILTERS,
    get_own_notification,
    get_visible_notification,
    list_notifications,
    mark_read,
    notification_detail,
    notification_payload,
    origin,
    read_all,
    resolve_event,
    transition,
    unread_count,
)

MAX_RESOLUTION_NOTE = 1000


@notification_bp.get("")
@require_auth()
def index():
    """Notifications visible to the signed-in user.

    Patient: own notifications. Nurse: events of assigned patients (one row per event).
    Admin: every patient event.

    Query: status=all|open|pending|new|acknowledged|in_progress|resolved (``unresolved`` = open,
    ``pending`` = new or acknowledged),
    priority (= severity) critical|warning|info, patient_id (public id), type, is_read,
    page, per_page (≤100). ``meta.counts`` holds the number of events per status.
    """
    args = request.args
    is_read_raw = query_choice(args, "is_read", "any", ("any", "true", "false"))
    severity = query_choice(args, "priority", "", ("",) + AlertSeverity.ALL) or query_choice(
        args, "severity", "", ("",) + AlertSeverity.ALL
    )
    items, meta = list_notifications(
        g.current_user,
        status=query_choice(args, "status", "all", STATUS_FILTERS),
        is_read=None if is_read_raw == "any" else is_read_raw == "true",
        type_=query_choice(args, "type", "", ("",) + NotificationType.ALL) or None,
        severity=severity or None,
        patient_public_id=args.get("patient_id") or None,
        page=query_int(args, "page", 1, 1, 10_000),
        per_page=query_int(args, "per_page", 20, 1, 100),
    )
    return ok([notification_payload(n, g.current_user) for n in items], meta=meta)


@notification_bp.post("")
@require_auth(RoleName.NURSE, RoleName.ADMIN)
def create_reminder():
    """Manual / scheduled reminder to a patient: ``{patient_id, title, message, severity?: info|warning,
    scheduled_for?, category?, email_mode?: none|summary|full}``. Nurse: currently assigned patients only (others 404). Optional
    ``Idempotency-Key``. The patient sees it once ``scheduled_for`` has passed (or at once).

    Email (second channel, delivery.py): the notification is committed first, then a summary email
    is sent when the patient's contact email is verified and email notifications are on.
    ``email_delivery`` in the response reports it (sent / failed / skipped + reason); an email
    failure never fails this request or changes the notification. A replay never re-sends."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    raw = body.get("patient_id")
    if not isinstance(raw, str) or not raw:
        raise APIError(400, "VALIDATION_ERROR", "提醒內容有誤", [{"field": "patient_id", "issue": "is required"}])
    patient = resolve_patient(raw)
    ensure_can_view_patient(patient)
    user = g.current_user

    planned = []

    def create():
        n, email_options = reminders.create_reminder(patient, body)
        email = delivery.plan_email(n, patient, **email_options)  # content policy applied here, not in the client
        record_data_event(AuditAction.CREATE, "notifications", n.id, patient=patient,
                          changes={"type": n.type, "origin": origin(n), "event_key": n.event_key, "severity": n.severity,
                                   "title": n.title, "scheduled_for": n.scheduled_for.isoformat() + "Z" if n.scheduled_for else None,
                                   "email_delivery": {"status": email.status, "skip_reason": email.skip_reason,
                                                      "category": email.content_category, "mode_requested": email.email_mode_requested,
                                                      "mode": email.email_mode,
                                                      "downgraded": delivery.delivery_payload(email)["downgraded"]}})
        planned.append(email)
        return n.id, {"data": notification_payload(n, user)}, 201

    def replay(nid):
        return {"data": notification_payload(db.session.get(Notification, nid), user)}

    response = run_idempotent(resource_type="notifications", body=body, create=create, replay=replay, required=False)
    if planned and delivery.dispatch(planned[0]):  # after the commit: the notification already exists
        return ok(notification_payload(planned[0].notification, user), status=201)
    return response


@notification_bp.get("/scheduled")
@require_auth(RoleName.NURSE, RoleName.ADMIN)
def scheduled():
    """Reminders of one patient that are not due yet (``?patient_id=``), soonest first. Staff only;
    patients never see them before their time."""
    raw = request.args.get("patient_id")
    if not raw:
        raise APIError(400, "VALIDATION_ERROR", "patient_id is required", [{"field": "patient_id", "issue": "is required"}])
    patient = resolve_patient(raw)
    ensure_can_view_patient(patient)
    return ok([reminders.scheduled_payload(n) for n in reminders.scheduled_reminders(patient)])


@notification_bp.get("/unread-count")
@require_auth()
def unread():
    """``{unread}`` — own unread notifications visible now (for the badge; polled by the app)."""
    return ok({"unread": unread_count(g.current_user)})


@notification_bp.post("/read-all")
@require_auth()
def read_all_route():
    """Mark all own visible notifications read. Returns ``{updated}``."""
    ids = read_all(g.current_user)
    if ids:
        user = g.current_user
        record_data_event(AuditAction.UPDATE, "notifications", user.public_id, patient=user.patient_profile,
                          changes={"read_all": True, "notification_ids": ids})
        db.session.commit()
    return ok({"updated": len(ids), "unread": 0})


@notification_bp.get("/<int:notification_id>")
@require_auth()
def show(notification_id):
    """Detail. Staff: patient context, trigger, source record, recommended action, handling
    timeline and allowed actions. Patient: the reminder and its handling status only."""
    n = get_visible_notification(g.current_user, notification_id)
    return ok(notification_detail(n, g.current_user))


def _lifecycle(notification_id, action):
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    note = required_text(body, "resolution_note", MAX_RESOLUTION_NOTE) if action == "resolve" else None

    user = g.current_user
    n = get_visible_notification(user, notification_id)  # nurse: assigned patients only (404 otherwise)
    old, rows = transition(n, user, action, note)
    record_data_event(
        AuditAction.ACKNOWLEDGE if action == "acknowledge" else AuditAction.UPDATE,
        "notifications", n.id, patient=n.patient,
        changes={
            "status": {"old": old, "new": n.status},
            "event_key": n.event_key,
            "notification_ids": [row.id for row in rows],
        },
    )
    db.session.commit()
    return ok(notification_detail(n, user))


@notification_bp.post("/<int:notification_id>/acknowledge")
@require_auth(RoleName.NURSE, RoleName.ADMIN)
def acknowledge(notification_id):
    """new → acknowledged: a nurse takes the alert over."""
    return _lifecycle(notification_id, "acknowledge")


@notification_bp.post("/<int:notification_id>/start")
@require_auth(RoleName.NURSE, RoleName.ADMIN)
def start(notification_id):
    """acknowledged → in_progress."""
    return _lifecycle(notification_id, "start")


@notification_bp.post("/<int:notification_id>/resolve")
@require_auth(RoleName.NURSE, RoleName.ADMIN)
def resolve(notification_id):
    """in_progress → resolved. Body: ``{resolution_note}`` (internal, never shown to the patient)."""
    return _lifecycle(notification_id, "resolve")


@notification_bp.patch("/<int:notification_id>/read")
@require_auth()
def read(notification_id):
    n = get_own_notification(g.current_user, notification_id)
    if mark_read(n):
        record_data_event(
            AuditAction.UPDATE, "notifications", n.id, patient=n.patient,
            changes={"is_read": {"old": False, "new": True}},
        )
        db.session.commit()
    return ok(notification_payload(n, g.current_user))


@notification_bp.patch("/<int:notification_id>/resolve")
@require_auth(RoleName.NURSE)
def quick_resolve(notification_id):
    """Quick resolve from any open status (vital-sign / lab panels, earlier clients).
    Fills any lifecycle step not yet taken with the same nurse; all copies of the event are
    closed together with the same note.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    note = required_text(body, "resolution_note", MAX_RESOLUTION_NOTE)

    n = get_own_notification(g.current_user, notification_id)
    if n.patient is not None:
        ensure_can_view_patient(n.patient)  # assignment may have ended since the alert was sent
    old = n.status
    updated = resolve_event(n, g.current_user, note)
    record_data_event(
        AuditAction.ACKNOWLEDGE, "notifications", n.id, patient=n.patient,
        changes={
            "status": {"old": old, "new": n.status},
            "event_key": n.event_key,
            "notification_ids": [row.id for row in updated],
        },
    )
    db.session.commit()
    return ok({**notification_payload(n, g.current_user), "related_notifications_updated": len(updated) - 1})


# ------------------------------------------------------------------ alert rules (admin, Sprint 7)



@notification_bp.get("/alert-rules")
@require_auth(RoleName.ADMIN)
def list_alert_rules():
    """Every risk rule the engine uses (active and inactive)."""
    rows = db.session.execute(select(AlertRule).order_by(AlertRule.source_type, AlertRule.code)).scalars().all()
    return ok([admin_services.rule_payload(r) for r in rows])


@notification_bp.post("/alert-rules")
@require_auth(RoleName.ADMIN)
def create_alert_rule():
    """New rule: {code, name, source_type: vital_sign | symptom | lab, vital_field | symptom_code | test_code,
    operator, threshold_value, severity, extra_conditions?, message_template?, notify_*?, cooldown_minutes?}."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    r = admin_services.create_rule(body)
    record_data_event(AuditAction.CREATE, "alert_rules", r.id, changes={"code": r.code, "rule": admin_services.rule_payload(r)})
    db.session.commit()
    return ok(admin_services.rule_payload(r), status=201)


@notification_bp.get("/alert-rules/<int:rule_id>")
@require_auth(RoleName.ADMIN)
def get_alert_rule(rule_id):
    return ok(admin_services.rule_payload(admin_services.get_rule(rule_id)))


@notification_bp.patch("/alert-rules/<int:rule_id>")
@require_auth(RoleName.ADMIN)
def update_alert_rule(rule_id):
    """Change or disable a rule (existing alerts keep their state; new records use the new rule)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise APIError(400, "VALIDATION_ERROR", "Request body must be a JSON object")
    r = admin_services.get_rule(rule_id)
    before = admin_services.rule_payload(r)
    changed = admin_services.update_rule(r, body)
    if changed:
        after = admin_services.rule_payload(r)
        record_data_event(AuditAction.UPDATE, "alert_rules", r.id,
                          changes={"code": r.code, "fields": changed, "old": {k: before[k] for k in changed if k in before},
                                   "new": {k: after[k] for k in changed if k in after}})
    db.session.commit()
    return ok(admin_services.rule_payload(r))


@notification_bp.post("/alert-rules/<int:rule_id>/test")
@require_auth(RoleName.ADMIN)
def test_alert_rule(rule_id):
    """Dry run with ``{value, in_nadir?}`` — nothing is created or sent."""
    body = request.get_json(silent=True) or {}
    return ok(admin_services.test_rule(admin_services.get_rule(rule_id), body))
