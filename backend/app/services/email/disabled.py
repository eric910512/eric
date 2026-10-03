"""No email provider configured (the default, also on Render until a provider is chosen).

Nothing is sent and nothing is attempted: notification deliveries are recorded as
``skipped`` / ``not_configured`` and email verification cannot be completed.
"""

from app.services.email.base import NOT_CONFIGURED, EmailService, SendResult


class DisabledEmailService(EmailService):
    name = "disabled"
    available = False

    def send(self, message):
        return SendResult(status=NOT_CONFIGURED)
