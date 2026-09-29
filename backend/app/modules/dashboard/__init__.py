from flask import Blueprint

from app.core.api import API_V1_PREFIX

dashboard_bp = Blueprint("dashboard", __name__, url_prefix=f"{API_V1_PREFIX}/dashboard")

from app.modules.dashboard import routes  # noqa: E402,F401
