from flask import Blueprint

from app.core.api import API_V1_PREFIX

notification_bp = Blueprint("notification", __name__, url_prefix=f"{API_V1_PREFIX}/notifications")

from app.modules.notification import routes  # noqa: E402,F401
