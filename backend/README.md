# Cancer Care Platform — Backend

Flask + Flask-SQLAlchemy + Flask-Migrate。環境建置（Python 3.11、venv、安裝套件）請見專案根目錄的 `SETUP.md`。

以下指令都在 `backend/` 目錄下、啟用 venv 後執行。`.env` 中的 `FLASK_ENV=development` 會讓 app 使用 `DevelopmentConfig`，資料庫為 `instance/app.db`（SQLite）。

## 部署（Render staging / demo）

見專案根目錄的 `DEPLOYMENT.md` 與 `render.yaml`。重點：

- `FLASK_ENV`：`development`（預設）/ `staging`（production 的安全設定，允許合成示範資料）/ `production`（禁止示範資料）/ `testing`。
- staging / production 會檢查設定，不安全就拒絕啟動：secret 必須 ≥ 32 字元、彼此不同且不是預設值；不可開 DEBUG；`DATABASE_URL` 必須是 PostgreSQL；`CORS_ORIGINS` 必須是明確的 https 網址。
- 伺服器：`gunicorn -c gunicorn.conf.py run:app`；Render 用 `scripts/render_start.sh`（先 `db upgrade`，再視 `SEED_DEMO_DATA` 載入示範資料）。
- 套件：執行期套件在 `backend/requirements.txt`（固定版本，含 `gunicorn`、`psycopg2-binary`）；根目錄 `requirements.txt` 另含開發工具。
- Render 部署：見專案根目錄 `DEPLOYMENT.md`；部署後以 `scripts/post-deploy-smoke.mjs` 從外部驗證（health、登入、refresh cookie、`/api` proxy、登出；不輸出密碼或 token）。
- CORS：`CORS_ORIGINS`（逗號分隔）；開發環境預設允許 `localhost:5173 / 5174`。瀏覽器的 API 請求都是**同源**（前端主機把 `/api/*` 轉給後端：Vite proxy / Render rewrite），CORS 只是保險；不使用 credentials。
- Refresh token：`refresh_token` cookie（`HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`，沒有 `Domain`），設定在 `app/config.py` 的 `REFRESH_COOKIE_*`。只存在 cookie，不在任何 response body。

## 測試

```bash
python tests/run_all.py              # 全部 suite（每個 suite 在獨立 process 執行）
python tests/run_all.py labs auth    # 只跑名稱包含 labs / auth 的 suite
python tests/test_labs.py            # 單一 suite 也可以直接執行
```

每個 suite 使用 `create_app("testing")`：記憶體中的 SQLite，並載入合成示範資料；不會碰到 `instance/app.db`，也不需要啟動伺服器。

| Suite | 範圍 |
|---|---|
| `test_auth` | 登入、鎖定、稽核、staging / production 啟動安全檢查 |
| `test_config_endpoints` | `GET /settings/public`、`GET /dashboard/layout` |
| `test_dashboard` | 病人 Dashboard 回應格式、驗證、紀錄篩選 |
| `test_symptoms`、`test_review` | 症狀回報、警示、護理師審閱 |
| `test_vitals`、`test_labs` | 生命徵象、檢驗 |
| `test_risk` | 風險摘要與個案排序 |
| `test_notifications` | 通知處理流程、權限、稽核、時間精度 |
| `test_timeline` | 照護時間軸 |
| `test_privacy` | 病人可見內容與跨病人存取 |
| `test_appointments` | Sprint 3：行程建立 / 修改 / 報到 / 完成 / 取消 / 改期（保留歷史）、今日行程、護理師今日清單、時間軸 APPOINTMENT、療程注射行程與延後同步改期、權限、隱私、稽核 |
| `test_chemotherapy` | Sprint 2：藥物 / 處方、療程與 Cycle 狀態、給藥 idempotency、更正 / 標示錯誤、cycle_day 不重算、Dashboard / Timeline / Risk 整合、權限、隱私、稽核 |
| `test_nursing_assessments` | Sprint 4：草稿 → 修改 → 簽署 → 鎖定 → 修正版本 → 版本紀錄、項目追蹤、時間軸、風險、隱私、權限、idempotency、稽核 |
| `test_corrections` | Sprint 5：症狀 / 生命徵象 / 檢驗的更正與標示錯誤、修正歷史、警示處理、Cycle 不重算、待審清單、權限、idempotency、稽核 |
| `test_patient_portal` | Sprint 6：未讀數、已讀、全部已讀（只影響自己、排除未到時間的排程通知）、病人通知內容隱私、個人資料、密碼修改 |
| `test_admin_console` | Sprint 7：總覽計數、帳號清單（角色 / 狀態 / 關鍵字）、建立管理者、停用（token 立即失效）/ 啟用 / 解除鎖定、不能停用自己或最後一位管理者、不能改角色 / email / 密碼、稽核查詢（篩選、分頁、查詢本身被稽核、不含密碼）、系統設定、風險規則（修改、停用、試算、新紀錄套用新門檻、既有警示不變）、症狀量表組成（版本 +1）、非管理者 403 |
| `test_reminders` | Sprint 8：手動 / 排程提醒建立（護理師限指派病人、管理者）、驗證、Idempotency、`origin`、未到時間病人看不到（清單、詳情、未讀、全部已讀）、到時間後出現、處理流程（接手 → 開始處理 → 完成）、病人隱私、結束指派後 404、稽核 |
| `test_refresh` | Refresh token 回歸：cookie 屬性（HttpOnly / Secure / SameSite=Strict / Path / 無 Domain）、body 不含 refresh token、DB 只存雜湊、access token 過期 → refresh（需 `X-Requested-With`）→ API 成功、輪替與同一 session / 到期時間、重複使用偵測（整個 session 撤銷）、登出（含過期 access token、只有 cookie）後不能 refresh、改密碼 / 管理者撤銷 / 重設密碼 / 停用後不能 refresh、單一裝置與其他裝置、裝置獨立、refresh token 到期、初始密碼帳號 |
| `test_sessions` | Authentication Hardening：登入建立 session（`auth_tokens` family、雜湊）、access token `sid`、登出立即失效、自己的 session 清單 / 結束 / 登出其他裝置、改密碼結束其他 session、管理者強制登出、重設密碼（初始密碼、需重設、session 結束）、停用結束 session、稽核不含密碼或 token |
| `test_patient_management` | Sprint 1：病人 CRUD、代碼產生與撞號、初始密碼與首次登入改密碼、指派 / 結束指派後的存取、角色權限、稽核、完整流程 |

