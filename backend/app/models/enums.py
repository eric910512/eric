"""Allowed values for VARCHAR enum-like columns.

The database stores these as plain VARCHAR (database-design.md §2: no DB ENUM);
validation happens in the application layer using the ``ALL`` tuples below.
"""


class RoleName:
    PATIENT = "patient"
    NURSE = "nurse"
    ADMIN = "admin"
    ALL = (PATIENT, NURSE, ADMIN)


class Gender:
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    ALL = (MALE, FEMALE, OTHER)


class RecordStatus:
    FINAL = "final"
    AMENDED = "amended"
    ENTERED_IN_ERROR = "entered_in_error"
    ALL = (FINAL, AMENDED, ENTERED_IN_ERROR)


class ObservationSource:
    PATIENT_APP = "patient_app"
    NURSE = "nurse"
    DEVICE = "device"
    IMPORT = "import"
    ALL = (PATIENT_APP, NURSE, DEVICE, IMPORT)


class DiagnosisStatus:
    ACTIVE = "active"
    REMISSION = "remission"
    RECURRENCE = "recurrence"
    ALL = (ACTIVE, REMISSION, RECURRENCE)


class EmetogenicRisk:
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    ALL = (HIGH, MODERATE, LOW)


class PlanIntent:
    CURATIVE = "curative"
    ADJUVANT = "adjuvant"
    NEOADJUVANT = "neoadjuvant"
    PALLIATIVE = "palliative"
    ALL = (CURATIVE, ADJUVANT, NEOADJUVANT, PALLIATIVE)


class PlanStatus:
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    DISCONTINUED = "discontinued"
    ALL = (PLANNED, ACTIVE, COMPLETED, DISCONTINUED)


class CycleStatus:
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DELAYED = "delayed"
    CANCELLED = "cancelled"
    ALL = (SCHEDULED, IN_PROGRESS, COMPLETED, DELAYED, CANCELLED)


class AppointmentType:
    CHEMO_INFUSION = "chemo_infusion"
    LAB_DRAW = "lab_draw"
    CLINIC_VISIT = "clinic_visit"
    IMAGING = "imaging"
    RADIOTHERAPY = "radiotherapy"
    EDUCATION_SESSION = "education_session"
    OTHER = "other"
    ALL = (CHEMO_INFUSION, LAB_DRAW, CLINIC_VISIT, IMAGING, RADIOTHERAPY, EDUCATION_SESSION, OTHER)


class AppointmentStatus:
    SCHEDULED = "scheduled"
    CHECKED_IN = "checked_in"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"
    ALL = (SCHEDULED, CHECKED_IN, COMPLETED, CANCELLED, NO_SHOW, RESCHEDULED)


class SymptomValueType:
    SCALE = "scale"
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    BOOLEAN = "boolean"
    NUMERIC = "numeric"
    TEXT = "text"
    ALL = (SCALE, SINGLE_CHOICE, MULTI_CHOICE, BOOLEAN, NUMERIC, TEXT)


class FormIntendedFor:
    PATIENT = "patient"
    NURSE = "nurse"
    BOTH = "both"
    ALL = (PATIENT, NURSE, BOTH)


class FormAvailability:
    ALWAYS = "always"
    SCHEDULED = "scheduled"
    ALL = (ALWAYS, SCHEDULED)


class ReviewStatus:
    DRAFT = "draft"
    SUBMITTED = "submitted"
    REVIEWED = "reviewed"
    ALL = (DRAFT, SUBMITTED, REVIEWED)


class TemperatureSite:
    ORAL = "oral"
    EAR = "ear"
    AXILLARY = "axillary"
    FOREHEAD = "forehead"
    ALL = (ORAL, EAR, AXILLARY, FOREHEAD)


class BpMeasureSite:
    LEFT_ARM = "left_arm"
    RIGHT_ARM = "right_arm"
    LEG = "leg"
    ALL = (LEFT_ARM, RIGHT_ARM, LEG)


