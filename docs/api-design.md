# Cancer Care Platform — REST API Architecture (v1.1)

> 狀態：設計文件；部分 Phase 1 endpoint 已實作（見「實作狀態」）
> 更新日期：2026-10-02（病人基本資料 + Email 通知）
> 依據：`docs/database-design.md`（v3.1）、`docs/ui-architecture.md`（v1）

## 實作狀態（2026-10-02）

以下 endpoint 已實作（`backend/app/modules/`）；本文件其他 endpoint 仍是設計，尚未實作。詳細行為以 `backend/README.md` 為準。

| Module | 已實作 |
|---|---|
| health | `GET /api/v1/health` |
| auth | `POST /api/v1/auth/login`、`GET /api/v1/auth/me`、`PUT /api/v1/auth/password`（Sprint 1） |
| dashboard | `GET /api/v1/dashboard/patient/{pid|me}`（一次回傳所有 widget，見 §8 實作說明）、`GET /api/v1/dashboard/layout`、`GET /api/v1/dashboard/widgets/caseload/data`、`GET /api/v1/dashboard/widgets/today-appointments/data`（Sprint 3） |
| settings | `GET /api/v1/settings/public` |
| symptom | `GET /api/v1/symptoms/forms/{code}`、`POST /api/v1/symptoms/records`、`GET /api/v1/symptoms/records/{pid|me}`、`POST /api/v1/symptoms/records/{id}/review` |
| vital_signs | `POST /api/v1/vital-signs`、`GET /api/v1/vital-signs/abnormal`、`GET /api/v1/vital-signs/reference-ranges` |
| labs | `GET /api/v1/labs/test-types`、`POST /api/v1/labs/results`、`GET /api/v1/labs/results/{pid}`、`GET /api/v1/labs/summary/{pid|me}`（見 §7A） |
| notification | Sprint 6：`GET /api/v1/notifications/unread-count`、`POST /api/v1/notifications/read-all`；`GET /api/v1/notifications`、`GET /api/v1/notifications/{id}`、`POST …/{id}/acknowledge`、`POST …/{id}/start`、`POST …/{id}/resolve`、`PATCH …/{id}/resolve`（快速處理）、`PATCH …/{id}/read` |
| patient | `GET /api/v1/patients/{pid|me}/timeline`；Sprint 1：`GET/POST /api/v1/patients`、`GET /api/v1/patients/cancer-types`、`GET/PATCH /api/v1/patients/{pid|me}`、`POST …/{pid}/account`、`GET/POST …/{pid}/care-alerts`、`PATCH …/care-alerts/{id}`、`GET/POST …/{pid}/diagnoses`、`PATCH …/diagnoses/{id}`、`GET/POST …/{pid}/nurse-assignments`、`POST …/nurse-assignments/{id}/end`（見 §4 實作說明） |
| chemotherapy | Sprint 2：`GET/POST /drugs`、`PATCH /drugs/{id}`、`GET/POST /regimens`、`GET/PATCH /regimens/{id}`、`GET/POST /plans`、`GET/PATCH /plans/{id}`、`POST /plans/{id}/discontinue`、`GET/POST /plans/{id}/cycles`、`GET/PATCH /cycles/{id}`、`POST /cycles/{id}/start|complete|delay`、`GET/POST /cycles/{id}/medications`、`GET /medications?patient_id=`、`POST /medications/{id}/amend`、`POST /medications/{id}/mark-error`（前綴 `/api/v1/chemotherapy`，見 §5 實作說明）；Sprint 3：`GET/POST /appointments`、`GET/PATCH /appointments/{id}`、`POST /appointments/{id}/check-in|complete|cancel|reschedule` |
| nursing | Sprint 4：`GET/POST /api/v1/nursing-assessments`、`GET/PATCH /nursing-assessments/{id}`、`GET /{id}/versions`、`POST /{id}/sign`、`POST /{id}/amend`、`PATCH /{id}/items/{item_id}`（見 §9 實作說明） |
| corrections | Sprint 5：`POST /symptoms/records/{id}/amend|mark-error`、`GET /symptoms/records/{id}/history`、`POST /vital-signs/{id}/amend|mark-error`、`GET /vital-signs/{id}/history`、`GET /vital-signs/patient/{pid}`、`POST /labs/results/{id}/amend|mark-error`、`GET /labs/results/{id}/history`、`GET /labs/abnormal`、`GET /dashboard/widgets/pending-symptom-reviews/data`（見 §1.6 實作說明） |
| admin | Sprint 1：`GET /api/v1/admin/users?role=nurse`、`POST /api/v1/admin/users`（只建立護理師，見 §13 實作說明） |
| profile / email（2026-10-02） | `GET /api/v1/patients/{pid|me}/profile`、`PATCH /api/v1/patients/me/profile`、`GET /api/v1/patients/{pid|me}/weights`、`POST /api/v1/patients/me/email-verification`、`POST /api/v1/patients/me/email-verification/confirm`；`POST /api/v1/notifications` 增加 Email 管道（`email_delivery`），見 §4、§10 實作說明 |

尚未實作：其餘 dashboard widget data endpoint、`GET /api/v1/settings/{key}`、patient 的 Phase 2 子資源（contacts / consents / clinical-events）、education、其餘 admin endpoint（停用 / 重設密碼 / audit-logs 查詢等）、AI。

## 變更摘要（v1 → v1.1）

| 項目 | 變更 |
|---|---|
| 路徑 | **全面採用 `/api/v1/<module>`**，包含 health（`/api/v1/health`）；`cancer-types` 移到 patient module 之下 |
| 分期 | 對齊 DB v3.1：Notification → Phase 1；Education、問卷排程、照護主題 widget → Phase 2；機構設定 DB 化、進階管理 → Phase 3；AI → Phase 4 |
| Idempotency | 改由 `idempotency_records` 表實作（取代觀察表 `client_request_id` 方案），新增兩個錯誤碼 |
| Dashboard | Phase 1 **沒有拖拉**，但提供 `GET /api/v1/dashboard/layout` 動態版面 JSON；新增 §8.1 **Layout JSON 契約**與 Widget Registry |
| Settings | Phase 1 `GET /api/v1/settings/public` 從後端設定檔讀取；Phase 3 改讀 `institution_settings` |
| ⚑ 標記 | 全部移除（v3.1 候選資料表均已併入 DB 設計） |

---

## 0. Module 與 Endpoint 總覽

| # | API | Prefix | Backend Module | Phase |
|---|---|---|---|---|
| 0 | Health | `/api/v1/health` | `health` | 1 |
| 1 | Authentication | `/api/v1/auth` | `auth` | 1 |
| 2 | Patient | `/api/v1/patients` | `patient` | 1（聯絡資料 / 同意書 / 臨床事件：2） |
| 3 | Chemotherapy（含治療行程） | `/api/v1/chemotherapy` | `chemotherapy` | 1 |
| 4 | Symptom | `/api/v1/symptoms` | `symptom` | 1（問卷排程：2） |
| 5 | Vital Signs | `/api/v1/vital-signs` | `vital_signs` | 1 |
| 6 | Nursing Assessment | `/api/v1/nursing-assessments` | `nursing` | 1 |
| 7 | Notification | `/api/v1/notifications` | `notification` | 1 |
| 8 | Dashboard | `/api/v1/dashboard` | `dashboard` | 1（動態版面 API）/ 2（可配置、拖拉） |
| 9 | Education | `/api/v1/education` | `education` | 2 |
| 10 | Settings | `/api/v1/settings` | `settings` | 1（讀設定檔）/ 3（DB） |
| 11 | Admin | `/api/v1/admin` | `admin` | 1（基本）/ 3（進階） |
| 12 | AI | `/api/v1/ai` | `ai` | 4（僅保留 prefix） |

### 對現有程式的影響

（已完成）各 blueprint 已改用 `/api/v1/<module>`，共用常數 `API_V1_PREFIX`。原規劃如下：

| Blueprint | 現在 | 改為 |
|---|---|---|
| `health_bp` | `/api` | `/api/v1`（route `/health`） |
| `auth_bp` | `/api/auth` | `/api/v1/auth` |
| `patient_bp` | `/api/patients` | `/api/v1/patients` |
| `chemotherapy_bp` | `/api/chemotherapy` | `/api/v1/chemotherapy` |
| `symptom_bp` | `/api/symptoms` | `/api/v1/symptoms` |
| `dashboard_bp` | `/api/dashboard` | `/api/v1/dashboard` |
| 新增 | — | `vital_signs`、`nursing`、`notification`、`settings`、`admin`（Phase 1）；`education`（Phase 2） |

> 建議把 `/api/v1` 定義成單一常數（例如 `API_V1_PREFIX`），各 blueprint 共用，避免日後前綴不一致。

---

## 1. 共通規範

### 1.1 命名與格式

| 項目 | 規範 |
|---|---|
| URL | `/api/v1/<module>/<resource>`；複數名詞、kebab-case |
| JSON 欄位 | snake_case |
| 時間 | ISO 8601 UTC，精確到毫秒（`2026-09-23T01:30:00.123Z`，與 JavaScript `toISOString()` 相同；前端 mock 使用同一格式）；日期 `2026-09-23`。用戶端送入的時間可帶或不帶毫秒，伺服器保存到毫秒 |
| 時區 | 回應一律用 UTC；病人相關回應附上 `meta.timezone` |
| 識別碼 | `users`、`patient_profiles` 的 `id` 一律是 `public_id`（UUID）；其他資源用整數 ID |
| `me` 別名 | 病人角色可以用 `me` 代替自己的 patient id |
| 動作型端點 | `POST /…/{id}/{action}`，如 `/amend`、`/sign`、`/acknowledge` |

### 1.2 Response 封套