## 機構設定與首頁版面

| Endpoint | 說明 |
|---|---|
| `GET /api/v1/settings/public` | 不需登入。機構名稱、科別、聯絡專線、免責文字，來源為 `Config.INSTITUTION`（`meta.source = config_file`） |
| `GET /api/v1/dashboard/layout` | 需登入。病人首頁的 Layout JSON（`app/modules/dashboard/layouts.py`）；`context=overview|patient`、`patient_id`。護理師 / 管理者回 404（Phase 1 為固定畫面） |

## 資料庫 Migration

```bash
flask --app run db upgrade      # 套用所有 migration（第一次建立資料庫也用這個）
flask --app run db current      # 查看目前版本
flask --app run db check        # 確認 Model 與資料庫一致
```

修改 Model 後產生新的 migration：

```bash
flask --app run db migrate -m "說明"
```

產生後請先檢查檔案再 `upgrade`。Alembic 自動產生時常見兩個問題：

- JSONB 欄位出現 `astext_type=Text()`：改成 `sa.Text()`，否則執行時會 `NameError`
- Boolean 預設值出現 `sa.text('1')` / `sa.text('0')`：改成 `sa.true()` / `sa.false()`，否則 PostgreSQL 無法建立

## 認證（JWT）

使用 Flask-JWT-Extended。登入取得 access token 後，呼叫受保護的 API 時帶上 `Authorization: Bearer <token>`。

| Endpoint | 說明 |
|---|---|
| `POST /api/v1/auth/login` | `{"email", "password"}` → `access_token`、`expires_in`、`user`（含 `role`、`patient_id`）；`Set-Cookie: refresh_token=…`（HttpOnly，body 不含） |
| `POST /api/v1/auth/refresh` | refresh cookie + `X-Requested-With: XMLHttpRequest` → 新的 `access_token`（同一 session），cookie 輪替；失敗 `401`（`REFRESH_TOKEN_*`）並清除 cookie |
| `POST /api/v1/auth/logout` | 結束目前 session（Bearer，可已過期；或 refresh cookie）、清除 cookie → `204` |
| `GET /api/v1/auth/me` | 目前登入的使用者（含 `must_change_password`） |
| `PUT /api/v1/auth/password` | `{"current_password", "new_password"}` → `204`；8–128 字元、需同時有英文字母與數字、不可與目前密碼相同 |
| `GET /api/v1/health` | 健康檢查（不需登入） |
| `GET /api/v1/dashboard/patient/<id|me>` | 需登入；病人只能看自己，護理師只能看被指派的病人，管理者可看全部（其他一律 404） |
| `GET /api/v1/dashboard/widgets/caseload/data` | 限護理師；自己負責的病人清單（依風險排序） |

