from flask import Blueprint

from app.core.api import API_V1_PREFIX

patient_bp = Blueprint("patient", __name__, url_prefix=f"{API_V1_PREFIX}/patients")

from app.modules.patient import routes  # noqa: E402,F401
