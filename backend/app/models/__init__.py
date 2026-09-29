"""SQLAlchemy models — Phase 1 Sprint 1 + Sprint 2 (database-design.md v3.1).

Importing this package registers every model on ``db.metadata`` so that
Flask-Migrate / ``db.create_all()`` can see all tables.
"""

from app.models.audit import AuditLog, IdempotencyRecord
from app.models.auth import AuthToken
from app.models.chemotherapy import (
    ChemoRegimen,
    ChemotherapyCycle,
    ChemotherapyPlan,
    Drug,
    MedicationRecord,
    RegimenDrug,
)
from app.models.diagnosis import CancerDiagnosis, CancerType
from app.models.lab import LabResult, LabTestType
from app.models.notification import AlertRule, Notification
from app.models.nursing import NursingAssessment, NursingAssessmentItem
from app.models.patient import NursePatientAssignment, PatientCareAlert, PatientProfile
from app.models.schedule import Appointment, AppointmentInstruction
from app.models.symptom_definition import (
    SymptomCategory,
    SymptomDefinition,
    SymptomDefinitionOption,
    SymptomForm,
    SymptomFormItem,
)
from app.models.symptom_record import (
    SymptomRecord,
    SymptomRecordValue,
    symptom_record_value_options,
)
from app.models.user import NurseProfile, Role, User
from app.models.vital_sign import VitalSign

__all__ = [
    "AlertRule",
    "Appointment",
    "AppointmentInstruction",
    "AuditLog",
    "AuthToken",
    "CancerDiagnosis",
    "CancerType",
    "ChemoRegimen",
    "ChemotherapyCycle",
    "ChemotherapyPlan",
    "Drug",
    "IdempotencyRecord",
    "LabResult",
    "LabTestType",
    "MedicationRecord",
    "Notification",
    "NursePatientAssignment",
    "NurseProfile",
    "NursingAssessment",
    "NursingAssessmentItem",
    "PatientCareAlert",
    "PatientProfile",
    "RegimenDrug",
    "Role",
    "SymptomCategory",
    "SymptomDefinition",
    "SymptomDefinitionOption",
    "SymptomForm",
    "SymptomFormItem",
    "SymptomRecord",
    "SymptomRecordValue",
    "User",
    "VitalSign",
    "symptom_record_value_options",
]
