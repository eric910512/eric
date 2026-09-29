import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

# Local dev servers (Vite dev / preview). Production-like environments must set CORS_ORIGINS.
_DEV_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"


def _csv(name, default=""):
    return [item.strip().rstrip("/") for item in os.environ.get(name, default).split(",") if item.strip()]


def _database_url():
    url = os.environ.get("DATABASE_URL", "sqlite:///app.db")
    # Some hosts hand out "postgres://"; SQLAlchemy only accepts "postgresql://".
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # pre_ping: reconnect transparently after the database closed an idle connection.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # JWT (api-design.md §1.5). Access tokens are sent as "Authorization: Bearer <token>".
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", SECRET_KEY)
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=int(os.environ.get("JWT_ACCESS_TOKEN_MINUTES", "15")))
    JWT_TOKEN_LOCATION = ["headers"]

    # Refresh token (api-design.md §1.5): an HttpOnly cookie scoped to the auth endpoints, on the
    # frontend's own origin (the API is served same-origin under /api — Render rewrite / Vite proxy).
    # No Domain attribute: host-only. Never readable by page JavaScript, never in a response body.
    REFRESH_COOKIE_NAME = "refresh_token"
    REFRESH_COOKIE_PATH = "/api/v1/auth"
    REFRESH_COOKIE_SAMESITE = "Strict"
    # Browsers accept Secure cookies from http://localhost / 127.0.0.1 (trustworthy origins), so the
    # attribute stays on everywhere, including development.
    REFRESH_COOKIE_SECURE = True

    # Browser origins allowed to call /api/* (comma-separated CORS_ORIGINS).
    CORS_ORIGINS = _csv("CORS_ORIGINS", _DEV_ORIGINS)
    # Number of reverse proxies in front of the app whose X-Forwarded-* headers are trusted.
    PROXY_FIX_HOPS = int(os.environ.get("PROXY_FIX_HOPS", "0"))
    # `flask seed dev` (synthetic demo data) is allowed in this environment.
    ALLOW_DEMO_SEED = True

    # Account lockout after repeated failed logins (users.failed_login_count / locked_until).
    LOGIN_MAX_FAILED_ATTEMPTS = 5
    LOGIN_LOCKOUT_MINUTES = 15

    # Phase 1–2 institution settings come from config (Phase 3: institution_settings table).
    INSTITUTION = {
        "organization": {"name": "Demo 醫院", "department": "腫瘤內科"},
        "contacts": [{"key": "leave", "label": "請假專線", "phone": "07-000-0000"}],
        "disclaimers": {
            "treatment_progress_disclaimer": "以上療程次數僅供參考，正確資訊請依醫療團隊告知為準",
        },
    }

    # Thresholds for vital-sign flags: *_high flags value >= threshold, *_low flags value <= threshold.
    VITAL_REFERENCE_RANGES = {
        "temperature_c": {"warning_high": 37.5, "critical_high": 38.0},
        "heart_rate_bpm": {"warning_low": 50, "warning_high": 100, "critical_high": 130},
        "systolic_bp_mmhg": {"warning_low": 90, "warning_high": 160},
        "spo2_pct": {"warning_low": 94, "critical_low": 90},
        "weight_change_pct_7d": {"warning_low": -3.0},
    }

    DEFAULT_PATIENT_SYMPTOM_FORM = "daily_chemo_check"


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    """Hardened settings; create_app() refuses to start unless the environment is safe
    (strong secrets, PostgreSQL, explicit CORS origins, no debug)."""

    DEBUG = False
    TESTING = False
    CORS_ORIGINS = _csv("CORS_ORIGINS")  # no default: must be set explicitly
    PROXY_FIX_HOPS = int(os.environ.get("PROXY_FIX_HOPS", "1"))  # one proxy in front (e.g. Render)
    # Recycle before the typical idle-connection cut-off of hosted PostgreSQL.
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    ALLOW_DEMO_SEED = False  # real patient data only


class StagingConfig(ProductionConfig):
    """Staging / demo deployment: the same hardening as production, but loading the
    synthetic demo data (`flask seed dev`) is allowed. No real patient data."""

    ALLOW_DEMO_SEED = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    JWT_SECRET_KEY = "testing-only-jwt-secret-key-at-least-32-bytes"


config_by_name = {
    "development": DevelopmentConfig,
    "staging": StagingConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}

# Environments that run with ProductionConfig hardening.
PRODUCTION_LIKE = ("staging", "production")
