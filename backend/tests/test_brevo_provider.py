"""Brevo email provider (EMAIL_PROVIDER=brevo): request contract (URL, method, api-key header, JSON
body), outcome mapping (2xx sent; timeout; 4xx rejected; 429 / 5xx / network error), the API key never
in results, logs, exceptions, the database or the audit trail, factory selection and configuration
checks, and the two flows end to end with the provider: verification link (APP_BASE_URL, existing
one-time token rules) and nurse notification (committed first; a Brevo failure keeps it).

No request leaves the machine: urllib.request.urlopen is replaced by a fake in this process."""
import sys
from pathlib import Path
import io
import json
import logging
import socket
import urllib.error
import uuid
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from app import create_app
from app.extensions import db
from app.models import AuditLog, Notification, NotificationDelivery, PatientProfile
from app.seeds.dev import seed_dev_data
from app.services.email import brevo as brevo_module
from app.services.email.base import PURPOSE_NOTIFICATION, EmailMessage
from app.services.email.brevo import API_URL, BrevoEmailService
from app.services.email.factory import configuration_problems, init_email

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


KEY = "unit-test-fake-brevo-key-0123456789abcdef"  # fake, only exists inside this test process
CONFIG = {"EMAIL_PROVIDER": "brevo", "EMAIL_API_KEY": KEY, "EMAIL_FROM": "no-reply@example.test",
          "EMAIL_FROM_NAME": "化療照護", "EMAIL_TIMEOUT_SECONDS": 4, "APP_BASE_URL": "https://care.example.test"}

# ------------------------------------------------------------------ fake transport + log capture
calls = []
behaviour = {"mode": "ok"}


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(request, timeout=None):
    calls.append({"url": request.full_url, "method": request.get_method(), "headers": dict(request.header_items()),
                  "body": json.loads(request.data.decode("utf-8")), "timeout": timeout})
    mode = behaviour["mode"]
    if mode == "ok":
        return FakeResponse(201, b'{"messageId": "<202610030000.1234567890@smtp-relay.mailin.fr>"}')
    if mode == "accepted-no-body":
        return FakeResponse(202, b"")
    if mode.startswith("http-"):
        code = int(mode.split("-")[1])
        body = json.dumps({"code": "unauthorized", "message": f"Key not found: {KEY}"}).encode()  # provider echoing input
        raise urllib.error.HTTPError(request.full_url, code, "error", {}, io.BytesIO(body))
    if mode == "timeout":
        raise socket.timeout("timed out")
    if mode == "url-timeout":
        raise urllib.error.URLError(TimeoutError("timed out"))
    if mode == "dns":
        raise urllib.error.URLError(OSError("Name or service not known"))
    raise RuntimeError(mode)


brevo_module.urllib.request.urlopen = fake_urlopen

log_stream = io.StringIO()
handler = logging.StreamHandler(log_stream)
handler.setLevel(logging.DEBUG)
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.DEBUG)

# ================================================================ provider unit
svc = BrevoEmailService(CONFIG)
msg = EmailMessage(to="patient@example.test", subject="【化療照護】您有一則新的通知",
                   text="您有一則來自護理團隊的新通知。\n請登入：\nhttps://care.example.test/", purpose=PURPOSE_NOTIFICATION)
r = svc.send(msg)
c = calls[-1]
check("2xx → sent with Brevo's messageId", r.status == "sent" and r.provider_message_id.startswith("<2026") and r.error_code is None, r)
check("POST https://api.brevo.com/v3/smtp/email", c["url"] == API_URL == "https://api.brevo.com/v3/smtp/email" and c["method"] == "POST")
headers = {k.lower(): v for k, v in c["headers"].items()}
check("api-key header from EMAIL_API_KEY; JSON content type", headers.get("api-key") == KEY and headers.get("content-type") == "application/json")
b = c["body"]
check("body: sender (EMAIL_FROM + EMAIL_FROM_NAME), to, subject", b["sender"] == {"email": "no-reply@example.test", "name": "化療照護"}
      and b["to"] == [{"email": "patient@example.test"}] and b["subject"] == msg.subject, b)
