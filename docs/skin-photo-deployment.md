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
| `PHOTO_OBJECT_CONNECT_TIMEOUT` | 選填，預設 `3`（秒，0–10）：連線逾時 |
| `PHOTO_OBJECT_READ_TIMEOUT` | 選填，預設 `5`（秒，0–15）：S3 多久沒有回應資料就放棄這一次請求 |
| `PHOTO_OBJECT_MAX_ATTEMPTS` | 選填，預設 `2`（含第一次，1–3；botocore standard retry mode，只重試逾時、連線錯誤、5xx、限流）|

- 三個用戶端設定集中在 `app/config.py`，`PrivateObjectStorage` 啟動時建立**一個**用戶端，所有照片 API 共用。每一次 S3 呼叫最多等 `MAX_ATTEMPTS × (CONNECT + READ)` 秒，必須 ≤ 20 秒；值不合理（非數字、超出範圍、超過 20 秒）時照片功能**關閉**（`object_storage_not_configured`），API 照常啟動。

- `backend/requirements.txt` 已加入固定版本的 `boto3==1.43.110`／`botocore==1.43.110` 與相依套件（2026-11-12；供應商：AWS S3＋KMS，Region 待定）。照片功能關閉時不會載入。
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


## 6. 物件儲存驗證：moto 模擬 vs 真正的 AWS（2026-11-12）

### 6.1 已完成：moto 模擬測試（**SIMULATED，不等於 AWS 驗證**）
- 工具：`backend/tests/object_storage/photo_object_storage_check.py`（在 `backend/` 執行）。本機 moto S3＋KMS（127.0.0.1）＋記憶體 PostgreSQL（PGlite）；應用程式走真正的 boto3 用戶端；每個階段是**獨立的程序**（新程序＝重新部署：只留下資料庫與 bucket）。只用程式產生的假照片與示範帳號。
- 安裝（只裝在本機 venv，**不加入任何 requirements 檔**）：`pip install "moto[server,s3,kms]==5.2.4"`。
- 結果：57/57（2026-11-12 第二次：加入 versioning 行為與 lifecycle 規則格式）。涵蓋：
  - 開關：所有設定齊全才開；production 無照片設定（現在的 Render）→ 關閉；缺任一 `PHOTO_OBJECT_*`、沒有 boto3、同意書版本未核可、staging 用本機磁碟 → 關閉。
  - 上傳：同意 → 上傳 → 每個物件 `aws:kms`＋設定的金鑰、`Cache-Control: private, no-store`、ACL 無公開授權、key 為隨機值（無病人編號、e-mail、檔名）、存的是重新編碼且無 EXIF／GPS 的影像。
  - 重新啟動後讀取：新程序中護理師、病人讀取成功，內容與上傳時的 checksum 相同。
  - 授權：其他病人 404、未配對護理師 404、配對結束立即 404、管理者 403、未登入 401；API 回應不含 bucket、endpoint、key、憑證或預簽網址。
  - 刪除：送出前移除 → 兩個物件刪除；`photos purge-pending` 清除逾時暫存照片、不動已送出照片；`photos verify` 正確回報被外部刪除的物件（exit 1）。
  - 失敗：儲存服務連不上、bucket 不存在／無權限 → 上傳 503 `PHOTO_STORAGE_UNAVAILABLE` 且不留資料列、讀取 503（不誤報為缺檔）；物件被刪 → 404 `PHOTO_FILE_MISSING`。
  - 本機磁碟沒有寫入任何照片；程式輸出與 log 不含憑證與物件 key。
- **moto 不能證明的事**：moto 接受任何憑證、不執行 IAM／bucket policy／Block Public Access、不真正加密、不檢查 KMS 金鑰權限、延遲與錯誤型態與 AWS 不同。