- Access token 預設 15 分鐘（`JWT_ACCESS_TOKEN_MINUTES`）。尚未實作 refresh token，過期後需重新登入。
- 連續 5 次密碼錯誤會鎖定帳號 15 分鐘（回 423 `ACCOUNT_LOCKED`）。
- **首次登入**：系統產生初始密碼的帳號（`password_changed_at` 為 NULL）登入後 `user.must_change_password = true`；設定新密碼前，除了 `/auth/me` 與 `/auth/password`，其他 API 都回 `403 PASSWORD_CHANGE_REQUIRED`（`app/core/auth.py` 的 `require_auth`）。改密碼寫入稽核，但不記錄任何密碼。
- 登入成功 / 失敗都會寫入 `audit_logs`（`LOGIN` / `LOGIN_FAILED`，不記錄密碼）。
- **staging / production 必須設定 `SECRET_KEY` 與 `JWT_SECRET_KEY`**：兩者都要 ≥ 32 字元、彼此不同、不是預設或範例值（`dev`、`change-me`、`.env.example` 的 JWT 範例字串），且 `JWT_SECRET_KEY` 必須由環境變數提供，否則 `create_app()` 拒絕啟動（其他檢查見「部署」）。開發與測試環境不受影響。本機 `.env` 已產生隨機的 `JWT_SECRET_KEY`。

快速測試：

```bash
curl -X POST http://127.0.0.1:5000/api/v1/auth/login   -H "Content-Type: application/json"   -d '{"email":"patient01@demo.local","password":"Demo@1234"}'

curl http://127.0.0.1:5000/api/v1/dashboard/patient/me -H "Authorization: Bearer <access_token>"
```

## 症狀回報

| Endpoint | 說明 |
|---|---|
| `GET /api/v1/symptoms/forms/{code}` | 量表定義（題目、題型、分數範圍），前端依此產生表單。例：`daily_chemo_check` |
| `POST /api/v1/symptoms/records` | 送出症狀回報（病人或被指派的護理師；管理者不可寫入臨床資料） |

送出時，伺服器會在**同一個 transaction** 內完成：

1. 依題目定義驗證每個答案（題型、範圍、級距、必填題）
2. 計算正規化分數 `score`，並自動帶入目前的 `cycle_id` / `cycle_day`
3. 寫入 `audit_logs`（`CREATE`，只記題目代碼，不記分數）
4. 比對 `alert_rules`，符合條件就建立通知給病人和負責的護理師（同一事件共用 `event_key`；冷卻時間內不重複通知，但回應仍會回傳給病人的建議）

**防重複送出**：病人送出時必須帶 `Idempotency-Key: <uuid>` header（沒帶回 428）。同一個 key 重送會回傳第一次的結果（header `Idempotent-Replayed: true`），不會重複建立紀錄或通知；同一個 key 但內容不同回 422。

Seed 內建的風險規則（`flask --app run seed dev`）：

| code | 條件 | 等級 |
|---|---|---|
| `severe_pain` | 疼痛 ≥ 7 | warning |
| `severe_nausea` | 噁心 ≥ 7 | warning |
| `severe_fatigue` | 疲倦 ≥ 8 | warning |
| `reported_fever` | 回報發燒或畏寒 | critical |

## 護理師審閱流程

| Endpoint | 說明 |
|---|---|
| `PATCH /api/v1/notifications/{id}/resolve` | 限護理師。快速處理：`{"resolution_note"}` 必填，從任何未完成狀態直接結案（見下方「通知處理流程」） |
| `GET /api/v1/symptoms/records/{patient_id}` | 病人的症狀回報（`me` 或 public id），`review_status=all|submitted|reviewed`；含每筆觸發的警示與處理狀態。病人看得到「已審閱」與時間，但看不到護理師的內部處置說明，也看不到審閱者、處理者姓名（護理師代為輸入的紀錄，`reported_by` 對病人也隱藏） |
| `POST /api/v1/symptoms/records/{id}/review` | 限被指派的護理師。`action_note` 必填；可選 `assessment_type`（`phone_follow_up` / `follow_up`）、`risk_level`、`ctcae_grades`、`resolve_alerts`（預設 true） |

審閱時：寫入 `reviewed_by` / `reviewed_at`；**處置說明存成一筆已簽署的 `nursing_assessments`**（`plan` 欄位），用 `symptom_records.nursing_assessment_id` 連結；預設同時把這筆回報的警示結案（狀態改為 `resolved`）。每個步驟都寫入 `audit_logs`（`CREATE` 評估、`UPDATE` 紀錄、`ACKNOWLEDGE` 通知）。

## 通知處理流程（Notification Lifecycle）

Migration `ea84e6940c21`：在 `notifications` 新增 `status`、`started_by/at`、`resolved_by/at`，並新增 `alert_rules.recommended_action`。既有欄位不變；升級時，原本 `acknowledged_at` 有值（代表已處理）的通知會回填為 `resolved`，其他回填為 `new`。

風險警示的狀態依序為 `new`（待處理）→ `acknowledged`（已接手）→ `in_progress`（處理中）→ `resolved`（已完成）。狀態記在同一事件的每一筆通知上（病人一筆、每位負責護理師各一筆），所以病人也看得到目前進度。

