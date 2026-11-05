from flask import Blueprint

from app.core.api import API_V1_PREFIX

# Routes live under /api/v1/patients/{id}/messages (the patient-scoped conversation) and
# /api/v1/chat/... (the signed-in user's inbox and unread count).
chat_bp = Blueprint("chat", __name__, url_prefix=API_V1_PREFIX)

from app.modules.chat import routes  # noqa: E402,F401
