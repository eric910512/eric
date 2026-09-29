from flask import Blueprint, jsonify
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.api import API_V1_PREFIX
from app.extensions import db

health_bp = Blueprint("health", __name__, url_prefix=API_V1_PREFIX)


@health_bp.route("/health", methods=["GET"])
def health_check():
    """Liveness + database reachability (used as the hosting health check).
    503 when the database is unreachable, so a broken deploy never receives traffic."""
    try:
        db.session.execute(text("SELECT 1"))
        database = "ok"
    except SQLAlchemyError:
        db.session.rollback()
        database = "unavailable"
    ok = database == "ok"
    return jsonify({"status": "ok" if ok else "error", "database": database}), 200 if ok else 503
