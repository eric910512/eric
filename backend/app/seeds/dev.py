"""Development seed data.

All people, codes and contact details are synthetic (database-design.md §3 Demo 政策).
The seed is idempotent: every row is looked up by a natural key and created only if
missing; existing rows are updated back to the values defined here. Dates are anchored
to "today" in the patient's timezone, so re-running refreshes them (e.g. the appointment
is always today and Cycle 1 is always on Day 4).
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models.base import utcnow
from app.models import (
    AlertRule,
    LabResult,
    LabTestType,
    Appointment,
    AppointmentInstruction,
    CancerDiagnosis,
    CancerType,
    ChemoRegimen,
    ChemotherapyCycle,
    ChemotherapyPlan,
    Drug,
    MedicationRecord,
    Notification,
    NursePatientAssignment,
    NurseProfile,
    PatientCareAlert,
    PatientProfile,
    RegimenDrug,
    Role,
    SymptomCategory,
    SymptomDefinition,
    SymptomForm,
    SymptomFormItem,
    SymptomRecord,
    SymptomRecordValue,
    User,
    VitalSign,
)
from app.models.enums import (
    AdministrationStatus,
    AlertSeverity,
    AppointmentStatus,
    AppointmentType,
    CareAlertSeverity,
    CareAlertType,
    CycleStatus,
    DiagnosisStatus,
    EmetogenicRisk,
    FormAvailability,
    FormIntendedFor,
    Gender,
    InstructionType,
    MedicationType,
    NotificationType,
    ObservationSource,
    PlanIntent,
    PlanStatus,
    RecordStatus,
    ReviewStatus,
    RoleName,
    SymptomValueType,
    TemperatureSite,
)

DEFAULT_DEMO_PASSWORD = "Demo@1234"
PATIENT_TZ = timezone(timedelta(hours=8))  # Asia/Taipei (no DST)

NURSE_EMAIL = "nurse01@demo.local"
PATIENT_EMAIL = "patient01@demo.local"
ADMIN_EMAIL = "admin01@demo.local"
PATIENT_CODE = "P00001"

CYCLE_DAY_TODAY = 4  # Cycle 1 started 3 days ago



# Suggested handling shown to staff with each alert (demo wording — to be confirmed clinically).
RECOMMENDED_ACTIONS = {
    "severe_pain": "電話評估疼痛部位、性質與止痛藥使用情形；必要時聯絡醫師調整止痛處方。",
    "severe_nausea": "確認止吐藥是否依時服用、進食與飲水量；有脫水徵象或無法進食時安排回診。",
    "severe_fatigue": "評估活動耐受度與睡眠，確認是否合併發燒、喘或貧血症狀，衛教節省體力。",
    "reported_fever": "立即電話聯絡病人確認體溫；化療期間 ≥38°C 請病人立即至急診，並通知主治醫師。",
    "suspected_febrile_neutropenia": "視為緊急狀況：立即聯絡病人前往急診，通知主治醫師，依嗜中性白血球低下發燒流程處理。",
    "fever": "立即電話聯絡病人複測體溫，評估感染徵象；持續 ≥38°C 請病人至急診並通知醫師。",
    "tachycardia": "請病人休息後複測心跳，評估是否發燒、脫水或胸悶；持續過快時通知醫師。",
    "hypotension": "評估頭暈、進食與水分攝取，請病人坐下或躺下休息；合併意識改變時請立即就醫。",
    "low_spo2": "立即聯絡病人確認是否喘或胸悶；血氧持續 ≤90% 請撥打 119 或立即至急診。",
    "severe_neutropenia": "立即電話聯絡病人確認有無發燒，衛教感染預防；通知主治醫師評估是否調整療程或使用白血球生長激素。",
    "neutropenia": "電話衛教感染預防（避免人多處、勤洗手、避免生食），提醒發燒 ≥38°C 立即就醫，下次抽血追蹤。",
}

def _local_to_utc(day: date, hh: int, mm: int = 0) -> datetime:
    """Local (patient timezone) wall-clock time -> naive UTC, as stored in the DB."""
    local = datetime.combine(day, time(hh, mm), tzinfo=PATIENT_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _upsert(model, lookup: dict, values: dict | None = None):
    """Find a row by ``lookup``; create it if missing; then apply ``values``."""
    instance = db.session.execute(select(model).filter_by(**lookup)).scalar_one_or_none()
    created = instance is None
    if created:
        instance = model(**lookup)
        db.session.add(instance)
    for key, value in (values or {}).items():
        setattr(instance, key, value)
    db.session.flush()
    return instance, created


class _Tracker:
    def __init__(self):
        self.created: dict[str, int] = {}
        self.updated: dict[str, int] = {}

    def __call__(self, result):
        instance, created = result
        bucket = self.created if created else self.updated
        name = type(instance).__tablename__
        bucket[name] = bucket.get(name, 0) + 1
        return instance


def seed_dev_data(password: str = DEFAULT_DEMO_PASSWORD) -> dict:
    """Create or refresh the development dataset. Caller commits."""
    track = _Tracker()
    today = datetime.now(PATIENT_TZ).date()
    cycle_start = today - timedelta(days=CYCLE_DAY_TODAY - 1)

    # 1. Roles
    roles = {
        name: track(_upsert(Role, {"name": name}, {"description": desc}))
        for name, desc in [
            (RoleName.ADMIN, "系統管理者"),
            (RoleName.NURSE, "護理人員"),
            (RoleName.PATIENT, "病人"),
        ]
    }

    # 2. Users
    password_hash = generate_password_hash(password)
    # Demo accounts have a known password, not a system temporary one: no forced change on login.
    seeded_at = utcnow()
    nurse = track(_upsert(
        User,
        {"email": NURSE_EMAIL},
        {"role": roles[RoleName.NURSE], "display_name": "測試護理師 林", "password_hash": password_hash,
         "is_active": True, "password_changed_at": seeded_at},
    ))
    track(_upsert(
        NurseProfile,
        {"user_id": nurse.id},
        {"staff_code": "N0001", "department": "腫瘤科（Demo）", "title": "個案管理師"},
    ))
    patient_user = track(_upsert(
        User,
        {"email": PATIENT_EMAIL},
        {"role": roles[RoleName.PATIENT], "display_name": "測試病人 甲", "password_hash": password_hash,
         "is_active": True, "password_changed_at": seeded_at},
    ))
    # Demo admin: care-team management (assignments, nurse accounts).
    track(_upsert(
        User,
        {"email": ADMIN_EMAIL},
        {"role": roles[RoleName.ADMIN], "display_name": "測試管理者", "password_hash": password_hash,
         "is_active": True, "password_changed_at": seeded_at},
    ))

    # 3. Patient profile (synthetic)
    patient = track(_upsert(
        PatientProfile,
        {"patient_code": PATIENT_CODE},
        {
            "user": patient_user,
            "display_name": "測試病人 甲",
            "gender": Gender.MALE,
            "date_of_birth": date(1970, 5, 1),
            "height_cm": Decimal("170.0"),
            "blood_type": "O",
            "allergies": "Penicillin",
            "baseline_ecog": 1,
            "timezone": "Asia/Taipei",
            "is_demo": True,
            "creator": nurse,
            "deleted_at": None,
        },
    ))
    track(_upsert(
        PatientCareAlert,
        {"patient_id": patient.id, "alert_type": CareAlertType.ALLERGY},
        {"description": "Penicillin 過敏", "severity": CareAlertSeverity.HIGH, "is_active": True,
         "recorder": nurse},
    ))
    track(_upsert(
        NursePatientAssignment,
        {"nurse_id": nurse.id, "patient_id": patient.id, "ended_at": None},
        {"is_primary": True},
    ))

    # 4. Cancer diagnosis
    cancer_type = track(_upsert(
        CancerType, {"code": "C11"}, {"name_zh": "鼻咽癌", "name_en": "Nasopharyngeal carcinoma", "is_active": True}
    ))
    diagnosis = track(_upsert(
        CancerDiagnosis,
        {"patient_id": patient.id, "cancer_type_id": cancer_type.id, "is_primary": True},
        {
            "diagnosis_date": cycle_start - timedelta(days=30),
            "stage": "III",
            "tnm_t": "T2",
            "tnm_n": "N2",
            "tnm_m": "M0",
            "histology": "Non-keratinizing carcinoma",
            "biomarkers": {"EBV_DNA": "positive"},
            "status": DiagnosisStatus.ACTIVE,
            "creator": nurse,
            "deleted_at": None,
        },
    ))

    # 5. Chemotherapy: drug, regimen, plan, cycle 1
    cisplatin = track(_upsert(
        Drug, {"generic_name": "Cisplatin"},
        {"brand_name": None, "drug_class": "platinum", "default_route": "IV", "is_active": True},
    ))
    regimen = track(_upsert(
        ChemoRegimen,
        {"name": "Cisplatin q3w"},
        {"description": "Cisplatin 100 mg/m² Day 1，每 21 天一個 Cycle（Demo）",
         "cycle_length_days": 21, "default_total_cycles": 3, "emetogenic_risk": EmetogenicRisk.HIGH,
         "is_active": True},
    ))
    track(_upsert(
        RegimenDrug,
        {"regimen_id": regimen.id, "drug_id": cisplatin.id, "day_of_cycle": "1"},
        {"dose_value": Decimal("100"), "dose_unit": "mg/m2", "route": "IV", "sequence": 1},
    ))
    plan = track(_upsert(
        ChemotherapyPlan,
        {"patient_id": patient.id, "diagnosis_id": diagnosis.id, "plan_name": "Cisplatin 同步化療（Demo）"},
        {
            "regimen": regimen,
            "intent": PlanIntent.CURATIVE,
            "line_of_therapy": 1,
            "total_cycles": 3,
            "start_date": cycle_start,
            "end_date": None,
            "status": PlanStatus.ACTIVE,
            "attending_physician_name": "測試醫師 王",
            "creator": nurse,
            "deleted_at": None,
        },
    ))
    cycle1 = track(_upsert(
        ChemotherapyCycle,
        {"plan_id": plan.id, "cycle_number": 1},
        {
            "patient": patient,
            "scheduled_date": cycle_start,
            "actual_start_date": cycle_start,
            "actual_end_date": None,
            "weight_kg": Decimal("64.5"),
            "bsa_m2": Decimal("1.74"),
            "dose_modification_pct": 100,
            "status": CycleStatus.IN_PROGRESS,
            "nadir_start_day": 7,
            "nadir_end_day": 14,
            "deleted_at": None,
        },
    ))
    track(_upsert(
        MedicationRecord,
        {"patient_id": patient.id, "cycle_id": cycle1.id, "drug_id": cisplatin.id, "cycle_day": 1},
        {
            "medication_type": MedicationType.CHEMO,
            "dose_value": Decimal("174"),
            "dose_unit": "mg",
            "route": "IV",
            "administered_at": _local_to_utc(cycle_start, 10, 0),
            "infusion_duration_min": 120,
            "administration_status": AdministrationStatus.GIVEN,
            "administrator": nurse,
            "source": ObservationSource.NURSE,
            "record_status": RecordStatus.FINAL,
        },
    ))

    # 6. Symptoms: definitions, daily form, two test records (Day 2 and Day 3)
    categories = {
        code: track(_upsert(SymptomCategory, {"code": code}, {"name_zh": name, "display_order": order}))
        for code, name, order in [("general", "全身症狀", 1), ("gastrointestinal", "腸胃症狀", 2)]
    }
    definitions = {}
    for code, name_zh, name_en, category, question in [
        ("pain", "疼痛", "Pain", "general", "過去 24 小時最嚴重的疼痛程度？"),
        ("nausea", "噁心", "Nausea", "gastrointestinal", "過去 24 小時最嚴重的噁心程度？"),
        ("fatigue", "疲倦", "Fatigue", "general", "過去 24 小時最嚴重的疲倦程度？"),
    ]:
        definitions[code] = track(_upsert(
            SymptomDefinition,
            {"code": code},
            {
                "category": categories[category],
                "name_zh": name_zh,
                "name_en": name_en,
                "question_text": question,
                "value_type": SymptomValueType.SCALE,
                "min_value": Decimal("0"),
                "max_value": Decimal("10"),
                "step": Decimal("1"),
                "min_label": "沒有",
                "max_label": "最嚴重",
                "higher_is_worse": True,
                "ctcae_term": name_en,
                "is_system": True,
                "is_active": True,
            },
        ))
    form = track(_upsert(
        SymptomForm,
        {"code": "daily_chemo_check"},
        {"name": "化療每日症狀自評", "intended_for": FormIntendedFor.PATIENT, "recall_period_hours": 24,
         "availability": FormAvailability.ALWAYS, "version": 1, "is_active": True},
    ))
    definitions["fever"] = track(_upsert(
        SymptomDefinition,
        {"code": "fever"},
        {
            "category": categories["general"],
            "name_zh": "發燒或畏寒",
            "name_en": "Fever or chills",
            "question_text": "過去 24 小時有沒有發燒（體溫 38°C 以上）或畏寒發抖？",
            "help_text": "化療期間發燒可能是嚴重感染的徵兆，請量體溫確認。",
            "value_type": SymptomValueType.BOOLEAN,
            "higher_is_worse": True,
            "ctcae_term": "Fever",
            "is_system": True,
            "is_active": True,
        },
    ))
    for order, code in enumerate(["pain", "nausea", "fatigue", "fever"], start=1):
        track(_upsert(
            SymptomFormItem,
            {"form_id": form.id, "definition_id": definitions[code].id},
            {"display_order": order, "is_required": True},
        ))

    # Phase 1 alert rules for symptom reports (database-design.md §6.K)
    for code, name, definition_code, op, threshold, severity, cooldown, template in [
        ("severe_pain", "疼痛程度偏高", "pain", ">=", "7", AlertSeverity.WARNING, 720,
         "{patient_name}（{patient_code}）回報疼痛 {value}/10，Cycle {cycle_number} Day {cycle_day}"),
        ("severe_nausea", "噁心程度偏高", "nausea", ">=", "7", AlertSeverity.WARNING, 720,
         "{patient_name}（{patient_code}）回報噁心 {value}/10，Cycle {cycle_number} Day {cycle_day}"),
        ("severe_fatigue", "疲倦程度偏高", "fatigue", ">=", "8", AlertSeverity.WARNING, 720,
         "{patient_name}（{patient_code}）回報疲倦 {value}/10，Cycle {cycle_number} Day {cycle_day}"),
        ("reported_fever", "病人回報發燒或畏寒", "fever", "==", "1", AlertSeverity.CRITICAL, 240,
         "{patient_name}（{patient_code}）回報發燒或畏寒，Cycle {cycle_number} Day {cycle_day}，請立即聯繫評估"),
    ]:
        track(_upsert(
            AlertRule,
            {"code": code},
            {
                "name": name,
                "source_type": "symptom",
                "vital_field": None,
                "symptom_definition_id": definitions[definition_code].id,
                "operator": op,
                "threshold_value": Decimal(threshold),
                "extra_conditions": None,
                "cancer_type_id": None,
                "severity": severity,
                "message_template": template,
                "notify_patient": True,
                "notify_nurse": True,
                "cooldown_minutes": cooldown,
                "recommended_action": RECOMMENDED_ACTIONS.get(code),
                "is_active": True,
            },
        ))

    # Phase 1 alert rules for vital signs. Fever is split by nadir so one reading raises
    # exactly one of the two. Demo thresholds: to be confirmed by the clinical team.
    for code, name, field, op, threshold, severity, cooldown, conditions, template in [
        ("suspected_febrile_neutropenia", "疑似嗜中性白血球低下發燒", "temperature_c", ">=", "38.0",
         AlertSeverity.CRITICAL, 240, {"within_nadir": True},
         "{patient_name}（{patient_code}）體溫 {value}，Cycle {cycle_number} Day {cycle_day}（骨髓抑制期），請立即評估"),
        ("fever", "化療期間發燒", "temperature_c", ">=", "38.0",
         AlertSeverity.CRITICAL, 240, {"outside_nadir": True},
         "{patient_name}（{patient_code}）體溫 {value}，Cycle {cycle_number} Day {cycle_day}，請立即聯繫評估"),
        ("tachycardia", "心跳過快", "heart_rate_bpm", ">=", "120",
         AlertSeverity.WARNING, 720, None,
         "{patient_name}（{patient_code}）心跳 {value}，Cycle {cycle_number} Day {cycle_day}"),
        ("hypotension", "血壓偏低", "systolic_bp_mmhg", "<=", "90",
         AlertSeverity.WARNING, 720, None,
         "{patient_name}（{patient_code}）收縮壓 {value}，Cycle {cycle_number} Day {cycle_day}"),
        ("low_spo2", "血氧過低", "spo2_pct", "<=", "90",
         AlertSeverity.CRITICAL, 240, None,
         "{patient_name}（{patient_code}）血氧 {value}，Cycle {cycle_number} Day {cycle_day}，請立即評估"),
    ]:
        track(_upsert(
            AlertRule,
            {"code": code},
            {
                "name": name,
                "source_type": "vital_sign",
                "vital_field": field,
                "symptom_definition_id": None,
                "operator": op,
                "threshold_value": Decimal(threshold),
                "extra_conditions": conditions,
                "cancer_type_id": None,
                "severity": severity,
                "message_template": template,
                "notify_patient": True,
                "notify_nurse": True,
                "cooldown_minutes": cooldown,
                "recommended_action": RECOMMENDED_ACTIONS.get(code),
                "is_active": True,
            },
        ))

    # Lab tests (reference / critical ranges: demo values, adult, to be confirmed clinically)
    lab_types = {}
    for order, (code, loinc, name, unit, ref_low, ref_high, crit_low, crit_high) in enumerate([
        ("WBC", "6690-2", "白血球 (WBC)", "10³/µL", "4.0", "10.0", "1.0", "30.0"),
        ("ANC", "751-8", "嗜中性白血球絕對數 (ANC)", "10³/µL", "1.5", "7.5", "0.5", None),
        ("HGB", "718-7", "血色素 (Hb)", "g/dL", "12.0", "16.0", "7.0", "20.0"),
        ("PLT", "777-3", "血小板 (Platelet)", "10³/µL", "150", "400", "20", "1000"),
    ], start=1):
        dec = lambda v: Decimal(v) if v is not None else None  # noqa: E731
        lab_types[code] = track(_upsert(
            LabTestType,
            {"code": code},
            {"loinc_code": loinc, "name_zh": name, "unit": unit, "ref_low": dec(ref_low), "ref_high": dec(ref_high),
             "critical_low": dec(crit_low), "critical_high": dec(crit_high), "display_order": order, "is_active": True},
        ))

    # ANC alert rules. value_above keeps the warning rule from also firing at critical levels.
    for code, name, op, threshold, severity, cooldown, conditions, template in [
        ("severe_neutropenia", "嗜中性白血球嚴重低下", "<=", "0.5", AlertSeverity.CRITICAL, 240, None,
         "{patient_name}（{patient_code}）ANC {value}，Cycle {cycle_number} Day {cycle_day}，感染風險極高，請立即評估"),
        ("neutropenia", "嗜中性白血球低下", "<=", "1.0", AlertSeverity.WARNING, 720, {"value_above": 0.5},
         "{patient_name}（{patient_code}）ANC {value}，Cycle {cycle_number} Day {cycle_day}"),
    ]:
        track(_upsert(
            AlertRule,
            {"code": code},
            {
                "name": name,
                "source_type": "lab",
                "vital_field": None,
                "symptom_definition_id": None,
                "lab_test_type_id": lab_types["ANC"].id,
                "operator": op,
                "threshold_value": Decimal(threshold),
                "extra_conditions": conditions,
                "cancer_type_id": None,
                "severity": severity,
                "message_template": template,
                "notify_patient": True,
                "notify_nurse": True,
                "cooldown_minutes": cooldown,
                "recommended_action": RECOMMENDED_ACTIONS.get(code),
                "is_active": True,
            },
        ))

    symptom_scores = {2: {"pain": 2, "nausea": 6, "fatigue": 5}, 3: {"pain": 3, "nausea": 4, "fatigue": 6}}
    for cycle_day, scores in symptom_scores.items():
        record = track(_upsert(
            SymptomRecord,
            {"patient_id": patient.id, "form_id": form.id, "cycle_id": cycle1.id, "cycle_day": cycle_day},
            {
                "form_version": form.version,
                "recorded_at": _local_to_utc(cycle_start + timedelta(days=cycle_day - 1), 20, 0),
                "reporter": patient_user,
                "review_status": ReviewStatus.SUBMITTED,
                "source": ObservationSource.PATIENT_APP,
                "record_status": RecordStatus.FINAL,
            },
        ))
        for code, score in scores.items():
            track(_upsert(
                SymptomRecordValue,
                {"symptom_record_id": record.id, "definition_id": definitions[code].id},
                {"value_numeric": Decimal(score), "score": Decimal(score)},
            ))

    # Pre-chemo CBC, Cycle 1 Day 1 morning (all within range)
    for code, value in [("WBC", "6.20"), ("ANC", "3.80"), ("HGB", "12.8"), ("PLT", "245")]:
        t = lab_types[code]
        track(_upsert(
            LabResult,
            {"patient_id": patient.id, "lab_test_type_id": t.id, "cycle_id": cycle1.id, "cycle_day": 1},
            {
                "collected_at": _local_to_utc(cycle_start, 7, 30),
                "resulted_at": _local_to_utc(cycle_start, 8, 30),
                "value_numeric": Decimal(value),
                "unit": t.unit,
                "ref_low": t.ref_low,
                "ref_high": t.ref_high,
                "abnormal_flag": "N",
                "recorder": nurse,
                "source": ObservationSource.NURSE,
                "record_status": RecordStatus.FINAL,
            },
        ))

    # 7. Vital signs (Day 3 evening)
    track(_upsert(
        VitalSign,
        {"patient_id": patient.id, "cycle_id": cycle1.id, "cycle_day": 3, "source": ObservationSource.PATIENT_APP},
        {
            "measured_at": _local_to_utc(cycle_start + timedelta(days=2), 20, 30),
            "temperature_c": Decimal("37.2"),
            "temperature_site": TemperatureSite.EAR,
            "heart_rate_bpm": 88,
            "systolic_bp_mmhg": 118,
            "diastolic_bp_mmhg": 76,
            "bp_measure_site": "left_arm",
            "respiratory_rate": 18,
            "spo2_pct": 98,
            "weight_kg": Decimal("64.0"),
            "pain_score": 3,
            "recorder": patient_user,
            "record_status": RecordStatus.FINAL,
        },
    ))

    # Today's appointment (supports the today-schedule widget and the reminder below)
    appointment = track(_upsert(
        Appointment,
        {"patient_id": patient.id, "cycle_id": cycle1.id, "appointment_type": AppointmentType.CLINIC_VISIT},
        {
            "title": "門診追蹤與抽血",
            "scheduled_at": _local_to_utc(today, 14, 0),
            "duration_min": 30,
            "location": "腫瘤科門診（Demo）",
            "status": AppointmentStatus.SCHEDULED,
            "creator": nurse,
            "deleted_at": None,
        },
    ))
    track(_upsert(
        AppointmentInstruction,
        {"appointment_id": appointment.id, "instruction_type": InstructionType.CHECK_IN},
        {"due_at": _local_to_utc(today, 13, 40), "text": "13:40 報到", "is_highlighted": True, "display_order": 1},
    ))

    # 8. Notification (reminder to the patient)
    track(_upsert(
        Notification,
        {"recipient_id": patient_user.id, "event_key": "seed:reminder:today-appointment"},
        {
            "patient": patient,
            "type": NotificationType.REMINDER,
            "severity": AlertSeverity.INFO,
            "title": "今日回診提醒",
            "message": "今天 14:00 門診追蹤與抽血，請於 13:40 報到。",
            "source_table": "appointments",
            "source_id": appointment.id,
            "scheduled_for": None,
            "sent_at": _local_to_utc(today, 8, 0),
            "is_read": False,
            "read_at": None,
        },
    ))

    return {
        "created": track.created,
        "updated": track.updated,
        "accounts": {NURSE_EMAIL: RoleName.NURSE, PATIENT_EMAIL: RoleName.PATIENT, ADMIN_EMAIL: RoleName.ADMIN},
        "patient_code": PATIENT_CODE,
        "today": today.isoformat(),
    }
