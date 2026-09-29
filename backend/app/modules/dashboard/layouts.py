"""Phase 1 dashboard layouts, defined in code (api-design.md §8.1 contract, §8.3).

Only layouts that a screen actually renders are defined: the patient home page. The nurse and
admin screens are fixed views in Phase 1 (the §8.3 nurse / admin layouts reference widgets
that do not exist yet), so no layout is served for them. Phase 2 reads ``dashboard_*`` tables
and returns the same contract.

Keep in sync with the frontend fallback ``frontend/src/config/defaults.js`` (patientLayout);
the contract test compares both.
"""

import copy

_ITEMS = [
    ("risk", "risk-summary", 3, {}),
    ("today", "today-schedule", 3, {}),
    ("progress", "treatment-progress", 3, {"show_physician": True}),
    ("report", "symptom-quick-report", 2, {}),
    ("vitals", "latest-vitals", 3, {}),
    ("labs", "lab-summary", 3, {}),
    ("trend", "symptom-trend", 4, {}),
    ("notes", "my-notifications", 2, {}),
]

PATIENT_HOME_LAYOUT = {
    "schema_version": 1,
    "layout_id": None,
    "source": "code_default",
    "scope": "role",
    "version": 1,
    "grid": {"columns": 1, "row_height": 80, "gap": 12},
    "pinned": [{"instance_key": "summary", "widget_code": "patient-summary", "config": {}}],
    "items": [
        {"instance_key": key, "widget_code": code, "position": {"x": 0, "y": y, "w": 1, "h": h}, "config": config}
        for y, (key, code, h, config) in enumerate(_ITEMS)
    ],
    "permissions": {"can_edit": False, "can_reorder": False, "can_remove": False, "can_collapse": True},
}


def patient_home_layout():
    return copy.deepcopy(PATIENT_HOME_LAYOUT)