| Endpoint | 權限 | 說明 |
|---|---|---|
| `GET /api/v1/notifications` | 已登入 | 病人：自己的通知。護理師：目前負責病人的警示，每個事件一筆。管理者：所有病人。Query：`status=all|open|pending|new|acknowledged|in_progress|resolved`（`pending` = new + acknowledged，`unresolved` = open）、`priority`（= `severity`）`critical|warning|info`、`patient_id`、`type`、`is_read`、`page`、`per_page`。`meta.counts` 是各狀態的事件數 |
| `GET /api/v1/notifications/{id}` | 同上 | 照護人員：病人資訊（診斷、療程天數、骨髓抑制期、注意事項）、觸發原因（數值 + 規則條件）、原始症狀 / 生命徵象 / 檢驗資料、建議處理、處理歷程、`allowed_actions`。病人：只有提醒內容與處理狀態 |
| `POST /api/v1/notifications/{id}/acknowledge` | 護理師（負責病人）/ 管理者 | `new → acknowledged` |
| `POST /api/v1/notifications/{id}/start` | 同上 | `acknowledged → in_progress` |
| `POST /api/v1/notifications/{id}/resolve` | 同上 | `in_progress → resolved`，`{"resolution_note"}` 必填（內部紀錄） |
| `PATCH /api/v1/notifications/{id}/read` | 收件者 | 標示已讀（只能操作自己的那一筆） |

- 每次狀態改變都會驗證權限、記錄操作者與時間，並寫入 `audit_logs`：接手記為 `ACKNOWLEDGE`，開始處理和完成記為 `UPDATE`；`changes` 記錄 `status` 的新舊值與受影響的通知 id，**不記錄處理說明內容**。
- 不符合順序的轉換（例如從 `new` 直接 `start`，或重複接手）回 `409 INVALID_TRANSITION`，並說明目前狀態。提醒類通知（`reminder`）沒有處理流程，回 `422 INVALID_STATE`。
- 權限：病人不能改變狀態（`403`）；護理師只能處理目前負責的病人，其他病人的通知回 `404`；管理者可處理全部。
- **病人看不到護理師的內部備註與姓名**：病人的通知列表、通知內容與首頁「今天的健康狀況」只顯示狀態文字（護理團隊已收到通知 / 護理師已接手 / 護理師正在處理 / 已處理完成）。
- 既有的快速處理仍可使用：`PATCH /notifications/{id}/resolve`、生命徵象異常與檢驗面板的「處理」、症狀審閱的「一併處理警示」會直接結案，尚未完成的步驟由同一位護理師補上。
- 各步驟時間：分開執行的步驟一定比前一步晚至少 1 毫秒（同一秒內、甚至系統時鐘沒有前進時也一樣）；只有快速處理會讓步驟時間相同，照護時間軸據此把快速處理顯示為單一「完成」。
- 時間精度：所有時間保存與回傳都精確到毫秒（`utcnow()`、用戶端送入的時間都截到毫秒；API 格式 `2026-09-25T06:10:00.123Z`）。資料庫欄位本身可存到微秒（SQLite / PostgreSQL `TIMESTAMP`），因此不需要 schema 變更。
- 未完成（`new` / `acknowledged` / `in_progress`）的警示，在 risk engine 與個案排序中都算「未處理」。
- 舊版前端使用的 `acknowledged` 欄位仍會回傳給照護人員，內容是「完成處理」的資訊（已標示為 deprecated）。

## 生命徵象

| Endpoint | 說明 |
|---|---|
| `POST /api/v1/vital-signs` | 病人或被指派的護理師記錄生命徵象（至少一項）。病人必須帶 `Idempotency-Key` |
| `GET /api/v1/vital-signs/abnormal` | 限護理師。負責病人中超出參考範圍的量測（`hours` 1–168，預設 72），含觸發的警示與處理狀態；`my_notification_id` 可直接用 `PATCH /notifications/{id}/resolve` 處理 |
| `GET /api/v1/vital-signs/reference-ranges` | 標色用的參考範圍（Phase 1–2 來自設定檔） |

與症狀回報相同流程：驗證（合理數值範圍、整數 / 小數位、血壓需成對且收縮壓 > 舒張壓、量測時間不可為未來或 7 天前）→ 伺服器計算 `cycle_day` → 寫入 `audit_logs` → 比對 `alert_rules`（`source_type='vital_sign'`）→ 通知病人與負責護理師。回應附 `flags`（參考範圍標示）與 `warnings`（例如在禁止量血壓的肢體量測時，仍會記錄，但提示並寫入稽核）。

Seed 內建的生命徵象規則（示範值，需臨床確認）：

| code | 條件 | 等級 |
|---|---|---|
| `suspected_febrile_neutropenia` | 體溫 ≥ 38.0°C，且在骨髓抑制期 | critical |
| `fever` | 體溫 ≥ 38.0°C，不在骨髓抑制期 | critical |
| `tachycardia` | 心跳 ≥ 120 | warning |
| `hypotension` | 收縮壓 ≤ 90 | warning |
| `low_spo2` | 血氧 ≤ 90% | critical |

