# 護理師追蹤管理與結案（Phase 1c）實作說明

> 分支 `feature/skin-follow-up`（自 Phase 1b `54e318f` 開出），**未 commit、未部署**。
> 設計來源：`skin-follow-up-and-grading-design.md` A 節；與設計的差異見第 9 節。
> 不包含：AI 判讀、CTCAE 自動分級、推播／簡訊／Email、醫療警示。逾期只是護理師工作清單上的提示。

## 1. 資料庫（migration `a9d4e2b7c5f1`，`down_revision = e7a2c4f9d1b3`）

只新增兩張表，不修改任何既有表或資料；**既有的 `nursing_assessments.next_follow_up_at` 不轉換**。

### `care_follow_ups`
| 欄位 | 說明 |
|---|---|
| `id`（int PK）、`public_id`（UUID，API 使用）| 與照片、病人同樣的慣例 |
| `patient_id` | FK `patient_profiles`，RESTRICT |
| `symptom_record_id` | FK `symptom_records`，NOT NULL（v1 一律來自症狀回報）；病人一致性由 API 檢查（追蹤的病人 = 紀錄的病人）|
| `review_assessment_id` | 規劃這次追蹤的**已簽署**護理評估：審閱，或前一次追蹤的結案評估 |
| `due_at` | 預定追蹤時間（UTC，naive，與全系統一致；API 以 `…Z` 回傳，畫面以台灣時間顯示）|
| `reason` | 追蹤原因（必填，≤1000）|
| `status` | `open` / `completed` / `cancelled`；**逾期不存**，= `open` 且 `due_at` 已過 |
| `version` | 樂觀鎖；每次變更 +1 |
| `created_by`、`created_at`、`updated_at` | |
| `completed_by`、`completed_at`、`completion_assessment_id` | 結案者、時間、結案的已簽署護理評估 |
| `cancelled_by`、`cancelled_at`、`cancellation_reason` | 取消者、時間、原因 |
| `previous_follow_up_id` | 追蹤鏈：結案時排下一次，新一筆指向前一筆 |

- CHECK：狀態值；各狀態必填欄位（open 時結案／取消欄位必須為空，completed 時結案三欄必填……）；`version >= 1`。
- 部分唯一索引 `uq_care_follow_ups_open_record (symptom_record_id) WHERE status = 'open'`：同一筆症狀紀錄最多一筆進行中的追蹤（PostgreSQL 與 SQLite 都支援；同時建立時由資料庫擋下）。
- 索引：`(patient_id, status, due_at)`、`(status, due_at)`、`(symptom_record_id)`。

### `care_follow_up_events`（只能新增）
`follow_up_id`、`event_type`（created / rescheduled / completed / cancelled）、`old_due_at`、`new_due_at`、`actor_id`、`occurred_at`、`reason`、`related_assessment_id`、`related_follow_up_id`（追蹤鏈）。ORM 層禁止 update / delete（同 `audit_logs`）。

### Downgrade 保護
兩張表有任何資料時 downgrade **拒絕執行**並說明原因；只有明確設定 `ALLOW_DROP_CARE_FOLLOW_UPS=yes` 才會刪除。空表時正常移除。

## 2. API（前綴 `/api/v1`）
| 方法與路徑 | 角色 | 說明 |
|---|---|---|
| `GET /care-follow-ups?status=open\|overdue\|completed\|cancelled\|all&patient_id=&sort=due_at\|-due_at\|-updated_at&page=&per_page=` | 護理師 | 只列**目前配對**病人的追蹤；`meta.counts` 各狀態數量 |
| `POST /care-follow-ups` `{symptom_record_id, due_at, reason}` | 護理師 | 已審閱的紀錄才可建立；已有進行中的追蹤 → 409 `FOLLOW_UP_EXISTS`（附既有 id）；未審閱 → 409 `RECORD_NOT_REVIEWED` |
| `GET /care-follow-ups/{id}` | 護理師 | 含結案內容（結果、處置、簽署時間）；稽核 VIEW |
| `GET /care-follow-ups/{id}/events` | 護理師 | 完整歷史；稽核 VIEW |
| `PATCH /care-follow-ups/{id}/reschedule` `{due_at, reason, version}` | 護理師 | 只限 open |
| `POST /care-follow-ups/{id}/complete` `{result, actions, assessment_type?, version, next_follow_up?: {due_at, reason}}` | 護理師 | 新增一筆已簽署護理評估；可同時排下一次 |
| `POST /care-follow-ups/{id}/cancel` `{reason, version}` | 護理師 | 只限 open；不刪任何資料 |
| `GET /patients/me/care-follow-ups` | 病人 | 唯讀：狀態、預定日期、對應回報時間；不含原因、護理紀錄、人名；`meta.notice` 為 119 說明 |
| `POST /symptoms/records/{id}/review`（既有） | 護理師 | 新增 `follow_up: {due_at, reason}`：審閱與建立追蹤在同一交易。Phase 1a 的 `next_follow_up_at` 仍可用（原因 = 處置說明）|

