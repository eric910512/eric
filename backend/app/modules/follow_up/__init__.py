from flask import Blueprint

from app.core.api import API_V1_PREFIX

# 護理師追蹤管理 (Phase 1c): /api/v1/care-follow-ups/... (nurse) and /api/v1/patients/me/care-follow-ups (patient, read only).
follow_up_bp = Blueprint("follow_up", __name__, url_prefix=API_V1_PREFIX)

from app.modules.follow_up import routes  # noqa: E402,F401
