"""Brevo Transactional Email provider (EMAIL_PROVIDER=brevo).

    POST https://api.brevo.com/v3/smtp/email   header ``api-key``   → 201 / 202 ``{"messageId": ...}``

- The API key comes from ``EMAIL_API_KEY`` (environment only — Render Environment Variables). It is
  only ever put in the request header: never in a message, an exception, a log line or a result.
- ``EMAIL_FROM`` must be a sender verified in the Brevo account; ``EMAIL_FROM_NAME`` is its name.
- One HTTP call per message, no retry; ``EMAIL_TIMEOUT_SECONDS`` bounds each socket operation (the
  call runs inside the HTTP request that triggered it).
- Outcomes map to our own codes (base.ERROR_CODES); the provider's response body is not kept:
  2xx → sent · timeout → TIMEOUT · 4xx except 429 → PROVIDER_REJECTED (bad sender / address / key,
  no credits) · 429, 5xx, network errors → PROVIDER_ERROR.
- Standard library only (urllib): no new dependency.
"""

import html
import json
import logging
import socket
import urllib.error
import urllib.request

from app.services.email.base import (
    FAILED,
    PROVIDER_ERROR,
    PROVIDER_REJECTED,
    SENT,
    TIMEOUT,
    EmailService,
    SendResult,
)

API_URL = "https://api.brevo.com/v3/smtp/email"
logger = logging.getLogger(__name__)


def _html(text):
    """Plain-text email → minimal HTML (escaped; lines that are a URL become links)."""
    lines = []
    for line in text.split("\n"):
        escaped = html.escape(line)
        if line.startswith(("https://", "http://")) and " " not in line:
            escaped = f'<a href="{escaped}">{escaped}</a>'
        lines.append(escaped)
    return '<div style="font-family:sans-serif;font-size:15px;line-height:1.6">' + "<br>".join(lines) + "</div>"


def _is_timeout(exc):
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (TimeoutError, socket.timeout)) or isinstance(exc, (TimeoutError, socket.timeout))


class BrevoEmailService(EmailService):
    name = "brevo"

    def __init__(self, config):
        super().__init__(config)
        self._api_key = config.get("EMAIL_API_KEY") or ""

    def __repr__(self):  # never show the key
        return f"<BrevoEmailService sender={self.sender!r} timeout={self.timeout}>"

    def _payload(self, message):
        sender = {"email": self.sender}
        if self.sender_name:
            sender["name"] = self.sender_name
        return {
            "sender": sender,
            "to": [{"email": message.to}],
            "subject": message.subject,
            "textContent": message.text,
            "htmlContent": _html(message.text),
            "tags": [message.purpose],
        }

    def send(self, message):
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(self._payload(message)).encode("utf-8"),
            method="POST",
            headers={"api-key": self._api_key, "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - fixed https URL
                status = response.status
                body = response.read()
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            logger.warning("brevo send failed: HTTP %s (purpose=%s)", code, message.purpose)
            if code == 429 or code >= 500:
                return SendResult(status=FAILED, error_code=PROVIDER_ERROR)
            return SendResult(status=FAILED, error_code=PROVIDER_REJECTED)
        except (urllib.error.URLError, OSError) as exc:
            if _is_timeout(exc):
                logger.warning("brevo send timed out after %ss (purpose=%s)", self.timeout, message.purpose)
                return SendResult(status=FAILED, error_code=TIMEOUT)
            logger.warning("brevo send failed: %s (purpose=%s)", type(exc).__name__, message.purpose)
            return SendResult(status=FAILED, error_code=PROVIDER_ERROR)

        if not 200 <= status < 300:
            logger.warning("brevo send failed: HTTP %s (purpose=%s)", status, message.purpose)
            return SendResult(status=FAILED, error_code=PROVIDER_ERROR)
        try:
            message_id = json.loads(body or b"{}").get("messageId")
        except (ValueError, AttributeError):
            message_id = None
        return SendResult(status=SENT, provider_message_id=str(message_id)[:255] if message_id else None)