`alert_rules.extra_conditions` 支援 `within_nadir`、`outside_nadir`、`consecutive_records`、`value_above`（數值還必須大於 X，讓一般警示在危急門檻以下停止，避免同一筆同時觸發兩條規則）。

## 檢驗值（Lab Results）

Migration `9253ef8ff76f` 新增 `lab_test_types`、`lab_results`，並在 `alert_rules` 加上可為 NULL 的 `lab_test_type_id`（既有欄位不變）。支援 WBC、ANC、Hb（`HGB`）、Platelet（`PLT`）。

| Endpoint | 權限 | 說明 |
|---|---|---|
| `GET /api/v1/labs/test-types` | 已登入 | 檢驗項目、單位、參考範圍與危急值 |
| `POST /api/v1/labs/results` | 護理師（被指派） | 登錄同一次採檢的一組結果：`{patient_id, collected_at, resulted_at?, results: [{test_code, value}]}`。`Idempotency-Key` 可選 |
| `GET /api/v1/labs/results/<id>` | 護理師 / 管理者 | 完整資料：每項最新值（含參考範圍快照、`abnormal_flag`、觸發的警示與 `my_notification_id`）、各項趨勢 `series`、全部紀錄。Query：`days`（1–365，預設 90）、`test` |
| `GET /api/v1/labs/summary/<id\|me>` | 病人本人 / 照護人員 | 簡化結果：白話名稱、數值、正常 / 偏低 / 過低，以及日常注意事項；不含參考範圍數字與內部資訊 |

- 驗證：`collected_at` 必填，不可為未來或超過 30 天前；`resulted_at` 不可早於 `collected_at`；項目不可重複；數值必須在合理範圍內（WBC 0–500、ANC 0–200、Hb 1–25、PLT 0–3000，只擋打錯，不代表臨床判斷），並依項目四捨五入（小數 2 / 2 / 1 / 0 位）。
- 每個項目存成一筆 `lab_results`，並記錄單位與參考範圍的快照。`abnormal_flag`：`LL` / `HH`（達危急值）、`L` / `H`（超出參考範圍）、`N`。伺服器計算 `cycle_day`；寫入 `audit_logs`（只記 id 與項目代碼，不記數值）。
- 每筆都會比對 `alert_rules`（`source_type='lab'`），通知流程與症狀、生命徵象相同，包括冷卻期與可重送（replay 不會重複發送通知）。
- **Risk engine**（`_assess_risk`）納入 7 天內各項最新結果：ANC `LL` → 高、`L` → 中；PLT `LL` → 高、`L` → 中；Hb `LL` → 高；WBC `LL` → 中。理由文字如「ANC 0.4 10³/µL（嚴重偏低）」，會同時反映在 nurse-view、個案排序與病人的 risk-summary。
- 病人 Dashboard 新增 `widgets["lab-summary"]`（與 `/labs/summary` 內容相同）。

Seed 內建的檢驗規則與範圍（示範值，需臨床確認）：

| 項目 | 參考範圍 | 危急值 |
|---|---|---|
| WBC | 4.0–10.0 10³/µL | ≤ 1.0、≥ 30.0 |
| ANC | 1.5–7.5 10³/µL | ≤ 0.5 |
| Hb | 12.0–16.0 g/dL | ≤ 7.0、≥ 20.0 |
| PLT | 150–400 10³/µL | ≤ 20、≥ 1000 |

| code | 條件 | 等級 | 冷卻 |
|---|---|---|---|
| `severe_neutropenia` | ANC ≤ 0.5 | critical | 240 分鐘 |
| `neutropenia` | 0.5 < ANC ≤ 1.0（`value_above: 0.5`） | warning | 720 分鐘 |

ANC 由護理師直接輸入檢驗報告上的數值，系統不會用 WBC × 嗜中性球 % 自行計算。

## 風險摘要

風險等級一律來自既有的 risk engine（`app/modules/dashboard/services.py` 的 `_assess_risk`），以下功能只是呈現，不另外計算風險。

- **`GET /api/v1/dashboard/patient/<id|me>` → `widgets["risk-summary"]`**：今日健康風險摘要。`level` 直接取自 risk engine；`status` 是給病人看的說法：
  - `urgent`：高風險，且有未處理的危急警示（或今天沒有任何已處理的警示）
  - `attention`：中風險；或高風險但危急警示已處理、只剩一般警示
  - `handled`：高風險，今天的警示都已由護理團隊處理
  - `stable`：低風險

  另含今天是否已回報症狀、是否已量生命徵象（`symptom_records`、`vital_signs`）、今天的提醒與處理情形（`notifications`），以及建議的下一步（撥打專線 / 回報症狀 / 量測生命徵象）。
