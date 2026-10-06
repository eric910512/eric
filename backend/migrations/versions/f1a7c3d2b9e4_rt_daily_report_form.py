"""rt daily report form (data only)

「每日症狀與自我照護回報」(``rt_daily_report``): inserts the two symptom categories, the nine
symptom definitions with their options, the form and its items — **no schema change**. Production
runs with SEED_DEMO_DATA=false, so this is how the form reaches it (Render runs ``db upgrade`` on
every start). Rows that already exist (looked up by code) are left as they are, so re-running or
running after ``flask seed dev`` is safe. The data is a literal copy of app/seeds/rt_daily_report.py
(test_rt_daily_report checks that both stay identical).

Downgrade removes these rows only when no symptom report uses the form or its definitions;
otherwise it leaves them in place (reports must keep their questions).

Revision ID: f1a7c3d2b9e4
Revises: 470df9cf2163
Create Date: 2026-10-06 15:00:00

"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a7c3d2b9e4'
down_revision = '470df9cf2163'
branch_labels = None
depends_on = None

FORM_CODE = "rt_daily_report"
FORM_NAME = "每日症狀與自我照護回報"
CATEGORIES = [
    ("rt_symptom_24h", "過去 24 小時症狀自主管理", 10),
    ("daily_self_care", "我的每日自評", 11),
]
YES_NO = [("yes", "有", 1), ("no", "沒有", 0)]
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


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _id(conn, table, **where):
    clause = " AND ".join(f"{k} = :{k}" for k in where)
    return conn.execute(sa.text(f"SELECT id FROM {table} WHERE {clause}"), where).scalar()


def _insert(conn, table, values):
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    conn.execute(sa.text(f"INSERT INTO {table} ({cols}) VALUES ({params})"), values)


def upgrade():
    conn = op.get_bind()
    now = _now()
    stamps = {"created_at": now, "updated_at": now}

    category_ids = {}
    for code, name, order in CATEGORIES:
        if _id(conn, "symptom_categories", code=code) is None:
            _insert(conn, "symptom_categories", {"code": code, "name_zh": name, "display_order": order, **stamps})
        category_ids[code] = _id(conn, "symptom_categories", code=code)

    if _id(conn, "symptom_forms", code=FORM_CODE) is None:
        _insert(conn, "symptom_forms", {
            "code": FORM_CODE, "name": FORM_NAME, "intended_for": "patient", "recall_period_hours": 24,
            "availability": "always", "version": 1, "is_active": True, **stamps,
        })
    form_id = _id(conn, "symptom_forms", code=FORM_CODE)

    for order, (code, category, text, value_type, worse, options) in enumerate(DEFINITIONS, start=1):
        scale = value_type == "scale"
        if _id(conn, "symptom_definitions", code=code) is None:
            _insert(conn, "symptom_definitions", {
                "code": code, "category_id": category_ids[category], "name_zh": text, "question_text": text,
                "value_type": value_type, "min_value": 0 if scale else None, "max_value": 10 if scale else None,
                "step": 1 if scale else None, "higher_is_worse": worse, "is_system": True, "is_active": True, **stamps,
            })
        definition_id = _id(conn, "symptom_definitions", code=code)
        for position, (value_code, label, score) in enumerate(options or [], start=1):
            if _id(conn, "symptom_definition_options", definition_id=definition_id, value_code=value_code) is None:
                _insert(conn, "symptom_definition_options", {
                    "definition_id": definition_id, "value_code": value_code, "label_zh": label, "score": score,
                    "display_order": position, "is_active": True, **stamps,
                })
        if _id(conn, "symptom_form_items", form_id=form_id, definition_id=definition_id) is None:
            _insert(conn, "symptom_form_items", {
                "form_id": form_id, "definition_id": definition_id, "display_order": order, "is_required": True, **stamps,
            })


def downgrade():
    conn = op.get_bind()
    form_id = _id(conn, "symptom_forms", code=FORM_CODE)
    codes = [d[0] for d in DEFINITIONS]
    definition_ids = [i for i in (_id(conn, "symptom_definitions", code=c) for c in codes) if i is not None]
    used = 0
    if form_id is not None:
        used += conn.execute(sa.text("SELECT COUNT(*) FROM symptom_records WHERE form_id = :f"), {"f": form_id}).scalar()
    for definition_id in definition_ids:
        used += conn.execute(sa.text("SELECT COUNT(*) FROM symptom_record_values WHERE definition_id = :d"), {"d": definition_id}).scalar()
    if used:
        return  # reports exist: keep the form and its questions
    if form_id is not None:
        conn.execute(sa.text("DELETE FROM symptom_form_items WHERE form_id = :f"), {"f": form_id})
        conn.execute(sa.text("DELETE FROM symptom_forms WHERE id = :f"), {"f": form_id})
    for definition_id in definition_ids:
        conn.execute(sa.text("DELETE FROM symptom_form_items WHERE definition_id = :d"), {"d": definition_id})
        conn.execute(sa.text("DELETE FROM symptom_definition_options WHERE definition_id = :d"), {"d": definition_id})
        conn.execute(sa.text("DELETE FROM symptom_definitions WHERE id = :d"), {"d": definition_id})
    for code, _name, _order in CATEGORIES:
        remaining = conn.execute(sa.text(
            "SELECT COUNT(*) FROM symptom_definitions d JOIN symptom_categories c ON c.id = d.category_id WHERE c.code = :c"
        ), {"c": code}).scalar()
        if not remaining:
            conn.execute(sa.text("DELETE FROM symptom_categories WHERE code = :c"), {"c": code})
