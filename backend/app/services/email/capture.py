"""Mock email provider for development and tests (EMAIL_PROVIDER=capture). Never a production provider:
create_app() refuses it in staging / production.

Messages are kept in memory (``outbox``) and, when ``EMAIL_CAPTURE_DIR`` is set, also written as one
JSON file each, which is how the API-mode E2E tests read verification links and notification
emails. Nothing leaves the machine.

Test hook (the frontend MockEmailService uses the same rule): a **notification** email to an address
whose local part ends with ``+fail`` fails with ``PROVIDER_REJECTED``, and one ending with
``+timeout`` raises ``TimeoutError``. Verification emails to those addresses succeed, so a test
can verify the address first and then watch the notification email fail.
"""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.services.email.base import FAILED, PROVIDER_REJECTED, PURPOSE_NOTIFICATION, SENT, EmailService, SendResult


def failure_mode(address, purpose):
    """``fail`` / ``timeout`` / None — the capture provider's (and the frontend mock's) test hook."""
    if purpose != PURPOSE_NOTIFICATION:
        return None
    local = (address or "").split("@", 1)[0].lower()
    if local.endswith("+fail"):
        return "fail"
    if local.endswith("+timeout"):
        return "timeout"
    return None


class CaptureEmailService(EmailService):
    name = "capture"

    def __init__(self, config):
        super().__init__(config)
        directory = config.get("EMAIL_CAPTURE_DIR")
        self.directory = Path(directory) if directory else None
        if self.directory is not None and not self.directory.is_absolute():
            self.directory = Path(config.get("_ROOT_PATH") or ".") / self.directory
        self.outbox = []
        self._lock = threading.Lock()

    def send(self, message):
        mode = failure_mode(message.to, message.purpose)
        if mode == "timeout":
            raise TimeoutError("capture provider: simulated timeout")
        status = FAILED if mode == "fail" else SENT
        message_id = f"capture-{uuid.uuid4()}"
        entry = {
            "id": message_id,
            "to": message.to,
            "subject": message.subject,
            "text": message.text,
            "purpose": message.purpose,
            "status": status,
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
        with self._lock:
            self.outbox.append(entry)
            if self.directory is not None:
                self.directory.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                (self.directory / f"{stamp}-{message_id}.json").write_text(
                    json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        if status == FAILED:
            return SendResult(status=FAILED, error_code=PROVIDER_REJECTED)
        return SendResult(status=SENT, provider_message_id=message_id)
