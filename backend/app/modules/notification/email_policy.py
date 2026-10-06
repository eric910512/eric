"""Email content policy for nurse-sent notifications — the single place that decides what an email
may contain. Called by delivery.plan_email() (service layer), so neither the frontend nor a direct
API call can get past it.

- The nurse picks a topic (``category``) and an email mode (``none`` / ``summary`` / ``full``).
- Only non-sensitive topics (行程與報到, 就診準備) may send ``full`` (title + content). For every
  other topic ``full`` is downgraded to ``summary`` (no error: requested / effective are recorded).
- No topic → ``clinical_other`` (sensitive); no mode → ``summary``.
- A ``summary`` shows the nurse's title only for non-sensitive topics; sensitive ones get a generic
  title, because the free-text title may carry a diagnosis or a lab value.
- No keyword / content detection: the decision rests on the topic only.
- The app notification itself is never affected.
"""

from app.models.enums import EmailMode, ReminderCategory

DEFAULT_CATEGORY = ReminderCategory.CLINICAL_OTHER
DEFAULT_MODE = EmailMode.SUMMARY

# topic → (label, may the email show the title and content?)
CATEGORIES = {
    ReminderCategory.SCHEDULE: ("行程與報到", True),
    ReminderCategory.PREPARATION: ("就診準備", True),
    ReminderCategory.MEDICATION: ("用藥與治療", False),
    ReminderCategory.SYMPTOM_FOLLOWUP: ("症狀與照護追蹤", False),
    ReminderCategory.CLINICAL_OTHER: ("其他醫療相關", False),
}
GENERIC_TITLE = "您有一則來自護理團隊的醫療照護通知"


def is_sensitive(category):
    """Unknown / missing topics are sensitive (fail closed)."""
    return not CATEGORIES.get(category or DEFAULT_CATEGORY, ("", False))[1]


def resolve(category, requested):
    """(category, requested mode, effective mode) for a new notification."""
    category = category or DEFAULT_CATEGORY
    requested = requested or DEFAULT_MODE
    effective = requested
    if requested == EmailMode.FULL and is_sensitive(category):
        effective = EmailMode.SUMMARY
    return category, requested, effective


def email_title(category, title):
    """Title an email may show: the nurse's title for non-sensitive topics, a generic one otherwise."""
    return GENERIC_TITLE if is_sensitive(category) else title