### 6.2 測試發現
1. **Bucket 版本保留**：見 §7（建議規則；天數待院方決定）。
2. ~~儲存服務連不上時等待約 45 秒才回 503~~ → **已修正**（2026-11-12）：逾時與重試集中設定（§2），見 6.5。
3. **（已修正）S3 沒有回應時 API 回 500 而不是 503**：部分 botocore 例外（例如 `ReadTimeoutError`）的 `response` 是 `None`，原本的錯誤碼判斷會再拋出 `AttributeError`。讀取逾時因此變成 500，而且 500 的 log 會帶出含 bucket 與物件 key 的完整網址。`db1c422` 的讀取路徑本來就有這個問題（只是在 moto 測試中沒有觸發）。
4. **（已修正，採方案 b，2026-11-12；`frontend/src/utils/photoUploadRetry.js`、`SkinPhotoPicker.vue`；後端與 `idempotency.py` 未改）** 原問題：上傳 503 之後，前端「重試」會一直失敗。修正方式：
   - 照片 API 明確回覆「沒有儲存、可稍後再試」（503 `PHOTO_STORAGE_UNAVAILABLE`、429）→ 重試時換新的 key。
   - 結果不明（網路中斷、逾時、proxy 502／504、409 處理中）→ 沿用原 key，重送即可查出原請求的結果。
   - 4xx（以及照片功能關閉）→ 標示「無法使用」，不提供重試。
   - 同一張照片上傳中時，再點擊不會送出第二個請求。

   原分析：伺服器把 503 記為該 Idempotency-Key 的最終結果並重播；`SkinPhotoPicker.vue` 遇到 5xx 卻沿用同一個 key 重試。儲存服務恢復後，病人仍需移除照片再重新加入才能上傳。修正方式二選一：(a) 只讓照片上傳的 5xx 可用同一個 key 重試（`run_idempotent` 加一個預設關閉的參數，聊天室等其他 API 行為不變）；(b) 前端在 5xx 後改用新 key。兩者都超出本次授權範圍。

### 6.3 尚未完成：真正 AWS S3／KMS 整合測試（未執行，需授權與資源）
在**獨立的測試 AWS 帳號或測試 bucket**（不可是正式 bucket）上，用同一支工具：
```
PHOTO_CHECK_TARGET=aws PHOTO_CHECK_AWS_CONFIRM=isolated-test-bucket \
PHOTO_OBJECT_BUCKET=… PHOTO_OBJECT_REGION=… PHOTO_OBJECT_KMS_KEY_ID=… \
PHOTO_OBJECT_ACCESS_KEY_ID=… PHOTO_OBJECT_SECRET_ACCESS_KEY=… \
python tests/object_storage/photo_object_storage_check.py
```
（bucket 名稱含 prod／prd 會拒絕執行；資料庫一律是本機記憶體 PGlite；不建立任何 AWS 資源。）aws 模式**從未執行過**。另需人工確認（AWS Console／CLI，唯讀）：
- Block Public Access 四項全開（帳號層級與 bucket 層級）；bucket policy 拒絕非 TLS（`aws:SecureTransport=false`）與未加密上傳。
- 預設加密 SSE-KMS（客戶管理金鑰）＋ Bucket Key；KMS key policy 只允許此服務帳號 `kms:GenerateDataKey`／`kms:Decrypt`；金鑰輪替開啟。
- IAM：此服務帳號只允許該 bucket `photos/*` 的 `s3:PutObject`、`s3:GetObject`、`s3:DeleteObject`（`photos verify` 另需 `s3:GetObject` 的 HEAD），**沒有** `s3:ListAllMyBuckets`、`s3:PutBucketPolicy`、`s3:PutObjectAcl`。
- 以**錯誤的 KMS 金鑰／被拒絕的金鑰權限**上傳 → 503（moto 無法模擬）。
- 從網際網路未簽章存取物件 URL → 403。
- CloudTrail data events（或 S3 server access logs）記錄物件讀寫。
- 版本保留與非現行版本生命週期規則（見 §7）；備份／複寫策略與資料庫備份還原後照片仍可讀。
- 真實網路下的逾時：Render（Singapore）到選定 Region 的延遲，確認預設 3／5 秒、2 次足夠（正常上傳不會誤判逾時）。
- 在 Render staging（非正式）以 Secret 設定變數、重新部署兩次後照片仍可讀；Render 靜態網站 `/api/*` 轉發的請求大小上限（UNKNOWN）。

