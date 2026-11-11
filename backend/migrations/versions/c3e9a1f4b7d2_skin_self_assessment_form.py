"""skin self assessment form (data only)

「放射治療皮膚反應自我評估」(``rt_skin_self_assessment``, Phase 1a): inserts one symptom category,
three new symptom definitions with their options, the form and its six items — **no schema change**.
Three items reuse the existing ``rt_daily_report`` questions (``rt_dermatitis_redness``,
``rt_dermatitis_desquamation``, ``rt_pain_skin``) as they are: their rows are only looked up, never
inserted, changed or removed here. The patient-facing wording is pending clinical review.

Rows that already exist (looked up by code) are left as they are, so re-running or running after
``flask seed dev`` is safe. The data is a literal copy of app/seeds/skin_self_assessment.py
(test_skin_self_assessment checks that both stay identical).

Downgrade removes this form's rows only when no symptom report uses the form or its new questions;
otherwise it leaves them in place (reports must keep their questions). It never touches the reused
questions or anything of ``rt_daily_report``.

Revision ID: c3e9a1f4b7d2
Revises: b5cd01c11e1d
Create Date: 2026-11-11 10:00:00

"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3e9a1f4b7d2'
down_revision = 'b5cd01c11e1d'
branch_labels = None
depends_on = None

FORM_CODE = "rt_skin_self_assessment"
FORM_NAME = "放射治療皮膚反應自我評估"
CATEGORY = ("skin_self_assessment", "皮膚狀況自我評估", 12)
YES_NO = [("yes", "有", 1), ("no", "沒有", 0)]
NEW_DEFINITIONS = [
    ("skin_itch", "搔癢", "搔癢", YES_NO),
    ("skin_broken_exudate", "破皮或有滲液", "破皮或有滲液", YES_NO),
    ("skin_assist_request", "需要護理師協助評估", "不確定自己的狀況，請護理師協助評估", [("yes", "是", 1)]),
]
ITEMS = [
    ("rt_dermatitis_redness", False),
    ("rt_dermatitis_desquamation", False),
    ("rt_pain_skin", False),
    ("skin_itch", False),
    ("skin_broken_exudate", False),
    ("skin_assist_request", False),
]
REUSED = ("rt_dermatitis_redness", "rt_dermatitis_desquamation", "rt_pain_skin")


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

    code, name, order = CATEGORY
    if _id(conn, "symptom_categories", code=code) is None:
        _insert(conn, "symptom_categories", {"code": code, "name_zh": name, "display_order": order, **stamps})
    category_id = _id(conn, "symptom_categories", code=code)

    if _id(conn, "symptom_forms", code=FORM_CODE) is None:
        _insert(conn, "symptom_forms", {
            "code": FORM_CODE, "name": FORM_NAME, "intended_for": "patient", "recall_period_hours": None,
            "availability": "always", "version": 1, "is_active": True, **stamps,
        })
    form_id = _id(conn, "symptom_forms", code=FORM_CODE)

    for code, name, text, options in NEW_DEFINITIONS:
        if _id(conn, "symptom_definitions", code=code) is None:
            _insert(conn, "symptom_definitions", {
                "code": code, "category_id": category_id, "name_zh": name, "question_text": text,
                "value_type": "single_choice", "higher_is_worse": True, "is_system": True, "is_active": True, **stamps,
            })
        definition_id = _id(conn, "symptom_definitions", code=code)
        for position, (value_code, label, score) in enumerate(options, start=1):
            if _id(conn, "symptom_definition_options", definition_id=definition_id, value_code=value_code) is None:
                _insert(conn, "symptom_definition_options", {
                    "definition_id": definition_id, "value_code": value_code, "label_zh": label, "score": score,
                    "display_order": position, "is_active": True, **stamps,
                })

    for position, (code, required) in enumerate(ITEMS, start=1):
        definition_id = _id(conn, "symptom_definitions", code=code)
        if definition_id is None:  # a reused rt_daily_report question (f1a7c3d2b9e4 runs first, so it exists)
            raise RuntimeError(f"symptom definition {code!r} is missing; run f1a7c3d2b9e4 first")
        if _id(conn, "symptom_form_items", form_id=form_id, definition_id=definition_id) is None:
            _insert(conn, "symptom_form_items", {
                "form_id": form_id, "definition_id": definition_id, "display_order": position, "is_required": required, **stamps,
            })


def downgrade():
    conn = op.get_bind()
    form_id = _id(conn, "symptom_forms", code=FORM_CODE)
    new_ids = [i for i in (_id(conn, "symptom_definitions", code=c) for c, _n, _t, _o in NEW_DEFINITIONS) if i is not None]
    used = 0
    if form_id is not None:
        used += conn.execute(sa.text("SELECT COUNT(*) FROM symptom_records WHERE form_id = :f"), {"f": form_id}).scalar()
    for definition_id in new_ids:
        used += conn.execute(sa.text("SELECT COUNT(*) FROM symptom_record_values WHERE definition_id = :d"), {"d": definition_id}).scalar()
    if used:
        return  # reports exist: keep the form and its questions
    if form_id is not None:
        # only this form's items: the reused questions stay in rt_daily_report
        conn.execute(sa.text("DELETE FROM symptom_form_items WHERE form_id = :f"), {"f": form_id})
        conn.execute(sa.text("DELETE FROM symptom_forms WHERE id = :f"), {"f": form_id})
    for definition_id in new_ids:
        if conn.execute(sa.text("SELECT COUNT(*) FROM symptom_form_items WHERE definition_id = :d"), {"d": definition_id}).scalar():
            continue  # another form uses it
        if conn.execute(sa.text("SELECT COUNT(*) FROM alert_rules WHERE symptom_definition_id = :d"), {"d": definition_id}).scalar():
            continue  # an alert rule was added for it later
        conn.execute(sa.text("DELETE FROM symptom_definition_options WHERE definition_id = :d"), {"d": definition_id})
        conn.execute(sa.text("DELETE FROM symptom_definitions WHERE id = :d"), {"d": definition_id})
    code = CATEGORY[0]
    remaining = conn.execute(sa.text(
        "SELECT COUNT(*) FROM symptom_definitions d JOIN symptom_categories c ON c.id = d.category_id WHERE c.code = :c"
    ), {"c": code}).scalar()
    if not remaining:
        conn.execute(sa.text("DELETE FROM symptom_categories WHERE code = :c"), {"c": code})
