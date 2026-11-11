"""「放射治療皮膚反應自我評估」form (``rt_skin_self_assessment``, Phase 1a, text only — no photos).

**Every patient-facing wording here is PENDING CLINICAL REVIEW** (docs/skin-self-assessment-clinical-review.md):
the new questions and options below are drafts, not an approved clinical scale. Patients describe what
they see in plain words; they never pick a CTCAE / RTOG grade. Professional grading stays with the nurse
(the standard is not confirmed yet — CTCAE v5.0 is only a candidate).

- Reuses the existing questions of the daily report (``rt_daily_report``) as they are — same
  definitions, wording and options: ``rt_dermatitis_redness``, ``rt_dermatitis_desquamation``,
  ``rt_pain_skin``. They stay in their own category; nothing about the daily report changes.
- New questions only for what the daily report does not ask: 搔癢, 破皮或滲液, and the
  「不確定，請護理師協助評估」 request. They live in a new category, which the home symptom chart leaves
  out (yes / no answers are not score lines — same as the self-care questions).
- Every question is optional (a patient who is unsure may answer nothing but the request); the
  patient page asks for at least one answer. Notes go in ``symptom_records.notes``.
- No alert rule applies to this form (thresholds are pending clinical review); the existing score ≥ 7
  "needs attention" rule still applies to the reused 0–10 pain question.
- Not once per day: a patient may report again whenever the skin changes.

Used by the development seed; production-like databases get the same rows from the data migration
``c3e9a1f4b7d2`` (a literal copy of this data — test_skin_self_assessment checks both stay identical).
"""

FORM_CODE = "rt_skin_self_assessment"
FORM = {
    "code": FORM_CODE,
    "name": "放射治療皮膚反應自我評估",
    "intended_for": "patient",
    "recall_period_hours": None,
    "availability": "always",
    "version": 1,
    "is_active": True,
}

CATEGORY = ("skin_self_assessment", "皮膚狀況自我評估", 12)  # code, name_zh, display_order

YES_NO = [("yes", "有", 1), ("no", "沒有", 0)]  # value_code, label, score (same as the daily report)

# New definitions: code, name (in summaries: 「搔癢：有」, 「需要護理師協助評估：是」), question text,
# options [(value_code, label, score)] — all single_choice
NEW_DEFINITIONS = [
    ("skin_itch", "搔癢", "搔癢", YES_NO),
    ("skin_broken_exudate", "破皮或有滲液", "破皮或有滲液", YES_NO),
    ("skin_assist_request", "需要護理師協助評估", "不確定自己的狀況，請護理師協助評估", [("yes", "是", 1)]),
]

# The form's questions in order: (definition code, is_required). Existing codes come from rt_daily_report.
ITEMS = [
    ("rt_dermatitis_redness", False),
    ("rt_dermatitis_desquamation", False),
    ("rt_pain_skin", False),
    ("skin_itch", False),
    ("skin_broken_exudate", False),
    ("skin_assist_request", False),
]
REUSED = ("rt_dermatitis_redness", "rt_dermatitis_desquamation", "rt_pain_skin")


def seed_skin_self_assessment(upsert, track, models):
    """Create / refresh the form with the development seed's ``_upsert`` / ``_Tracker``. Runs after
    ``seed_rt_daily_report`` (the reused questions must exist); never changes those questions."""
    from decimal import Decimal

    from sqlalchemy import select

    from app.extensions import db

    code, name, order = CATEGORY
    category = track(upsert(models.SymptomCategory, {"code": code}, {"name_zh": name, "display_order": order}))
    form = track(upsert(models.SymptomForm, {"code": FORM_CODE}, {k: v for k, v in FORM.items() if k != "code"}))
    for code, name, text, options in NEW_DEFINITIONS:
        definition = track(upsert(models.SymptomDefinition, {"code": code}, {
            "category": category, "name_zh": name, "question_text": text, "value_type": "single_choice",
            "min_value": None, "max_value": None, "step": None, "min_label": None, "max_label": None,
            "higher_is_worse": True, "is_system": True, "is_active": True,
        }))
        for position, (value_code, label, score) in enumerate(options, start=1):
            track(upsert(models.SymptomDefinitionOption, {"definition_id": definition.id, "value_code": value_code},
                         {"label_zh": label, "score": Decimal(score), "display_order": position, "is_active": True}))
    for position, (code, required) in enumerate(ITEMS, start=1):
        definition = db.session.execute(select(models.SymptomDefinition).filter_by(code=code)).scalar_one()
        track(upsert(models.SymptomFormItem, {"form_id": form.id, "definition_id": definition.id},
                     {"display_order": position, "is_required": required}))
    return form
