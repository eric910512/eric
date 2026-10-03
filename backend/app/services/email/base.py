"""EmailService interface (patient contact email).

Callers build an ``EmailMessage`` and call ``send``; they never talk to a provider directly, so a
real provider (Resend / SendGrid / SES / SMTP, not chosen yet) only needs a new class registered in
``factory.py``.

Contract for every implementation:
- ``send`` returns a ``SendResult``; a provider problem is reported as ``failed`` with one of our
  own ``error_code`` values (``ERROR_CODES``), never with the provider's raw response.
- One call must not take longer than ``timeout`` seconds (the call runs inside the HTTP request).
- Exceptions may still escape (network, bugs): the caller treats them as ``PROVIDER_ERROR`` /
  ``TIMEOUT``, so a failed email can never fail the request that triggered it.
"""

from dataclasses import dataclass

# purpose of a message (templates, tests; the capture provider's test hook)
PURPOSE_VERIFICATION = "verification"
PURPOSE_NOTIFICATION = "notification"

# SendResult.status
SENT = "sent"
FAILED = "failed"
NOT_CONFIGURED = "not_configured"  # nothing was attempted: no provider is configured

# SendResult.error_code (our own codes; safe to store and audit)
TIMEOUT = "TIMEOUT"
PROVIDER_REJECTED = "PROVIDER_REJECTED"
PROVIDER_ERROR = "PROVIDER_ERROR"
ERROR_CODES = (TIMEOUT, PROVIDER_REJECTED, PROVIDER_ERROR)


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    purpose: str


@dataclass(frozen=True)
class SendResult:
    status: str
    provider_message_id: str | None = None
    error_code: str | None = None


class EmailService:
    """Base class. ``name`` is stored in ``notification_deliveries.provider``."""

    name = "base"
    #: False for the disabled service: nothing can be delivered (deliveries become skipped / not_configured)
    available = True

    def __init__(self, config):
        self.timeout = float(config.get("EMAIL_TIMEOUT_SECONDS") or 10)
        self.sender = config.get("EMAIL_FROM")
        self.sender_name = config.get("EMAIL_FROM_NAME")

    def send(self, message: EmailMessage) -> SendResult:  # pragma: no cover - interface
        raise NotImplementedError
