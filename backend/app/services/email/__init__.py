"""Email delivery abstraction (EmailService) — see base.py for the contract, factory.py for providers."""

from app.services.email.base import EmailMessage, EmailService, SendResult
from app.services.email.factory import get_email_service, init_email, send_email

__all__ = ["EmailMessage", "EmailService", "SendResult", "get_email_service", "init_email", "send_email"]
