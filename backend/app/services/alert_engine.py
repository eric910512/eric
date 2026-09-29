"""Alert rule evaluation → notifications (database-design.md §6.K, api-design.md §10).

One engine for every observation source. A source adapter says how a rule reads a value
from a record; matching, conditions, cooldown, notification fan-out and replay are shared.

Supported sources (Phase 1):
- ``symptom``    → symptom_records; compares the answer's normalized ``score``
- ``vital_sign`` → vital_signs; compares the column named by ``alert_rules.vital_field``
- ``lab``        → lab_results; compares ``value_numeric`` of the rule's ``lab_test_type_id``

``extra_conditions``: ``within_nadir``, ``outside_nadir``, ``consecutive_records``,
``value_above`` (value must also be > X — lets a warning rule stop where a critical rule starts).
"""

import operator
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.extensions import db
from app.models import (
    AlertRule,
    LabResult,
    Notification,
    NursePatientAssignment,
    SymptomRecord,
    SymptomRecordValue,
    VitalSign,
)
from app.models.base import utcnow
from app.models.enums import AlertSeverity, AlertSourceType, NotificationType, RecordStatus
from app.services.labs import PATIENT_LABELS
from app.services.vitals import VITAL_FIELDS, format_value

OPERATORS = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
}


@dataclass
class TriggeredAlert:
    rule: AlertRule
    code: str  # symptom definition code, vital field or lab test code
    label: str  # 疼痛 / 體溫 …
    score: Decimal
    value_text: str  # "8" / "38.4°C" / "" for yes-no answers
    notified: bool
    patient_message: str


@dataclass
class _Match:
    rule: AlertRule
    code: str
    label: str
    score: Decimal
    value_text: str
    is_boolean: bool


def _matches(rule, score):
    compare = OPERATORS.get(rule.operator)
    return compare is not None and score is not None and compare(Decimal(score), Decimal(rule.threshold_value))


# ------------------------------------------------------------------ source adapters


class _SymptomSource:
    source_type = AlertSourceType.SYMPTOM
    table = "symptom_records"

    def __init__(self, record):
        self.record = record
        self.values = {v.definition_id: v for v in record.values}

    def value_for(self, rule):
        value = self.values.get(rule.symptom_definition_id)
        if value is None:
            return None
        d = value.definition
        is_boolean = d.value_type == "boolean"
        text = "" if is_boolean else f"{float(value.score):g}" if value.score is not None else ""
        return value.score, d.code, d.name_zh, text, is_boolean

    def recent_scores(self, rule, needed):
        return db.session.execute(
            select(SymptomRecordValue.score)
            .join(SymptomRecord, SymptomRecordValue.symptom_record_id == SymptomRecord.id)
            .where(
                SymptomRecord.patient_id == self.record.patient_id,
                SymptomRecord.record_status == RecordStatus.FINAL,
                SymptomRecordValue.definition_id == rule.symptom_definition_id,
                SymptomRecord.recorded_at <= self.record.recorded_at,
            )
            .order_by(SymptomRecord.recorded_at.desc(), SymptomRecord.id.desc())
            .limit(needed)
        ).scalars().all()


class _VitalSource:
    source_type = AlertSourceType.VITAL_SIGN
    table = "vital_signs"

    def __init__(self, record):
        self.record = record

    def value_for(self, rule):
        field = rule.vital_field
        if field not in VITAL_FIELDS:
            return None
        value = getattr(self.record, field)
        if value is None:
            return None
        return Decimal(value), field, VITAL_FIELDS[field].label, format_value(field, value), False

    def recent_scores(self, rule, needed):
        column = getattr(VitalSign, rule.vital_field)
        return db.session.execute(
            select(column)
            .where(
                VitalSign.patient_id == self.record.patient_id,
                VitalSign.record_status == RecordStatus.FINAL,
                column.is_not(None),
                VitalSign.measured_at <= self.record.measured_at,
            )
            .order_by(VitalSign.measured_at.desc(), VitalSign.id.desc())
            .limit(needed)
        ).scalars().all()


