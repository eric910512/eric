"""Lab result helpers: abnormal flags, patient-friendly wording, input ranges.

Reference and critical ranges live in ``lab_test_types`` (data, not code); results store a
snapshot of the unit and reference range at the time they were recorded.
"""

from decimal import Decimal

# Plausible input ranges (reject typos, not a clinical judgement) and stored precision.
INPUT_RANGES = {
    "WBC": (Decimal("0"), Decimal("500"), 2),
    "ANC": (Decimal("0"), Decimal("200"), 2),
    "HGB": (Decimal("1"), Decimal("25"), 1),
    "PLT": (Decimal("0"), Decimal("3000"), 0),
}

FLAG_LEVEL = {"LL": "critical", "HH": "critical", "L": "warning", "H": "warning", "N": None}

# Patient view: plain names and what an abnormal value means day to day.
PATIENT_LABELS = {"WBC": "白血球", "ANC": "嗜中性白血球（抵抗力）", "HGB": "血色素", "PLT": "血小板"}
_PATIENT_STATUS = {
    "N": ("normal", "正常"),
    "L": ("low", "偏低"),
    "LL": ("very_low", "過低"),
    "H": ("high", "偏高"),
    "HH": ("very_high", "過高"),
}
_EXPLAIN_LOW = {
    "ANC": ("抵抗力偏低：避免出入人多的地方、勤洗手；發燒 38°C 以上請立即就醫。",
            "抵抗力很低，感染風險高：請避免外出和生食，一旦發燒請立即就醫。"),
    "WBC": ("白血球偏低，抵抗力較弱，請注意防護與手部清潔。",
            "白血球過低，感染風險高，請與醫療團隊聯繫。"),
    "HGB": ("血色素偏低，可能容易疲倦或頭暈，起身時請放慢動作。",
            "血色素過低，如果頭暈、喘或心悸，請立即聯絡醫療團隊。"),
    "PLT": ("血小板偏低，容易瘀青或出血：避免碰撞，使用軟毛牙刷。",
            "血小板過低：若有出血不止、黑便或血尿，請立即就醫。"),
}
_EXPLAIN_HIGH = "數值偏高，醫療團隊會一併評估。"


def abnormal_flag(test_type, value):
    """N / L / H / LL / HH from the test type's current ranges."""
    if value is None:
        return None
    value = Decimal(value)
    if test_type.critical_low is not None and value <= test_type.critical_low:
        return "LL"
    if test_type.critical_high is not None and value >= test_type.critical_high:
        return "HH"
    if test_type.ref_low is not None and value < test_type.ref_low:
        return "L"
    if test_type.ref_high is not None and value > test_type.ref_high:
        return "H"
    return "N"


def format_value(result):
    code = result.test_type.code
    decimals = INPUT_RANGES.get(code, (None, None, 2))[2]
    value = float(result.value_numeric) if result.value_numeric is not None else None
    return value if decimals else (int(value) if value is not None else None)


def patient_view(result):
    """Simplified result for the patient: friendly name, value, status word, what it means."""
    code = result.test_type.code
    status, status_text = _PATIENT_STATUS.get(result.abnormal_flag or "N", ("normal", "正常"))
    explanation = None
    if result.abnormal_flag in ("L", "LL"):
        mild, severe = _EXPLAIN_LOW.get(code, ("數值偏低，醫療團隊會一併評估。",) * 2)
        explanation = severe if result.abnormal_flag == "LL" else mild
    elif result.abnormal_flag in ("H", "HH"):
        explanation = _EXPLAIN_HIGH
    return {
        "code": code,
        "label": PATIENT_LABELS.get(code, result.test_type.name_zh),
        "value": format_value(result),
        "unit": result.unit,
        "status": status,
        "status_text": status_text,
        "level": FLAG_LEVEL.get(result.abnormal_flag or "N"),
        "explanation": explanation,
    }