- 所有寫入：必須帶 Idempotency-Key（428），驗證資料（400 `VALIDATION_ERROR`），交易內完成並寫稽核。
- 日期：`due_at` 為含時區的 ISO 8601；範圍為病人當地「今天 00:00」起至 180 天內。
- 錯誤：409 `VERSION_CONFLICT`（版本已變）、409 `FOLLOW_UP_CLOSED`（已結案或取消）、404 `NOT_FOUND`（不存在**或**無權限，兩者回應完全相同）、403（病人或管理者呼叫護理師 API）。
- 症狀紀錄清單（護理師）多 `follow_up` 摘要（id、狀態、預定時間、是否逾期、版本）與 `legacy_follow_up_at`；病人的紀錄清單兩者都沒有。
- **與 Phase 1a `next_follow_up_at` 的相容（讀取時處理，不轉換資料）**：`review_status=follow_up`（需追蹤）= 有進行中的追蹤，**或**審閱評估上有 `next_follow_up_at` 且這筆紀錄還沒有任何追蹤事項（Phase 1a 的審閱）。後者以 `legacy_follow_up_at` 回傳，審閱卡片顯示「原審閱排定追蹤…（尚未建立追蹤事項）」，「建立追蹤」預填該日期；護理師確認建立後才成為追蹤事項。一旦有追蹤事項（不論狀態），就由追蹤事項決定，舊日期不會讓已結案或取消的紀錄重新出現。讀取不寫入任何資料。

## 3. 狀態轉換
```
（審閱時 或 審閱後建立）──► open ──改期（版本 +1，歷史保留舊日期）──► open
                            │
                            ├─結案（新的已簽署護理評估）──► completed ──（可同時）──► 新的 open（previous_follow_up_id → 前一筆）
                            └─取消（必填原因）──────────► cancelled
completed / cancelled 是最終狀態：不能改期、再結案、再取消，也不能改回 open。
```

## 4. 權限
- 護理師：只限目前有效配對的病人；配對結束後立即 404（清單也不再出現）。
- 不存在與無權限的 404 回應相同，不會透露其他病人的追蹤是否存在。
- 病人：只能讀自己的追蹤（唯讀），不能建立、改期、結案或取消（403）。
- 管理者：不因角色名稱取得臨床操作權限，全部 403。

## 5. 交易與競爭條件
- 每次改期／結案／取消都是**條件更新**：`WHERE status = 'open' AND version = <護理師看到的版本>`；兩人同時操作只有一人成功，另一人得到 409，什麼都不會寫入。
- 結案順序：先新增已簽署護理評估 → 條件更新追蹤 → 事件與稽核 →（可選）下一次追蹤，全部在同一交易；任何一步失敗都整筆退回，不會留下「看起來已完成」的追蹤。
- 同時建立：先檢查，再由部分唯一索引兜底（savepoint 攔截 IntegrityError → 409）。
- 重複送出：Idempotency-Key 重送會回同一個結果，不會產生第二筆結案評估或事件。

## 6. 與 NursingAssessment 的整合
- 審閱仍是原本的已簽署評估；建立追蹤時記錄 `review_assessment_id`，並照舊在審閱評估寫入 `next_follow_up_at`（審閱當下的計畫），**之後不再修改**。
- 結案 = **新增**一筆已簽署護理評估（`assessment_type` 電話追蹤／一般追蹤；S 以「【追蹤結案】」開頭，含追蹤原因與預定時間；A：追蹤結果；P：處置；下一次追蹤時寫入其 `next_follow_up_at`），由 `completion_assessment_id` 連結。既有的簽署、修正（amend）機制不變（已簽署的評估直接修改 → 422 `RECORD_LOCKED`）。護理端時間軸標題加「・追蹤結案」並附追蹤 id；病人時間軸只顯示中性文字（「護理師已追蹤您的狀況。」），不含結果、處置、原因或標記。
- 配對交接：舊護理師配對結束立即 404；新配對護理師可看到並處理未完成的追蹤；`created_by` 與歷史中的每位操作者、時間都保留，追蹤不會因交接而重設。
- 「簽署」沿用系統既有的護理評估簽署狀態，**不代表具有法律效力的電子簽章**（待醫院確認）。