class _LabSource:
    source_type = AlertSourceType.LAB
    table = "lab_results"

    def __init__(self, record):
        self.record = record

    def value_for(self, rule):
        r = self.record
        if rule.lab_test_type_id != r.lab_test_type_id or r.value_numeric is None:
            return None
        code = r.test_type.code
        text = f"{float(r.value_numeric):g} {r.unit or ''}".strip()
        return Decimal(r.value_numeric), code, PATIENT_LABELS.get(code, r.test_type.name_zh), text, False

    def recent_scores(self, rule, needed):
        return db.session.execute(
            select(LabResult.value_numeric)
            .where(
                LabResult.patient_id == self.record.patient_id,
                LabResult.record_status == RecordStatus.FINAL,
                LabResult.lab_test_type_id == rule.lab_test_type_id,
                LabResult.value_numeric.is_not(None),
                LabResult.collected_at <= self.record.collected_at,
            )
            .order_by(LabResult.collected_at.desc(), LabResult.id.desc())
            .limit(needed)
        ).scalars().all()


# ------------------------------------------------------------------ shared engine


def _in_cooldown(rule, patient_id, now):
    if not rule.cooldown_minutes:
        return False
    since = now - timedelta(minutes=rule.cooldown_minutes)
    return db.session.execute(
        select(Notification.id).where(
            Notification.patient_id == patient_id,
            Notification.alert_rule_id == rule.id,
            Notification.created_at >= since,
        ).limit(1)
    ).first() is not None


def _patient_message(m, severity):
    if m.is_boolean:
        base = f"您回報了「{m.label}」，護理團隊已收到通知。"
    elif m.value_text.replace(".", "", 1).isdigit():  # symptom score (bare number)
        base = f"您的{m.label}程度偏高（{m.value_text} 分），護理團隊已收到通知，會與您聯繫。"
    else:  # vital sign with unit
        base = f"您的{m.label}為 {m.value_text}，護理團隊已收到通知，會與您聯繫。"
    if severity == AlertSeverity.CRITICAL:
        return base + "化療期間請不要等待，請立即撥打照護專線或前往急診。"
    return base + "若情況加重，請撥打照護專線。"


def _render(template, fields):
    if not template:
        return None
    return template.format_map(defaultdict(str, fields))


def _matching(source, *, in_nadir, cancer_type_ids):
    """Rules whose conditions the record meets. Pure: creates nothing."""
    rules = db.session.execute(
        select(AlertRule).filter_by(source_type=source.source_type, is_active=True)
    ).scalars().all()
    matches = []
    for rule in rules:
        found = source.value_for(rule)
        if found is None:
            continue
        score, code, label, text, is_boolean = found
        if not _matches(rule, score):
            continue
        if rule.cancer_type_id and rule.cancer_type_id not in cancer_type_ids:
            continue
        conditions = rule.extra_conditions or {}
        if conditions.get("within_nadir") and not in_nadir:
            continue
        if conditions.get("outside_nadir") and in_nadir:
            continue
        if conditions.get("value_above") is not None and not Decimal(score) > Decimal(str(conditions["value_above"])):
            continue
        needed = int(conditions.get("consecutive_records", 1) or 1)
        if needed > 1:
            scores = source.recent_scores(rule, needed)
            if len(scores) < needed or not all(_matches(rule, s) for s in scores):
                continue
        matches.append(_Match(rule, code, label, Decimal(score), text, is_boolean))
    return matches


def _event_key(source, rule):
    return f"{source.table}:{source.record.id}:rule:{rule.id}"


def _sorted(triggered):
    return sorted(triggered, key=lambda t: t.rule.severity != AlertSeverity.CRITICAL)


def _evaluate(source, patient, *, in_nadir, cancer_type_ids):
    matches = _matching(source, in_nadir=in_nadir, cancer_type_ids=cancer_type_ids)
    if not matches:
        return []
    record = source.record
    nurses = db.session.execute(
        select(NursePatientAssignment).filter_by(patient_id=patient.id, ended_at=None)
    ).scalars().all()
    now = utcnow()
    triggered = []

    for m in matches:
        rule = m.rule
        notified = not _in_cooldown(rule, patient.id, now)
        patient_message = _patient_message(m, rule.severity)
        if notified:
            fields = {
                "patient_name": patient.display_name,
                "patient_code": patient.patient_code,
                "symptom": m.label,
                "label": m.label,
                "value": m.value_text or f"{float(m.score):g}",
                "cycle_number": record.cycle.cycle_number if record.cycle else "",
                "cycle_day": record.cycle_day or "",
            }
            nurse_message = _render(rule.message_template, fields) or (
                f"{patient.display_name}（{patient.patient_code}）{m.label} {fields['value']}"
            )
            common = {
                "patient_id": patient.id,
                "alert_rule_id": rule.id,
                "event_key": _event_key(source, rule),
                "type": NotificationType.RISK_ALERT,
                "severity": rule.severity,
                "source_table": source.table,
                "source_id": record.id,
                "sent_at": now,
            }
            if rule.notify_patient and patient.user_id:
                db.session.add(Notification(recipient_id=patient.user_id, title=rule.name, message=patient_message, **common))
            if rule.notify_nurse:
                for assignment in nurses:
                    db.session.add(
                        Notification(recipient_id=assignment.nurse_id, title=rule.name, message=nurse_message, **common)
                    )
        triggered.append(TriggeredAlert(rule, m.code, m.label, m.score, m.value_text, notified, patient_message))
    return _sorted(triggered)


