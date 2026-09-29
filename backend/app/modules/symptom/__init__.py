from flask import Blueprint

from app.core.api import API_V1_PREFIX

symptom_bp = Blueprint("symptom", __name__, url_prefix=f"{API_V1_PREFIX}/symptoms")

from app.modules.symptom import routes  # noqa: E402,F401
