"""Vital-sign field metadata and reference-range flags (database-design.md §6.I).

Thresholds live in ``VITAL_REFERENCE_RANGES`` (config; Phase 3: institution_settings).
``*_high`` flags value >= threshold, ``*_low`` flags value <= threshold.
"""

from dataclasses import dataclass

from flask import current_app


@dataclass(frozen=True)
class VitalField:
    label: str
    unit: str
    minimum: float  # physiologically plausible input range (validation, not alerting)
    maximum: float
    decimals: int


VITAL_FIELDS = {
    "temperature_c": VitalField("體溫", "°C", 30, 45, 1),
    "heart_rate_bpm": VitalField("心跳", "次/分", 20, 250, 0),
    "systolic_bp_mmhg": VitalField("收縮壓", "mmHg", 50, 260, 0),
    "diastolic_bp_mmhg": VitalField("舒張壓", "mmHg", 30, 160, 0),
    "respiratory_rate": VitalField("呼吸", "次/分", 4, 60, 0),
    "spo2_pct": VitalField("血氧", "%", 50, 100, 0),
    "weight_kg": VitalField("體重", "kg", 20, 300, 1),
    "pain_score": VitalField("疼痛", "分", 0, 10, 0),
}

_DIRECTION_TEXT = {
    "temperature_c": ("偏低", "偏高"),
    "heart_rate_bpm": ("偏慢", "偏快"),
    "systolic_bp_mmhg": ("偏低", "偏高"),
    "diastolic_bp_mmhg": ("偏低", "偏高"),
    "respiratory_rate": ("偏慢", "偏快"),
    "spo2_pct": ("偏低", "偏高"),
}


def reference_ranges():
    return current_app.config["VITAL_REFERENCE_RANGES"]


def flag(field, value):
    """'critical' / 'warning' / None for one value."""
    ranges = reference_ranges().get(field)
    if value is None or not ranges:
        return None
    value = float(value)
    if "critical_high" in ranges and value >= ranges["critical_high"]:
        return "critical"
    if "critical_low" in ranges and value <= ranges["critical_low"]:
        return "critical"
    if "warning_high" in ranges and value >= ranges["warning_high"]:
        return "warning"
    if "warning_low" in ranges and value <= ranges["warning_low"]:
        return "warning"
    return None


def _direction(field, value):
    ranges = reference_ranges().get(field, {})
    low_text, high_text = _DIRECTION_TEXT.get(field, ("偏低", "偏高"))
    highs = [ranges[k] for k in ("warning_high", "critical_high") if k in ranges]
    return high_text if highs and float(value) >= min(highs) else low_text


def format_value(field, value):
    meta = VITAL_FIELDS[field]
    return f"{float(value):.{meta.decimals}f}{meta.unit}"


def flags_for(vital, *, in_nadir=False):
    """Reference-range flags for one vital_signs row, most severe first."""
    result = []
    for field in _DIRECTION_TEXT:
        value = getattr(vital, field)
        level = flag(field, value)
        if level is None:
            continue
        meta = VITAL_FIELDS[field]
        message = f"{meta.label}{_direction(field, value)}（{format_value(field, value)}）"
        if field == "temperature_c" and level == "critical":
            message = (
                f"體溫 {format_value(field, value)}，目前處於骨髓抑制期，請立即聯絡醫療團隊"
                if in_nadir else f"體溫 {format_value(field, value)}，化療期間發燒請立即聯絡醫療團隊"
            )
        result.append({"field": field, "level": level, "message": message})
    return sorted(result, key=lambda f: f["level"] != "critical")
