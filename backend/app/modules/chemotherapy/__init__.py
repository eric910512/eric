from flask import Blueprint

from app.core.api import API_V1_PREFIX

chemotherapy_bp = Blueprint("chemotherapy", __name__, url_prefix=f"{API_V1_PREFIX}/chemotherapy")

from app.modules.chemotherapy import routes  # noqa: E402,F401