- **病人帳號不再收到 `widgets["nurse-view"]`**（內部的待審、護理評估、風險細節），只有護理師 / 管理者會收到。
- **`GET /api/v1/dashboard/widgets/caseload/data?sort=risk|last_report|name`**：`risk`（預設）依序比較：風險等級 → 未處理危急警示數 → 未處理警示數 → 最新警示時間 → 最久未回報。每筆附 `priority_rank`、`unacknowledged_critical_count`、`latest_alert_at`。

## 照護時間軸（Patient Care Timeline）

`GET /api/v1/patients/<id|me>/timeline`：把既有的臨床資料依時間整合成一條時間軸，**不另建資料表**，每次查詢時即時組合。

| 參數 | 說明 |
|---|---|
| `start_date` / `end_date` | 病人時區的日期（`YYYY-MM-DD`，含頭尾），可只給一邊 |
| `limit` | 1–100，預設 30 |
| `cursor` | 上一頁的 `meta.next_cursor`（keyset 分頁；有新資料進來時，下一頁也不會重複或漏掉） |

權限：病人只能看自己（`me`）；護理師只能看目前負責的病人；管理者可看全部。其他情況回 `404`。照護人員查看會寫入 `audit_logs`（`VIEW`、`patient_timeline`）。

| `event_type` | 來源 | 說明 |
|---|---|---|
| `CHEMOTHERAPY` | `chemotherapy_cycles`、`medication_records` | 療程開始 / 結束（全天事件，`all_day: true`）、給藥 |
| `SYMPTOM` | `symptom_records` | 每筆症狀回報 |
| `VITAL_SIGN` | `vital_signs` | 每次量測，依參考範圍給 `severity` |
| `LAB_RESULT` | `lab_results` | 同一次採檢合成一筆（`source.ids` 列出各項） |
| `NOTIFICATION` | `notifications`（風險警示） | 病人看自己那一份通知；照護人員每個事件一筆 |
| `NOTIFICATION_STATUS` | `notifications.acknowledged_at / started_at / resolved_at` | 接手、開始處理、完成；同一時間完成的步驟（快速處理、升級前的舊資料）只顯示「完成」 |
| `NURSING_ASSESSMENT` | `nursing_assessments` | 護理評估；病人只看得到已簽署的，且只有類型與一般說明 |

每個事件：`event_id`、`event_type`、`occurred_at`、`all_day`、`title`、`summary`、`severity`（`critical` / `warning` / `null`）、`source`（`table`、`id`）、`source_id`、`cycle_id`、`cycle_day`、`detail`（依類型而定，展開時顯示）。排序為最新在前；同一時間時，處理進度 → 風險提醒 → 護理評估 → 檢驗 → 生命徵象 → 症狀 → 給藥 → 療程。

**病人看不到的內容**：護理師的處理說明、護理評估的 SOAP 與風險等級、評估草稿、給藥反應紀錄、處理者姓名、規則的建議處理，以及檢驗參考範圍數字（改用白話的正常 / 偏低 / 過低）。通知的文字與狀態遵循通知處理流程的病人可見規則。

效能：每種來源各自查詢，條件包含病人、時間範圍與 cursor，並限制在 `limit + 1` 筆，再合併取前 `limit` 筆；症狀、生命徵象、護理評估、給藥都走 `(patient_id, 時間)` 索引。檢驗與通知目前走以 `patient_id` 開頭的既有索引再排序，資料量大時可再加 `lab_results(patient_id, collected_at)` 與 `notifications(patient_id, created_at)` 索引（需 migration，尚未建立）。

## 病人與照護團隊管理（Sprint 1）

`app/modules/patient/management.py`、`app/modules/patient/routes.py`、`app/modules/admin/`。完整規格見 `docs/api-design.md` §4、§13 的實作說明。