## 7. 前端
- **審閱卡片**（`SymptomReviewCard`）：審閱時可勾「需要追蹤」（日期、時間、原因）；已審閱的紀錄顯示追蹤狀態（需追蹤／追蹤逾期／已完成／已取消）與「追蹤管理」連結；沒有進行中的追蹤時可「建立追蹤」。現在適用所有表單（不只皮膚自評）。
- **追蹤管理**（`/nurse/follow-ups`，側邊導覽與手機版首頁入口）：待追蹤／已逾期／已完成／已取消／全部（含數量）、病人篩選、排序、分頁、逾期標示；詳細內容、歷史、改期／結案／取消對話框；版本衝突時顯示「已被其他護理師更新」並重新載入；按鈕在處理中停用。
- **待審清單**：顯示「待追蹤 N・已逾期 N」捷徑。
- **病人端**（`/patient/skin`）：每筆回報顯示追蹤狀態；「照護追蹤」區塊唯讀，固定顯示「本功能為照護追蹤紀錄，並非即時監測或緊急通報系統……緊急情況請撥打 119」。
- 時間：送出以台灣時間（+08:00，無日光節約）組成 ISO 字串；顯示一律 Asia/Taipei。

## 8. 稽核
建立（CREATE care_follow_ups）、改期／結案／取消（UPDATE）、結案評估（CREATE nursing_assessments）、查看詳細（VIEW care_follow_ups）、查看歷史（VIEW care_follow_up_events）。稽核只記錄 id、日期、狀態與版本，**不記錄追蹤原因、結果或護理內容**；應用程式日誌也不寫這些內容。

## 9. 與設計文件（A 節）的差異
1. API 路徑採 `/care-follow-ups/...`；改期為 `PATCH …/reschedule`（設計為 `PATCH /follow-ups/{id}`）。
2. 欄位 `plan` 改名為 `reason` 且必填；`symptom_record_id` 在 v1 為 NOT NULL。
3. 新增 `version`（樂觀鎖）與 `care_follow_up_events`（設計列為選用，這次實作）。
4. 下一次追蹤的 `review_assessment_id` 指向前一筆的結案評估（規劃它的那筆評估）。
5. 逾期不發通知（設計列為待院方決定）。
6. 不轉換既有 `next_follow_up_at`：Phase 1a 未部署，正式環境沒有這類資料；若日後需要轉換，需先確認對應與去重規則。

## 10. 部署注意事項
- 順序：Phase 1b（`e7a2c4f9d1b3`）之後；API 啟動時 `flask db upgrade` 會依序執行 Phase 1a → 1b → 1c 的 migration。部署前先備份資料庫。
- 退回：資料庫到 `a9d4e2b7c5f1` 後，舊版 API 無法啟動（Alembic 找不到版本）；若必須降級，有追蹤資料時 downgrade 會拒絕，需先備份並明確設定 `ALLOW_DROP_CARE_FOLLOW_UPS=yes`。
- 不需要新的環境變數或套件；不影響 Phase 1b 照片的預設關閉。

## 11. 待醫院確認
1. 追蹤的臨床流程：誰可以建立／結案、結案需要記錄哪些項目、是否需要醫師覆核。
2. 逾期的處理：是否需要提醒、提醒對象與時間（目前只顯示）。
3. 病人端要顯示到什麼程度（目前：已安排／預定日期／已完成／已取消）。
4. 護理評估「簽署」的法律效力與電子簽章規範。
5. 追蹤原因、結果的常用選項或範本（目前自由文字）。
6. 配對結束後，未完成追蹤的交接方式（目前：新配對的護理師可看到並處理）。

## 12. 測試結果（2026-11-12，隔離環境）
- 後端 `test_care_follow_ups` 70/70；後端全部 32 suites / 1582 項。
- E2E `skin-follow-up` 62/62（MOCK 與 API）；完整 E2E 開發環境 31/32 suites / 1405 項、PostgreSQL 模擬 32/33 / 1368 項（含部署檢查 22/22，PostgreSQL 執行 `e7a2c4f9d1b3 -> a9d4e2b7c5f1`）。唯一失敗為既有 3 項 mock `labs`。
- Migration：空白與既有資料庫 upgrade → downgrade → upgrade；有追蹤資料時 downgrade 被拒絕。
