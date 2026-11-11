# 皮膚照片追蹤：私有物件儲存設計（Phase 1b，**設計稿，尚未實作**）

> 狀態：設計提案，待院方資訊安全、法務與臨床確認。本文件不建立任何雲端資源、金鑰或 Render 環境變數。
> 標示「待確認」的事實（法規條文適用性、雲端區域、價格）請以官方文件與院方意見為準。

## 1. 前提
- 照片**選填**，永遠不是送出皮膚自評的必要條件；照片附在 `symptom_records`（Phase 1a 的紀錄）上。
- 正式照片儲存採**私有物件儲存**；PostgreSQL bytea 不作為正式方案（最多只用於本機測試替身）。
- 目前 Render 環境是 staging／教學用（`FLASK_ENV=staging`、合成資料），**不收真實病人照片**。真實資料需要另建正式環境（第 13 節）。

## 2. 供應商與區域（待院方決定）

| 選項 | 區域 | 優點 | 注意 |
|---|---|---|---|
| Google Cloud Storage | `asia-east1`（台灣彰化） | 資料可留在台灣；CMEK（Cloud KMS）；物件版本、保留政策、稽核日誌 | 新廠商帳號與合約；Render（新加坡）↔ 台灣跨境傳輸 |
| AWS S3 | 台北區域（`ap-east-2`，**待確認**可用服務）或新加坡 `ap-southeast-1` | 生態成熟；SSE-KMS；Object Lock | 台北區域服務範圍需確認 |
| Cloudflare R2 | 無法指定台灣（只有地區提示） | 便宜、無下載費 | 無法保證資料在台灣，**不建議**用於醫療照片 |

- 目前 API 與 PostgreSQL 都在 Render **新加坡**。若院方要求「資料存放於台灣境內」，問題不只是照片，資料庫與 API 也要一起評估。
- 建議：先由院方確認境內存放要求，再選 GCS `asia-east1` 或 AWS 台北區域。

## 3. 台灣個資與醫療資料治理（**待院方法務／資安確認**）
1. 皮膚照片屬醫療資料，可能為個資法第 6 條之特種個資：蒐集的法律依據、告知事項、書面同意的形式。
2. 照片是否視為病歷的一部分（影響醫療法的保存年限，常見為至少 7 年）與誰負保存責任。
3. 電子病歷與委外雲端儲存的相關規範（境內存放、委外契約、稽核權）是否適用。
4. 醫院資通安全規範（資安等級、委外廠商要求、事件通報）。
5. 跨境傳輸：Render 新加坡處理台灣病人資料是否可接受。
6. 研究或教學使用照片時的去識別化與 IRB。

## 4. 加密與金鑰管理
- 傳輸：全程 HTTPS／TLS 1.2+（瀏覽器 → Render → 物件儲存）。
- 靜態：bucket 預設加密 + **客戶管理金鑰**（GCS CMEK／AWS SSE-KMS），金鑰輪替（例如每年），金鑰刪除權限與資料權限分開。
- API 存取憑證：專用服務帳號，只授權單一 bucket 的 `create / get / delete`，**不授權 list 全部、不授權改 IAM**；憑證只放在正式環境的 Secret（Render secret env），不進 git；正式與測試各用不同帳號。
- bucket：禁止公開存取（Public Access Prevention／Block Public Access）、uniform bucket-level access、關閉靜態網站功能。

## 5. 存取機制（不公開網址）
- **建議 v1：後端代理**。`GET /api/v1/symptom-photos/{id}/content?variant=thumb|full`：API 先做權限檢查與稽核，再從物件儲存讀出並串流回應。
  - 回應標頭：`Cache-Control: private, no-store`、`Content-Type: image/jpeg`、`X-Content-Type-Options: nosniff`、`Content-Disposition: inline`、`Cross-Origin-Resource-Policy: same-origin`。
  - 前端以帶 Token 的請求取得 blob，再用 `URL.createObjectURL` 顯示，離開頁面時 `revokeObjectURL`。
- 備選：短效簽名網址（≤ 5 分鐘、單一物件、僅 GET）。可減少 API 負載，但網址本身就是憑證，可能被轉傳或留在瀏覽紀錄，而且無法逐次稽核實際下載；**v1 不採用**。
- 物件名稱不含病人資訊：`photos/{random-uuid}/full.jpg`；對照關係只存在資料庫。

## 6. 存取控制
- 沿用 `require_auth` + `resolve_patient` + `ensure_can_view_patient`：病人只能存取自己的照片；護理師只能存取**目前**配對病人的照片，配對一結束立即 404；未配對 404；Admin v1 不可看（403）；未登入 401。
- 照片 id 用 UUID，不用流水號；每次 API 都重新檢查權限（不快取授權結果）。
- 刪除：病人只能刪除尚未被審閱的自己的照片；已審閱的照片由醫護依程序處理（軟刪除 + 原因）。

## 7. EXIF 與 GPS
- 前端：送出前在瀏覽器用 canvas 縮圖並轉成 JPEG（降低流量，也會去除 EXIF）。
- 後端（**必做，不信任前端**）：用 Pillow 解碼 → `ImageOps.exif_transpose` 轉正 → 重新編碼成新的 JPEG（不帶 EXIF／XMP／ICC 以外的中繼資料）→ 只保存重新編碼後的檔案，**原始檔不落地、不保存**。
- 測試：上傳含 GPS／相機型號的測試圖，驗證儲存的物件與下載內容都沒有 EXIF。