| Endpoint | 角色 | 說明 |
|---|---|---|
| `GET /api/v1/patients` | nurse、admin | 護理師：目前指派的病人；管理者：全部（`assigned=true|false`）。`q`、`sort`、`page`、`per_page` |
| `POST /api/v1/patients` | nurse、admin | 建立病人；代碼 `P00001`… 由系統產生；選填 `account: {email}` 同時建立帳號 |
| `GET/PATCH /api/v1/patients/<id|me>` | 讀：有權者；改：nurse、admin | 詳情 / 更新基本資料（不能改代碼） |
| `POST /api/v1/patients/<id>/account` | nurse、admin | 為既有病人建立登入帳號 |
| `GET/POST /api/v1/patients/<id>/care-alerts`、`PATCH …/<alert_id>` | 讀：有權者；寫：nurse | 照護注意事項 |
| `GET/POST /api/v1/patients/<id>/diagnoses`、`PATCH …/<diagnosis_id>` | 讀：有權者；寫：nurse | 診斷（單一主要診斷） |
| `GET /api/v1/patients/cancer-types` | 全部 | 癌別清單 |
| `GET /api/v1/patients/<id>/nurse-assignments` | nurse、admin | 照護團隊歷程 |
| `POST /api/v1/patients/<id>/nurse-assignments`、`POST …/<assignment_id>/end` | admin | 指派 / 結束指派 |
| `GET /api/v1/admin/overview` | admin | 管理總覽計數（病人、護理師、帳號狀態、指派、未結案警示、待審症狀） |
| `GET /api/v1/admin/users?role=nurse|patient|admin|all&status=any|active|inactive|locked&q=`、`POST /api/v1/admin/users` | admin | 帳號清單 / 建立護理師或管理者（初始密碼系統產生、只回傳一次） |
| `GET/PATCH /api/v1/admin/users/<id>` | admin | `is_active`、`unlock`、`display_name`、`nurse_profile`；不能停用自己 / 最後一位管理者；不能改 role / email / 密碼 |
| `GET /api/v1/admin/audit-logs` | admin | 稽核查詢（`actor_id`、`patient_id`、`action`、`resource_type`、`category`、`outcome`、`from`、`to`、分頁）；查詢本身寫入稽核 |
| `GET /api/v1/admin/settings` | admin | 機構資訊與安全政策（設定檔，唯讀） |
| `GET/POST /api/v1/notifications/alert-rules`、`GET/PATCH …/<id>`、`POST …/<id>/test` | admin | 風險規則；變更只影響之後的紀錄；試算不建立資料 |
| `GET /api/v1/symptoms/forms`、`PUT /api/v1/symptoms/forms/<code>` | admin | 症狀量表題目順序與必填（`version` +1） |
| `POST /api/v1/notifications`、`GET /api/v1/notifications/scheduled?patient_id=` | nurse（指派病人）、admin | 手動 / 排程提醒（`scheduled_for`）；尚未到時間的提醒清單；處理流程與風險警示相同 |
| `GET /api/v1/auth/sessions`、`DELETE /api/v1/auth/sessions/<id>`、`POST /api/v1/auth/sessions/revoke-others` | 已登入 | 登入裝置清單、結束某個 / 其他 session（access token 立即失效、該 session 不能再 refresh） |
| `GET /api/v1/admin/users/<id>/sessions`、`POST …/revoke-sessions`、`POST …/password-reset` | admin | 登入裝置、強制登出、重設密碼（初始密碼只回傳一次；不能對自己） |

- **存取控制沿用既有的 `ensure_can_view_patient`**：護理師只有在目前有效的指派期間能存取病人（其他一律 404），所以結束指派後原護理師立即失去所有資料的存取權，新指派的護理師立即取得。護理師建立的病人也要等管理者指派。
- **初始密碼**：`app/core/passwords.py` 產生（12 碼、排除易混淆字元），雜湊仍用 Werkzeug。只在建立的回應中出現一次，不寫入 `audit_logs`。
- 不儲存身分證字號：`national_id` 等欄位一律拒絕；病人以 `patient_code` 識別。
- 修改病人資料不會重算既有紀錄的 `cycle_day`，也不會改動歷史紀錄。本 Sprint 不需要 migration（使用既有的 `users.password_changed_at`、`nurse_patient_assignments.ended_at / assigned_by` 等欄位）。

## 化療療程與給藥（Sprint 2）

`app/modules/chemotherapy/`（`/api/v1/chemotherapy`）。規格見 `docs/api-design.md` §5 實作說明。

- 藥物 / 處方主檔：nurse、admin 讀；admin 寫。療程、Cycle、給藥紀錄：依病人存取規則讀（其他 404），目前指派的護理師寫。
- Cycle：`start`（Day 1）、`complete`、`delay`；依序開始、同一病人只有一個進行中 Cycle。**開始 Cycle 不重算既有紀錄的 `cycle_day`**，之後的新紀錄才屬於新 Cycle。
- 給藥紀錄 append-only：`POST /cycles/<id>/medications`（Idempotency-Key）、`POST /medications/<id>/amend`（新紀錄 + 原紀錄 `amended`）、`POST /medications/<id>/mark-error`（`entered_in_error`）；原因寫入 AuditLog。病人只看到 `final` 紀錄且不含護理師姓名、反應紀錄。
- Dashboard、Timeline、Risk Engine 讀同一份資料，邏輯沒有修改。沒有 migration。
- 建立療程可同時建立注射行程（`generate_infusion_appointments`），延後 Cycle 可同步改期（`reschedule_appointments`）。

## 治療行程（Sprint 3）

`app/modules/chemotherapy/appointments.py`（`/api/v1/chemotherapy/appointments`）與 `GET /api/v1/dashboard/widgets/today-appointments/data`。

- 狀態：`scheduled → checked_in → completed`，`cancel` 需原因；改期建立新行程並保留原行程（`rescheduled`），準備事項時間一起順移。
- 今日行程 widget 與護理師今日清單排除已取消 / 已改期；時間軸新增 `APPOINTMENT` 事件（時間已到的行程，病人看不到內部備註）。
- Known limitation：報到 / 完成時間只記在 AuditLog（資料表沒有時間欄位）；`no_show` 狀態目前沒有 API。

