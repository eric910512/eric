"""Sprint 4: nursing assessments — draft → edit → sign → locked → correction (new version,
original amended) → version history, items follow-up, timeline, risk, privacy, permissions,
idempotency, audit."""
import sys
from pathlib import Path
import json
import uuid
import warnings
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from app import create_app
from app.extensions import db
from app.models import AuditLog, NursingAssessment, PatientProfile, User
from app.models.base import utcnow
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()


def token(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", **({"Idempotency-Key": key} if key else {})}


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code"), [d.get("field") for d in j.get("error", {}).get("details", [])]


def iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


URL = "/api/v1/nursing-assessments"

with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    at, nt, pt = token("admin01@demo.local"), token("nurse01@demo.local"), token("patient01@demo.local")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    P1 = p1.public_id
    # a second nurse assigned to the same patient, and one not assigned
    n2 = User(role=nurse.role, email="n2@demo.local", password_hash=nurse.password_hash, display_name="測試護理師 陳", password_changed_at=nurse.password_changed_at)
    n3 = User(role=nurse.role, email="n3@demo.local", password_hash=nurse.password_hash, display_name="其他護理師", password_changed_at=nurse.password_changed_at)
    db.session.add_all([n2, n3])
    db.session.commit()
    c.post(f"/api/v1/patients/{P1}/nurse-assignments", json={"nurse_id": n2.public_id}, headers=H(at))
    n2t, n3t = token("n2@demo.local"), token("n3@demo.local")

    # ================================================================ draft
    body = {"patient_id": P1, "assessment_type": "pre_chemo", "assessed_at": iso(utcnow() - timedelta(minutes=5)),
            "ecog_status": 1, "overall_condition": "stable", "risk_level": "low", "chemo_readiness": "ready",
            "subjective": "食慾稍差，手指偶有麻感", "objective": "口腔黏膜完整",
            "items": [{"item_type": "problem", "code": "neuropathy_risk", "description": "周邊神經病變風險", "priority": "medium"},
                      {"item_type": "education", "description": "衛教避免接觸冰冷物品"}]}
    check("patient cannot create / read assessments → 403", c.post(URL, json=body, headers=H(pt)).status_code == 403
          and c.get(f"{URL}?patient_id=me", headers=H(pt)).status_code == 403)
    check("admin cannot create (nurse only) → 403", c.post(URL, json=body, headers=H(at)).status_code == 403)
    check("unassigned nurse → 404", c.post(URL, json=body, headers=H(n3t)).status_code == 404)
    check("validation (type, ecog, risk, item, future time)", set(err(c.post(URL, json={
        **body, "assessment_type": "x", "ecog_status": 7, "risk_level": "extreme", "assessed_at": iso(utcnow() + timedelta(hours=2)),
        "items": [{"item_type": "wish", "description": ""}]}, headers=H(nt)))[2])
          >= {"assessment_type", "ecog_status", "risk_level", "assessed_at", "items[0].item_type", "items[0].description"})
    check("appointment of another patient → 400", "appointment_id" in err(c.post(URL, json={**body, "appointment_id": 99999}, headers=H(nt)))[2])
    key = str(uuid.uuid4())
    r = c.post(URL, json=body, headers=H(nt, key))
    A = r.get_json()["data"]
    r2 = c.post(URL, json=body, headers=H(nt, key))
    check("nurse creates a draft with items; cycle context set", r.status_code == 201 and A["sign_status"] == "draft"
          and [i["item_status"] for i in A["items"]] == ["open", "open"] and A["cycle_id"] is not None and A["cycle_day"] == 4, A)
    check("same Idempotency-Key → same draft, no second row", r2.get_json()["data"]["id"] == A["id"] and r2.headers.get("Idempotent-Replayed") == "true"
          and db.session.query(NursingAssessment).filter_by(patient_id=p1.id, sign_status="draft").count() == 1)

    # drafts: internal
    nv = c.get(f"/api/v1/dashboard/patient/{P1}", headers=H(nt)).get_json()["data"]["widgets"]["nurse-view"]
    check("dashboard: draft listed in pending sign-off", any(x["id"] == A["id"] for x in nv["pending_assessment_signoff"]["items"]))
    mine = c.get(URL, headers=H(nt)).get_json()["data"]
    check("nurse's own list (no patient_id) shows the draft with patient code", any(x["id"] == A["id"] and x["patient_code"] == "P00001" for x in mine))
    check("another nurse's own list does not show it", all(x["id"] != A["id"] for x in c.get(URL, headers=H(n2t)).get_json()["data"]))
    check("patient timeline hides the draft", all(e["event_id"] != f"NURSING_ASSESSMENT:{A['id']}" for e in c.get("/api/v1/patients/me/timeline", headers=H(pt)).get_json()["data"]))

    # ================================================================ edit (author only)
    check("another assigned nurse cannot edit the draft → 403", c.patch(f"{URL}/{A['id']}", json={"plan": "x"}, headers=H(n2t)).status_code == 403)
    r = c.patch(f"{URL}/{A['id']}", json={"assessment": "化療前評估可施打", "plan": "持續監測神經病變", "risk_level": "high",
                                           "items": [{"item_type": "problem", "description": "周邊神經病變風險", "priority": "high"}]}, headers=H(nt))
    check("author edits the draft (fields + items replaced)", r.status_code == 200 and r.get_json()["data"]["risk_level"] == "high"
          and len(r.get_json()["data"]["items"]) == 1)

    # ================================================================ sign
    check("another nurse cannot sign it → 403", c.post(f"{URL}/{A['id']}/sign", headers=H(n2t)).status_code == 403)
    # earlier than A: the Risk Engine uses the latest final assessment (drafts included — existing definition)
    empty = c.post(URL, json={"patient_id": P1, "assessment_type": "follow_up", "assessed_at": iso(utcnow() - timedelta(hours=1))}, headers=H(nt)).get_json()["data"]
    check("signing an empty SOAP → 400", err(c.post(f"{URL}/{empty['id']}/sign", headers=H(nt)))[0] == 400)
    r = c.post(f"{URL}/{A['id']}/sign", headers=H(nt))
    check("author signs → signed, signed_at", r.get_json()["data"]["sign_status"] == "signed" and r.get_json()["data"]["signed_at"])
    check("signed → PATCH 422 RECORD_LOCKED", err(c.patch(f"{URL}/{A['id']}", json={"plan": "x"}, headers=H(nt)))[:2] == (422, "RECORD_LOCKED"))
    check("signing twice → 409", c.post(f"{URL}/{A['id']}/sign", headers=H(nt)).status_code == 409)

    # ================================================================ timeline / risk / privacy
    tl = {e["event_id"]: e for e in c.get("/api/v1/patients/me/timeline", headers=H(pt)).get_json()["data"]}
    pev = tl.get(f"NURSING_ASSESSMENT:{A['id']}")
    check("patient timeline: signed assessment as a neutral line, no SOAP / risk / nurse", pev and pev["summary"] == "護理師已完成評估。"
          and not {"subjective", "objective", "assessment", "plan", "risk_level", "assessed_by"} & set(pev["detail"]))
    sev = next(e for e in c.get(f"/api/v1/patients/{P1}/timeline", headers=H(nt)).get_json()["data"] if e["event_id"] == f"NURSING_ASSESSMENT:{A['id']}")
    check("staff timeline: SOAP and risk visible", sev["detail"]["plan"] == "持續監測神經病變" and sev["detail"]["risk_level"] == "high" and sev["severity"] == "critical")
    nv = c.get(f"/api/v1/dashboard/patient/{P1}", headers=H(nt)).get_json()["data"]["widgets"]["nurse-view"]
    check("risk engine (unchanged): latest assessment risk high → patient risk high with the reason", nv["risk"]["level"] == "high"
          and any("護理評估風險" in x for x in nv["risk"]["reasons"]) and nv["latest_assessment"]["id"] == A["id"])
    pdash = c.get("/api/v1/dashboard/patient/me", headers=H(pt)).get_json()["data"]["widgets"]
    check("patient dashboard: no nurse-view / assessment internals", "nurse-view" not in pdash and "護理評估風險" not in json.dumps(pdash, ensure_ascii=False))

    # ================================================================ items follow-up
    item = A["items"][0]["id"]
    signed_items = c.get(f"{URL}/{A['id']}", headers=H(nt)).get_json()["data"]["items"]
    r = c.patch(f"{URL}/{A['id']}/items/{signed_items[0]['id']}", json={"item_status": "resolved"}, headers=H(n2t))
    check("any nurse of the patient updates an item status after signing", r.status_code == 200 and r.get_json()["data"]["item_status"] == "resolved"
          and r.get_json()["data"]["resolved_at"])
    check("bad item status → 400", c.patch(f"{URL}/{A['id']}/items/{signed_items[0]['id']}", json={"item_status": "x"}, headers=H(nt)).status_code == 400)

    # ================================================================ correction
    check("amend a draft → 409 (edit it instead)", c.post(f"{URL}/{empty['id']}/amend", json={"amend_reason": "x"}, headers=H(nt)).status_code == 409)
    check("amend needs a reason", "amend_reason" in err(c.post(f"{URL}/{A['id']}/amend", json={"risk_level": "medium"}, headers=H(n2t)))[2])
    r = c.post(f"{URL}/{A['id']}/amend", json={"amend_reason": "風險判斷誤植", "risk_level": "medium"}, headers=H(n2t))
    B = r.get_json()["data"]
    check("amend (by another nurse of the patient) → new draft version copying the rest", r.status_code == 201 and B["amends_id"] == A["id"]
          and B["sign_status"] == "draft" and B["risk_level"] == "medium" and B["plan"] == "持續監測神經病變" and B["assessed_by"]["id"] == n2.public_id
          and B["assessed_at"] == A["assessed_at"] and B["items"][0]["item_status"] == "resolved")
    check("original stays signed and final until the correction is signed", c.get(f"{URL}/{A['id']}", headers=H(nt)).get_json()["data"]["record_status"] == "final")
    check("a second open correction → 409", c.post(f"{URL}/{A['id']}/amend", json={"amend_reason": "y", "plan": "z"}, headers=H(nt)).status_code == 409)
    c.post(f"{URL}/{B['id']}/sign", headers=H(n2t))
    orig = c.get(f"{URL}/{A['id']}", headers=H(nt)).get_json()["data"]
    check("signing the correction → original amended (kept, points to the new version)", orig["record_status"] == "amended" and orig["amended_by_id"] == B["id"]
          and orig["risk_level"] == "high")
    check("amended original cannot be amended or its items changed → 409", c.post(f"{URL}/{A['id']}/amend", json={"amend_reason": "x", "plan": "y"}, headers=H(nt)).status_code == 409
          and c.patch(f"{URL}/{A['id']}/items/{signed_items[0]['id']}", json={"item_status": "open"}, headers=H(nt)).status_code == 409)
    vers = c.get(f"{URL}/{B['id']}/versions", headers=H(nt)).get_json()["data"]
    check("version history: original → correction", [v["id"] for v in vers] == [A["id"], B["id"]] and [v["record_status"] for v in vers] == ["amended", "final"])
    lst = c.get(f"{URL}?patient_id={P1}", headers=H(nt)).get_json()["data"]
    hist = c.get(f"{URL}?patient_id={P1}&include_history=true", headers=H(nt)).get_json()["data"]
    check("patient list shows the current version; include_history adds the original", B["id"] in [x["id"] for x in lst] and A["id"] not in [x["id"] for x in lst]
          and A["id"] in [x["id"] for x in hist])
    stl = [e["event_id"] for e in c.get(f"/api/v1/patients/{P1}/timeline", headers=H(nt)).get_json()["data"]]
    check("timeline shows the corrected version only", f"NURSING_ASSESSMENT:{B['id']}" in stl and f"NURSING_ASSESSMENT:{A['id']}" not in stl)
    nv = c.get(f"/api/v1/dashboard/patient/{P1}", headers=H(nt)).get_json()["data"]["widgets"]["nurse-view"]
    check("risk uses the corrected assessment (medium)", nv["latest_assessment"]["id"] == B["id"] and nv["latest_assessment"]["risk_level"] == "medium")

    # ================================================================ access after assignment ends
    admin_view = c.get(f"{URL}/{B['id']}", headers=H(at))
    check("admin reads an assessment", admin_view.status_code == 200)
    check("admin cannot edit / sign / amend → 403", c.post(f"{URL}/{empty['id']}/sign", headers=H(at)).status_code == 403
          and c.post(f"{URL}/{B['id']}/amend", json={"amend_reason": "x"}, headers=H(at)).status_code == 403)
    asg = next(x for x in c.get(f"/api/v1/patients/{P1}/nurse-assignments", headers=H(at)).get_json()["data"] if x["nurse"]["id"] == n2.public_id and x["active"])
    c.post(f"/api/v1/patients/{P1}/nurse-assignments/{asg['id']}/end", headers=H(at))
    codes = [c.get(f"{URL}/{B['id']}", headers=H(n2t)).status_code, c.get(f"{URL}?patient_id={P1}", headers=H(n2t)).status_code,
             c.patch(f"{URL}/{B['id']}/items/{B['items'][0]['id']}", json={"item_status": "open"}, headers=H(n2t)).status_code]
    check("nurse whose assignment ended: 404 on the patient's assessments (even own ones)", codes == [404, 404, 404], codes)
    check("ended nurse's own list no longer shows that patient", all(x["patient_code"] != "P00001" for x in c.get(URL, headers=H(n2t)).get_json()["data"]))

    # ================================================================ audit
    logs = [l for l in db.session.query(AuditLog).all() if l.resource_type in ("nursing_assessments", "nursing_assessment_items")]
    by = lambda action: [l for l in logs if l.action == action]  # noqa: E731
    check("audit: CREATE (once for the replayed key), UPDATE, SIGN ×2", len([l for l in by("CREATE") if l.resource_id == str(A["id"])]) == 1
          and by("UPDATE") and len(by("SIGN")) == 2)
    check("audit: SIGN of the correction names the superseded version", any(l.changes.get("supersedes_id") == A["id"] for l in by("SIGN")))
    check("audit: AMEND on the original with reason and new version", [l.changes for l in by("AMEND")] == [{"new_version_id": B["id"], "amend_reason": "風險判斷誤植", "fields": ["risk_level"]}])
    check("audit: item status change", any(l.resource_type == "nursing_assessment_items" and l.changes.get("item_status") == "resolved" for l in logs))
    check("audit: reads recorded (VIEW)", any(l.action == "VIEW" for l in logs))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
