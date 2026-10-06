"""「每日症狀與自我照護回報」form (``rt_daily_report``): the question texts and options exactly as
provided by the clinical team, in their order. Used by the development seed; production gets the
same rows from the data migration ``f1a7c3d2b9e4`` (which carries a literal copy of this data —
test_rt_daily_report checks that both stay identical).

- Two sections = two symptom categories (the patient page groups the questions by category).
- 0–10 questions are ``scale`` (higher is worse); everything else is ``single_choice`` with the
  options in the given order (「有／沒有」 too, so the order and wording stay as given).
- New definitions only: the existing ``pain`` / ``fatigue`` definitions (and their alert rules)
  are not reused, so no alert rule applies to this form; "needs attention" for the 0–10 questions
  is the existing score ≥ 7 rule.
- One report per patient per local day (enforced in app/modules/symptom/services.py).
"""

FORM_CODE = "rt_daily_report"
FORM = {
    "code": FORM_CODE,
    "name": "每日症狀與自我照護回報",
    "intended_for": "patient",
    "recall_period_hours": 24,
    "availability": "always",
    "version": 1,
    "is_active": True,
}

CATEGORIES = [
    # code, name_zh (section title), display_order
    ("rt_symptom_24h", "過去 24 小時症狀自主管理", 10),
    ("daily_self_care", "我的每日自評", 11),
]

YES_NO = [("yes", "有", 1), ("no", "沒有", 0)]  # value_code, label, score (有 first, as given)

# code, category, question text (also the name), value_type, higher_is_worse, options [(value_code, label, score)]
DEFINITIONS = [
    ("rt_pain_skin", "rt_symptom_24h", "疼痛－放射線皮膚發紅、脫皮導致", "scale", True, None),
    ("rt_pain_oral", "rt_symptom_24h", "疼痛－口腔黏膜紅腫、發炎導致", "scale", True, None),
    ("rt_dermatitis_redness", "rt_symptom_24h", "放射線皮膚炎－發紅情形", "single_choice", True, [
        ("none", "無", 0), ("light", "淺紅、粉紅", 1), ("dark", "深紅", 2),
    ]),
    ("rt_dermatitis_desquamation", "rt_symptom_24h", "放射線皮膚炎－脫皮、脫屑情形", "single_choice", True, [
        ("none", "無", 0), ("dry", "乾燥、有脫皮脫屑", 1), ("moist", "潮濕、有脫皮脫屑", 2), ("bleeding", "有脫皮脫屑伴出血", 3),
    ]),
    ("rt_appetite_poor", "rt_symptom_24h", "食慾不佳", "scale", True, None),
    ("rt_fatigue", "rt_symptom_24h", "疲倦", "scale", True, None),
    ("self_care_moisturizer", "daily_self_care", "我今天擦保濕乳液或醫師開的藥膏了嗎？", "single_choice", False, YES_NO),
    ("self_care_towel_pat", "daily_self_care", "我今天洗完澡有用毛巾「按壓」，沒有來回摩擦皮膚嗎？", "single_choice", False, YES_NO),
    ("self_care_mouth_rinse", "daily_self_care", "除了睡覺以外，我有每個小時，以及飯後都有確實漱口嗎？", "single_choice", False, YES_NO),
]


def seed_rt_daily_report(upsert, track, models):
    """Create / refresh the form with the development seed's ``_upsert`` / ``_Tracker``."""
    from decimal import Decimal

    categories = {
        code: track(upsert(models.SymptomCategory, {"code": code}, {"name_zh": name, "display_order": order}))
        for code, name, order in CATEGORIES
    }
    form = track(upsert(models.SymptomForm, {"code": FORM_CODE}, {k: v for k, v in FORM.items() if k != "code"}))
    for order, (code, category, text, value_type, worse, options) in enumerate(DEFINITIONS, start=1):
        scale = value_type == "scale"
        definition = track(upsert(models.SymptomDefinition, {"code": code}, {
            "category": categories[category], "name_zh": text, "question_text": text, "value_type": value_type,
            "min_value": Decimal("0") if scale else None, "max_value": Decimal("10") if scale else None,
            "step": Decimal("1") if scale else None, "min_label": None, "max_label": None,
            "higher_is_worse": worse, "is_system": True, "is_active": True,
        }))
        for position, (value_code, label, score) in enumerate(options or [], start=1):
            track(upsert(models.SymptomDefinitionOption, {"definition_id": definition.id, "value_code": value_code},
                         {"label_zh": label, "score": Decimal(score), "display_order": position, "is_active": True}))
        track(upsert(models.SymptomFormItem, {"form_id": form.id, "definition_id": definition.id},
                     {"display_order": order, "is_required": True}))
    return form