### 6.4 正式 AWS 設定需求（待院方決定 Region 與核可後才建立；**不得自行建立**）
| 項目 | 需求 |
|---|---|
| Region | 待院方決定（資料境內存放、與 Render Singapore 的延遲）|
| Bucket | 正式專用；Block Public Access 全開；Object Ownership = Bucket owner enforced（停用 ACL）；不開靜態網站；版本保留＋非現行版本生命週期；存取記錄 |
| KMS | 客戶管理金鑰（對稱）；key policy 限縮；自動輪替；正式與測試使用不同金鑰 |
| IAM | 專用 IAM user（Render 無法使用 IAM role）＋最小權限 policy（見 6.3）；存取金鑰定期輪替 |
| Render | 只在 `cancer-care-api` 的 Environment 以 Secret 設定 §2 的變數；**`PHOTO_UPLOADS_ENABLED` 在所有核可完成前維持 `false`／不設定** |
| 程式 | `boto3==1.43.110`、`botocore==1.43.110`（含相依套件）已加入 `backend/requirements.txt`；照片關閉時不會載入 boto3 |

### 6.5 儲存服務故障處理（`backend/tests/test_photo_storage_resilience.py`，50 項，納入 `run_all.py`）
本機假 S3 端點（127.0.0.1，模仿 S3 的回應格式，**不是 AWS、也不是 moto**），應用程式使用真正的 boto3 用戶端。測試設定：connect 0.5 秒、read 1 秒、2 次。
- 設定：預設值、用戶端實際套用的值、只建立一個用戶端；不合理的值 → 照片關閉、API 不會崩潰。
- 上傳失敗，每一種都回 503 `PHOTO_STORAGE_UNAVAILABLE`、**不留資料列、不留物件**，並在時間上限內回應：
  - S3 不回應（逾時，重試 1 次）；連不上（拒絕或連線逾時）；
  - KMS 拒絕 `kms:GenerateDataKey`（403）、KMS 金鑰停用（`KMS.DisabledException`）、bucket 不存在（這三種**不重試**，重試沒有幫助）；
  - S3 500（重試 1 次）。
- 上傳到一半失敗（縮圖寫入失敗）：刪除已寫入的原圖，不留資料列。若補償刪除也失敗，仍不留資料列，但 bucket 會多一個沒有資料列的物件（只能用 bucket inventory 找到；見 §7）。
- 讀取失敗：KMS 拒絕 `kms:Decrypt`、逾時、500、bucket 不存在 → 503（不會誤報為缺檔）；物件不存在 → 404 `PHOTO_FILE_MISSING`。
- 清理：S3 故障時，上傳前的順手清理在**第一次刪除失敗後就停止**（原本每個檔案都會等一次逾時）。資料列仍標記為已丟棄，留下的檔案由 `photos verify --fix` 清除。
- 外洩檢查：所有錯誤回應不含 bucket、帳號、KMS ARN、存取金鑰、物件 key、病人 e-mail 或病歷號；log 只記錄安全的錯誤代碼（`AccessDenied`、`KMS.DisabledException`、`NoSuchBucket`、`InternalError`、`ReadTimeoutError`、`ConnectTimeoutError`），不含上述資訊。
- 每次呼叫的時間上限（預設值）：2 ×（3 + 5）＝ 16 秒。一次上傳最多 3 次會失敗的呼叫（清理 1 次、縮圖 1 次、補償刪除 1 次）≈ 48 秒，低於 gunicorn 的 60 秒。實際故障多半是連不上，大約 2 × 3 秒就會回應。