class AssessmentType:
    INITIAL = "initial"
    PRE_CHEMO = "pre_chemo"
    DURING_INFUSION = "during_infusion"
    POST_CHEMO = "post_chemo"
    FOLLOW_UP = "follow_up"
    PHONE_FOLLOW_UP = "phone_follow_up"
    ALL = (INITIAL, PRE_CHEMO, DURING_INFUSION, POST_CHEMO, FOLLOW_UP, PHONE_FOLLOW_UP)


class OverallCondition:
    STABLE = "stable"
    CONCERN = "concern"
    URGENT = "urgent"
    ALL = (STABLE, CONCERN, URGENT)


class RiskLevel:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    ALL = (LOW, MEDIUM, HIGH)


class ChemoReadiness:
    READY = "ready"
    HOLD = "hold"
    DELAY = "delay"
    REFER_PHYSICIAN = "refer_physician"
    ALL = (READY, HOLD, DELAY, REFER_PHYSICIAN)


class SignStatus:
    DRAFT = "draft"
    SIGNED = "signed"
    ALL = (DRAFT, SIGNED)


class AssessmentItemType:
    PROBLEM = "problem"
    GOAL = "goal"
    INTERVENTION = "intervention"
    EDUCATION = "education"
    REFERRAL = "referral"
    ALL = (PROBLEM, GOAL, INTERVENTION, EDUCATION, REFERRAL)


class AssessmentItemStatus:
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    DONE = "done"
    ALL = (OPEN, IN_PROGRESS, RESOLVED, DONE)


class Priority:
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    ALL = (HIGH, MEDIUM, LOW)


class TokenType:
    REFRESH = "refresh"
    PASSWORD_RESET = "password_reset"
    EMAIL_VERIFICATION = "email_verification"  # contact email (patient_contacts)
    ALL = (REFRESH, PASSWORD_RESET, EMAIL_VERIFICATION)


class AuditCategory:
    AUTH = "auth"
    DATA = "data"
    ACCESS = "access"
    ADMIN = "admin"
    EXPORT = "export"
    ALL = (AUTH, DATA, ACCESS, ADMIN, EXPORT)


class NotificationStatus:
    """Clinical handling lifecycle of a risk alert (shared by every copy of one event)."""

    NEW = "new"
    ACKNOWLEDGED = "acknowledged"  # a nurse has taken it over
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    ALL = (NEW, ACKNOWLEDGED, IN_PROGRESS, RESOLVED)
    OPEN = (NEW, ACKNOWLEDGED, IN_PROGRESS)


class TimelineEventType:
    """Patient Care Timeline event types (composed at query time from existing tables)."""

    CHEMOTHERAPY = "CHEMOTHERAPY"  # cycle start / end, medication administration
    SYMPTOM = "SYMPTOM"
    VITAL_SIGN = "VITAL_SIGN"
    LAB_RESULT = "LAB_RESULT"  # one event per collection (panel)
    NOTIFICATION = "NOTIFICATION"  # risk alerts
    NOTIFICATION_STATUS = "NOTIFICATION_STATUS"  # acknowledged / in_progress / resolved
    NURSING_ASSESSMENT = "NURSING_ASSESSMENT"
    APPOINTMENT = "APPOINTMENT"  # treatment schedule (Sprint 3): appointments that are due or past
    ALL = (CHEMOTHERAPY, SYMPTOM, VITAL_SIGN, LAB_RESULT, NOTIFICATION, NOTIFICATION_STATUS, NURSING_ASSESSMENT, APPOINTMENT)


class AuditAction:
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    AMEND = "AMEND"
    MARK_ERROR = "MARK_ERROR"
    DELETE = "DELETE"
    VIEW = "VIEW"
    EXPORT = "EXPORT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    ASSIGN = "ASSIGN"
    SIGN = "SIGN"
    ACKNOWLEDGE = "ACKNOWLEDGE"
    ALL = (
        LOGIN, LOGIN_FAILED, LOGOUT, CREATE, UPDATE, AMEND, MARK_ERROR, DELETE,
        VIEW, EXPORT, PERMISSION_DENIED, ASSIGN, SIGN, ACKNOWLEDGE,
    )


class AuditOutcome:
    SUCCESS = "success"
    FAILURE = "failure"
    ALL = (SUCCESS, FAILURE)


class IdempotencyStatus:
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    ALL = (PROCESSING, COMPLETED, FAILED)