check("body: textContent = the message; htmlContent escaped with the link clickable", b["textContent"] == msg.text
      and '<a href="https://care.example.test/">' in b["htmlContent"] and "<br>" in b["htmlContent"])
check("API key not in the JSON body", KEY not in json.dumps(b))
check("timeout = EMAIL_TIMEOUT_SECONDS", c["timeout"] == 4)
check("html escapes markup in the text", "&lt;b&gt;" in brevo_module._html("<b>x</b>"))

behaviour["mode"] = "accepted-no-body"
r = svc.send(msg)
check("202 without a body → sent (no message id)", r.status == "sent" and r.provider_message_id is None)

for code, expected in ((400, "PROVIDER_REJECTED"), (401, "PROVIDER_REJECTED"), (402, "PROVIDER_REJECTED"), (403, "PROVIDER_REJECTED"),
                       (429, "PROVIDER_ERROR"), (500, "PROVIDER_ERROR"), (503, "PROVIDER_ERROR")):
    behaviour["mode"] = f"http-{code}"
    r = svc.send(msg)
    check(f"HTTP {code} → failed / {expected} (no exception)", r.status == "failed" and r.error_code == expected, r)
for mode in ("timeout", "url-timeout"):
    behaviour["mode"] = mode
    r = svc.send(msg)
    check(f"{mode} → failed / TIMEOUT", r.status == "failed" and r.error_code == "TIMEOUT", r)
behaviour["mode"] = "dns"
r = svc.send(msg)
check("network error → failed / PROVIDER_ERROR", r.status == "failed" and r.error_code == "PROVIDER_ERROR", r)
check("results never carry the key or the provider response", all(KEY not in str(x) for x in (r, svc.send(msg))))
check("repr of the service hides the key", KEY not in repr(svc) and KEY not in str(svc))

# ================================================================ factory + configuration
check("EMAIL_PROVIDER=brevo with key + sender → accepted (development)", configuration_problems(CONFIG, production_like=False) == [])
check("…and in staging / production with an https APP_BASE_URL", configuration_problems(CONFIG, production_like=True) == [])
probs = configuration_problems({**CONFIG, "EMAIL_API_KEY": None}, production_like=False)
check("brevo without EMAIL_API_KEY → refused (any environment); message has no value", probs == ["EMAIL_API_KEY must be set for the email provider."], probs)
check("brevo without EMAIL_FROM → refused", configuration_problems({**CONFIG, "EMAIL_FROM": ""}, production_like=False) != [])
probs = configuration_problems({**CONFIG, "APP_BASE_URL": "http://care.example.test"}, production_like=True)
check("production-like: http APP_BASE_URL → refused", any("APP_BASE_URL" in p for p in probs) and not any(KEY in p for p in probs), probs)
check("EMAIL_PROVIDER unset → disabled, still accepted", configuration_problems({}, production_like=True) == [])
check("capture still refused in staging / production", configuration_problems({"EMAIL_PROVIDER": "capture"}, production_like=True) != [])

app = create_app("testing")
check("testing app keeps the capture provider", app.extensions["email_service"].name == "capture")
app.config.update(CONFIG)
init_email(app)
check("factory: EMAIL_PROVIDER=brevo → BrevoEmailService", isinstance(app.extensions["email_service"], BrevoEmailService)
      and app.extensions["email_service"].available)
bad = create_app("testing")
bad.config.update({**CONFIG, "EMAIL_API_KEY": ""})
try:
    init_email(bad)
    refused = False
except RuntimeError as exc:
    refused = "EMAIL_API_KEY" in str(exc)
check("init with brevo but no key → refuses to start", refused)

# ================================================================ flows with the Brevo provider
c_ = app.test_client()


