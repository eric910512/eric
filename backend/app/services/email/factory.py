"""Pick the EmailService from ``EMAIL_PROVIDER`` (one instance per app) and check the configuration.

Providers:
- ``disabled`` — default; nothing is sent (production / staging until a provider is chosen)
- ``capture``  — mock provider for development and tests; refused in staging / production

A real provider is added by registering its class in ``REAL_PROVIDERS``; it then needs
``EMAIL_API_KEY``, ``EMAIL_FROM`` and an https ``APP_BASE_URL`` in production-like environments
(Render Environment Variables — never in the repository).
"""

from pathlib import Path

from flask import current_app

from app.services.email.capture import CaptureEmailService
from app.services.email.disabled import DisabledEmailService

MOCK_PROVIDERS = {"capture": CaptureEmailService}
REAL_PROVIDERS = {}  # e.g. {"resend": ResendEmailService} once a provider is chosen
PROVIDERS = {"disabled": DisabledEmailService, **MOCK_PROVIDERS, **REAL_PROVIDERS}


def configuration_problems(config, production_like):
    """Why the email configuration is not acceptable. Never echoes secret values."""
    name = config.get("EMAIL_PROVIDER") or "disabled"
    if name not in PROVIDERS:
        return [f"EMAIL_PROVIDER {name!r} is not supported (one of: {', '.join(sorted(PROVIDERS))})."]
    problems = []
    if production_like and name in MOCK_PROVIDERS:
        problems.append(f"EMAIL_PROVIDER={name} is a development / test provider; use 'disabled' or a real provider.")
    if production_like and name in REAL_PROVIDERS:
        if not config.get("EMAIL_API_KEY"):
            problems.append("EMAIL_API_KEY must be set for the email provider.")
        if not config.get("EMAIL_FROM"):
            problems.append("EMAIL_FROM must be set for the email provider.")
        if not str(config.get("APP_BASE_URL") or "").startswith("https://"):
            problems.append("APP_BASE_URL must be the https:// address of the web app (used in email links).")
    return problems


def init_email(app):
    problems = configuration_problems(app.config, production_like=False)
    if problems:
        raise RuntimeError("Email configuration: " + " ".join(problems))
    config = dict(app.config)
    config["_ROOT_PATH"] = str(Path(app.root_path).parent)  # relative EMAIL_CAPTURE_DIR → backend/
    app.extensions["email_service"] = PROVIDERS[app.config.get("EMAIL_PROVIDER") or "disabled"](config)


def get_email_service():
    return current_app.extensions["email_service"]


def send_email(message):
    """Send through the configured EmailService and never raise: a timeout or any provider exception
    becomes ``failed`` with our own error code (no provider detail is kept)."""
    from app.services.email.base import FAILED, PROVIDER_ERROR, TIMEOUT, SendResult

    service = get_email_service()
    try:
        return service.send(message)
    except TimeoutError:
        current_app.logger.warning("email send timed out (provider=%s)", service.name)
        return SendResult(status=FAILED, error_code=TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - an email must never fail the request that triggered it
        # the exception type only: provider messages may echo request details
        current_app.logger.warning("email send failed (provider=%s, %s)", service.name, type(exc).__name__)
        return SendResult(status=FAILED, error_code=PROVIDER_ERROR)
