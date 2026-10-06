"""Email texts (plain text + HTML). Privacy rules for every email:

- notification emails follow the content policy (app/modules/notification/email_policy.py):
  ``summary`` = system name, a title (the nurse's title only for non-sensitive topics, otherwise a
  generic one), time and a sign-in link — never the content; ``full`` = title + content, only for
  non-sensitive topics
- no patient code / internal id, no access / refresh token, no password, no login email
- the verification link is the only secret in any email; it is never logged or audited
- HTML bodies escape everything the nurse wrote; the only link is the system's own sign-in button
  (a URL typed by the nurse is never turned into a link, and is broken up so mail clients do not
  auto-link it)
"""

import html

from flask import current_app

from app.core.timeutil import patient_zone, to_local
from app.models.enums import EmailMode
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


def _subject_text(value, limit=120):
    """A single line for the Subject header (no CR / LF, bounded length)."""
    line = " ".join(str(value).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _sign_in_url():
    return f"{current_app.config['APP_BASE_URL']}/patient/notifications"


def _defang(escaped):
    """Already-escaped text with ``://`` broken by a zero-width space, so clients do not auto-link it."""
    return escaped.replace("://", ":/\u200b/")


def _html(paragraphs, link_label, link_url):
    """HTML body: escaped paragraphs (``(label, text)`` or plain text) + the system sign-in button."""
    parts = []
    for item in paragraphs:
        if isinstance(item, tuple):
            label, value = item
            body = "<br>".join(_defang(html.escape(line)) for line in str(value).split("\n"))
            parts.append(f'<p style="margin:0 0 16px"><strong>{html.escape(label)}</strong><br>{body}</p>')
        else:
            parts.append(f'<p style="margin:0 0 16px">{_defang(html.escape(item))}</p>')
    button = (f'<p style="margin:24px 0"><a href="{html.escape(link_url, quote=True)}" '
              'style="display:inline-block;padding:12px 24px;border-radius:999px;background:#1f5f7a;'
              f'color:#ffffff;text-decoration:none;font-weight:bold">{html.escape(link_label)}</a></p>')
    note = f'<p style="margin:24px 0 0;font-size:13px;color:#666">{html.escape(SECURITY_NOTE)}</p>'
    return ('<div style="font-family:sans-serif;font-size:15px;line-height:1.6;color:#222">'
            + "".join(parts) + button + note + "</div>")


def notification_email(to, *, mode, title, message, sent_at, timezone_name):
    """``summary`` or ``full`` email of a nurse-sent notification. The caller has applied the content
    policy: ``mode`` is the allowed mode and, for a summary, ``title`` is the title the email may show
    (a generic one for sensitive topics); ``message`` is only used by ``full``."""
    system = current_app.config["EMAIL_SYSTEM_NAME"]
    when = _local_time(sent_at, timezone_name)
    url = _sign_in_url()
    login = f"登入{system}"
    if mode == EmailMode.FULL:
        subject = _subject_text(f"{system}｜{title}")
        paragraphs = ["您好，", "您有一則來自護理團隊的新通知。", ("標題：", title), ("內容：", message), ("發送時間：", when)]
    else:
        subject = f"{system}｜您有一則新通知"
        paragraphs = ["您好，", "您有一則來自護理團隊的新通知。", ("通知標題：", title),
                      ("發送時間：", when), f"為保護您的醫療資訊，完整內容請登入{system}查看。"]
    lines = []
    for item in paragraphs:
        if isinstance(item, tuple):
            lines += [item[0], *str(item[1]).split("\n"), ""]
        else:
            lines += [item, ""]
    text = "\n".join([*lines, f"{login}：", url, "", SECURITY_NOTE])
    return EmailMessage(to=to, subject=subject, text=text, purpose=PURPOSE_NOTIFICATION, html=_html(paragraphs, login, url))


def mask_email(address):
    """``j***@example.com`` — what staff, audit and delivery records see."""
    if not address or "@" not in address:
        return None
    local, domain = address.split("@", 1)
    return f"{local[:1]}***@{domain}"
