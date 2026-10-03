"""Email texts (plain text). Privacy rules for every email:

- notification emails are a **summary only**: never the notification's title or message (they may
  hold medical information) — the patient reads the content after signing in
- no patient code / internal id, no access / refresh token, no password
- the verification link is the only secret in any email; it is never logged or audited
"""

from flask import current_app

from app.core.timeutil import patient_zone, to_local
from app.services.email.base import PURPOSE_NOTIFICATION, PURPOSE_VERIFICATION, EmailMessage

SECURITY_NOTE = (
    "安全提醒：本系統不會在 Email 中要求您提供密碼或驗證碼，也不會附上檔案。"
    "如果您沒有使用本系統，請忽略這封信。此信件由系統自動發送，請勿直接回覆。"
)


def _local_time(at, timezone_name):
    return to_local(at, patient_zone(timezone_name)).strftime("%Y-%m-%d %H:%M")


def verification_email(to, link, hours, timezone_name, now):
    system = current_app.config["EMAIL_SYSTEM_NAME"]
    text = "\n".join([
        f"{system}",
        "",
        "您好：",
        f"請在 {hours} 小時內開啟下列連結，完成通知 Email 的驗證（需要先登入{system}）：",
        "",
        link,
        "",
        f"寄送時間：{_local_time(now, timezone_name)}",
        "",
        SECURITY_NOTE,
    ])
    return EmailMessage(to=to, subject=f"【{system}】請驗證您的通知 Email", text=text, purpose=PURPOSE_VERIFICATION)


def notification_email(to, sent_at, timezone_name):
    """Summary only: the notification content stays in the app."""
    system = current_app.config["EMAIL_SYSTEM_NAME"]
    base = current_app.config["APP_BASE_URL"]
    text = "\n".join([
        f"{system}",
        "",
        "您有一則來自護理團隊的新通知。",
        f"發送時間：{_local_time(sent_at, timezone_name)}",
        "",
        f"請登入{system} App / 網頁查看詳細內容：",
        f"{base}/",
        "",
        "如不想再收到 Email 通知，可以在「我的」頁面關閉「接收 Email 通知」。",
        "",
        SECURITY_NOTE,
    ])
    return EmailMessage(to=to, subject=f"【{system}】您有一則新的通知", text=text, purpose=PURPOSE_NOTIFICATION)


def mask_email(address):
    """``j***@example.com`` — what staff, audit and delivery records see."""
    if not address or "@" not in address:
        return None
    local, domain = address.split("@", 1)
    return f"{local[:1]}***@{domain}"