## 8. 檔案限制（建議值，可調整）
| 項目 | 限制 |
|---|---|
| 格式 | JPEG、PNG、WebP（以解碼結果判斷，不信任副檔名或 Content-Type）；HEIC 需實機確認 iOS 是否自動轉檔，必要時加 `pillow-heif` |
| 上傳大小 | 單張 ≤ 10 MB（只對上傳端點設定 `request.max_content_length`）|
| 像素 | 解碼前檢查 ≤ 40 MP（`Image.MAX_IMAGE_PIXELS`），防解壓縮炸彈 |
| 儲存尺寸 | full 長邊 1600 px（JPEG 品質約 82）、thumb 長邊 320 px |
| 數量 | 每筆紀錄 ≤ 3 張；每位病人每天 ≤ 10 張（429）|

## 9. 惡意檔案防護
- 只接受能被 Pillow 完整解碼的單張點陣圖；SVG、GIF、PDF、HTML、多影格、動畫一律拒絕。
- 重新編碼後只保存畫素，任何內嵌內容都不會留下。
- 解碼在記憶體上限內進行（像素上限 + 先檢查尺寸再載入）。
- 不以使用者提供的檔名儲存；回應永遠 `nosniff`。
- 防毒掃描：重新編碼已移除內嵌內容；若院方政策要求，可再接雲端或自建掃描（**待確認**）。

## 10. 同意、撤回、保存、刪除
- 首次上傳前顯示同意說明（用途、誰看得到、不是即時監測、保存期限、如何撤回／刪除），同意書有版本號；記錄在新表 `patient_consents`（`consent_type`、`version`、`accepted_at`、`revoked_at`）。
- 撤回後不能再上傳；已上傳照片的處理依院方政策（保留於病歷或刪除）。
- 上傳後 24 小時仍未附到紀錄的暫存照片自動刪除。
- 保存期限依院方決定（若屬病歷，依法定年限）；到期以排程刪除（物件 + 資料庫軟刪除紀錄），並保留刪除稽核。
- 物件儲存啟用版本保留／軟刪除（例如 7–30 天），避免誤刪；刪除流程需同時清除舊版本。

## 11. 稽核紀錄
- 沿用 `audit_logs`（只能新增）：上傳 CREATE、附加到紀錄 UPDATE、每次檢視 VIEW（含 thumb／full）、刪除 DELETE、同意／撤回。記錄照片 id、病人、操作者、IP、時間，**不記錄影像內容或檔案名稱**。
- 物件儲存端也開啟資料存取稽核日誌（GCS Data Access logs／S3 server access logs 或 CloudTrail data events），可與應用程式稽核交叉比對。

## 12. 備份、還原、災難復原
- 物件儲存本身多副本；另開物件版本保留，並視需要複製到第二區域或第二 bucket（不同金鑰權限）。
- 資料庫與物件的一致性：照片 metadata 在 PostgreSQL，物件在 bucket。還原資料庫到某時間點時，需比對 bucket（較新的物件成為孤兒，由清理工作處理；資料庫有紀錄但物件不存在時，畫面顯示「照片無法取得」而不是錯誤）。
- 定期（例如每月）演練：從備份還原一份資料庫到隔離環境，抽樣驗證照片可讀取。
- 目標值（RPO／RTO）待院方訂定。

## 13. 正式與測試環境隔離
- 不同專案／帳號、不同 bucket、不同 KMS 金鑰、不同服務帳號；測試環境的憑證不能讀正式 bucket。
- 測試與教學環境只使用合成圖片（不得上傳真實病人照片），頁面顯示「示範環境」提示。
- 自動化測試使用本機替身（記憶體或暫存目錄的儲存介面實作），不連真實雲端。
- 正式環境另建 Render 服務（或院方指定平台），不沿用目前的 staging。

## 14. 費用與容量估算（概估，**以官方價目為準**）
- 每張照片儲存約 300 KB（full）+ 30 KB（thumb）。
- 假設 100 位放療病人 × 每週 3 張 × 7 週 ≈ 2,100 張 ≈ 0.7 GB／療程梯次；一年約數 GB。
- 標準儲存每 GB 每月約 0.02–0.03 美元等級，加上請求與 KMS 費用，**每月預估低於數美元**；主要成本在合約、資安稽核與維運，不在儲存量。

## 15. Render Free API 的限制
- 免費方案記憶體約 512 MB、CPU 有限，並以 `WEB_CONCURRENCY=2` 執行：同時解碼多張大圖可能耗盡記憶體 → 像素上限、一次只處理一張、前端先縮圖。
- 閒置會休眠，喚醒約 1 分鐘；前端 API 逾時 60 秒，上傳可能在喚醒時失敗 → 每張照片獨立 Idempotency-Key，可安全重試。
- 上傳會經過靜態網站的 `/api/*` 轉發：轉發的請求大小上限與逾時**未驗證**，需實測。
- 正式收集真實照片前，建議 API 改付費方案（或院方平台），不要在免費方案上提供正式服務。

## 16. 資料表草案（Phase 1b 實作時再審查）
- `symptom_photos`：`id` (UUID)、`patient_id`、`symptom_record_id`（可空）、`uploaded_by`、`status`（pending／attached／deleted）、`storage_backend`、`storage_key`、`content_type`、`byte_size`、`width`、`height`、`sha256`、`created_at`、`attached_at`、`deleted_at`、`deleted_by`、`delete_reason`。
- `patient_consents`：見第 10 節。
- 只新增資料表，不改 `symptom_records` 等既有表。