def token(email, password="Demo@1234"):
    return c_.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    pt, nt = token("patient01@demo.local"), token("nurse01@demo.local")
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    pid = p1.public_id

    behaviour["mode"] = "ok"
    c_.patch("/api/v1/patients/me/profile", json={"email": "patient.one@example.test"}, headers=H(pt))
    n_calls = len(calls)
    r = c_.post("/api/v1/patients/me/email-verification", headers=H(pt))
    sent = calls[-1]["body"]
    check("verification through Brevo → 200, delivery sent", r.status_code == 200 and r.get_json()["data"]["delivery"]["status"] == "sent"
          and len(calls) == n_calls + 1, r.get_json())
    link = sent["textContent"].split("\n")[[i for i, line in enumerate(sent["textContent"].split("\n")) if "#token=" in line][0]]
    check("verification link points to APP_BASE_URL /patient/verify-email#token=…", link.startswith("https://care.example.test/patient/verify-email#token="), link)
    check("verification email: to the contact address, no patient code / ids / login email",
          sent["to"] == [{"email": "patient.one@example.test"}] and all(x not in json.dumps(sent, ensure_ascii=False)
                                                                        for x in ("P00001", pid, p1.user.public_id, p1.user.email)))
    raw = link.split("#token=", 1)[1]
    r = c_.post("/api/v1/patients/me/email-verification/confirm", json={"token": raw}, headers=H(pt))
    check("existing token rules unchanged: link verifies once…", r.status_code == 200 and r.get_json()["data"]["email_verified"] is True)
    r = c_.post("/api/v1/patients/me/email-verification/confirm", json={"token": raw}, headers=H(pt))
    check("…and not twice", r.status_code == 422 and r.get_json()["error"]["code"] == "VERIFICATION_LINK_USED")
    c_.patch("/api/v1/patients/me/profile", json={"email_notification_enabled": True}, headers=H(pt))

    body = {"patient_id": pid, "title": "明日治療提醒", "message": "明日上午 09:00 有治療行程，請提前 15 分鐘報到。"}
    r = c_.post("/api/v1/notifications", json=body, headers=H(nt))
    d = r.get_json()["data"]
    sent = calls[-1]["body"]
    row = db.session.query(NotificationDelivery).filter_by(notification_id=d["id"]).one()
    check("nurse notification → 201, email sent through Brevo", r.status_code == 201 and d["email_delivery"]["status"] == "sent" and d["status"] == "new")
    check("delivery row: provider brevo, Brevo messageId kept", row.provider == "brevo" and row.provider_message_id.startswith("<2026") and row.attempted_at and row.completed_at)
    check("notification email is the summary only (no title / content)", body["title"] not in json.dumps(sent, ensure_ascii=False)
          and body["message"] not in json.dumps(sent, ensure_ascii=False) and "您有一則來自護理團隊的新通知" in sent["textContent"])

    for mode, code in (("http-401", "PROVIDER_REJECTED"), ("http-503", "PROVIDER_ERROR"), ("timeout", "TIMEOUT")):
        behaviour["mode"] = mode
        before = db.session.query(Notification).count()
        r = c_.post("/api/v1/notifications", json={**body, "title": f"失敗測試 {mode}"}, headers=H(nt))
        d = r.get_json()["data"]
        n = db.session.get(Notification, d["id"])
        check(f"Brevo {mode} → still 201, notification kept (status new), delivery failed / {code}",
              r.status_code == 201 and n is not None and n.status == "new" and db.session.query(Notification).count() == before + 1
              and d["email_delivery"]["status"] == "failed" and d["email_delivery"]["error_code"] == code, d.get("email_delivery"))

    # the key never lands anywhere persistent or visible
    stored = json.dumps([[a.changes, a.reason, a.endpoint, a.user_agent] for a in db.session.query(AuditLog).all()], ensure_ascii=False)
    deliveries = json.dumps([[d.provider, d.provider_message_id, d.error_code, d.recipient_masked] for d in db.session.query(NotificationDelivery).all()])
    check("API key not in audit logs or delivery records", KEY not in stored and KEY not in deliveries)
    check("provider response text not stored", "Key not found" not in stored and "Key not found" not in deliveries)

logs = log_stream.getvalue()
check("logs mention failures by status / type only", "HTTP 401" in logs and "timed out" in logs)
check("API key never in any log line", KEY not in logs and "Key not found" not in logs)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
