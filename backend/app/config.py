import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

# Local dev servers (Vite dev / preview). Production-like environments must set CORS_ORIGINS.
_DEV_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"


def _csv(name, default=""):
    return [item.strip().rstrip("/") for item in os.environ.get(name, default).split(",") if item.strip()]


def _flag(name, default):
    raw = os.environ.get(name)
    return default if raw is None or not raw.strip() else raw.strip().lower() in ("1", "true", "yes", "on")


def _number(name, default, cast):
    """A numeric setting; None when the value is not a number (the feature using it then refuses to start)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return cast(raw.strip())
    except ValueError:
        return None


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

    # 病人與護理師聊天室 (app/modules/chat): fixed 義大醫院放射腫瘤科 service information, independent
    # of INSTITUTION (the demo organization). Hours are Asia/Taipei, Monday–Friday. The auto reply is
    # posted once per patient per Asia/Taipei day, after the patient's first message of that day.
    CHAT_SERVICE = {
        "name": "義大醫院放射腫瘤科",
        "timezone": "Asia/Taipei",
        "hours": [["09:00", "12:30"], ["13:30", "17:30"]],
        "hours_text": "星期一至五 09:00-12:30、13:30-17:30",
        "phone": "07-6150011",
        "extension": "2205",
        "phone_text": "07-6150011 分機 2205",
        "nurse_label": "放射腫瘤科護理師",
        "auto_reply_text": (
            "您好！這裡是義大醫院放射腫瘤科\n"
            "小幫手將於上班時間星期一至五，早上 09:00-12:30，以及下午 13:30-17:30，依序回覆訊息，"
            "若您有放射線治療照護上的問題皆可詢問，若您的問題無法透過訊息解決，請您於上班時間至本科掛號"
            "或是撥打電話 07-6150011 分機 2205，至放射腫瘤科門診，由醫師看診評估，謝謝您"
        ),
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

    # Email (patient contact email: verification + notification emails). EMAIL_PROVIDER picks the
    # EmailService: "disabled" (nothing is sent; deliveries are recorded as skipped / not_configured),
    # "capture" (development / tests only: messages are kept in memory and, with EMAIL_CAPTURE_DIR,
    # written as JSON files; nothing leaves the machine), "brevo" (Brevo Transactional Email API; needs
    # EMAIL_API_KEY + EMAIL_FROM, and an https APP_BASE_URL in staging / production — app/services/email/).
    EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "disabled").strip().lower()
    EMAIL_API_KEY = os.environ.get("EMAIL_API_KEY")  # secret: environment only (Render), never in the repository
    EMAIL_FROM = os.environ.get("EMAIL_FROM")
    EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "癌症照護系統")
    EMAIL_TIMEOUT_SECONDS = float(os.environ.get("EMAIL_TIMEOUT_SECONDS", "10"))  # one provider call, inside the request
    EMAIL_CAPTURE_DIR = os.environ.get("EMAIL_CAPTURE_DIR")
    # Public URL of the web app, for links in emails (email verification). No trailing slash.
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5173").rstrip("/")
    EMAIL_SYSTEM_NAME = "癌症照護系統"  # name used in every email (subject, body, sign-in button)
    EMAIL_VERIFICATION_HOURS = 24
    EMAIL_VERIFICATION_RESEND_SECONDS = 60

    # 皮膚照片 (Phase 1b; docs/skin-photo-deployment.md). Off unless every condition holds (see
    # app/services/photo_storage.py: photo_feature_status): PHOTO_UPLOADS_ENABLED, a storage backend
    # allowed in this environment, and — in staging / production — a hospital-approved consent text
    # (PHOTO_CONSENT_APPROVED_VERSION = the version in app/modules/photo/consent.py). Never falls back
    # to another backend: an unusable configuration disables uploads.
    PHOTO_UPLOADS_ENABLED = _flag("PHOTO_UPLOADS_ENABLED", False)
    # "database" = DatabasePhotoStorage (BYTEA in PostgreSQL; needs PHOTO_DB_QUOTA_BYTES) — the production choice;
    # "local" = LocalPrivateStorage (development / tests only, refused in staging / production);
    # "object" = PrivateObjectStorage (S3-compatible private bucket; needs the PHOTO_OBJECT_* settings; future option)
    PHOTO_STORAGE_BACKEND = os.environ.get("PHOTO_STORAGE_BACKEND", "").strip().lower()
    PHOTO_LOCAL_DIR = os.environ.get("PHOTO_LOCAL_DIR")  # default: <instance>/private-photos (git-ignored)
    PHOTO_OBJECT_BUCKET = os.environ.get("PHOTO_OBJECT_BUCKET")
    PHOTO_OBJECT_REGION = os.environ.get("PHOTO_OBJECT_REGION")
    PHOTO_OBJECT_ENDPOINT_URL = os.environ.get("PHOTO_OBJECT_ENDPOINT_URL")  # optional (non-AWS S3-compatible)
    PHOTO_OBJECT_KMS_KEY_ID = os.environ.get("PHOTO_OBJECT_KMS_KEY_ID")  # customer-managed key (SSE-KMS)
    PHOTO_OBJECT_ACCESS_KEY_ID = os.environ.get("PHOTO_OBJECT_ACCESS_KEY_ID")  # secret: environment only
    PHOTO_OBJECT_SECRET_ACCESS_KEY = os.environ.get("PHOTO_OBJECT_SECRET_ACCESS_KEY")  # secret: environment only
    PHOTO_CONSENT_APPROVED_VERSION = os.environ.get("PHOTO_CONSENT_APPROVED_VERSION")
    # The S3 client (PrivateObjectStorage builds it once from these; every photo request shares it). An
    # unreachable or hanging bucket must not hold a request (gunicorn timeout 60 s): one S3 call waits at
    # most MAX_ATTEMPTS × (CONNECT + READ) seconds — at most 20 s, or photos stay off (photo_storage.py).
    PHOTO_OBJECT_CONNECT_TIMEOUT = _number("PHOTO_OBJECT_CONNECT_TIMEOUT", 3.0, float)  # seconds
    PHOTO_OBJECT_READ_TIMEOUT = _number("PHOTO_OBJECT_READ_TIMEOUT", 5.0, float)  # seconds without data from S3
    PHOTO_OBJECT_MAX_ATTEMPTS = _number("PHOTO_OBJECT_MAX_ATTEMPTS", 2, int)  # including the first; 1 = no retry
    # Database photo storage ("database" = DatabasePhotoStorage, images as BYTEA in symptom_photo_blobs). The quota
    # is the sum of stored image bytes, set per environment (no default: photos stay off without it — e.g.
    # 209715200 = 200 MiB for the first test deployment). Warnings in the log from WARN / CRITICAL percent.
    PHOTO_DB_QUOTA_BYTES = _number("PHOTO_DB_QUOTA_BYTES", None, int)
    PHOTO_DB_WARN_PERCENT = _number("PHOTO_DB_WARN_PERCENT", 70, int)
    PHOTO_DB_CRITICAL_PERCENT = _number("PHOTO_DB_CRITICAL_PERCENT", 85, int)
    PHOTO_DB_DISK_BYTES = _number("PHOTO_DB_DISK_BYTES", None, int)  # optional: the database disk size, for the report
    PHOTO_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
    PHOTO_MAX_PIXELS = 40_000_000  # checked before decoding (decompression bombs)
    PHOTO_MIN_SIDE = 64
    PHOTO_FULL_MAX_SIDE = 1600
    PHOTO_THUMB_MAX_SIDE = 320
    PHOTO_MAX_PER_RECORD = 3
    PHOTO_MAX_PER_DAY = 20  # uploads per patient per Asia/Taipei day (pending ones count too)
    PHOTO_MAX_PER_10_MINUTES = 8
    PHOTO_PENDING_TTL_HOURS = 24  # pending photos older than this are discarded by the clean-up


class DevelopmentConfig(Config):
    DEBUG = True
    # photos: private files under the git-ignored instance folder (or PHOTO_LOCAL_DIR); draft consent text
    PHOTO_UPLOADS_ENABLED = _flag("PHOTO_UPLOADS_ENABLED", True)
    PHOTO_STORAGE_BACKEND = os.environ.get("PHOTO_STORAGE_BACKEND", "local").strip().lower()
    EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "capture").strip().lower()
    EMAIL_CAPTURE_DIR = os.environ.get("EMAIL_CAPTURE_DIR", "instance/email-outbox")  # git-ignored


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
    EMAIL_PROVIDER = "capture"  # in memory; tests read the captured messages
    EMAIL_CAPTURE_DIR = None
    PHOTO_UPLOADS_ENABLED = True
    PHOTO_STORAGE_BACKEND = "local"
    PHOTO_LOCAL_DIR = None  # a fresh temporary directory per app (create_app)


config_by_name = {
    "development": DevelopmentConfig,
    "staging": StagingConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}

# Environments that run with ProductionConfig hardening.
PRODUCTION_LIKE = ("staging", "production")