## 護理評估（Sprint 4）

`app/modules/nursing/`（`/api/v1/nursing-assessments`）。規格見 `docs/api-design.md` §9 實作說明。

- 草稿由撰寫者修改 / 簽署；已簽署 → `422 RECORD_LOCKED`，改用 `amend` 建立新草稿版本，簽署後原評估變成 `amended`（`GET /{id}/versions` 可看全部版本）。
- 讀取限照護人員（病人 403、未負責的護理師 404）；病人只在時間軸看到一句中性文字。
- Known limitation：建立時附帶生命徵象 / 症狀紀錄尚未支援；Risk Engine 以最新有效評估（含草稿）計算（既有定義）。

## 紀錄修正與待審清單（Sprint 5）

`app/services/corrections.py`（由 symptom / vital_signs / labs 的 routes 註冊 `…/{id}/amend`、`/mark-error`、`/history`），以及 `GET /labs/abnormal`、`GET /vital-signs/patient/{pid}`、`GET /dashboard/widgets/pending-symptom-reviews/data`。

- 更正經由原本的建立函式（`correction_of=` 參數）產生新紀錄：同樣的驗證與 Alert Engine，保留原 Cycle / `cycle_day`、填寫者與來源；原紀錄 `amended`。
- 原紀錄的警示：更正後不再符合或已重新通知 → 關閉；仍符合但被冷卻時間擋下 → 保留。標示錯誤 → 關閉全部。
- Pending：病人自行更正。

## Development Seed Data

建立開發用的測試資料，**全部為假資料**，不含任何真實個資（`database-design.md` §3）。

```bash
flask --app run seed dev
```

- **可重複執行**：每筆資料依固定的識別欄位（email、`patient_code`、代碼等）查找，不存在才建立，存在就更新回預設內容，不會產生重複資料。
- **日期以執行當天為準**：每次執行都會把日期更新成相對於今天（病人時區 Asia/Taipei），所以今日行程永遠是今天，Cycle 1 永遠是 Day 4。
- **只能在開發 / 測試環境執行**：`FLASK_ENV=production` 時會拒絕執行。

### 測試帳號

| 角色 | Email | 密碼 |
|---|---|---|
| 護理師 | `nurse01@demo.local` | `Demo@1234` |
| 病人 | `patient01@demo.local` | `Demo@1234` |
| 管理者 | `admin01@demo.local` | `Demo@1234` |

密碼可用環境變數覆寫：`SEED_DEMO_PASSWORD=xxxx flask --app run seed dev`（重新執行會一併更新密碼）。

### 建立的資料

| 類別 | 內容 |
|---|---|
| Role | admin、nurse、patient |
| User | 管理者 1 位、護理師 1 位（含 NurseProfile `N0001`）、病人帳號 1 個（seed 帳號都視為已設定密碼，不需首次改密碼） |
| Patient | `P00001` 測試病人 甲（出生日期為合成資料），注意事項：Penicillin 過敏；指派給上述護理師為主責 |
| Cancer Diagnosis | C11 鼻咽癌 Stage III |
| Chemotherapy | Drug：Cisplatin；Regimen：Cisplatin q3w（含處方藥物）；Plan：共 3 個 Cycle，進行中；Cycle 1（3 天前開始）；Cycle 1 Day 1 的給藥紀錄 |
| Symptom | 定義：Pain、Nausea、Fatigue（0–10 分）、Fever（是 / 否）；量表 `daily_chemo_check`；Day 2、Day 3 各一筆自評紀錄（待護理師審閱）；4 條風險規則 |
| Vital Signs | Day 3 晚上一筆（體溫 37.2°C 等） |
| Lab | 4 個檢驗項目（WBC、ANC、Hb、PLT）；Cycle 1 Day 1 化療前 CBC 一組（都在正常範圍）；2 條 ANC 風險規則 |
| 建議處理 | 每條風險規則都有 `recommended_action`（示範內容，需臨床確認），顯示在通知內容 |
| Appointment | 今天 14:00 門診追蹤與抽血，13:40 報到 |
| Notification | 給病人的今日回診提醒（未讀） |

這些資料足以讓 Phase 1 Dashboard 的所有元件都有內容可以顯示：病人摘要、今日行程、化療進度、最新生命徵象、症狀趨勢、我的通知，以及護理端的個案清單與待審症狀。

### 重建開發資料庫

```bash
rm instance/app.db
flask --app run db upgrade
flask --app run seed dev
```

### 注意

- Seed 的密碼雜湊使用 Werkzeug 的 `generate_password_hash`。認證功能實作時若改用其他演算法（設計文件寫的是 bcrypt / argon2），請同步修改 `app/seeds/dev.py`。
- Seed 不寫入 `audit_logs`，因為它不是使用者操作。
