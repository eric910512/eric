from flask import Blueprint

from app.core.api import API_V1_PREFIX

admin_bp = Blueprint("admin", __name__, url_prefix=f"{API_V1_PREFIX}/admin")

from app.modules.admin import routes  # noqa: E402,F401
