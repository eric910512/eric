from flask import Blueprint

from app.core.api import API_V1_PREFIX

# 皮膚照片 (Phase 1b): /api/v1/symptom-photos/..., /api/v1/patients/{id|me}/symptom-photos,
# /api/v1/patients/me/photo-consent. Photos are attached to reports through POST /symptoms/records.
photo_bp = Blueprint("photo", __name__, url_prefix=API_V1_PREFIX)

from app.modules.photo import routes  # noqa: E402,F401
