"""皮膚及傷口照片蒐集告知與同意 — versioned text (**draft, pending the hospital's review; no legal effect**).

A new wording = a new ``CONSENT_VERSION`` (never edit a version that patients may have accepted). The
patient's acceptance stores the version and the SHA-256 of this exact content (``patient_consents``).
In staging / production uploads stay off until ``PHOTO_CONSENT_APPROVED_VERSION`` equals this version; production
also needs ``REVIEW_STATUS = "approved"`` and a version without "draft" — app/services/photo_storage.py.

draft-2 (2026-11-13): covers skin AND wound photos (the hospital allowed patients to upload both). Every
【待院方確認：…】 marks a policy the hospital has not confirmed yet. The approved wording must replace this text
under a NEW version without "draft", with REVIEW_STATUS = "approved" and the approval reference in this docstring.
Only facts about how the system works are stated without a marker.
"""

import hashlib
import json

CONSENT_TYPE = "skin_photo"
CONSENT_VERSION = "skin-photo-2026-11-draft-2"
REVIEW_STATUS = "draft"  # 待院方審核 — never set "approved" without the hospital's approved wording and reference
TITLE = "皮膚及傷口照片蒐集與使用說明"
NOTICE = "本說明為草稿，尚未經醫院審核，不具法律效力。"
SECTIONS = [
    ("照片用途", "照片只用於您在治療期間的皮膚與傷口照護及追蹤，協助負責照護您的護理人員了解皮膚或傷口的變化。系統不會用照片自動判斷嚴重程度或分級。"
                 "【待院方確認：適用範圍，例如是否僅限放射治療期間】"),
    ("誰可以查看", "您本人，以及目前負責照護您的醫療人員。照護關係結束後，對方就不能再查看。系統管理者不能查看照片，每次查看照片都會留下紀錄。"),
    ("不是即時監測", "照片送出後不會立即有人查看，護理師會在服務時間內查看。上傳照片不代表醫護人員已經看過或已完成評估。"),
    ("緊急狀況", "如有呼吸困難、意識不清、傷口大量出血等緊急狀況，請立即撥打 119 或就近急診就醫；想詢問皮膚或傷口狀況，請於服務時間撥打 07-6150011 分機 2205。"),
    ("拍攝提醒", "請只拍需要照護的皮膚或傷口部位，避免拍到臉部、刺青、證件或其他能辨識您身分的內容。系統會移除照片中的拍攝地點、裝置等資訊，但照片畫面本身仍可能辨識出您。"),
    ("撤回同意", "您可以隨時撤回同意。撤回後不能再上傳照片，尚未送出的照片也不能再送出。已送出的照片屬於照護紀錄，撤回同意不會刪除這些照片。"
                 "【待院方確認：撤回後已送出照片的處理方式】"),
    ("保存期限", "送出前移除的照片會從系統中刪除；上傳後超過 24 小時仍沒有送出的照片，會由系統自動清除。已送出的照片保存【待院方確認：保存年限】，"
                 "保存與刪除依醫院規定辦理。【待院方確認：已送出照片是否屬於病歷】"),
    ("備份", "系統資料庫會定期備份。已刪除的照片在備份保留期間內仍可能存在於備份中，【待院方確認：備份保留天數】後才會完全消失。"),
    ("資料存放地點", "照片經加密連線（HTTPS）傳送，儲存在本系統使用的雲端資料庫（Render，新加坡資料中心）。【待院方確認：是否核可此存放地點】"),
]


def content():
    return {"version": CONSENT_VERSION, "title": TITLE, "notice": NOTICE, "sections": [{"heading": h, "text": t} for h, t in SECTIONS]}


def content_sha256():
    return hashlib.sha256(json.dumps(content(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
