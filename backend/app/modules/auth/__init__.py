from flask import Blueprint

from app.core.api import API_V1_PREFIX

auth_bp = Blueprint("auth", __name__, url_prefix=f"{API_V1_PREFIX}/auth")

from app.modules.auth import routes  # noqa: E402,F401
