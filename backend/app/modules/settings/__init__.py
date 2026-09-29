from flask import Blueprint

from app.core.api import API_V1_PREFIX

settings_bp = Blueprint("settings", __name__, url_prefix=f"{API_V1_PREFIX}/settings")

from app.modules.settings import routes  # noqa: E402,F401