```json
{ "data": { "id": 42 } }
```
```json
{ "data": [ { "id": 42 } ], "meta": { "page": 1, "per_page": 20, "total": 57 } }
```
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "輸入資料格式錯誤",
    "details": [ { "field": "values[0].value_numeric", "issue": "must be between 0 and 10" } ],
    "request_id": "c1f3a9e2-5b7d-4e1a-9c0f-2d8e6b4a1f37"
  }
}
```

### 1.3 HTTP Status 與錯誤碼

| Status | error.code | 情境 |
|---|---|---|
| 200 / 201 / 204 | — | |
| 400 | `VALIDATION_ERROR` | |
| 401 | `UNAUTHENTICATED`、`TOKEN_EXPIRED` | |
| 403 | `FORBIDDEN` | 角色不允許 |
| 404 | `NOT_FOUND` | 資源不存在，或無權存取該病人 |
| 409 | `CONFLICT`、`VERSION_CONFLICT`、`IDEMPOTENCY_IN_PROGRESS` | 重複 / 樂觀鎖衝突 / 相同 key 的請求還在處理中 |
| 422 | `RECORD_LOCKED`、`DEFINITION_LOCKED`、`INVALID_STATE`、`IDEMPOTENCY_KEY_MISMATCH`；2026-10-02：`EMAIL_NOT_VERIFIED`、`NO_EMAIL`、`EMAIL_NOT_CONFIGURED`、`VERIFICATION_LINK_INVALID` / `_USED` / `_EXPIRED` | 相同 key 但 request body 不同 |
| 423 | `ACCOUNT_LOCKED` | |
| 428 | `IDEMPOTENCY_KEY_REQUIRED` | 病人端的觀察類 POST 沒帶 key |
| 429 | `RATE_LIMITED` | |

### 1.4 分頁、篩選、排序

`page` / `per_page`（預設 20，上限 100）、`from` / `to`（臨床時間）、`sort=-recorded_at`、`include=values,form`

### 1.5 認證與授權

| 項目 | 設計 |
|---|---|
| Access Token | JWT，有效 15 分鐘，`Authorization: Bearer`，只放在前端記憶體 |
| Refresh Token | 有效 14 天；httpOnly + Secure + SameSite=Strict cookie（path=`/api/v1/auth`）；DB 存雜湊（`auth_tokens`） |
| 輪替 | 每次 refresh 發新 token；已撤銷的 token 被重複使用時，撤銷同一個 `family_id` 的所有 token |
| JWT claims | `sub`（user public_id）、`role`、`patient_id`（僅病人）、`exp` |

| 角色 | 病人資料範圍 | 可寫入 |
|---|---|---|
| patient | 只有自己 | 自己的症狀、生命徵象；通知已讀 |
| nurse | 目前被指派的病人 | 臨床資料、行程、護理評估、審閱、通知處理 |
| admin | 全部（讀取） | 帳號、主檔、通知規則；不直接寫臨床觀察資料 |

> **實作說明（Authentication Hardening）**：**Server-side session 已實作**——登入時建立一個 session＝`auth_tokens` 的一個 family（`family_id` 即 session id，第一筆為該 session 的 refresh token，只存 SHA-256 雜湊，14 天），access token 多一個 claim `sid`。JWT user lookup 除了 `is_active` 之外，還要求 `sid` 對應的 session 未撤銷、未過期；因此**登出、結束某個裝置、登出其他裝置、改密碼、管理者強制登出 / 重設密碼 / 停用帳號都會讓對應的 access token 立即失效**（不必等 15 分鐘）。沒有 `sid` 或 `sid` 不存在的 token 一律 `401`。**Refresh Token 已實作（same-origin）**：

| 項目 | 實作 |
|---|---|
| 架構 | 瀏覽器只連線到前端 origin；`/api/*` 由前端主機轉給 Flask（production：Render Static Site rewrite；開發：Vite proxy；E2E：`static-server.mjs`）。前端 API base URL 固定為相對路徑 `/api/v1`（build 拒絕跨網域的 `VITE_API_BASE_URL`） |
| Cookie | 名稱 `refresh_token`；`HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth; Expires=<session 到期>`；**不設 `Domain`**（host-only，只屬於前端網域）。Secure 在本機 `http://localhost` / `127.0.0.1` 也有效（瀏覽器視為可信來源）。refresh token **不出現在任何 response body**，也不放 `localStorage` / `sessionStorage`；頁面 JavaScript 讀不到（`document.cookie` 看不到） |
| 登入 | `POST /auth/login` → body：`access_token`、`token_type`、`expires_in`、`user`（與先前相同）；`Set-Cookie: refresh_token=…`。DB 只存 SHA-256 雜湊 |
| Refresh | `POST /auth/refresh`（無 body，需標頭 `X-Requested-With: XMLHttpRequest`，缺少回 `403`——跨站表單無法帶自訂標頭，與 SameSite=Strict 一起防 CSRF）→ `200` 同登入的 body（**同一個 session / `sid`**），並輪替 cookie：舊列標記 `used_at`，同一 family 新增一列，**到期時間沿用**（session 自登入起最長 14 天，不會因持續使用而延長）。失敗一律 `401` 並清除 cookie：`REFRESH_TOKEN_MISSING` / `_INVALID` / `_EXPIRED` / `_REVOKED`（已登出、被結束、改密碼、管理者撤銷）/ `_REUSED`（已輪替過的 token 再次出現 → 視為外洩，撤銷整個 session，稽核 `LOGOUT failure refresh_token_reused`）/ `ACCOUNT_DISABLED`。使用初始密碼的帳號可以 refresh，但其他 API 仍回 `403 PASSWORD_CHANGE_REQUIRED` |
| 前端 | Access token（15 分鐘）與 user 存在分頁的 `sessionStorage`（只有這個分頁；refresh token 不在這裡）。到期前（1 分鐘前，短效 token 取一半時間）主動 refresh；API 回 `401 TOKEN_EXPIRED` 時 axios interceptor 先 refresh（同時只會有一個 refresh 請求），成功後**重送原本的 request**；換頁時 token 已過期則 router 先 refresh 再進頁面。refresh 失敗 → 回登入頁（`登入已逾時`）。其他 `401`（session 已被結束、帳號停用、token 無效）直接回登入頁 |
| 登出 | `POST /auth/logout` 不需要有效的 access token：用 Bearer token（簽章正確、只是過期也可以）的 `sid`，沒有則用 refresh cookie，找出要結束的 session；撤銷整個 family、清除 cookie，回 `204`。重複登出回 `204`、不會再結束任何 session |
| 多裝置 | 每次登入是獨立的 session（獨立 family、獨立 cookie）；結束其中一個不影響其他 |

Mock mode 不模擬 HttpOnly cookie：mock access token 內含 mock session id 與到期時間（`mock-token-<user>~<sid>~<exp>`），到期時 mock handler 回 `401 TOKEN_EXPIRED`，`call()` / router 以 `mockRefresh` 取得新 token 後重試；session 被結束後 refresh 回 `401 REFRESH_TOKEN_REVOKED`，使用者看到的行為與 API mode 相同。

> Phase 1–2 的權限在程式內依角色判斷；Phase 3 改由 `permissions` / `role_permissions` 決定。病人範圍的限制永遠由 `nurse_patient_assignments` 決定。

### 1.6 觀察類資料寫入規則

適用：`vital_signs`、`symptom_records`、`medication_records`、`nursing_assessments`（Phase 2 加 `lab_results`、`clinical_events`）

| 操作 | API |
|---|---|
| 新增 | `POST`；`cycle_id`、`cycle_day` 由伺服器計算 |
| 修改 | 沒有 PUT / PATCH；`POST /…/{id}/amend` |
| 刪除 | 沒有 DELETE；`POST /…/{id}/mark-error`（必須附 `reason`） |
| 查詢 | 預設只回 `record_status=final`；`include_history=true` 連同更正鏈 |

> **實作說明（Sprint 5，Implemented：症狀紀錄、生命徵象、檢驗結果）**
> - `POST …/{id}/amend`（目前負責的護理師；必填 `amend_reason`，只送要更正的欄位：症狀 `values`（整份）/ `recorded_at` / `notes`；生命徵象各量測欄位、部位、`notes`、`measured_at`；檢驗 `value` / `collected_at`）。以原紀錄 + 更正欄位**經由原本的建立流程**產生新紀錄（同樣的驗證與 Alert Engine），保留原紀錄的 Cycle 與 `cycle_day`（不重算）、填寫者、來源與症狀審閱狀態；原紀錄變成 `amended`，`amends_id` 串接。沒有變更 `400`，對非 `final` 紀錄 `409`，`Idempotency-Key` 支援。修正舊紀錄時不要求之後才新增的必填題目。
> - `POST …/{id}/mark-error`（必填 `reason`）→ `entered_in_error`（保留，不再使用）。
> - **原紀錄的警示**（沿用 Notification Workflow 的完成步驟）：更正後不再符合規則、或已依更正後紀錄重新通知 → 以系統說明關閉；仍符合規則但新警示因冷卻時間未發出 → 保留（不會漏掉真正的警示）。標示錯誤 → 關閉該紀錄所有未處理警示。
> - `GET …/{id}/history`（nurse、admin）：更正鏈（最舊到最新）。
> - 待審清單：`GET /dashboard/widgets/pending-symptom-reviews/data`（目前病人的待審症狀，最久的在前，含 `open_alert_count`）、`GET /labs/abnormal?hours=`（異常檢驗），加上既有的 `GET /vital-signs/abnormal`、`GET /notifications?status=pending`。`GET /vital-signs/patient/{pid}`：照護人員的生命徵象清單。
> - Dashboard、Risk Engine、Timeline、今日症狀等都只使用 `final` 紀錄，因此自動改用更正後的資料。症狀紀錄回應新增 `amends_id`。
> - Pending：病人自行更正自己的回報（設計的「原填寫者」）尚未開放；給藥紀錄的更正見 §5（Sprint 2）。

### 1.7 Idempotency（對應 `idempotency_records`）

| 項目 | 規則 |
|---|---|
| Header | `Idempotency-Key: <uuid v4>` |
| 必填 | 病人端（`role=patient`）的觀察類 POST **必填**，沒帶回 `428`；其他寫入端點建議帶 |
| 適用端點 | 觀察類 POST、`/amend`、`POST /api/v1/nursing-assessments`、`POST /api/v1/chemotherapy/cycles/{id}/medications` |
| 範圍 | 以「使用者 + key」為唯一；有效 24 小時 |
| 重送（內容相同） | 不重新執行，回傳原本的 status 與**目前的**資源內容，header `Idempotent-Replayed: true` |
| 重送（內容不同） | `422 IDEMPOTENCY_KEY_MISMATCH` |
| 前一個請求還在處理 | `409 IDEMPOTENCY_IN_PROGRESS`（前端稍後重試） |
| 前一個請求 5xx | 可以用同一個 key 重試 |
| 前端規則 | **使用者按一次送出就產生一個 key**；自動重試沿用同一個 key；使用者修改內容後再送出要產生新的 key |

### 1.8 稽核與追蹤

- `X-Request-ID`：沒帶就由伺服器產生，並在 response header 回傳。
- 所有寫入操作和病人層級的讀取都寫入 `audit_logs`（與業務資料同一個 transaction）。
- Idempotent replay 不會重複寫入稽核的 `CREATE` 紀錄。

---

## 2. Health API

| Endpoint | Method | 角色 | 說明 | Tables |
|---|---|---|---|---|
| `/api/v1/health` | GET | 公開 | 服務狀態與資料庫連線（部署平台的 health check） | — |

```json
{ "status": "ok", "database": "ok" }
```

> 已實作：資料庫無法連線時回 `503 { "status": "error", "database": "unavailable" }`。

> 這是唯一不使用 `data` 封套的端點，方便負載平衡器與監控工具直接讀取。

---

## 3. Authentication API

| Endpoint | Method | 角色 | 說明 | Tables |
|---|---|---|---|---|
| `/api/v1/auth/login` | POST | 公開 | 登入 | users, roles, auth_tokens, audit_logs |
| `/api/v1/auth/refresh` | POST | refresh cookie（`X-Requested-With: XMLHttpRequest`） | 換發 Access Token（輪替 refresh cookie，同一 session）——已實作 | auth_tokens, audit_logs |
| `/api/v1/auth/logout` | POST | Bearer（可已過期）或 refresh cookie | 結束目前 session、清除 cookie——已實作 | auth_tokens, audit_logs |
| `/api/v1/auth/me` | GET | 已登入 | 目前使用者 | users, roles, patient_profiles, nurse_profiles |
| `/api/v1/auth/password` | PUT | 已登入 | 變更密碼 | users, auth_tokens, audit_logs |
| `/api/v1/auth/password-reset/request` | POST | 公開 | 申請重設 | users, auth_tokens |
| `/api/v1/auth/password-reset/confirm` | POST | 公開 | 重設 | users, auth_tokens, audit_logs |

### POST `/api/v1/auth/login`

Request
```json
{ "email": "patient.a@demo.local", "password": "********" }
```

Response `200`（並 `Set-Cookie: refresh_token=...; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`）
```json
{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "Bearer",
    "expires_in": 900,
    "user": {
      "id": "3f2b8c1e-9a4d-4f6b-8e2a-1c7d5b9e0a34",
      "display_name": "測試病人 A",
      "email": "patient.a@demo.local",
      "role": "patient",
      "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    }
  }
}
```

錯誤：`401 UNAUTHENTICATED`（不區分是帳號還是密碼錯）、`423 ACCOUNT_LOCKED`

### GET `/api/v1/auth/me`

Response `200`（護理師）
```json
{
  "data": {
    "id": "a8d1e4f2-6c3b-4a9e-b7d5-2f1e8c6a4b90",
    "display_name": "護理師 林",
    "email": "nurse01@demo.local",
    "role": "nurse",
    "nurse_profile": { "staff_code": "N-0001", "department": "腫瘤科病房", "title": "個管師" },
    "assigned_patient_count": 18,
    "last_login_at": "2026-09-23T00:12:45Z"
  }
}
```

### PUT `/api/v1/auth/password`

Request
```json
{ "current_password": "********", "new_password": "********" }
```
Response `204`；成功後撤銷其他所有 Refresh Token。

> **實作說明（Authentication Hardening）**：改密碼成功後，**同帳號的其他 session 全部結束**（該些 access token 立即 `401`），目前這個 session 繼續有效；稽核 `changes` 另有 `sessions_revoked`（數量）。新增：`POST /auth/logout`（`204`，結束目前 session、清除 refresh cookie，稽核 `LOGOUT`；access token 過期或使用初始密碼期間也可呼叫，見 §1.5）、`GET /auth/sessions`（自己的有效 session：`id, created_at, expires_at, ip_address, user_agent, current`，目前的排第一；不含 token 或雜湊）、`DELETE /auth/sessions/{id}`（結束自己的某個 session，`204`；不存在或別人的 `404`）、`POST /auth/sessions/revoke-others`（`{revoked}`）。管理端：`GET /admin/users/{id}/sessions`、`POST /admin/users/{id}/revoke-sessions`（`{revoked}`，不能對自己 `409`）、`POST /admin/users/{id}/password-reset`（系統產生新的初始密碼，只在回應出現一次 `temporary_password`，帳號回到「首次登入需設定新密碼」狀態，並清除登入鎖定、結束全部 session；不能對自己 `409`；稽核 `{"password": "reset", "temporary_password_issued": true, "sessions_revoked": n}`，不含密碼）。停用帳號同時結束全部 session，重新啟用不會恢復舊 session。使用者物件新增 `active_sessions`。`/auth/refresh`：已實作（見 §1.5）；改密碼、管理者強制登出 / 重設密碼、停用帳號、結束裝置後，被結束的 session 也不能再 refresh。**Pending**：公開的 `password-reset/request` / `confirm`（需要 email / 簡訊寄送管道，Phase 1 沒有；目前以管理者重設密碼取代）。

> **實作說明（Sprint 1）**：已實作。規則：8–128 字元、同時包含英文字母與數字、不可與目前密碼相同；錯誤回 `400 VALIDATION_ERROR`，`details` 的 `field` 為 `current_password` / `new_password`。稽核寫 `UPDATE users`，`changes` 只有 `{"password": "changed", "temporary_password_replaced": true|false}`，不含任何密碼。尚未實作 refresh token，因此沒有撤銷動作；已發出的 access token 到期前仍有效。
> **首次登入**：系統產生的初始密碼帳號 `users.password_changed_at` 為 NULL，登入回應的 `user.must_change_password = true`（`/auth/me` 同）。在設定新密碼前，除了 `GET /auth/me` 與 `PUT /auth/password`，其他需登入的 API 一律回 `403 PASSWORD_CHANGE_REQUIRED`。

---

## 4. Patient API

| Endpoint | Method | 角色 | 說明 | Tables | Phase |
|---|---|---|---|---|---|
| `/api/v1/patients` | GET | nurse, admin | 病人列表 | patient_profiles, nurse_patient_assignments, cancer_diagnoses, chemotherapy_cycles | 1 |
| `/api/v1/patients` | POST | nurse, admin | 建立病人 | patient_profiles, audit_logs | 1 |
| `/api/v1/patients/cancer-types` | GET | 全部 | 癌別清單 | cancer_types | 1 |
| `/api/v1/patients/{pid}` | GET | 有權者 | 詳情；`pid` 可為 `me` | patient_profiles, patient_care_alerts, cancer_diagnoses, nurse_patient_assignments | 1 |
| `/api/v1/patients/{pid}` | PATCH | nurse, admin | 更新基本資料 | patient_profiles | 1 |
| `/api/v1/patients/{pid}/account` | POST | nurse, admin | 建立病人登入帳號 | users, patient_profiles | 1 |
| `/api/v1/patients/{pid}/care-alerts` | GET / POST | 讀：有權者；寫：nurse | 注意事項 | patient_care_alerts | 1 |
| `/api/v1/patients/{pid}/care-alerts/{id}` | PATCH | nurse | 更新或停用 | patient_care_alerts | 1 |
| `/api/v1/patients/{pid}/diagnoses` | GET / POST | 讀：有權者；寫：nurse | | cancer_diagnoses, cancer_types | 1 |
| `/api/v1/patients/{pid}/diagnoses/{id}` | PATCH | nurse | | cancer_diagnoses | 1 |
| `/api/v1/patients/{pid}/nurse-assignments` | GET / POST | 讀：nurse；寫：admin | | nurse_patient_assignments | 1 |
| `/api/v1/patients/{pid}/nurse-assignments/{id}/end` | POST | admin | 結束指派 | nurse_patient_assignments | 1 |
| `/api/v1/patients/{pid}/timeline` | GET | 有權者（病人本人、負責護理師、admin） | 照護時間軸（已實作，見下方） | chemotherapy_cycles, medication_records, symptom_records, vital_signs, lab_results, notifications, nursing_assessments | 1 |
| `/api/v1/patients/{pid}/profile` | GET | 有權者（本人、負責護理師、admin） | 病人基本資料：身高、最新體重、BMI、通知 Email 與驗證狀態（員工只看到遮罩）（已實作，2026-10-02） | patient_profiles, patient_contacts, vital_signs, auth_tokens | 1 |
| `/api/v1/patients/me/profile` | PATCH | patient（本人） | 病人自行維護：通知 Email、身高、Email 通知開關（已實作） | patient_profiles, patient_contacts, auth_tokens, audit_logs | 1 |
| `/api/v1/patients/{pid}/weights` | GET | 有權者 | 體重紀錄（`vital_signs.weight_kg`，新到舊；新增體重用 `POST /vital-signs`）（已實作） | vital_signs | 1 |
| `/api/v1/patients/me/email-verification` | POST | patient（本人） | 寄出一次性驗證連結（已實作） | auth_tokens, audit_logs | 1 |
| `/api/v1/patients/me/email-verification/confirm` | POST | patient（本人） | `{token}` 完成驗證（已實作） | auth_tokens, patient_contacts, audit_logs | 1 |
| `/api/v1/patients/{pid}/contacts` | GET / PUT | 主責 nurse, admin | | patient_contacts | 2 |
| `/api/v1/patients/{pid}/consents` | GET / POST | nurse, admin | | patient_consents | 2 |
| `/api/v1/patients/{pid}/clinical-events` | GET / POST | nurse | | clinical_events | 2 |

> 路由順序：`/patients/cancer-types` 這種固定路徑要比 `/patients/{pid}` 先註冊；`pid` 只接受 UUID 或 `me`，不會衝突。

> **實作說明（Sprint 1，已實作上表 Phase 1 的列）**
> - **病人代碼**：由系統產生 `P00001`、`P00002`…（取所有代碼含已刪除者的最大值 + 1，不重複使用）；不接受 `patient_code`、`id`、`is_demo`、`user_id`，也拒絕 `national_id` 等身分證欄位（`400`）。同時建立撞號時（`UNIQUE(patient_code)`）自動改用下一個代碼重試。本範例的 `CCP-000123` 為原始設計格式。
> - **POST `/patients`**：`display_name`、`gender`（male / female / other）、`date_of_birth` 必填；選填 `height_cm`、`blood_type`、`allergies`、`baseline_ecog`、`timezone`（IANA，預設 Asia/Taipei）與 `account: {email}`（同時建立登入帳號）。回應 `201` 的 `data.account.temporary_password` 是系統產生的初始密碼，**只在這個回應出現一次**，不寫入稽核。
> - **POST `/patients/{pid}/account`**：`{email}` → `201 { patient_id, email, temporary_password, must_change_password: true }`；已有帳號回 `409`。
> - **權限**：沿用既有規則——管理者可存取全部病人、病人只能存取自己、護理師只能存取**目前有效指派**的病人，其他一律 `404`（URL 帶別人的 `patient_id` 也一樣）。列表：護理師只列出目前指派的病人；管理者可用 `assigned=true|false` 篩選。護理師建立的病人，要等管理者指派後才看得到。指派結束後，原護理師立即對該病人所有資料得到 `404`；新指派的護理師立即可存取。
> - **GET `/patients`** 每筆：`id`、`patient_code`、`display_name`、`gender`、`age`、`primary_diagnosis`、`current_cycle`、`has_account`、`care_team`（`primary_nurse`、`nurses[]`）、`created_at`；護理師另有 `is_primary_nurse`。Query：`q`（代碼或姓名）、`sort=patient_code|display_name|created_at`、`page`、`per_page`（≤100）。風險欄位請用 `dashboard/widgets/caseload/data`。
> - **GET `/patients/{pid}`**：基本資料、`care_alerts`（啟用中）、`diagnoses`、`current_cycle`；照護人員另有 `care_team`、`account`（`has_account`、`email`、`is_active`、`must_change_password`、`last_login_at`）、`created_at`、`created_by`，病人本人看不到這些欄位。
> - **指派**：`POST …/nurse-assignments` `{nurse_id, is_primary?}`（`nurse_id` 為護理師 user id；同一護理師重複指派 `409`；新的主責會取消原主責）；`POST …/{id}/end` 結束（已結束 `409`）。`GET …/nurse-assignments` 回傳歷程（有效的在前），每筆含 `nurse`、`is_primary`、`assigned_at`、`ended_at`、`active`、`assigned_by`。
> - **稽核**（皆不含密碼）：`CREATE patient_profiles`（代碼與欄位名稱）、`CREATE users`（`temporary_password_issued: true`）、`UPDATE patient_profiles`（只記欄位名稱）、`ASSIGN nurse_patient_assignments`、`UPDATE nurse_patient_assignments`（`ended: true`）、care alerts / diagnoses 的 `CREATE` / `UPDATE`、詳情頁 `VIEW`。
> - 修改病人資料不會重算既有紀錄的 `cycle_day`，也不會改動歷史症狀 / 生命徵象 / 檢驗 / 給藥紀錄。

### GET `/api/v1/patients`

Query：`?q=CCP-0001&risk_level=high&has_active_plan=true&page=1&per_page=20&sort=display_name`

Response `200`
```json
{
  "data": [
    {
      "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      "patient_code": "CCP-000123",
      "display_name": "測試病人 A",
      "gender": "male",
      "age": 31,
      "primary_diagnosis": { "cancer_type_code": "C11", "name_zh": "鼻咽癌", "stage": "III" },
      "current_cycle": { "cycle_number": 3, "total_cycles": 6, "cycle_day": 9, "in_nadir": true },
      "latest_risk_level": "high",
      "last_symptom_report_at": "2026-09-22T23:40:00Z",
      "pending_review_count": 2,
      "unacknowledged_alert_count": 1,
      "is_primary_nurse": true
    }
  ],
  "meta": { "page": 1, "per_page": 20, "total": 18 }
}
```

### POST `/api/v1/patients`

Request
```json
{
  "display_name": "測試病人 B",
  "gender": "female",
  "date_of_birth": "1968-05-02",
  "height_cm": 158.0,
  "blood_type": "A",
  "allergies": "無",
  "baseline_ecog": 1,
  "timezone": "Asia/Taipei"
}
```

Response `201`
```json
{
  "data": {
    "id": "e4b7c2d9-1f3a-4c8e-9b6d-5a2f7e1c3d80",
    "patient_code": "CCP-000124",
    "display_name": "測試病人 B",
    "is_demo": true,
    "created_at": "2026-09-23T02:05:11Z"
  }
}
```

> 請求中出現 `national_id`、`id_number` 等欄位時，回 `400 VALIDATION_ERROR`。

### GET `/api/v1/patients/{pid}`

Response `200`
```json
{
  "data": {
    "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "patient_code": "CCP-000123",
    "display_name": "測試病人 A",
    "gender": "male",
    "date_of_birth": "1995-03-14",
    "age": 31,
    "height_cm": 172.0,
    "blood_type": "O",
    "allergies": "Penicillin",
    "baseline_ecog": 1,
    "timezone": "Asia/Taipei",
    "is_demo": true,
    "care_alerts": [
      { "id": 12, "alert_type": "allergy", "description": "Penicillin 過敏", "severity": "high" },
      { "id": 13, "alert_type": "limb_restriction", "body_site": "right_arm", "description": "右手禁止注射及量血壓", "severity": "high" }
    ],
    "diagnoses": [
      { "id": 5, "cancer_type": { "code": "C11", "name_zh": "鼻咽癌" }, "stage": "III", "diagnosis_date": "2026-06-10", "status": "active", "is_primary": true }
    ],
    "care_team": {
      "primary_nurse": { "id": "a8d1e4f2-6c3b-4a9e-b7d5-2f1e8c6a4b90", "display_name": "護理師 林" }
    }
  }
}
```

### POST `/api/v1/patients/{pid}/care-alerts`

Request
```json
{
  "alert_type": "limb_restriction",
  "body_site": "right_arm",
  "description": "右手禁止注射及量血壓（淋巴水腫風險）",
  "severity": "high"
}
```
Response `201`：回傳建立的 alert。

### GET `/api/v1/patients/{pid}/timeline`

> **Patient Care Timeline sprint（已實作）**：Query 改為 `start_date` / `end_date`（病人時區的日期）、`limit`（1–100，預設 30）、`cursor`（keyset 分頁，`meta.next_cursor` / `meta.has_more`）。`event_type` 固定為 `CHEMOTHERAPY` / `SYMPTOM` / `VITAL_SIGN` / `LAB_RESULT` / `NOTIFICATION` / `NOTIFICATION_STATUS` / `NURSING_ASSESSMENT`；每筆含 `event_id`、`occurred_at`、`all_day`、`title`、`summary`、`severity`、`source`、`source_id`、`cycle_id`、`cycle_day`、`detail`。病人可查看自己的時間軸，內容遵循通知處理流程的病人可見規則（無內部備註、處理者姓名、護理評估內容與風險判斷）。Sprint 3 新增第 8 種 `APPOINTMENT`：時間已到的行程（含已完成、已取消、已改期；`detail` 含 `status_text`、準備事項、`rescheduled_from` / `rescheduled_to`，照護人員另有 `notes`），尚未到的行程不列入；原本 7 種事件行為不變。下方為原始設計範例。

Response `200`（實作）
```json
{
  "data": [
    { "event_id": "NOTIFICATION_STATUS:12:resolved", "event_type": "NOTIFICATION_STATUS", "occurred_at": "2026-09-25T06:10:00Z", "all_day": false,
      "title": "已處理完成", "summary": "「化療期間發燒」已處理完成", "severity": null,
      "source": { "table": "notifications", "id": 12 }, "source_id": 12, "cycle_id": 1, "cycle_day": 4,
      "detail": { "notification_id": 12, "status": "resolved" } },
    { "event_id": "VITAL_SIGN:5", "event_type": "VITAL_SIGN", "occurred_at": "2026-09-25T05:40:00Z", "all_day": false,
      "title": "生命徵象量測", "summary": "體溫 38.6°C、心跳 92次/分", "severity": "critical",
      "source": { "table": "vital_signs", "id": 5 }, "source_id": 5, "cycle_id": 1, "cycle_day": 4,
      "detail": { "values": [{ "field": "temperature_c", "label": "體溫", "value": "38.6°C", "flag": "critical" }], "flags": ["體溫 38.6°C，化療期間發燒請立即聯絡醫療團隊"] } }
  ],
  "meta": { "limit": 30, "returned": 2, "has_more": true, "next_cursor": "WyIyMDI2LTA5LTI1VDA1OjQwOjAwIiw3LDVd", "start_date": null, "end_date": null, "timezone": "Asia/Taipei" }
}
```

原始設計：

Query：`?from=2026-09-15T00:00:00Z&to=2026-09-23T00:00:00Z&types=symptom,vital,medication,assessment,appointment`

Response `200`
```json
{
  "data": [
    { "type": "vital", "id": 881, "occurred_at": "2026-09-22T12:00:00Z", "cycle_day": 8, "summary": "T 38.2°C, HR 104", "flags": ["fever"] },
    { "type": "symptom", "id": 402, "occurred_at": "2026-09-22T11:30:00Z", "cycle_day": 8, "summary": "噁心 7、疲倦 6", "review_status": "submitted" },
    { "type": "medication", "id": 77, "occurred_at": "2026-09-15T02:00:00Z", "cycle_day": 1, "summary": "Cisplatin 170 mg IV" },
    { "type": "appointment", "id": 290, "occurred_at": "2026-09-15T01:00:00Z", "cycle_day": 1, "summary": "化療注射（Cycle 3）", "status": "completed" }
  ],
  "meta": { "timezone": "Asia/Taipei" }
}
```

---

> **實作說明（病人基本資料 + Email 通知，2026-10-02）**
> - **路由**：`GET /patients/{pid|me}/profile`（patient：本人；nurse：目前指派的病人；admin：全部；其他一律 `404`）。`PATCH /patients/{pid|me}/profile` 只限 patient 角色（nurse / admin `403`；別的病人 `404`）。沒有 `/me/profile`。
> - **Response**（`data`，所有角色同一形狀）：`patient_id`、`patient_code`、`display_name`、`height_cm`、`latest_weight`（`{id, weight_kg, measured_at, source, entered_by_patient}`，員工另有 `recorded_by`）、`bmi`、`email`（**員工一律 `null`**）、`email_masked`（`p***@example.com`）、`email_verified`、`email_verified_at`、`email_notification_enabled`、`email_verification_sent_at`（本人、尚未驗證且有有效連結時）、`email_delivery_available`（沒有設定 Email 服務時為 `false`）。員工的 `GET /patients/{pid}` 另有 `notification_contact: {email_masked, email_verified, email_notification_enabled}`；病人本人的 `GET /patients/me` 不變。
> - **PATCH body**：只接受 `email`（`null` / 空字串 = 移除；trim + 小寫）、`height_cm`（30–250）、`email_notification_enabled`（boolean）；其他欄位 `400`（姓名、過敏等仍由護理師以 `PATCH /patients/{pid}` 維護；護理師的 `PATCH /patients/{pid}` 不接受 `email`，`400`）。**更換 Email**：清除驗證、`email_notification_enabled` 改為 `false`、已寄出的驗證連結失效。**開啟 Email 通知**需要已驗證的 Email，否則 `422 EMAIL_NOT_VERIFIED`（整個請求不寫入）。通知 Email 與登入帳號 `users.email` 無關，登入帳號不會改變。
> - **體重**：沿用 `POST /vital-signs`（`{patient_id: "me", weight_kg}`，病人必須帶 `Idempotency-Key`）；病人輸入 `source = patient_app`、`recorded_by` = 病人帳號，稽核 `CREATE vital_signs` 的 actor 為病人。歷史保留（append-only，更正走既有 amend / mark-error）。`GET /patients/{pid}/weights?limit=1–100`（預設 30）：final 且有體重的紀錄，新到舊；病人看不到 `recorded_by`。病人輸入的體重會被既有 Risk Engine（7 天體重變化 ≤ −3% → medium）讀取，Risk Engine 與門檻都沒有修改。
> - **BMI**：最新 final 體重 ÷（身高 m）²，四捨五入（half up）到小數 1 位；缺身高或體重為 `null`；不存 DB。
> - **Email 驗證**：`POST /patients/me/email-verification` → `200 {delivery: {status: sent|failed, error_code}, profile}`；寄出 `<APP_BASE_URL>/patient/verify-email#token=…`（token 在 URL fragment，不會進入伺服器 log / Referer），24 小時有效、只能用一次，重寄或更換 Email 後舊連結失效。沒有 Email → `422 NO_EMAIL`；已驗證 → `409`；60 秒內重寄 → `429 RATE_LIMITED`；沒有 Email 服務（`EMAIL_PROVIDER=disabled`）→ `422 EMAIL_NOT_CONFIGURED`。`POST /patients/me/email-verification/confirm {token}`：必須是收到連結的病人本人登入（別人的連結 `422`）；`VERIFICATION_LINK_INVALID`（不存在 / 已被取代）、`_USED`、`_EXPIRED` 皆為 `422`（不使用 `401` / `TOKEN_EXPIRED`，避免觸發前端 refresh）。驗證成功後 Email 通知仍為關閉，由病人自行開啟。
> - **稽核**：`UPDATE patient_profiles` / `UPDATE patient_contacts`（只記欄位名稱，`by: patient`；更換 Email 另記 `email_verification_reset: true`）、`email_verification_requested`、`email_verified`；**不記錄** Email 地址、驗證連結 / token、Email 內容。員工讀取 profile 記 `VIEW patient_profile`。

## 5. Chemotherapy API（含 Treatment Schedule）

| Endpoint | Method | 角色 | 說明 | Tables |
|---|---|---|---|---|
| `/api/v1/chemotherapy/drugs` | GET / POST | 讀：nurse, admin；寫：admin | 藥物主檔 | drugs |
| `/api/v1/chemotherapy/drugs/{id}` | PATCH | admin | | drugs |
| `/api/v1/chemotherapy/regimens` | GET / POST | 讀：nurse, admin；寫：admin | 處方範本 | chemo_regimens, regimen_drugs, drugs |
| `/api/v1/chemotherapy/regimens/{id}` | GET / PATCH | 同上 | | chemo_regimens, regimen_drugs |
| `/api/v1/chemotherapy/plans` | GET | 有權者 | `?patient_id=`（必填） | chemotherapy_plans |
| `/api/v1/chemotherapy/plans` | POST | nurse | 建立療程，可自動產生 cycles | chemotherapy_plans, chemotherapy_cycles |
| `/api/v1/chemotherapy/plans/{id}` | GET / PATCH | 讀：有權者；寫：nurse | | chemotherapy_plans, chemotherapy_cycles |
| `/api/v1/chemotherapy/plans/{id}/discontinue` | POST | nurse | | chemotherapy_plans |
| `/api/v1/chemotherapy/plans/{id}/cycles` | GET / POST | 讀：有權者；寫：nurse | | chemotherapy_cycles |
| `/api/v1/chemotherapy/cycles/{id}` | GET / PATCH | 同上 | | chemotherapy_cycles |
| `/api/v1/chemotherapy/cycles/{id}/start` | POST | nurse | 設定 Day 1，並重算相關紀錄的 `cycle_day` | chemotherapy_cycles |
| `/api/v1/chemotherapy/cycles/{id}/complete` | POST | nurse | | chemotherapy_cycles |
| `/api/v1/chemotherapy/cycles/{id}/delay` | POST | nurse | | chemotherapy_cycles, appointments |
| `/api/v1/chemotherapy/cycles/{id}/medications` | GET / POST | 讀：有權者；寫：nurse | 給藥紀錄 | medication_records, drugs, idempotency_records |
| `/api/v1/chemotherapy/medications/{id}/amend` | POST | nurse | | medication_records |
| `/api/v1/chemotherapy/medications/{id}/mark-error` | POST | nurse | | medication_records |
| `/api/v1/chemotherapy/appointments` | GET | 有權者 | `?patient_id=&date=` 或 `from/to` | appointments, appointment_instructions |
| `/api/v1/chemotherapy/appointments` | POST | nurse | 建立行程 + 準備事項 | appointments, appointment_instructions |
| `/api/v1/chemotherapy/appointments/{id}` | GET / PATCH | 讀：有權者；寫：nurse | 修改內容、準備事項 | appointments, appointment_instructions |
| `/api/v1/chemotherapy/appointments/{id}/check-in` | POST | nurse | 報到 | appointments |
| `/api/v1/chemotherapy/appointments/{id}/complete` | POST | nurse | | appointments |
| `/api/v1/chemotherapy/appointments/{id}/cancel` | POST | nurse | | appointments |
| `/api/v1/chemotherapy/appointments/{id}/reschedule` | POST | nurse | 建立新行程（`rescheduled_from_id`），原行程改為 `rescheduled` | appointments, appointment_instructions |

> **實作說明（Sprint 2，Implemented：上表除 appointments 以外的列）**
> - **權限**：沿用既有病人存取規則。療程 / Cycle / 給藥紀錄都屬於某位病人：admin 全部、病人本人、目前指派的護理師可讀，其他一律 `404`；寫入限目前指派的護理師（admin 寫入回 `403`）。藥物 / 處方主檔：nurse、admin 可讀，admin 可寫。
> - **cycle_day 不重算（與原設計不同）**：`POST /cycles/{id}/start` 只設定 Day 1（`start_date`，預設今天，病人時區；不可為未來、不可早於 30 天前、需晚於前一個 Cycle），**不重算既有紀錄的 `cycle_id` / `cycle_day`**；開始之後寫入的症狀 / 生命徵象 / 檢驗 / 給藥紀錄才屬於新 Cycle。已開始的 Cycle 不能修改開始日期（`PATCH` 拒絕 `actual_start_date`）。
> - **狀態規則**：同一位病人同時只能有一個 `planned`/`active` 療程、一個 `in_progress` Cycle；Cycle 必須依序開始（前面的 Cycle 需已完成或取消），開始第一個 Cycle 時療程由 `planned` 變成 `active`；最後一個 Cycle 完成後療程變成 `completed`。`delay` 只適用尚未開始的 Cycle（`delay_days` 累加）；`discontinue`（需 `discontinue_reason`）會取消尚未開始的 Cycle、進行中的 Cycle 以今天結束。開始時若未指定骨髓抑制期，沿用前一個已完成 Cycle 的 `nadir_start_day` / `nadir_end_day`。錯誤碼：狀態不符 `409 INVALID_STATE`、尚未開始的 Cycle 登錄給藥 `409 CYCLE_NOT_STARTED`。
> - **給藥紀錄**（觀察類，append-only）：`administered_at` 必須在該 Cycle 期間內且不是未來；`cycle_day` 在寫入時計算。`Idempotency-Key`（建議帶）：相同 key + 相同內容重送回原紀錄（`Idempotent-Replayed: true`），不會新增第二筆；不同內容回 `422`。`amend`（必填 `amend_reason`，其他欄位只送要更正的）建立新紀錄（`amends_id` → 原紀錄），原紀錄變成 `amended`；`mark-error`（必填 `reason`）→ `entered_in_error`。原因記在 AuditLog（`AMEND` / `MARK_ERROR`），不另建欄位。
> - **病人可見內容**：病人只看到 `final` 的給藥紀錄，不含 `administered_by`、`reaction_notes`、`amends_id`；療程不含 `created_by`，Cycle 不含體重 / BSA / 備註。照護人員另可看到完整更正歷程（`amended_by_id`）。
> - `generate_infusion_appointments: {enabled, time: "HH:MM", location}`（需 `generate_cycles`）替每個 Cycle 建立 `chemo_infusion` 行程；Cycle 回應含 `appointment_id`。`delay` 帶 `reschedule_appointments: true` 時，該 Cycle 尚未報到的行程以改期方式順延相同天數（Sprint 3）。
> - **治療行程（Sprint 3，Implemented）**：`POST /appointments`（nurse）含 `instructions[]`（`instruction_type`、`text`、`due_at?`、`is_highlighted?`，最多 10 項）；時間需在 30 天前到一年內。`PATCH` 可改內容與準備事項（整份取代），**不能改時間**。狀態：`scheduled → checked_in → completed`、`scheduled/checked_in → cancelled`（需 `reason`）；還沒到的日期不能報到 / 完成；狀態不符 `409 INVALID_STATE`。`reschedule`（需 `scheduled_at`、`reason`）建立新行程（`rescheduled_from_id`），原行程變成 `rescheduled` 並回傳 `rescheduled_to_id`，準備事項時間一併順移。`GET /appointments?patient_id=&date=|from=&to=&status=`（病人時區日期）。病人看不到 `notes`、`created_by`。今日行程（`today-schedule` widget）排除已取消 / 已改期；`GET /dashboard/widgets/today-appointments/data`（nurse）列出目前負責病人今天的行程。所有寫入皆寫 AuditLog（原因在 `changes.reason`）。
> - Dashboard（treatment-progress、caseload）、Patient Timeline（Cycle 開始 / 結束、`final` 給藥紀錄）與 Risk Engine（骨髓抑制期、Cycle 內的新紀錄）直接使用上述資料，邏輯未修改。

### POST `/api/v1/chemotherapy/plans`

Request
```json
{
  "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "diagnosis_id": 5,
  "regimen_id": 3,
  "plan_name": "Cisplatin + 5-FU 誘導化療",
  "intent": "neoadjuvant",
  "line_of_therapy": 1,
  "total_cycles": 6,
  "start_date": "2026-08-04",
  "attending_physician_name": "王醫師",
  "generate_cycles": true,
  "generate_infusion_appointments": { "enabled": true, "time": "09:00", "location": "日間化療室" }
}
```

Response `201`
```json
{
  "data": {
    "id": 21,
    "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "regimen": { "id": 3, "name": "PF", "cycle_length_days": 21, "emetogenic_risk": "high" },
    "plan_name": "Cisplatin + 5-FU 誘導化療",
    "intent": "neoadjuvant",
    "total_cycles": 6,
    "start_date": "2026-08-04",
    "status": "planned",
    "attending_physician_name": "王醫師",
    "cycles": [
      { "id": 101, "cycle_number": 1, "scheduled_date": "2026-08-04", "status": "scheduled", "appointment_id": 250 },
      { "id": 102, "cycle_number": 2, "scheduled_date": "2026-08-25", "status": "scheduled", "appointment_id": 251 }
    ]
  }
}
```

> `generate_infusion_appointments` 會替每個 Cycle 建立一筆 `chemo_infusion` 行程（`time` 依病人時區解讀）。

### GET `/api/v1/chemotherapy/plans/{id}`

Response `200`
```json
{
  "data": {
    "id": 21,
    "status": "active",
    "diagnosis": { "id": 5, "name_zh": "鼻咽癌", "stage": "III" },
    "regimen": { "id": 3, "name": "PF" },
    "attending_physician_name": "王醫師",
    "total_cycles": 6,
    "progress": { "completed_cycles": 2, "current_cycle_number": 3, "current_cycle_day": 9 },
    "cycles": [
      { "id": 101, "cycle_number": 1, "status": "completed", "actual_start_date": "2026-08-04", "dose_modification_pct": 100 },
      { "id": 102, "cycle_number": 2, "status": "completed", "actual_start_date": "2026-08-27", "delay_days": 2, "dose_modification_pct": 80 },
      { "id": 103, "cycle_number": 3, "status": "in_progress", "actual_start_date": "2026-09-15", "nadir_start_day": 7, "nadir_end_day": 14 }
    ]
  }
}
```

### POST `/api/v1/chemotherapy/cycles/{id}/delay`

Request
```json
{ "new_scheduled_date": "2026-10-09", "delay_reason": "ANC 過低", "reschedule_appointments": true }
```
Response `200`：更新後的 cycle（`status: "delayed"`）；`reschedule_appointments=true` 時，會同步改期這個 Cycle 關聯的行程。

### POST `/api/v1/chemotherapy/cycles/{id}/medications`

Header：`Idempotency-Key: 5d1f7c2a-…`

Request
```json
{
  "drug_id": 8,
  "medication_type": "chemo",
  "dose_value": 170.0,
  "dose_unit": "mg",
  "route": "IV",
  "administered_at": "2026-09-15T02:00:00Z",
  "infusion_duration_min": 120,
  "administration_status": "given",
  "reaction_notes": null
}
```

Response `201`
```json
{
  "data": {
    "id": 77,
    "cycle_id": 103,
    "cycle_day": 1,
    "drug": { "id": 8, "generic_name": "Cisplatin" },
    "dose_value": 170.0,
    "dose_unit": "mg",
    "administration_status": "given",
    "administered_by": { "id": "a8d1e4f2-6c3b-4a9e-b7d5-2f1e8c6a4b90", "display_name": "護理師 林" },
    "record_status": "final",
    "amends_id": null
  }
}
```

### POST `/api/v1/chemotherapy/medications/{id}/amend`

Request
```json
{
  "amend_reason": "劑量誤植",
  "dose_value": 160.0,
  "dose_unit": "mg",
  "administered_at": "2026-09-15T02:00:00Z",
  "administration_status": "given"
}
```
Response `201`：新紀錄（`amends_id: 77`）；紀錄 77 變成 `amended`。

### POST `/api/v1/chemotherapy/appointments`

Request
```json
{
  "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "cycle_id": 104,
  "appointment_type": "lab_draw",
  "title": "化療前抽血",
  "scheduled_at": "2026-10-06T00:30:00Z",
  "duration_min": 15,
  "location": "一樓檢驗科",
  "instructions": [
    { "instruction_type": "fasting", "due_at": "2026-10-05T21:00:00Z", "text": "05:00 起空腹" },
    { "instruction_type": "check_in", "due_at": "2026-10-06T00:20:00Z", "text": "08:20 報到" }
  ]
}
```

Response `201`
```json
{
  "data": {
    "id": 310,
    "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "cycle_id": 104,
    "appointment_type": "lab_draw",
    "title": "化療前抽血",
    "scheduled_at": "2026-10-06T00:30:00Z",
    "location": "一樓檢驗科",
    "status": "scheduled",
    "instructions": [
      { "id": 901, "instruction_type": "fasting", "due_at": "2026-10-05T21:00:00Z", "text": "05:00 起空腹", "is_highlighted": true },
      { "id": 902, "instruction_type": "check_in", "due_at": "2026-10-06T00:20:00Z", "text": "08:20 報到", "is_highlighted": true }
    ]
  }
}
```

### GET `/api/v1/chemotherapy/appointments`

Query：`?patient_id=me&date=2026-09-23`（`date` 依病人時區解讀）

Response `200`
```json
{
  "data": [
    {
      "id": 301,
      "appointment_type": "lab_draw",
      "title": "化療前抽血",
      "scheduled_at": "2026-09-23T00:30:00Z",
      "location": "一樓檢驗科",
      "status": "scheduled",
      "cycle_id": 103,
      "instructions": [
        { "instruction_type": "fasting", "due_at": "2026-09-22T21:00:00Z", "text": "05:00 起空腹" },
        { "instruction_type": "check_in", "due_at": "2026-09-23T00:20:00Z", "text": "08:20 報到" }
      ]
    }
  ],
  "meta": { "timezone": "Asia/Taipei" }
}
```

### POST `/api/v1/chemotherapy/appointments/{id}/reschedule`

Request
```json
{ "new_scheduled_at": "2026-10-09T00:30:00Z", "reason": "配合 Cycle 延後", "copy_instructions": true }
```
Response `201`：新行程（`rescheduled_from_id: 310`）；原行程變成 `rescheduled`。

---

## 6. Symptom API

### 6.1 定義層（Symptom Definition）

| Endpoint | Method | 角色 | 說明 | Tables | Phase |
|---|---|---|---|---|---|
| `/api/v1/symptoms/categories` | GET / POST | 讀：全部；寫：admin | | symptom_categories | 1 |
| `/api/v1/symptoms/definitions` | GET | 全部 | `?category=&active=true` | symptom_definitions, symptom_definition_options | 1 |
| `/api/v1/symptoms/definitions` | POST | admin | | symptom_definitions, symptom_definition_options | 1 |
| `/api/v1/symptoms/definitions/{id}` | GET / PATCH | 讀：全部；寫：admin | 已被引用時只能改文字欄位（否則 `422 DEFINITION_LOCKED`） | symptom_definitions | 1 |
| `/api/v1/symptoms/definitions/{id}/new-version` | POST | admin | 建立新版本（`supersedes_id`） | symptom_definitions | 1 |
| `/api/v1/symptoms/forms` | GET | 全部 | `?intended_for=patient` | symptom_forms | 1 |
| `/api/v1/symptoms/forms` | POST | admin | | symptom_forms, symptom_form_items | 1 |
| `/api/v1/symptoms/forms/{code}` | GET | 全部 | 前端依此動態產生表單 | symptom_forms, symptom_form_items, symptom_definitions, symptom_definition_options | 1 |
| `/api/v1/symptoms/forms/{code}` | PUT | admin | 修改題目組成（`version` +1） | symptom_forms, symptom_form_items | 1 |
| `/api/v1/symptoms/forms/{code}/schedules` | GET / PUT | 讀：全部；寫：admin | 開放時段 | symptom_form_schedules | 2 |
| `/api/v1/symptoms/forms/{code}/targets` | GET / PUT | admin | 依癌別 / 處方指派 | symptom_form_targets | 2 |

### 6.2 紀錄層（Symptom Record）

| Endpoint | Method | 角色 | 說明 | Tables | Phase |
|---|---|---|---|---|---|
| `/api/v1/symptoms/records` | GET | 有權者 | `?patient_id=&from=&to=&review_status=` | symptom_records, symptom_record_values | 1 |
| `/api/v1/symptoms/records` | POST | patient, nurse | 送出症狀回報 | symptom_records, symptom_record_values, symptom_record_value_options, alert_rules, notifications, idempotency_records, audit_logs | 1 |
| `/api/v1/symptoms/records/{id}` | GET | 有權者 | | 同上 | 1 |
| `/api/v1/symptoms/records/{id}/amend` | POST | 原填寫者, nurse | | 同上 | 1 |
| `/api/v1/symptoms/records/{id}/mark-error` | POST | 原填寫者, nurse | | symptom_records | 1 |
| `/api/v1/symptoms/records/{id}/review` | POST | nurse | 審閱 + CTCAE 分級 | symptom_records, symptom_record_values | 1 |
| `/api/v1/symptoms/trends` | GET | 有權者 | 趨勢圖 | symptom_records, symptom_record_values, symptom_definitions, chemotherapy_cycles | 1 |
| `/api/v1/symptoms/questionnaires` | GET | 有權者 | 各量表開放狀態 | symptom_forms, symptom_form_schedules, chemotherapy_plans, chemotherapy_cycles, symptom_records | 2 |

### GET `/api/v1/symptoms/forms/{code}`

Response `200`
```json
{
  "data": {
    "id": 1,
    "code": "daily_chemo_check",
    "name": "化療每日症狀自評",
    "intended_for": "patient",
    "availability": "always",
    "recall_period_hours": 24,
    "version": 2,
    "items": [
      {
        "display_order": 1,
        "is_required": true,
        "display_condition": null,
        "definition": {
          "id": 11, "code": "nausea", "name_zh": "噁心",
          "question_text": "過去 24 小時最嚴重的噁心程度？",
          "value_type": "scale", "min_value": 0, "max_value": 10, "step": 1,
          "min_label": "沒有", "max_label": "最嚴重", "higher_is_worse": true
        }
      },
      {
        "display_order": 2,
        "is_required": false,
        "display_condition": null,
        "definition": {
          "id": 14, "code": "pain_quality", "name_zh": "疼痛性質",
          "value_type": "multi_choice",
          "options": [
            { "id": 51, "value_code": "stabbing", "label_zh": "刺痛", "score": null },
            { "id": 52, "value_code": "burning", "label_zh": "灼熱感", "score": null }
          ]
        }
      },
      {
        "display_order": 9,
        "is_required": false,
        "display_condition": null,
        "definition": { "id": 30, "code": "other_symptoms", "name_zh": "其他症狀", "value_type": "text" }
      }
    ]
  }
}
```

### POST `/api/v1/symptoms/records`

Header：`Idempotency-Key: 9b2c4e1d-…`（病人端必填）

Request
```json
{
  "patient_id": "me",
  "form_code": "daily_chemo_check",
  "recorded_at": "2026-09-22T11:30:00Z",
  "source": "patient_app",
  "notes": "",
  "values": [
    { "definition_code": "nausea", "value_numeric": 7 },
    { "definition_code": "fatigue", "value_numeric": 6 },
    { "definition_code": "appetite", "option_code": "poor" },
    { "definition_code": "pain_quality", "option_codes": ["stabbing"] },
    { "definition_code": "fever_chills", "value_boolean": false },
    { "definition_code": "other_symptoms", "value_text": "口腔有點破" }
  ]
}
```

伺服器處理（同一個 transaction）：
1. 檢查 Idempotency（§1.7）
2. 依各定義的 `value_type`、`min/max`、選項驗證
3. 計算每個值的 `score`
4. 填入 `cycle_id`、`cycle_day` 和 `form_version`
5. 比對 `alert_rules`（`source_type=symptom`），符合時產生 `notifications`（考慮 cooldown）
6. 寫入 `audit_logs`

Response `201`
```json
{
  "data": {
    "id": 402,
    "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "form": { "code": "daily_chemo_check", "version": 2 },
    "recorded_at": "2026-09-22T11:30:00Z",
    "cycle_id": 103,
    "cycle_day": 8,
    "review_status": "submitted",
    "record_status": "final",
    "values": [
      { "definition_code": "nausea", "value_numeric": 7, "score": 7 },
      { "definition_code": "fatigue", "value_numeric": 6, "score": 6 },
      { "definition_code": "appetite", "option_code": "poor", "score": 3 },
      { "definition_code": "pain_quality", "option_codes": ["stabbing"], "score": null },
      { "definition_code": "fever_chills", "value_boolean": false, "score": 0 },
      { "definition_code": "other_symptoms", "value_text": "口腔有點破", "score": null }
    ],
    "triggered_alerts": [
      { "alert_rule_code": "severe_nausea", "severity": "warning", "message": "噁心程度偏高，護理師會與您聯繫；可先依指示服用止吐藥" }
    ]
  }
}
```

> `triggered_alerts` 是給病人看的即時回饋；實際通知寫在 `notifications`（病人 1 筆 + 每位負責護理師 1 筆）。

### POST `/api/v1/symptoms/records/{id}/review`

> **實作說明（隱私）**：`GET /api/v1/symptoms/records/{pid|me}` 對病人只回傳「已審閱」與時間，不含護理師的處置說明、風險判斷、審閱者或處理者姓名；護理師代為輸入的紀錄，`reported_by` 對病人也不顯示。

Request
```json
{
  "grades": [
    { "definition_code": "nausea", "ctcae_grade": 2 },
    { "definition_code": "fatigue", "ctcae_grade": 2 }
  ],
  "note": "已電話衛教止吐藥使用"
}
```
Response `200`：`review_status: "reviewed"`，並附上 `reviewed_by`、`reviewed_at`。

### GET `/api/v1/symptoms/trends`

Query：`?patient_id=…&definition_codes=nausea,fatigue&from=2026-09-01T00:00:00Z&to=2026-09-23T00:00:00Z&bucket=day&agg=max`

Response `200`
```json
{
  "data": {
    "bucket": "day",
    "agg": "max",
    "series": [
      {
        "key": "nausea",
        "label": "噁心",
        "higher_is_worse": true,
        "points": [
          { "date": "2026-09-15", "cycle_number": 3, "cycle_day": 1, "value": 2 },
          { "date": "2026-09-16", "cycle_number": 3, "cycle_day": 2, "value": 6 },
          { "date": "2026-09-17", "cycle_number": 3, "cycle_day": 3, "value": null }
        ]
      }
    ],
    "cycle_markers": [
      { "cycle_number": 3, "start_date": "2026-09-15", "nadir_start_date": "2026-09-21", "nadir_end_date": "2026-09-28" }
    ]
  },
  "meta": { "timezone": "Asia/Taipei" }
}
```

> 沒有資料的日子回 `null`，不補 0。`bucket` 可為 `raw` / `day` / `cycle_day`。`series[].key` / `label` 與 Vital Signs trends 相同，前端圖表元件可以共用。

### GET `/api/v1/symptoms/questionnaires`（Phase 2）

Query：`?patient_id=me`

Response `200`
```json
{
  "data": [
    {
      "form_code": "pro_ctcae_neuropathy",
      "name": "周邊神經病變評估問卷",
      "status": "not_open",
      "windows": [
        { "label": "療程第一週", "start_date": "2026-08-04", "end_date": "2026-08-10", "state": "submitted", "record_id": 210 },
        { "label": "療程第三週", "start_date": "2026-09-28", "end_date": "2026-10-04", "state": "upcoming" },
        { "label": "療程結束第一週", "start_date": null, "end_date": null, "state": "upcoming" }
      ],
      "next_open_at": "2026-09-28"
    }
  ],
  "meta": { "timezone": "Asia/Taipei" }
}
```

> `status`：`not_open` / `open` / `submitted` / `overdue` / `closed`。Phase 1 的量表都是 `availability=always`，不需要這支 API。

---

> **實作說明（每日症狀與自我照護回報，2026-10-06）**：「每日症狀與自我照護回報」（form `rt_daily_report`，2026-10-06）：沿用既有 symptom 架構，兩個區段為兩個 `symptom_categories`（`rt_symptom_24h`「過去 24 小時症狀自主管理」、`daily_self_care`「我的每日自評」），9 個新 `symptom_definitions`（4 題 0–10 `scale`、5 題 `single_choice`，「有／沒有」也是單選以維持順序與文字；疲倦為新的 `rt_fatigue`，不沿用 `fatigue`，因此既有 `severe_fatigue` 等 alert rule 都不套用到這份表單），全部必填。API 皆為既有 endpoint：`GET /symptoms/forms/rt_daily_report`（每題 `definition` 新增 `category: {code, name_zh}`，所有表單皆有，additive）；`POST /symptoms/records`（`form_code: rt_daily_report`，0–10 用 `value_numeric`、單選用 `option_code`）——**只有這份表單**每位病人每個在地日（病人時區）只能一筆：第二筆回 `409 ALREADY_REPORTED_TODAY`「今天已回報」（`details[0].record_id` 為當天那一筆；不分病人或護理師代填；Idempotency replay 不受影響；更正（amend）不算第二筆，標示錯誤後可再回報）；其他表單（例如 `daily_chemo_check`）不受影響。`GET /symptoms/records/{pid}` 新增選填 `form_code` 篩選。回答 payload 的單選題新增 `option_label`。護理端「需要注意」只沿用既有規則：0–10 分數 ≥ 7（Risk Engine 的 symptom ≥ 7 規則與審閱卡片標示），單選與自我照護答案只顯示、不判定。待審摘要與審閱建立的護理評估摘要中，單選題顯示「題目：選項」。病人選「沒有」時只在畫面提醒，不阻擋送出。

## 7. Vital Signs API

> **實作說明**：已實作 `POST /api/v1/vital-signs`、`GET /api/v1/vital-signs/reference-ranges`，以及護理師用的 `GET /api/v1/vital-signs/abnormal`（負責病人中超出參考範圍的量測，`?hours=1–168`，含觸發的警示與處理狀態）。`latest`、`trends` 尚未實作。

| Endpoint | Method | 角色 | 說明 | Tables |
|---|---|---|---|---|
| `/api/v1/vital-signs` | GET | 有權者 | `?patient_id=&from=&to=` | vital_signs |
| `/api/v1/vital-signs` | POST | patient, nurse | 新增 | vital_signs, patient_care_alerts, alert_rules, notifications, idempotency_records, audit_logs |
| `/api/v1/vital-signs/latest` | GET | 有權者 | 各項目最新值 | vital_signs |
| `/api/v1/vital-signs/trends` | GET | 有權者 | 趨勢圖 | vital_signs, chemotherapy_cycles |
| `/api/v1/vital-signs/reference-ranges` | GET | 全部 | 異常門檻（前端標色用） | Phase 1–2：設定檔；Phase 3：institution_settings |
| `/api/v1/vital-signs/{id}` | GET | 有權者 | | vital_signs |
| `/api/v1/vital-signs/{id}/amend` | POST | 原填寫者, nurse | | vital_signs |
| `/api/v1/vital-signs/{id}/mark-error` | POST | 原填寫者, nurse | | vital_signs |

### POST `/api/v1/vital-signs`

Header：`Idempotency-Key: 1e7a9f30-…`（病人端必填）

Request
```json
{
  "patient_id": "me",
  "measured_at": "2026-09-22T12:00:00Z",
  "temperature_c": 38.2,
  "temperature_site": "ear",
  "heart_rate_bpm": 104,
  "systolic_bp_mmhg": 112,
  "diastolic_bp_mmhg": 70,
  "bp_measure_site": "left_arm",
  "spo2_pct": 97,
  "weight_kg": 64.5,
  "source": "patient_app"
}
```

驗證：
- 至少要有一個量測值；數值要在合理範圍內（例如體溫 30–45°C）。
- `bp_measure_site` 對到病人有效的肢體限制（`patient_care_alerts.body_site`）時**仍會接受**，但回應會附上 `warning` flag，並寫入稽核紀錄。不擋下是因為資料本身是真實發生的量測。

Response `201`
```json
{
  "data": {
    "id": 881,
    "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "measured_at": "2026-09-22T12:00:00Z",
    "cycle_id": 103,
    "cycle_day": 8,
    "temperature_c": 38.2,
    "heart_rate_bpm": 104,
    "systolic_bp_mmhg": 112,
    "diastolic_bp_mmhg": 70,
    "bp_measure_site": "left_arm",
    "spo2_pct": 97,
    "weight_kg": 64.5,
    "flags": [
      { "field": "temperature_c", "level": "critical", "message": "體溫 ≥ 38°C，且目前處於骨髓抑制期，請立即聯絡醫療團隊" },
      { "field": "heart_rate_bpm", "level": "warning", "message": "心跳偏快" }
    ],
    "triggered_alerts": [
      { "alert_rule_code": "suspected_febrile_neutropenia", "severity": "critical", "notification_event_key": "vital_signs:881:rule:4" }
    ],
    "record_status": "final"
  }
}
```

> `flags`：依參考範圍即時算出的標色資訊（不存 DB）。`triggered_alerts`：依 `alert_rules` 產生、已寫入 `notifications` 的通知。

### GET `/api/v1/vital-signs/latest`

Response `200`
```json
{
  "data": {
    "temperature_c": { "value": 38.2, "measured_at": "2026-09-22T12:00:00Z", "flag": "critical" },
    "heart_rate_bpm": { "value": 104, "measured_at": "2026-09-22T12:00:00Z", "flag": "warning" },
    "blood_pressure": { "systolic": 112, "diastolic": 70, "measure_site": "left_arm", "measured_at": "2026-09-22T12:00:00Z", "flag": null },
    "spo2_pct": { "value": 97, "measured_at": "2026-09-22T12:00:00Z", "flag": null },
    "weight_kg": { "value": 64.5, "measured_at": "2026-09-22T12:00:00Z", "change_pct_7d": -3.1, "flag": "warning" }
  }
}
```

### GET `/api/v1/vital-signs/trends`

Query：`?patient_id=…&fields=temperature_c,heart_rate_bpm&from=…&to=…&bucket=raw`
Response：結構與 `/api/v1/symptoms/trends` 相同（`series[].key` = 欄位名稱）。

### GET `/api/v1/vital-signs/reference-ranges`

```json
{
  "data": {
    "temperature_c": { "warning_high": 37.5, "critical_high": 38.0 },
    "heart_rate_bpm": { "warning_low": 50, "warning_high": 100, "critical_high": 130 },
    "systolic_bp_mmhg": { "warning_low": 90, "warning_high": 160 },
    "spo2_pct": { "warning_low": 94, "critical_low": 90 },
    "weight_change_pct_7d": { "warning_low": -3.0 }
  },
  "meta": { "source": "config_file" }
}
```

---

## 7A. Lab Result API（已實作）

| Endpoint | Method | 角色 | 說明 | Tables |
|---|---|---|---|---|
| `/api/v1/labs/test-types` | GET | 已登入 | 檢驗項目、單位、參考範圍、危急值 | lab_test_types |
| `/api/v1/labs/results` | POST | 被指派的 nurse | 登錄同一次採檢的一組結果 `{patient_id, collected_at, resulted_at?, results: [{test_code, value}]}`；`Idempotency-Key` 可選；比對 `alert_rules`（`source_type=lab`） | lab_results, alert_rules, notifications, idempotency_records, audit_logs |
| `/api/v1/labs/results/{pid}` | GET | nurse（被指派）、admin | 完整資料：最新值（含參考範圍快照、判讀、警示）、趨勢、全部紀錄；`?days=1–365&test=` | lab_results, lab_test_types, notifications |
| `/api/v1/labs/summary/{pid|me}` | GET | 病人本人、照護人員 | 簡化結果：白話名稱、正常 / 偏低 / 過低與注意事項，不含參考範圍數字 | lab_results, lab_test_types |

支援 WBC、ANC、HGB、PLT；判讀 `N` / `L` / `H` / `LL` / `HH`。

## 8. Dashboard API

> **實作說明**：目前以 `GET /api/v1/dashboard/patient/{pid|me}` 一次回傳所有 widget 資料（`data.widgets` 的 key 為 `widget_code`：`patient-summary`、`risk-summary`、`today-schedule`、`treatment-progress`、`symptom-quick-report`、`latest-vitals`、`lab-summary`、`symptom-trend`、`notifications`；照護人員另有 `nurse-view`，病人不會收到）。版面 API `GET /api/v1/dashboard/layout` 已實作（Sprint 0，見下方說明）。下方各 widget 的個別 data endpoint 除 `caseload` 外尚未實作。

### 8.1 Layout JSON 契約（Phase 1 起固定）

**核心原則**：Vue 只認得這份 JSON。Phase 1 由後端程式產生，Phase 2 改由 `dashboard_*` 資料表產生；**契約不變，前端渲染器不用改**。

```json
{
  "data": {
    "schema_version": 1,
    "layout_id": null,
    "source": "code_default",
    "scope": "role",
    "version": 1,
    "grid": { "columns": 1, "row_height": 80, "gap": 12 },
    "pinned": [
      {
        "instance_key": "summary",
        "widget_code": "patient-summary",
        "data_endpoint": "/api/v1/dashboard/widgets/patient-summary/data",
        "config": {}
      }
    ],
    "items": [
      {
        "instance_key": "today",
        "widget_code": "today-schedule",
        "title": "今日行程",
        "data_endpoint": "/api/v1/dashboard/widgets/today-schedule/data",
        "position": { "x": 0, "y": 0, "w": 1, "h": 3 },
        "config": { "show_instructions": true, "contact_key": "leave" },
        "refresh_interval_sec": 60,
        "collapsible": true,
        "removable": false
      },
      {
        "instance_key": "progress",
        "widget_code": "treatment-progress",
        "title": "化療進度",
        "data_endpoint": "/api/v1/dashboard/widgets/treatment-progress/data",
        "position": { "x": 0, "y": 3, "w": 1, "h": 2 },
        "config": { "show_physician": true },
        "refresh_interval_sec": 0,
        "collapsible": true,
        "removable": false
      }
    ],
    "permissions": { "can_edit": false, "can_reorder": false, "can_remove": false, "can_collapse": true }
  }
}
```

| 欄位 | 說明 |
|---|---|
| `schema_version` | 契約版本；前端遇到不支援的版本就顯示降級畫面 |
| `source` | `code_default`（Phase 1）/ `system` / `role` / `regimen` / `user`（Phase 2） |
| `pinned` | 固定區塊（病人摘要），不能收合、移動或移除 |
| `items[].widget_code` | 前端 **Component Registry** 的 key（`widget_code → Vue component`） |
| `items[].data_endpoint` | 元件取資料的 API；前端的通用 `WidgetFrame` 依此呼叫，並處理 loading / empty / error |
| `items[].position` | Grid 座標；Phase 1 只使用 `y`（單欄順序），桌面版才用 `x/w` |
| `items[].config` | 傳給元件與 data endpoint 的參數 |
| `refresh_interval_sec` | 0 = 不自動更新 |
| `permissions` | Phase 1 固定為 `can_edit=false`（沒有拖拉） |

**前端處理規則**
- `widget_code` 不在 Registry 中時，顯示「此元件需要更新版本」的佔位卡片，不中斷整頁渲染。
- 每個 widget 各自取資料、各自處理錯誤，一個失敗不影響其他元件。
- 呼叫 `data_endpoint` 時，會加上 `?patient_id=`（護理端單一病人頁）與 `instance_key`。

### 8.2 Widget Registry（Phase 1）

| widget_code | 角色 | data_endpoint | 主要 Tables |
|---|---|---|---|
| `patient-summary` | patient, nurse（Banner） | `/api/v1/dashboard/widgets/patient-summary/data` | patient_profiles, patient_care_alerts |
| `today-schedule` | patient, nurse（單一病人） | `/api/v1/dashboard/widgets/today-schedule/data` | appointments, appointment_instructions |
| `treatment-progress` | patient, nurse | `/api/v1/dashboard/widgets/treatment-progress/data` | chemotherapy_plans, chemotherapy_cycles, cancer_diagnoses, cancer_types |
| `latest-vitals` | patient, nurse | `/api/v1/dashboard/widgets/latest-vitals/data` | vital_signs |
| `symptom-quick-report` | patient | `/api/v1/dashboard/widgets/symptom-quick-report/data` | symptom_forms, symptom_records |
| `my-notifications` | patient | `/api/v1/dashboard/widgets/my-notifications/data` | notifications |
| `caseload` | nurse | `/api/v1/dashboard/widgets/caseload/data` | nurse_patient_assignments, patient_profiles, vital_signs, symptom_records, notifications |
| `unacknowledged-alerts` | nurse | `/api/v1/dashboard/widgets/unacknowledged-alerts/data` | notifications |
| `pending-symptom-reviews` | nurse | `/api/v1/dashboard/widgets/pending-symptom-reviews/data` | symptom_records |
| `pending-assessment-signoff` | nurse | `/api/v1/dashboard/widgets/pending-assessment-signoff/data` | nursing_assessments |
| `today-appointments` | nurse | `/api/v1/dashboard/widgets/today-appointments/data` | appointments（被指派病人） |
| `user-overview` | admin | `/api/v1/dashboard/widgets/user-overview/data` | users, roles |

Phase 2 新增：`care-topics`、`questionnaire-status`、`education-shortcuts`（依賴 Education 與問卷排程）、`system-usage`。

### 8.3 Phase 1 程式內預設版面

| 角色 | grid.columns | pinned | items（依序） |
|---|---|---|---|
| patient | 1 | patient-summary | today-schedule → treatment-progress → symptom-quick-report → latest-vitals → my-notifications |
| nurse（總覽） | 12 | — | unacknowledged-alerts(w6) + today-appointments(w6) → caseload(w12) → pending-symptom-reviews(w6) + pending-assessment-signoff(w6) |
| nurse（單一病人） | 12 | patient-summary（Banner） | treatment-progress(w6) + latest-vitals(w6) → today-schedule(w12) |
| admin | 12 | — | user-overview(w12) |

### 8.4 Endpoint 清單

| Endpoint | Method | 角色 | 說明 | Tables | Phase |
|---|---|---|---|---|---|
| `/api/v1/dashboard/layout` | GET | 已登入 | `?context=overview|patient&patient_id=`；回傳 §8.1 契約 | Phase 1：程式定義；Phase 2：dashboard_layouts, dashboard_layout_items, dashboard_widgets | **1** |
| `/api/v1/dashboard/widgets/{widget_code}/data` | GET | 依 widget | 各元件資料（見 §8.2） | 依 widget | 1 |
| `/api/v1/dashboard/layout` | PUT | nurse, admin | 儲存個人版面（樂觀鎖） | dashboard_layouts, dashboard_layout_items | 2 |
| `/api/v1/dashboard/layout` | DELETE | nurse, admin | 重設為預設 | dashboard_layouts | 2 |
| `/api/v1/dashboard/widgets` | GET | 已登入 | 可用元件目錄 | dashboard_widgets, dashboard_widget_roles | 2 |
| `/api/v1/dashboard/layouts` | GET / POST | admin | 角色 / 處方預設版面 | dashboard_layouts, dashboard_layout_items | 2 |
| `/api/v1/dashboard/layouts/{id}` | PUT / DELETE | admin | | 同上 | 2 |

### GET `/api/v1/dashboard/layout`

> **實作說明（Sprint 0）**：已實作，需登入。Phase 1 只提供實際使用中的版面——**病人首頁**（程式定義於 `backend/app/modules/dashboard/layouts.py`，`source=code_default`）。`context=overview|patient`（預設 overview）；病人帶 `context=patient&patient_id=` 時只能是自己（`me` 或自己的 public id），否則 `404`。護理端與管理端畫面在 Phase 1 為固定版面（§8.3 中的護理 / 管理版面所需的 widget 尚未實作），因此回 `404 NOT_FOUND`。`items` 目前沒有 `data_endpoint` / `title` / `refresh_interval_sec`，資料由 `GET /dashboard/patient/{pid|me}` 一次取得。`meta` 為 `{ "role": "patient", "context": "overview" }`。前端優先使用此 API，請求失敗時才使用內建副本（`frontend/src/config/defaults.js`，contract test 確認兩者一致）。

Query：`?context=patient&patient_id=7c9e…`（護理師看單一病人）
Response：見 §8.1。護理端範例的 `grid.columns=12`，`pinned` 為 Banner 版的 `patient-summary`。

### GET `/api/v1/dashboard/widgets/patient-summary/data`

```json
{
  "data": {
    "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "patient_code": "CCP-000123",
    "display_name": "測試病人 A",
    "age": 31,
    "gender": "male",
    "care_alerts": [
      { "alert_type": "allergy", "description": "Penicillin 過敏", "severity": "high" },
      { "alert_type": "limb_restriction", "body_site": "right_arm", "description": "右手禁止注射及量血壓", "severity": "high" }
    ]
  }
}
```

### GET `/api/v1/dashboard/widgets/today-schedule/data`

```json
{
  "data": {
    "date": "2026-09-23",
    "server_time": "2026-09-23T07:23:38Z",
    "appointments": [
      {
        "id": 302,
        "appointment_type": "chemo_infusion",
        "title": "化療注射（Cycle 4）",
        "scheduled_at": "2026-09-23T01:00:00Z",
        "location": "日間化療室",
        "status": "scheduled"
      }
    ],
    "highlight_instructions": [
      { "appointment_id": 301, "instruction_type": "fasting", "due_at": "2026-09-22T21:00:00Z", "text": "05:00 空腹" },
      { "appointment_id": 301, "instruction_type": "check_in", "due_at": "2026-09-23T00:30:00Z", "text": "08:30 報到" }
    ],
    "quick_contact": { "key": "leave", "label": "請假專線", "phone": "07-000-0000" }
  },
  "meta": { "timezone": "Asia/Taipei" }
}
```

> `quick_contact` 在 Phase 1–2 來自設定檔、Phase 3 來自 `institution_settings`。`server_time` 供 LiveClock 校正。

### GET `/api/v1/dashboard/widgets/treatment-progress/data`

```json
{
  "data": {
    "plan_id": 21,
    "diagnosis_name": "鼻咽癌",
    "regimen_name": "PF",
    "attending_physician_name": "王醫師",
    "completed_cycles": 2,
    "total_cycles": 6,
    "current_cycle": { "cycle_number": 3, "cycle_day": 9, "in_nadir": true, "nadir_end_date": "2026-09-28" },
    "next_cycle_date": "2026-10-06",
    "disclaimer_key": "treatment_progress_disclaimer"
  }
}
```

### GET `/api/v1/dashboard/widgets/caseload/data`

```json
{
  "data": [
    {
      "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      "patient_code": "CCP-000123",
      "display_name": "測試病人 A",
      "risk_level": "high",
      "risk_reasons": ["骨髓抑制期發燒 38.2°C", "噁心 7/10"],
      "cycle": { "cycle_number": 3, "cycle_day": 9 },
      "last_report_at": "2026-09-22T12:00:00Z",
      "hours_since_last_report": 19,
      "pending_review_count": 2,
      "unacknowledged_alert_count": 1,
      "care_alert_types": ["allergy", "limb_restriction"]
    }
  ],
  "meta": { "total": 18, "high": 2, "medium": 5, "low": 11 }
}
```

### PUT `/api/v1/dashboard/layout`（Phase 2）

Request
```json
{
  "version": 3,
  "items": [
    { "instance_key": "k1", "widget_code": "caseload", "position": { "x": 0, "y": 0, "w": 8, "h": 6 }, "config": { "sort": "risk" } },
    { "instance_key": "k2", "widget_code": "pending-symptom-reviews", "position": { "x": 8, "y": 0, "w": 4, "h": 6 }, "config": {} }
  ]
}
```
Response `200`：新版面（`version: 4`，格式同 §8.1）。版本不符時回 `409 VERSION_CONFLICT`。

---

## 9. Nursing Assessment API

| Endpoint | Method | 角色 | 說明 | Tables |
|---|---|---|---|---|
| `/api/v1/nursing-assessments` | GET | nurse | `?patient_id=&from=&to=&sign_status=` | nursing_assessments |
| `/api/v1/nursing-assessments` | POST | nurse | 建立草稿，可同時附上生命徵象與症狀 | nursing_assessments, nursing_assessment_items, vital_signs, symptom_records, symptom_record_values, idempotency_records |
| `/api/v1/nursing-assessments/{id}` | GET | nurse | | 同上 |
| `/api/v1/nursing-assessments/{id}` | PATCH | 建立者 | 只有草稿可以修改（否則 `422 RECORD_LOCKED`） | nursing_assessments, nursing_assessment_items |
| `/api/v1/nursing-assessments/{id}/sign` | POST | 建立者 | 簽署 | nursing_assessments, audit_logs |
| `/api/v1/nursing-assessments/{id}/amend` | POST | nurse | 已簽署的評估改走這裡 | nursing_assessments |
| `/api/v1/nursing-assessments/{id}/items/{item_id}` | PATCH | nurse | 更新問題 / 措施狀態 | nursing_assessment_items |

> **實作說明（Sprint 4，Implemented）**
> - **權限**：讀取限照護人員（目前指派的護理師、admin；其他護理師 404），病人 `403`。寫入限目前指派的護理師；草稿只有撰寫者能修改與簽署（其他人 `403`），已簽署的評估 `PATCH` 回 `422 RECORD_LOCKED`。
> - **修正（amend）**：只能對已簽署、仍有效的評估；需 `amend_reason`，只送要修改的欄位，其餘（含項目）複製。產生一份新的**草稿版本**（`amends_id` → 原評估，撰寫者為修正者）；簽署後原評估變成 `amended`（保留，可在 `GET /{id}/versions` 看到整個版本鏈），同一份評估同時只能有一個未簽署的修正。修正保留原評估時間。
> - **列表**：`?patient_id=`（單一病人，預設只列有效版本；`include_history=true` 含被修正的版本）；不帶 `patient_id` 時列出目前護理師自己撰寫、且病人仍由自己負責的評估（例如待簽署草稿）。`sign_status`、`from` / `to` 篩選。
> - **簽署**：至少要有 S / O / A / P 其中一項。`items` 的狀態（`open` / `in_progress` / `resolved` / `done`）簽署後仍可由病人的護理師更新（`resolved` / `done` 記錄 `resolved_at`）。
> - **整合**：Timeline（照護人員看到 SOAP 與風險；病人只看到已簽署評估的一句「護理師已完成評估」）、Dashboard nurse-view（待簽署、最新評估）與 Risk Engine（最新有效評估的 `risk_level` medium / high 會提高風險）沿用既有邏輯。建立時依評估日的進行中 Cycle 設定 `cycle_id` / `cycle_day`。
> - Pending：建立評估時一併附上生命徵象 / 症狀紀錄（`vital_signs`、`symptom_record`）尚未支援；請分別登錄。
> - 稽核：`CREATE`、`UPDATE`、`SIGN`（修正簽署時含 `supersedes_id`）、`AMEND`（原因與新版本 id）、項目 `UPDATE`、讀取 `VIEW`。

### POST `/api/v1/nursing-assessments`

Request
```json
{
  "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "appointment_id": 302,
  "assessment_type": "pre_chemo",
  "assessed_at": "2026-10-06T00:40:00Z",
  "ecog_status": 1,
  "overall_condition": "stable",
  "risk_level": "medium",
  "chemo_readiness": "ready",
  "subjective": "食慾稍差，手指偶有麻感",
  "objective": "口腔黏膜完整，右手有淋巴水腫",
  "assessment": "化療前評估可施打",
  "plan": "持續監測神經病變，衛教保暖",
  "vital_signs": { "temperature_c": 36.7, "heart_rate_bpm": 82, "systolic_bp_mmhg": 118, "diastolic_bp_mmhg": 74, "bp_measure_site": "left_arm", "weight_kg": 63.8 },
  "symptom_record": {
    "form_code": "nurse_pre_chemo_check",
    "values": [ { "definition_code": "neuropathy", "value_numeric": 2 } ]
  },
  "items": [
    { "item_type": "problem", "code": "peripheral_neuropathy_risk", "description": "周邊神經病變風險", "priority": "medium" },
    { "item_type": "education", "code": "cold_avoidance", "description": "衛教避免接觸冰冷物品" }
  ]
}
```

Response `201`
```json
{
  "data": {
    "id": 55,
    "sign_status": "draft",
    "appointment_id": 302,
    "cycle_id": 104,
    "cycle_day": 1,
    "linked_vital_sign_id": 902,
    "linked_symptom_record_id": 455,
    "items": [
      { "id": 301, "item_type": "problem", "code": "peripheral_neuropathy_risk", "item_status": "open" },
      { "id": 302, "item_type": "education", "code": "cold_avoidance", "item_status": "open" }
    ]
  }
}
```

### POST `/api/v1/nursing-assessments/{id}/sign`

Request：`{}`
Response `200`：`sign_status: "signed"`、`signed_at`。

---

## 10. Notification API（Phase 1）

| Endpoint | Method | 角色 | 說明 | Tables |
|---|---|---|---|---|
| `/api/v1/notifications` | GET | 已登入 | 病人：自己的通知；護理師：負責病人的警示（每事件一筆）；管理者：全部。`?status=&priority=&patient_id=&type=&is_read=`（只回 `scheduled_for <= now` 的通知） | notifications |
| `/api/v1/notifications/{id}` | GET | 已登入 | 通知內容：照護人員含病人資訊、觸發原因、原始資料、建議處理、處理歷程；病人只有提醒與狀態 | notifications, alert_rules, symptom_records / vital_signs / lab_results |
| `/api/v1/notifications/unread-count` | GET（已實作） | 已登入 | 未讀數：自己的、目前可見（排除未到時間的排程通知）`{unread}` | notifications |
| `/api/v1/notifications/{id}/read` | PATCH（已實作） | 收件者 | 已讀 | notifications |
| `/api/v1/notifications/read-all` | POST（已實作） | 已登入 | 全部已讀：只影響自己的、目前可見的通知，回 `{updated, unread: 0}`；寫入 AuditLog | notifications |
| `/api/v1/notifications/{id}/acknowledge` | POST | nurse, admin | 接手：`new → acknowledged`（同 `event_key` 的通知一起更新） | notifications, audit_logs |
| `/api/v1/notifications/{id}/start` | POST | nurse, admin | 開始處理：`acknowledged → in_progress` | notifications, audit_logs |
| `/api/v1/notifications/{id}/resolve` | POST | nurse, admin | 完成：`in_progress → resolved`，`resolution_note` 必填 | notifications, audit_logs |
| `/api/v1/notifications/{id}/resolve` | PATCH | nurse | 快速處理（既有流程）：從任何未完成狀態直接結案 | notifications, audit_logs |
| `/api/v1/notifications` | POST | nurse, admin | 手動發送提醒（可設 `scheduled_for`） | notifications |
| `/api/v1/notifications/alert-rules` | GET / POST | admin | 風險規則 | alert_rules, symptom_definitions |
| `/api/v1/notifications/alert-rules/{id}` | GET / PATCH | admin | 修改 / 停用 | alert_rules |
| `/api/v1/notifications/alert-rules/{id}/test` | POST | admin | 用假資料試跑規則，不會真的發送 | alert_rules |

> 即時性：Phase 1 前端每 60 秒輪詢 `unread-count`；有需要再加 SSE `GET /api/v1/notifications/stream`。

### GET `/api/v1/notifications`

Response `200`（護理師）
```json
{
  "data": [
    {
      "id": 9001,
      "event_key": "vital_signs:881:rule:4",
      "type": "risk_alert",
      "severity": "critical",
      "title": "疑似嗜中性白血球低下發燒",
      "message": "測試病人 A（CCP-000123）體溫 38.2°C，目前為 Cycle 3 Day 8（骨髓抑制期）",
      "patient": { "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7", "patient_code": "CCP-000123", "display_name": "測試病人 A" },
      "alert_rule": { "id": 4, "code": "suspected_febrile_neutropenia" },
      "source": { "table": "vital_signs", "id": 881 },
      "is_read": false,
      "acknowledged": null,
      "created_at": "2026-09-22T12:00:03Z"
    }
  ],
  "meta": { "page": 1, "per_page": 20, "total": 4, "unread": 3 }
}
```

> **Notification Workflow sprint（已實作）**：處理流程為 `new → acknowledged → in_progress → resolved`，順序不符回 `409 INVALID_TRANSITION`；護理師只能處理目前負責病人的通知（其他回 `404`），病人不能改變狀態（`403`）。病人收到的內容不含 `resolution_note` 與處理者姓名。每筆通知另含 `status`、`status_text`、`handling`（`acknowledged` / `started` / `resolved` 的時間與操作者）、`is_mine`；列表的 `meta.counts` 為各狀態事件數。下方 `acknowledge` 範例為原始設計；實作的 `acknowledge` 不需要 body，處理說明改在 `resolve` 提供。

### POST `/api/v1/notifications/{id}/acknowledge`

Request
```json
{ "resolution_note": "已電話聯繫病人，建議立即至急診就醫", "action_taken": "advise_er" }
```

Response `200`
```json
{
  "data": {
    "id": 9001,
    "acknowledged": {
      "by": { "id": "a8d1e4f2-6c3b-4a9e-b7d5-2f1e8c6a4b90", "display_name": "護理師 林" },
      "at": "2026-09-22T12:06:40Z",
      "resolution_note": "已電話聯繫病人，建議立即至急診就醫"
    },
    "related_notifications_updated": 2
  }
}
```

### POST `/api/v1/notifications`（手動提醒）

Request
```json
{
  "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "type": "reminder",
  "severity": "info",
  "title": "明日化療提醒",
  "message": "明天 08:30 報到，05:00 起請空腹",
  "scheduled_for": "2026-10-05T10:00:00Z"
}
```
Response `201`：回傳通知物件（收件者為病人帳號）。

> **實作說明（Sprint 8）**：`POST /notifications`（nurse：目前指派的病人，否則 `404`；admin：全部），Body `{patient_id, title (≤200), message (≤2000), severity?: info|warning（critical 保留給風險警示）, scheduled_for?}`；`type` 只能是 `reminder`；`scheduled_for` 需為含時區的 ISO 8601，1 分鐘後到 1 年內（省略 = 立即送出）；病人沒有登入帳號回 `422 NO_PATIENT_ACCOUNT`；可帶 `Idempotency-Key`（重送同一內容回同一筆，不同內容 `422`）。一筆通知、收件者為病人帳號、獨立 `event_key`（`reminder:<uuid>`），`status = new`。**與風險警示相同的處理流程**：`POST /notifications/{id}/acknowledge|start|resolve`（`new → acknowledged → in_progress → resolved`，順序不符 `409`；病人 `403`）；快速結案 `PATCH /notifications/{id}/resolve` 仍只限風險警示。員工清單：`GET /notifications?type=reminder&patient_id=…`（`status` 篩選與 `meta.counts` 依 `type` 計算；未帶 `type` 時仍只列風險警示，行為不變）。**可見性**：`scheduled_for` 未到之前，病人的清單、詳情、未讀數、全部已讀都看不到（`404`）；員工以 `GET /notifications/scheduled?patient_id=`（nurse / admin；病人 `403`）查看尚未送出的提醒（`id, event_key, type, origin, severity, title, message, scheduled_for, created_at, patient`），到時間後移到一般清單。每筆通知新增 `origin`（**衍生欄位，無 schema 變更**）：`alert_rule`（風險規則產生）/ `scheduled`（指定時間送出，或由紀錄產生，例如行程提醒）/ `manual`（立即送出）；需求中的 `source = manual / scheduled / alert_rule` 以 `origin` 表示，因為 `source` 已是觸發紀錄 `{table, id}`。另新增 `scheduled_for`。員工看到提醒的 `status` / `status_text` / `handling`；病人端的提醒仍不含處理流程、處理者與內部說明。稽核：`CREATE notifications`（`type, origin, event_key, severity, title, scheduled_for`）；處理步驟沿用 `ACKNOWLEDGE` / `UPDATE notifications`。照護時間軸不變（仍只含風險警示，7 種 event type 不變）。

> **實作說明（Email 通知管道，2026-10-02）**：`POST /notifications` 的 request 不變。流程：同一個 transaction 建立通知（`status = new`）、規劃一筆 `notification_deliveries`（`pending`，或 `skipped` + `skip_reason`）與稽核並 commit；**commit 之後**才同步呼叫 EmailService 寄出（timeout `EMAIL_TIMEOUT_SECONDS`，預設 10 秒），結果另外 commit 為 `sent` / `failed`（`error_code`：`TIMEOUT` / `PROVIDER_REJECTED` / `PROVIDER_ERROR`）。**Email 失敗、逾時或例外都不會讓請求失敗（仍為 `201`），也不會 rollback 或改變通知與其處理流程**。寄送條件：病人的通知 Email 已驗證 **且** 開啟 Email 通知；否則 `skipped`：`no_email` / `not_verified` / `disabled`；排程提醒（`scheduled_for` 在未來）一律 `skipped / scheduled`（沒有背景排程，不寄）；沒有 Email 服務 `skipped / not_configured`。風險警示不寄 Email。Idempotency replay 不會重寄。Email 內容只有摘要：系統名稱、「您有一則來自護理團隊的新通知」、發送時間、請登入 App / 網頁查看、安全提醒——**不含通知標題與內容**、病人代碼、任何 id 或 token。員工的通知 payload（列表、詳情、`/scheduled`、建立回應）新增 `email_delivery: {channel, status, skip_reason, error_code, recipient_masked, attempted_at, completed_at}`（沒有 Email 管道的通知為 `null`）；病人的 payload 不含此欄位。稽核：`CREATE notifications` 增加 `email_delivery: {status, skip_reason}`；寄送結果 `UPDATE notification_deliveries`（`notification_id, channel, status, error_code`）。照護時間軸不變（寄送不是時間軸事件）。**Known limitation**：尚未送出的排程提醒不能取消或修改（需要新欄位，例如 `cancelled_at`，設計文件未規劃）；排程由查詢時比較 `scheduled_for` 達成，沒有背景工作與推播。

> **實作說明（Email 內容分級，2026-10-06）**：`POST /notifications` 新增兩個選填欄位——通知主題 `category`：`schedule`（行程與報到）、`preparation`（就診準備）→ 允許 `full`；`medication`（用藥與治療）、`symptom_followup`（症狀與照護追蹤）、`clinical_other`（其他醫療相關）→ 最多 `summary`；未指定 = `clinical_other`。`email_mode`：`none` / `summary` / `full`，未指定 = `summary`；其他值 `400 VALIDATION_ERROR`。判斷在後端 `app/modules/notification/email_policy.py`，由 `delivery.plan_email()`（service 層）呼叫，前端或直接呼叫 API 都無法繞過：敏感主題要求 `full` 時**不回 422**，照常建立通知（201），Email 自動降級為 `summary`（`mode_requested = full`、`mode = summary`、`downgraded = true`），結果記在 `notification_deliveries` 與 `CREATE notifications` 稽核。`none` → delivery `skipped / not_requested`。病人仍必須已驗證 Email 且開啟 Email 通知；排程提醒仍 `skipped / scheduled`；風險警示仍不寄。員工的 `email_delivery` 新增 `category`、`mode_requested`、`mode`、`downgraded`（舊資料為 `null` / `false`）。Email 內容（系統名稱「癌症照護系統」）：**summary** 主旨「癌症照護系統｜您有一則新通知」，內文含通知標題（敏感主題改為「您有一則來自護理團隊的醫療照護通知」）、發送時間、「為保護您的醫療資訊，完整內容請登入癌症照護系統查看。」與登入按鈕，不含內容；**full** 主旨「癌症照護系統｜{title}」（單行、限長），內文含標題、內容、發送時間與登入按鈕。HTML 一律 escape；護理師輸入的 URL 不會變成連結（`://` 以零寬字元斷開），唯一的連結是系統的「登入癌症照護系統」（`<APP_BASE_URL>/patient/notifications`，不含任何 id）。App 內通知內容不受影響。

### POST `/api/v1/notifications/alert-rules`

Request
```json
{
  "code": "suspected_febrile_neutropenia",
  "name": "疑似嗜中性白血球低下發燒",
  "source_type": "vital_sign",
  "vital_field": "temperature_c",
  "operator": ">=",
  "threshold_value": 38.0,
  "extra_conditions": { "within_nadir": true },
  "severity": "critical",
  "message_template": "{patient_name}（{patient_code}）體溫 {value}°C，目前為 Cycle {cycle_number} Day {cycle_day}",
  "notify_patient": true,
  "notify_nurse": true,
  "cooldown_minutes": 240
}
```
Response `201`：回傳規則物件。Phase 1 的 `source_type` 只接受 `vital_sign` / `symptom` / `schedule`。

> **實作說明（Sprint 7）**：admin 專用。`GET /notifications/alert-rules`（全部規則，依 `source_type`、`code` 排序）、`POST`（`source_type` 為 `vital_sign`（`vital_field`）/ `symptom`（`symptom_code`）/ `lab`（`test_code`）；`schedule` 尚未實作；`code` 重複 `409`）、`GET/PATCH /alert-rules/{id}`（可改 `name`、`operator`、`threshold_value`、`severity`、`message_template`、`recommended_action`、`notify_*`、`cooldown_minutes`、`extra_conditions`（鍵限 `within_nadir`、`outside_nadir`、`consecutive_records`、`value_above`）、`is_active`；不能改 `code` / `source_type` / 對象）、`POST /alert-rules/{id}/test`（`{value, in_nadir?}` → `{matches, comparison, conditions_met, consecutive_records, is_active, sent: false}`，只試算，不建立任何資料）。規則物件：`id, code, name, source_type, target{type, code, label}, operator, threshold_value, extra_conditions, severity, message_template, recommended_action, notify_patient, notify_nurse, cooldown_minutes, is_active`。變更只影響之後送出的紀錄；既有警示與紀錄不重算（Risk Engine 定義不變）。稽核：`CREATE alert_rules`（規則內容）、`UPDATE alert_rules`（`fields` + `old` / `new`）。

---

## 11. Education API（Phase 2）

| Endpoint | Method | 角色 | 說明 | Tables |
|---|---|---|---|---|
| `/api/v1/education/categories` | GET / POST | 讀：全部；寫：admin | | education_categories |
| `/api/v1/education/materials` | GET | 全部 | `?category_id=&cancer_type=&symptom_code=&drug_id=&q=`（病人只看得到已發布的） | education_materials, material_cancer_types |
| `/api/v1/education/materials` | POST | admin | 建立草稿 | education_materials, material_cancer_types |
| `/api/v1/education/materials/{id}` | GET | 全部 | 內容 | education_materials |
| `/api/v1/education/materials/{id}` | PATCH | admin | `version` +1 | education_materials |
| `/api/v1/education/materials/{id}/publish` | POST | admin | | education_materials |
| `/api/v1/education/materials/{id}/unpublish` | POST | admin | | education_materials |
| `/api/v1/education/recommendations` | GET | 有權者 | 依癌別、藥物、近期症狀推薦 | education_materials, material_cancer_types, cancer_diagnoses, medication_records, symptom_record_values |
| `/api/v1/education/assignments` | GET | 有權者 | `?patient_id=&status=unread` | patient_education_assignments |
| `/api/v1/education/assignments` | POST | nurse | 指派（同時產生 `education` 類通知） | patient_education_assignments, notifications |
| `/api/v1/education/assignments/{id}/view` | POST | patient | 記錄閱讀 | patient_education_assignments |
| `/api/v1/education/assignments/{id}/complete` | POST | patient | 讀完 | patient_education_assignments |

### GET `/api/v1/education/materials`

Query：`?symptom_code=nausea&page=1`

Response `200`
```json
{
  "data": [
    {
      "id": 17,
      "code": "nausea_self_care",
      "title": "化療引起噁心嘔吐的自我照顧",
      "summary": "少量多餐、止吐藥的正確使用時機…",
      "category": { "id": 2, "name": "副作用處理" },
      "content_type": "article",
      "cancer_types": ["C11", "C50"],
      "related_symptom_code": "nausea",
      "language": "zh-TW",
      "version": 3,
      "published_at": "2026-07-01T00:00:00Z"
    }
  ],
  "meta": { "page": 1, "per_page": 20, "total": 3 }
}
```

### GET `/api/v1/education/materials/{id}`

```json
{
  "data": {
    "id": 17,
    "title": "化療引起噁心嘔吐的自我照顧",
    "content_type": "article",
    "content": "## 為什麼會噁心？\n化療藥物會刺激…",
    "media_url": null,
    "version": 3,
    "assignment": { "id": 88, "first_viewed_at": null, "completed_at": null }
  }
}
```

### POST `/api/v1/education/assignments`

Request
```json
{ "patient_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7", "material_ids": [17, 41] }
```
Response `201`：建立的 assignments；已指派過的會略過，並列在 `meta.skipped`。

---

## 12. Settings API

| Endpoint | Method | 角色 | 說明 | 資料來源 | Phase |
|---|---|---|---|---|---|
| `/api/v1/settings/public` | GET | 公開 | 機構名稱、科別、聯絡專線、免責文字 | Phase 1–2：後端設定檔；Phase 3：`institution_settings`（`is_public=true`） | 1 |
| `/api/v1/settings/{key}` | GET | admin | 單一設定（含非公開） | 同上 | 1（唯讀）/ 3（可寫） |
| `/api/v1/settings/{key}` | PUT | admin | 修改設定（樂觀鎖 `version`，寫入稽核） | institution_settings, audit_logs | 3 |

### GET `/api/v1/settings/public`

> **實作說明（Sprint 0）**：已實作，不需登入（登入頁會顯示機構名稱）。內容為後端設定檔 `Config.INSTITUTION`，`meta.source = config_file`。前端優先使用此 API，請求失敗時才使用內建副本。

```json
{
  "data": {
    "organization": { "name": "Demo 醫院", "department": "腫瘤內科" },
    "contacts": [ { "key": "leave", "label": "請假專線", "phone": "07-000-0000" } ],
    "disclaimers": { "treatment_progress_disclaimer": "以上療程次數僅供參考，正確資訊請依醫療團隊告知為準" }
  },
  "meta": { "source": "config_file" }
}
```

> Phase 3 時 `meta.source` 變成 `database`，其他欄位不變。

### PUT `/api/v1/settings/{key}`（Phase 3）

Request
```json
{ "version": 2, "value": [ { "key": "leave", "label": "請假專線", "phone": "07-111-1111" } ] }
```
Response `200`：更新後的設定（`version: 3`）。會依 `value_schema` 驗證。

---

## 13. Admin API

| Endpoint | Method | 角色 | 說明 | Tables | Phase |
|---|---|---|---|---|---|
| `/api/v1/admin/users` | GET / POST | admin | 帳號管理（建立護理師 / 管理者） | users, roles, nurse_profiles | 1 |
| `/api/v1/admin/users/{id}` | GET / PATCH | admin | 停用、改角色 | users, nurse_profiles | 1 |
| `/api/v1/admin/users/{id}/revoke-sessions` | POST | admin | 強制登出 | auth_tokens | 1 |
| `/api/v1/admin/audit-logs` | GET | admin | `?patient_id=&actor_id=&action=&from=&to=`；查詢本身也會被稽核 | audit_logs | 1 |
| `/api/v1/admin/roles` | GET / POST | admin | 自訂角色 | roles, role_permissions | 3 |
| `/api/v1/admin/roles/{id}/permissions` | GET / PUT | admin | 設定角色權限 | role_permissions, permissions | 3 |
| `/api/v1/admin/permissions` | GET | admin | 權限清單 | permissions | 3 |
| `/api/v1/admin/export-requests` | GET / POST | admin | 申請匯出（必填 `purpose`） | data_export_requests | 3 |
| `/api/v1/admin/export-requests/{id}/approve` | POST | admin（非申請人） | 核准（雙人覆核） | data_export_requests, audit_logs | 3 |
| `/api/v1/admin/export-requests/{id}/reject` | POST | admin（非申請人） | 退回 | data_export_requests | 3 |
| `/api/v1/admin/export-requests/{id}/download` | GET | 申請人 | 下載（寫入 `EXPORT` 稽核） | data_export_requests, audit_logs | 3 |

### POST `/api/v1/admin/users`

Request
```json
{
  "email": "nurse02@demo.local",
  "display_name": "護理師 陳",
  "role": "nurse",
  "temporary_password": "********",
  "nurse_profile": { "staff_code": "N-0002", "department": "日間化療室", "title": "專科護理師" }
}
```
Response `201`：使用者物件（不含密碼）；首次登入必須改密碼。

> **實作說明（Sprint 7，取代 Sprint 1 的限制）**：可建立 `role: "nurse"` 或 `"admin"`（admin 不可帶 `nurse_profile`）；病人帳號仍經 `POST /patients/{id}/account`。使用者物件（`GET /admin/users`、`GET/PATCH /admin/users/{id}`）：`id, email, display_name, role, is_active, must_change_password, locked, locked_until, last_login_at, created_at, nurse_profile, patient{id, patient_code}`，護理師另有 `active_patient_count`。`GET /admin/users?role=nurse(預設)|patient|admin|all&status=any|active|inactive|locked&q=`。`PATCH /admin/users/{id}`：`is_active`（停用後該帳號的 token 立即失效——JWT user lookup 要求 `is_active`）、`unlock: true`（清除登入鎖定）、`display_name`（病人帳號同步病人姓名）、`nurse_profile`（僅護理師）；**不能停用自己、不能停用最後一個啟用中的管理者（`409`）**；`role`、`email`、`password`、`temporary_password` 一律 `400`（**與上表「改角色」不同：刻意不開放**，避免管理者藉改角色取得臨床資料權限）。稽核 `UPDATE users`：`fields`（與 `is_active`），不含密碼。`GET /admin/overview`：`patients{total, unassigned, without_account}`、`nurses{total, active, must_change_password}`、`accounts{inactive, locked}`、`assignments{active}`、`alerts{open, critical}`（未結案風險事件數）、`symptom_reviews{pending}`、`generated_at`（唯讀計數，不含病人明細）。`GET /admin/settings`：`institution`、`security{access_token_minutes, login_lockout{max_failed_attempts, lock_minutes}, password_policy}`、`source: "config_file"`（Phase 1 唯讀）。`/admin/users/{id}/revoke-sessions`：**Pending**（Authentication Hardening）。

> **實作說明（Sprint 1，歷史）**：只能建立 `role: "nurse"`。**初始密碼由系統產生**（不接受 `temporary_password` / `password`，傳入回 `400`），在 `201` 回應的 `data.temporary_password` 出現一次，不寫入稽核（`CREATE users` 只記 `role` 與 `temporary_password_issued`）。email 或 `nurse_profile.staff_code` 重複回 `409`。回應為 `GET /admin/users` 的使用者物件：`id`、`email`、`display_name`、`role`、`is_active`、`must_change_password`、`last_login_at`、`active_patient_count`、`nurse_profile`。`GET /admin/users?role=nurse&q=` 目前只支援 `role=nurse`。

### GET `/api/v1/admin/audit-logs`

Query：`?patient_id=7c9e…&from=2026-09-01T00:00:00Z&action=VIEW`

```json
{
  "data": [
    {
      "id": 120394,
      "occurred_at": "2026-09-22T12:05:10Z",
      "actor": { "id": "a8d1e4f2-6c3b-4a9e-b7d5-2f1e8c6a4b90", "display_name": "護理師 林", "role": "nurse" },
      "category": "access",
      "action": "VIEW",
      "resource_type": "patient_profiles",
      "resource_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      "outcome": "success",
      "ip_address": "10.0.3.21",
      "request_id": "c1f3a9e2-5b7d-4e1a-9c0f-2d8e6b4a1f37"
    }
  ],
  "meta": { "page": 1, "per_page": 50, "total": 37 }
}
```

> **實作說明（Sprint 7）**：Query：`patient_id`、`actor_id`（public id）、`action`（`CREATE`、`UPDATE`、`VIEW`、`LOGIN`…，大寫）、`resource_type`、`category`（`auth` / `data` / `access` / `admin` / `export`）、`outcome`（`success` / `failure`）、`from` / `to`（日期 `YYYY-MM-DD`，Asia/Taipei；`to < from` 回 `400`）、`page`、`per_page`（≤ 200）；新到舊。每筆：`id, occurred_at, category, action, actor{id, display_name, role} | {identifier} | null, resource_type, resource_id, patient{id, patient_code} | null, outcome, reason, changes, request_id, http_method, endpoint`（不回傳 `ip_address`）。查詢本身寫入 `VIEW audit_logs`（`filters`、`total`）。稽核內容從不含密碼或初始密碼。

### POST `/api/v1/admin/export-requests`（Phase 3）

Request
```json
{
  "export_type": "audit_logs",
  "parameters": { "from": "2026-07-01", "to": "2026-09-30", "patient_id": null },
  "purpose": "季度資安稽核"
}
```
Response `201`：`status: "pending"`，等待另一位管理者核准。

---

## 14. AI API（Phase 4，保留）

`/api/v1/ai` 先保留 prefix，Phase 4 再設計。預計包含：`/api/v1/ai/predictions?patient_id=`、`/api/v1/ai/predictions/{id}/feedback`、`/api/v1/ai/models`（admin）。對應 DB v3.1 §9。

---

## 15. UI 元件 ↔ API 對照

| UI 元件（ui-architecture） | API | Phase |
|---|---|---|
| AppHeader / OrganizationTitle | `GET /api/v1/settings/public` | 1 |
| DashboardRenderer | `GET /api/v1/dashboard/layout` | 1 |
| PatientSummaryCard / PatientBanner | `GET /api/v1/dashboard/widgets/patient-summary/data` | 1 |
| TodayScheduleWidget | `GET /api/v1/dashboard/widgets/today-schedule/data` | 1 |
| TreatmentProgressWidget | `GET /api/v1/dashboard/widgets/treatment-progress/data` | 1 |
| 症狀填寫頁 | `GET /api/v1/symptoms/forms/{code}` → `POST /api/v1/symptoms/records` | 1 |
| 生命徵象填寫 | `GET /api/v1/vital-signs/reference-ranges`、`POST /api/v1/vital-signs` | 1 |
| 趨勢圖 | `GET /api/v1/symptoms/trends`、`GET /api/v1/vital-signs/trends` | 1 |
| 通知紅點 / 通知列表 | `GET /api/v1/notifications/unread-count`、`GET /api/v1/notifications` | 1 |
| 護理師個案清單 | `GET /api/v1/dashboard/widgets/caseload/data` | 1 |
| CareTopicWidget | `GET /api/v1/dashboard/widgets/care-topics/data` | 2 |
| QuestionnaireCard | `GET /api/v1/symptoms/questionnaires` | 2 |
| EducationLinkButton / 「資源」分頁 | `GET /api/v1/education/materials` | 2 |
| Dashboard 拖拉編輯 | `PUT /api/v1/dashboard/layout` | 2 |
