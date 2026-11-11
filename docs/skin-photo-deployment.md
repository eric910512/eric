# 皮膚照片（Phase 1b）：正式儲存啟用條件與部署設定

> 狀態：程式已實作，**正式環境預設關閉**。本文件說明照片功能在什麼條件下才會開啟、需要哪些設定，以及部署時的注意事項。
> 沒有建立任何雲端 bucket、服務帳號或金鑰，也沒有修改 Render 環境變數。設計背景見 `skin-photo-storage-design.md`。

## 1. 開關邏輯（`app/services/photo_storage.py`：`init_photos`）

API 啟動時判斷一次，任一條件不符就**關閉上傳**，不會改用其他較不安全的儲存方式：

| 順序 | 條件 | 不符時的 `reason` | 結果 |
|---|---|---|---|
| 1 | `PHOTO_UPLOADS_ENABLED=true` | `disabled_by_config` | 上傳關閉 |
| 2 | 有可用的儲存後端 | `no_storage_backend` | 上傳與讀取都關閉 |
| 2a | `PHOTO_STORAGE_BACKEND=local` 只允許 development／testing | `local_storage_not_allowed` | staging／production 一律拒絕本機磁碟 |
| 2b | `PHOTO_STORAGE_BACKEND=object` 需要全部 `PHOTO_OBJECT_*` 設定 **且** 已安裝 `boto3` | `object_storage_not_configured` | 關閉 |
| 3 | staging／production：`PHOTO_CONSENT_APPROVED_VERSION` 必須等於程式中的同意書版本（`app/modules/photo/consent.py` 的 `CONSENT_VERSION`）| `consent_not_approved` | 上傳關閉；已存照片仍可讀取 |

- 預設值：base／staging／production `PHOTO_UPLOADS_ENABLED=false`、沒有儲存後端 → **目前 Render（staging）部署後照片功能是關閉的**，病人頁顯示「照片上傳目前未開放」，文字自評照常。
- development／testing 預設開啟，使用 `LocalPrivateStorage`（私有暫存目錄；開發預設 `backend/instance/private-photos`，已被 `.gitignore` 排除；E2E 用 `frontend/e2e/.artifacts/private-photos`；後端測試用系統暫存目錄）。

## 2. 正式物件儲存需要的設定（全部放在 Render Secret／環境變數，不進 Git）

| 變數 | 說明 |
|---|---|
| `PHOTO_UPLOADS_ENABLED` | `true` |
| `PHOTO_STORAGE_BACKEND` | `object` |
| `PHOTO_OBJECT_BUCKET` | 私有 bucket 名稱（禁止公開存取、uniform access、不開靜態網站）|
| `PHOTO_OBJECT_REGION` | 例如台灣區域（待院方決定，見設計文件第 2 節）|
| `PHOTO_OBJECT_ENDPOINT_URL` | 選填：非 AWS 的 S3 相容服務（例如 GCS 的 S3 互通端點）|
| `PHOTO_OBJECT_KMS_KEY_ID` | 客戶管理金鑰（SSE-KMS）；上傳一律帶 `ServerSideEncryption=aws:kms` |
| `PHOTO_OBJECT_ACCESS_KEY_ID` / `PHOTO_OBJECT_SECRET_ACCESS_KEY` | 專用服務帳號，只授權此 bucket 的 put／get／delete，不授權 list 全部或改權限 |
| `PHOTO_CONSENT_APPROVED_VERSION` | 院方核可的同意書版本字串 |

- 另需在 `backend/requirements.txt` 加入固定版本的 `boto3`（目前**未加入**；決定供應商後再加，避免無用依賴）。
- 若供應商不提供 S3 相容 API，可在 `PhotoStorageService` 介面下另寫 adapter，不需改資料表（`storage_backend` 欄位記錄每張照片所在後端）。

## 3. 啟用前的檢查清單（全部完成才開啟）
1. 院方核可：照片蒐集告知與同意書文字（新版本寫進 `consent.py`，版本字串放進 `PHOTO_CONSENT_APPROVED_VERSION`）。
2. 院方決定：保存年限、病歷歸屬、刪除與撤回後的處理、境內存放、委外雲端（見設計文件第 3 節）。
3. 建立**正式專用**的 bucket、KMS 金鑰、服務帳號；測試／教學環境使用不同的帳號、bucket 與金鑰。
4. bucket：版本保留／軟刪除、存取稽核日誌、生命週期規則（**不可**設定自動刪除已送出的照片，除非符合核可的保存政策）。
5. API 改用付費方案或院方平台（免費方案 512 MB 記憶體、會休眠）；確認 Render 靜態網站 `/api/*` 轉發的請求大小上限（照片上傳上限 10 MB）。
6. 部署前備份資料庫；在隔離環境演練：上傳 → 送出 → 護理師讀取 → 配對結束後拒絕 → 備份還原後照片可讀。
7. 排程或手動執行 `flask --app run photos purge-pending`（清除 24 小時未送出的暫存照片）。Render 免費方案沒有排程時，上傳 API 會順便清理（每次最多 20 張）。

## 4. Migration 與部署順序
- `e7a2c4f9d1b3`（新增 `symptom_photos`、`patient_consents`）接在 Phase 1a 的 `c3e9a1f4b7d2` 之後，`c3e9a1f4b7d2` 又接在聊天室 `b5cd01c11e1d` 之後。API 啟動時 `flask db upgrade` 會依序執行。
- 只新增資料表，不改既有資料；照片功能關閉時這兩張表維持空的。
- 退回：資料庫一旦到 `e7a2c4f9d1b3`，舊版 API 無法啟動（Alembic 找不到版本）。請用修正後重新部署；若必須降級，先用新版程式 `flask db downgrade c3e9a1f4b7d2`（會刪除兩張表的 metadata；儲存中的影像檔不會被刪除，需另行處理）。**有真實照片後不要降級。**
- 新增依賴：`pillow==11.3.0`（Render build 會安裝）。

## 5. 營運工具
- `flask --app run photos purge-pending`：丟棄過期的未送出照片（影像刪除、metadata 保留、稽核記為 `system:photo-cleanup`）。
- `flask --app run photos verify [--fix]`：照片資料列與儲存檔案的一致性檢查（任何儲存後端）。
  - 待送出／已送出的照片**缺檔**：列出並以 exit code 1 結束，**不自動處理**（影響照護紀錄，需人工確認）；讀取時 API 回 404 `PHOTO_FILE_MISSING`。
  - 已丟棄照片仍留著的檔案（例如刪檔失敗）：`--fix` 會刪除。
  - 沒有資料列的檔案（僅 `LocalPrivateStorage` 可列舉；物件儲存請另用 bucket inventory）：`--fix` 會刪除。
- 一致性原則：移除（病人送出前移除、逾時清理）一律**先以條件更新把狀態從 pending 改成 discarded 並 commit，之後才刪檔**；同一時間有回報附上同一張照片時，只有一方成功，已送出的照片不會被刪檔。刪檔失敗不影響資料，可重跑清理或 `verify --fix`。
