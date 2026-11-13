"""皮膚照片蒐集告知與同意 — versioned text (**draft, pending the hospital's review; no legal effect**).

A new wording = a new ``CONSENT_VERSION`` (never edit a version that patients may have accepted). The
patient's acceptance stores the version and the SHA-256 of this exact content (``patient_consents``).
In staging / production uploads stay off until ``PHOTO_CONSENT_APPROVED_VERSION`` equals this version
(the hospital approved it) — app/services/photo_storage.py.
"""

import hashlib
import json

CONSENT_TYPE = "skin_photo"
CONSENT_VERSION = "skin-photo-2026-11-draft-1"
REVIEW_STATUS = "draft"  # 待院方審核
TITLE = "皮膚照片蒐集與使用說明"
NOTICE = "本說明為草稿，尚未經醫院審核，不具法律效力。"
SECTIONS = [
    ("照片用途", "照片只用於放射治療期間的皮膚照護與追蹤，協助負責照護您的護理人員了解皮膚變化。系統不會用照片自動判斷嚴重程度或分級。"),
    ("誰可以查看", "您本人，以及目前負責照護您的醫療人員。照護關係結束後，對方就不能再查看。系統管理者在此版本不能查看照片，每次查看照片都會留下紀錄。"),
    ("不是即時監測", "照片送出後不會立即有人查看，護理師會在服務時間內查看。上傳照片不代表醫護人員已經看過或已完成評估。"),
    ("緊急狀況", "如有呼吸困難、意識不清等緊急狀況，請立即撥打 119 或就近急診就醫；想詢問皮膚狀況，請於服務時間撥打 07-6150011 分機 2205。"),
    ("拍攝提醒", "請只拍需要照護的皮膚部位，避免拍到臉部、刺青、證件或其他能辨識您身分的內容。系統會移除照片中的拍攝地點、裝置等資訊，但照片畫面本身仍可能辨識出您。"),
    ("撤回與保存", "您可以隨時撤回同意，撤回後不能再上傳照片。送出前移除的照片會被刪除；已送出的照片屬於照護紀錄，保存與刪除方式依醫院規定辦理（待醫院確認）。"),
]


def content():
    return {"version": CONSENT_VERSION, "title": TITLE, "notice": NOTICE, "sections": [{"heading": h, "text": t} for h, t in SECTIONS]}


def content_sha256():
    return hashlib.sha256(json.dumps(content(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
