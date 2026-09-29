from flask import Blueprint

from app.core.api import API_V1_PREFIX

nursing_bp = Blueprint("nursing", __name__, url_prefix=f"{API_V1_PREFIX}/nursing-assessments")

from app.modules.nursing import routes  # noqa: E402,F401
