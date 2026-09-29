import os
import re

from flask import Flask, request
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import PRODUCTION_LIKE, config_by_name
from app.core.api import init_api
from app.extensions import cors, db, jwt, migrate

# Secrets that must never be used outside development / testing.
_WEAK_SECRETS = {"dev", "change-me", "change-me-to-a-random-string-of-at-least-32-bytes"}
MIN_SECRET_LENGTH = 32
_LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")


def _deployment_problems(app):
    """Why a production-like app (staging / production) must not start. Never echoes secret values."""
    cfg = app.config
    problems = []
    for name in ("SECRET_KEY", "JWT_SECRET_KEY"):
        value = cfg.get(name) or ""
        if value in _WEAK_SECRETS or len(value) < MIN_SECRET_LENGTH:
            problems.append(f"{name} must be a random value of at least {MIN_SECRET_LENGTH} characters (not a default).")
    if not os.environ.get("JWT_SECRET_KEY") or cfg.get("JWT_SECRET_KEY") == cfg.get("SECRET_KEY"):
        problems.append("JWT_SECRET_KEY must be set in the environment and differ from SECRET_KEY.")
    if cfg.get("DEBUG") or os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes"):
        problems.append("DEBUG must be off (unset FLASK_DEBUG).")
    if not cfg["SQLALCHEMY_DATABASE_URI"].startswith("postgresql"):
        problems.append("DATABASE_URL must point to PostgreSQL (the local SQLite file is not durable on a server).")
    origins = cfg.get("CORS_ORIGINS") or []
    if not origins:
        problems.append("CORS_ORIGINS must list the frontend origin(s), e.g. https://<frontend-host>.")
    for origin in origins:
        if origin == "*" or not (origin.startswith("https://") or _LOCAL_ORIGIN.match(origin)):
            problems.append(f"CORS_ORIGINS entry {origin!r} must be an https:// origin (no '*').")
    return problems


def create_app(config_name=None):
    config_name = config_name or os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])
    if config_name in PRODUCTION_LIKE:
        problems = _deployment_problems(app)
        if problems:
            raise RuntimeError(f"Refusing to start in {config_name}: " + " ".join(problems))
    if app.config.get("PROXY_FIX_HOPS"):
        # Behind a reverse proxy: take the client IP / scheme from X-Forwarded-* (audit_logs.ip_address).
        hops = app.config["PROXY_FIX_HOPS"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    _register_extensions(app)
    init_api(app)
    _register_blueprints(app)
    _register_commands(app)

    return app


def _register_extensions(app):
    from app import models  # noqa: F401  (register models on db.metadata for migrations)

    db.init_app(app)
    migrate.init_app(app, db)
    # Bearer tokens (no cookies): credentials are not needed for cross-origin calls.
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["Idempotent-Replayed", "X-Request-ID"],
        methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        supports_credentials=False,
        max_age=600,
    )

    @app.before_request
    def _answer_preflight():
        # Preflights succeed for every /api/* path (Flask-CORS still decides the allowed origin),
        # so a cross-origin client sees the real status of the actual request (e.g. 404 for an
        # endpoint that does not exist yet) instead of a CORS "network error". Runs before routing
        # errors are raised; the actual GET/POST keeps normal routing (404 / 405).
        if request.method == "OPTIONS" and request.path.startswith("/api/"):
            return app.make_default_options_response()

    jwt.init_app(app)

    from app.core.auth import init_jwt

    init_jwt(jwt)


def _register_blueprints(app):
    from app.routes.health import health_bp

    app.register_blueprint(health_bp)

    from app.modules.admin import admin_bp
    from app.modules.auth import auth_bp
    from app.modules.chemotherapy import chemotherapy_bp
    from app.modules.dashboard import dashboard_bp
    from app.modules.labs import labs_bp
    from app.modules.notification import notification_bp
    from app.modules.nursing import nursing_bp
    from app.modules.patient import patient_bp
    from app.modules.settings import settings_bp
    from app.modules.symptom import symptom_bp
    from app.modules.vital_signs import vital_signs_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(patient_bp)
    app.register_blueprint(chemotherapy_bp)
    app.register_blueprint(symptom_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(notification_bp)
    app.register_blueprint(nursing_bp)
    app.register_blueprint(vital_signs_bp)
    app.register_blueprint(labs_bp)
    app.register_blueprint(settings_bp)


def _register_commands(app):
    from app.seeds.cli import seed_cli

    app.cli.add_command(seed_cli)