## 7. S3 Versioning 與照片刪除：行為與 Lifecycle 建議（**保存天數不由本文件決定**）

### 7.1 現有刪除流程在 versioning 下的實際行為（moto 已驗證行為；AWS 的行為依 S3 文件推定）
| 情況 | 程式的動作 | versioning 開啟時 bucket 裡實際的結果 |
|---|---|---|
| 病人送出前移除照片 | 資料列改為 discarded 並 commit → `DeleteObject`（不帶 VersionId）| 加上 delete marker；**影像仍以非現行版本留存**，可用 version id 取回 |
| 逾時未送出（`purge-pending`、上傳時順手清理）| 同上 | 同上 |
| `photos verify --fix` 清除殘留 | 同上 | 同上 |
| 已送出的照片（照護紀錄）| **程式沒有任何刪除路徑** | 不受影響；物件 key 不會被覆寫（每張照片的 key 都是新的）|
| 撤回同意 | 只停止之後的上傳；不刪除已送出的照片（依同意書草稿與院方規定）| 不受影響 |

- 應用程式從不讀取舊版本，也**不應**取得 `s3:DeleteObjectVersion`／`s3:PutLifecycleConfiguration` 權限：有了這些權限，服務帳號外洩時就能永久抹除照護紀錄。永久刪除交給 bucket lifecycle 規則（由管理者設定，與應用程式權限分開）。
- 結論：versioning 開啟後，「刪除」在 S3 層只是「隱藏」。被移除的照片何時真正消失，完全由下面的 lifecycle 規則決定，因此天數必須寫進院方的保存政策與同意書說明（同意書目前寫「送出前移除的照片會被刪除」）。

### 7.2 建議的 Lifecycle 規則（形狀已在 moto 驗證可接受；**天數 `<N>` 由院方決定**）
```json
{"Rules": [{
  "ID": "photos-noncurrent-versions",
  "Status": "Enabled",
  "Filter": {"Prefix": "photos/"},
  "NoncurrentVersionExpiration": {"NoncurrentDays": <N>},
  "Expiration": {"ExpiredObjectDeleteMarker": true},
  "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}
}]}
```
- `NoncurrentVersionExpiration`：被移除照片的舊版本在 `<N>` 天後永久刪除，這是唯一的永久刪除機制。
- `ExpiredObjectDeleteMarker`：舊版本刪除後，自動清掉留下的 delete marker。
- **不設定**現行版本的 `Expiration`（Days／Date）：已送出的照片是照護紀錄，不可自動到期。若院方日後訂定已送出照片的保存年限，需另行設計（例如由程式在資料列層級處理，或以標籤區分），不可用 bucket 全域規則。
- 選擇 `<N>` 時要考慮的限制（供院方決策，不是建議值）：
  1. **下限 ≥ 資料庫可回復的時間窗**（Render PITR／備份保留天數）。資料庫還原到較早時間點時，當時仍是 pending／attached 的照片可能已在之後被移除；若舊版本已過期，就只能回報 `PHOTO_FILE_MISSING`，無法補回。
  2. **上限**要符合同意書對「送出前移除的照片會被刪除」的承諾與個資最小保存原則。
  3. 若啟用跨區複寫或 AWS Backup，備份副本的保存期間也要一併符合政策。
- 其他建議：
  - 開啟 S3 Inventory（每日，含版本），定期比對資料庫，找出「沒有資料列的物件」（例如補償刪除失敗留下的）。程式本身無法列出 bucket，這是刻意的最小權限設計。
  - KMS 金鑰的排程刪除等待期，必須長於所有版本與備份的保存期；否則舊版本與備份都會無法解密。
  - Object Lock（Governance）可防止已送出照片被竄改或刪除，但會讓 lifecycle 刪除舊版本更複雜。是否採用，待院方決定是否需要不可變更的紀錄。
