from flask import Blueprint

from app.core.api import API_V1_PREFIX

labs_bp = Blueprint("labs", __name__, url_prefix=f"{API_V1_PREFIX}/labs")

from app.modules.labs import routes  # noqa: E402,F401
