from flask import Blueprint

from app.core.api import API_V1_PREFIX

vital_signs_bp = Blueprint("vital_signs", __name__, url_prefix=f"{API_V1_PREFIX}/vital-signs")

from app.modules.vital_signs import routes  # noqa: E402,F401