class CareAlertType:
    ALLERGY = "allergy"
    LIMB_RESTRICTION = "limb_restriction"
    FALL_RISK = "fall_risk"
    ISOLATION = "isolation"
    OTHER = "other"
    ALL = (ALLERGY, LIMB_RESTRICTION, FALL_RISK, ISOLATION, OTHER)


class CareAlertSeverity:
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    ALL = (HIGH, MEDIUM, LOW)


class DrugRoute:
    IV = "IV"
    PO = "PO"
    SC = "SC"
    ALL = (IV, PO, SC)


class MedicationType:
    CHEMO = "chemo"
    PREMEDICATION = "premedication"
    SUPPORTIVE = "supportive"
    ALL = (CHEMO, PREMEDICATION, SUPPORTIVE)


class AdministrationStatus:
    GIVEN = "given"
    HELD = "held"
    PARTIAL = "partial"
    REFUSED = "refused"
    ALL = (GIVEN, HELD, PARTIAL, REFUSED)


class InstructionType:
    FASTING = "fasting"
    CHECK_IN = "check_in"
    MEDICATION = "medication"
    BRING_ITEM = "bring_item"
    OTHER = "other"
    ALL = (FASTING, CHECK_IN, MEDICATION, BRING_ITEM, OTHER)


class AlertSourceType:
    VITAL_SIGN = "vital_sign"
    SYMPTOM = "symptom"
    SCHEDULE = "schedule"
    LAB = "lab"
    ALL = (VITAL_SIGN, SYMPTOM, SCHEDULE, LAB)


class AlertOperator:
    GTE = ">="
    LTE = "<="
    GT = ">"
    LT = "<"
    EQ = "=="
    ALL = (GTE, LTE, GT, LT, EQ)


class AlertSeverity:
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    ALL = (INFO, WARNING, CRITICAL)


class NotificationType:
    RISK_ALERT = "risk_alert"
    REMINDER = "reminder"
    SYSTEM = "system"
    ALL = (RISK_ALERT, REMINDER, SYSTEM)  # Phase 2 adds "education"; Phase 4 adds "ai_alert"


class DeliveryChannel:
    EMAIL = "email"
    ALL = (EMAIL,)


class DeliveryStatus:
    """Delivery of one notification over an outside channel (notification_deliveries).
    Independent of the notification's handling lifecycle (NotificationStatus)."""

    PENDING = "pending"  # planned, not attempted yet (stays pending if the process died mid-send)
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"  # not attempted: see DeliverySkipReason
    ALL = (PENDING, SENT, FAILED, SKIPPED)


class DeliverySkipReason:
    NO_EMAIL = "no_email"
    NOT_VERIFIED = "not_verified"
    DISABLED = "disabled"  # the patient switched email notifications off
    SCHEDULED = "scheduled"  # scheduled reminders are not emailed (no background scheduler)
    NOT_CONFIGURED = "not_configured"  # no real email provider configured (EMAIL_PROVIDER=disabled)
    NOT_REQUESTED = "not_requested"  # the nurse chose email_mode=none
    ALL = (NO_EMAIL, NOT_VERIFIED, DISABLED, SCHEDULED, NOT_CONFIGURED, NOT_REQUESTED)


class EmailMode:
    """What the email of a nurse-sent notification may contain (notification_deliveries.email_mode)."""

    NONE = "none"  # no email
    SUMMARY = "summary"  # system name, a (possibly generic) title, time, sign-in link — no content
    FULL = "full"  # title + content (non-sensitive topics only)
    ALL = (NONE, SUMMARY, FULL)


class ReminderCategory:
    """Topic of a nurse-sent notification; decides the most an email may show (email_policy.py)."""

    SCHEDULE = "schedule"  # 行程與報到
    PREPARATION = "preparation"  # 就診準備
    MEDICATION = "medication"  # 用藥與治療
    SYMPTOM_FOLLOWUP = "symptom_followup"  # 症狀與照護追蹤
    CLINICAL_OTHER = "clinical_other"  # 其他醫療相關 (also: not specified)
    ALL = (SCHEDULE, PREPARATION, MEDICATION, SYMPTOM_FOLLOWUP, CLINICAL_OTHER)