def _replay(source, *, in_nadir, cancer_type_ids):
    sent_keys = set(
        db.session.execute(
            select(Notification.event_key).filter_by(source_table=source.table, source_id=source.record.id)
        ).scalars()
    )
    return _sorted(
        TriggeredAlert(m.rule, m.code, m.label, m.score, m.value_text,
                       _event_key(source, m.rule) in sent_keys, _patient_message(m, m.rule.severity))
        for m in _matching(source, in_nadir=in_nadir, cancer_type_ids=cancer_type_ids)
    )


# ------------------------------------------------------------------ public API


def evaluate_symptom_record(record, patient, *, in_nadir, cancer_type_ids):
    """Evaluate symptom rules for a just-created (flushed) record and create notifications.

    Every matching rule is returned so the patient always gets the advice, even when a
    rule in cooldown sends no new notification.
    """
    return _evaluate(_SymptomSource(record), patient, in_nadir=in_nadir, cancer_type_ids=cancer_type_ids)


def replay_symptom_record(record, *, in_nadir, cancer_type_ids):
    """triggered alerts for an idempotent replay; nothing is sent again."""
    return _replay(_SymptomSource(record), in_nadir=in_nadir, cancer_type_ids=cancer_type_ids)


def evaluate_vital_sign(vital, patient, *, in_nadir, cancer_type_ids):
    """Evaluate vital-sign rules for a just-created (flushed) vital_signs row."""
    return _evaluate(_VitalSource(vital), patient, in_nadir=in_nadir, cancer_type_ids=cancer_type_ids)


def replay_vital_sign(vital, *, in_nadir, cancer_type_ids):
    return _replay(_VitalSource(vital), in_nadir=in_nadir, cancer_type_ids=cancer_type_ids)


def evaluate_lab_result(result, patient, *, in_nadir, cancer_type_ids):
    """Evaluate lab rules for a just-created (flushed) lab_results row."""
    return _evaluate(_LabSource(result), patient, in_nadir=in_nadir, cancer_type_ids=cancer_type_ids)


def replay_lab_result(result, *, in_nadir, cancer_type_ids):
    return _replay(_LabSource(result), in_nadir=in_nadir, cancer_type_ids=cancer_type_ids)


_SOURCES = {
    AlertSourceType.SYMPTOM: _SymptomSource,
    AlertSourceType.VITAL_SIGN: _VitalSource,
    AlertSourceType.LAB: _LabSource,
}


def describe_trigger(rule, record):
    """Why a rule fired, for staff: the value read from the source record and the condition.
    Read-only (used by the notification detail)."""
    label, value_text = None, None
    source_cls = _SOURCES.get(rule.source_type)
    if record is not None and source_cls is not None:
        found = source_cls(record).value_for(rule)
        if found is not None:
            score, _code, label, text, is_boolean = found
            value_text = ("是" if score else "否") if is_boolean else (text or f"{float(score):g}")
    if label is None:
        label = (
            rule.symptom_definition.name_zh if rule.symptom_definition
            else rule.lab_test_type.name_zh if rule.lab_test_type
            else VITAL_FIELDS[rule.vital_field].label if rule.vital_field in VITAL_FIELDS
            else rule.name
        )
    conditions = rule.extra_conditions or {}
    condition = f"{label} {rule.operator} {float(rule.threshold_value):g}"
    extras = []
    if conditions.get("value_above") is not None:
        extras.append(f"且 > {conditions['value_above']:g}")
    if conditions.get("within_nadir"):
        extras.append("且在骨髓抑制期")
    if conditions.get("outside_nadir"):
        extras.append("且不在骨髓抑制期")
    if int(conditions.get("consecutive_records", 1) or 1) > 1:
        extras.append(f"且連續 {int(conditions['consecutive_records'])} 次")
    return {
        "rule_code": rule.code,
        "rule_name": rule.name,
        "source_type": rule.source_type,
        "severity": rule.severity,
        "label": label,
        "value": value_text,
        "condition": " ".join([condition, *extras]),
    }
