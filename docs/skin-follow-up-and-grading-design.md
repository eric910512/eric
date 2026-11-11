# 需追蹤結案機制 與 護理師專業分級：資料結構設計

> 狀態（2026-11-12）：**A 節（追蹤結案）已於 Phase 1c 實作**（`feature/skin-follow-up`，migration `a9d4e2b7c5f1`），實際結構與差異見 `skin-follow-up-implementation.md`。**B 節（專業分級）仍為設計稿，未建表**。

> 範圍：皮膚自我評估（`rt_skin_self_assessment`）Phase 1a 之後的兩項結構。兩者都只**新增**資料表，不修改既有表、不改既有紀錄。
> 臨床內容（分級標準、版本、分級文字、追蹤規則）仍待院方確認，見 `skin-self-assessment-clinical-review.md`。

---

## A. 需追蹤結案機制

### A1. 現況（Phase 1a）
- 審閱時可帶 `next_follow_up_at`，存在該次審閱建立的**已簽署**護理評估（`nursing_assessments.next_follow_up_at`）。
- 「需追蹤」= 已審閱 且 有追蹤日期。沒有「已完成」狀態，所以需追蹤清單只會越來越長。
- 已簽署的護理評估不能修改（`RECORD_LOCKED`），因此不能靠「清掉 next_follow_up_at」結案，也不應該這樣做（會失去紀錄）。

### A2. 評估過的做法
| 做法 | 說明 | 結論 |
|---|---|---|
| 在 `symptom_records` 加狀態欄位 | 改既有表；一筆紀錄可能有多次追蹤；把護理工作流程混進病人回報 | 不採用 |
| 重用 `nursing_assessment_items`（problem / goal / intervention…，可在簽署後改 `item_status`） | 已有 open / done 與 `resolved_at`；但**沒有到期日、完成者、完成說明**，也不能連到「完成時的處理紀錄」 | 不足 |
| 改已簽署評估的 `next_follow_up_at` | 違反簽署後不可改的原則 | 不採用 |
| **新增獨立追蹤表**（建議） | 追蹤是有生命週期的工作項目：建立 → 改期 → 完成／取消；原審閱紀錄完全不動 | **建議** |

### A3. 資料表 `care_follow_ups`（建議）
| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | int PK | |
| `patient_id` | FK patient_profiles RESTRICT, NOT NULL | 追蹤屬於病人，不屬於某位護理師（換護理師時由新配對的護理師接手） |
| `symptom_record_id` | FK symptom_records RESTRICT, NULL 可 | 來源紀錄（皮膚自評）；保留未來從其他來源建立追蹤的彈性 |
| `review_assessment_id` | FK nursing_assessments RESTRICT, NOT NULL | 建立追蹤的那次審閱（已簽署，不修改） |
| `due_at` | datetime NOT NULL | 追蹤日期（UTC；畫面以病人時區顯示日期） |
| `plan` | text NULL | 追蹤要做什麼（例如「回診時評估脫皮範圍」） |
| `status` | varchar(20) NOT NULL | `open` / `completed` / `cancelled`（CHECK） |
| `created_by` / `created_at` | FK users / datetime | |
| `completed_by` / `completed_at` | FK users / datetime NULL | 完成者、完成時間 |
| `completion_assessment_id` | FK nursing_assessments NULL | **完成時的處理紀錄**：一筆新的已簽署護理評估（電話追蹤／一般追蹤，SOAP 文字） |
| `cancelled_by` / `cancelled_at` / `cancel_reason` | NULL | 例如病人轉院、療程結束 |
| `previous_follow_up_id` | self-FK NULL | 完成時再排下一次追蹤 → 串成追蹤鏈 |
| `updated_at` | datetime | |

- 限制：`CHECK`（`completed` ⇔ `completed_by`、`completed_at`、`completion_assessment_id` 都有值；`cancelled` ⇔ 取消三欄有值）。
- 索引：`(patient_id, status, due_at)`、`(status, due_at)`（逾期查詢）、`(symptom_record_id)`。
- 一筆來源紀錄同時最多一筆 `open`：部分唯一索引 `UNIQUE (symptom_record_id) WHERE status = 'open'`（PostgreSQL 與 SQLite 都支援）。
- 改期不另建表：`due_at` 更新 + 稽核紀錄保留 `{old, new, reason}`；如院方要求在畫面上看到改期歷程，再加 `care_follow_up_events`。

### A4. 狀態與畫面
| 顯示狀態 | 條件 |
|---|---|
| 待審 | `symptom_records.review_status = submitted` |
| 已審 | 已審閱，沒有 open 追蹤 |
| 需追蹤 | 有 `open` 追蹤，`due_at` 未到 |
| **追蹤逾期** | 有 `open` 追蹤，`due_at` 已過（以病人時區的日期比較）— 由查詢計算，不需要排程工作 |
| 追蹤完成 | 追蹤 `completed`（可展開完成者、時間、處理紀錄）|

- 逾期：v1 在「待審清單」新增「追蹤逾期」區塊、病人頁顯示紅色標籤；是否另發通知（App 通知給目前配對護理師、冷卻時間、非服務時間）**待院方決定**。Render Free 沒有排程，若要通知，可在護理師開啟待審清單／總覽時以固定 `event_key`（`care_follow_ups:{id}:overdue`）冪等產生。
- 病人端 v1 看不到追蹤（與審閱內容相同的隱私原則）。

### A5. API（建議）
| 方法與路徑 | 角色 | 說明 |
|---|---|---|
| `POST /symptoms/records/{id}/review` | nurse | 既有；`follow_up: {due_at, plan?}` → 同一交易建立 `care_follow_ups`（相容：仍接受 `next_follow_up_at`，同時寫入評估與追蹤）|
| `GET /follow-ups?status=open\|overdue\|completed\|cancelled&patient_id=&page=` | nurse | 只列目前配對病人；`overdue` = open 且已過期 |
| `POST /follow-ups/{id}/complete` | nurse（目前配對） | Idempotency-Key 必填；body `{note (必填), assessment_type: phone_follow_up\|follow_up, next_due_at?, next_plan?}` → 一筆新的已簽署護理評估 + 追蹤 `completed`；有 `next_due_at` 時建立下一筆並以 `previous_follow_up_id` 串接 |
| `PATCH /follow-ups/{id}` | nurse | 改期 `{due_at, reason}`（只限 open）|
| `POST /follow-ups/{id}/cancel` | nurse | `{reason}`（只限 open）|
| `GET /symptoms/records/{pid}?review_status=follow_up` | nurse | 改為「有 open 追蹤」；新增 `follow_up_overdue` 篩選 |

- 錯誤：不是 open → 422 `INVALID_STATE`；未配對 / 配對結束 → 404；管理者 403；病人 403。
- 稽核：建立 CREATE、改期 UPDATE（新舊日期與原因）、完成 UPDATE（+ 完成評估 CREATE）、取消 UPDATE。
- **不影響原審閱**：原審閱的護理評估與 `symptom_records` 都不修改；完成時的處理寫在新的護理評估。
- 遷移：Phase 1a 的 `next_follow_up_at` 若已有資料，migration 為「已審閱且有追蹤日期」的紀錄各建一筆 `open` 追蹤（目前尚未部署，正式環境沒有這類資料）。

---

## B. 護理師專業分級

### B1. 為什麼不用既有 `symptom_record_values.ctcae_grade`
- 它是「**每一題**一個 CTCAE 分級」（0–5），沒有記錄標準或版本。
- 放射性皮膚炎是**整體一個分級**，綜合發紅、脫皮、出血、潰瘍等；寫在「發紅」或「脫皮」任何一題上都會誤導。
- RTOG（0–4）與 CTCAE（1–5，無 Grade 0）的數字意義不同，不能放在同一欄而不標示標準。
- 既有欄位維持原樣，繼續服務既有表單；皮膚分級改用獨立紀錄。

### B2. 資料表 `professional_grades`（建議）
| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | int PK | |
| `patient_id` | FK patient_profiles RESTRICT, NOT NULL | |
| `symptom_record_id` | FK symptom_records RESTRICT, NULL 可 | 依據的病人自評；門診直接評估時可為空（此時 `nursing_assessment_id` 必填）|
| `nursing_assessment_id` | FK nursing_assessments RESTRICT, NULL 可 | 在哪一次審閱／護理評估中分級 |
| `grading_system` | varchar(20) NOT NULL | `ctcae` / `rtog`（CHECK）|
| `grading_version` | varchar(20) NOT NULL | 例如 `5.0`；必須是已啟用的量表版本 |
| `term_code` | varchar(50) NOT NULL | 例如 `dermatitis_radiation`（CTCAE term）／`rtog_acute_skin` |
| `grade` | smallint NULL | 依量表檢查範圍（CTCAE 1–5、RTOG 0–4）|
| `finding` | varchar(20) NOT NULL | `graded` / `absent`（CTCAE 沒有 Grade 0，「沒有反應」需明確記錄）/ `not_assessable` |
| `body_site_code` | varchar(50) NULL | 受控清單（部位 + 左右），清單待院方確認 |
| `body_site_text` | varchar(100) NULL | 補充 |
| `note` | text NULL | 分級依據（例如「腋下皺摺處片狀濕性脫皮」）|
| `assessed_by` / `assessed_at` | FK users / datetime NOT NULL | 評估者、評估時間 |
| `sign_status` / `signed_at` | | v1 建議**建立即簽署**（與審閱相同），不做草稿 |
| `source` / `record_status` / `amends_id` / `cycle_id` / `cycle_day` | ObservationMixin | **修訂歷程**沿用既有 amend／mark-error：更正 = 新一筆（`amends_id` 指向原筆），原筆 `amended`；誤填 = `entered_in_error`；原筆不刪不改 |
| `created_at` / `updated_at` | | |

- 唯一：同一紀錄、同一 term、同一部位只有一筆有效分級：`UNIQUE (symptom_record_id, term_code, body_site_code) WHERE record_status = 'final'`。
- 索引：`(patient_id, term_code, assessed_at)`（分級趨勢）。

### B3. 量表定義 `grading_scale_definitions`（建議，**院方確認後**才寫入內容）
| 欄位 | 說明 |
|---|---|
| `grading_system`、`grading_version`、`term_code`、`grade` | 主鍵組合 |
| `label_zh`、`description_zh`、`description_source` | 給護理師看的分級說明與出處（例如 CTCAE v5.0 原文 + 院方中文翻譯）|
| `is_active`、`approved_by`、`approved_at` | 院方核可紀錄；未核可的版本不可用 |

- 沒有已核可的量表時，審閱畫面的分級欄位保持隱藏（目前行為）。
- 換版本（例如 CTCAE v6）＝新增一組列；舊紀錄保留原版本，不換算。

### B4. API（建議）
| 方法與路徑 | 角色 | 說明 |
|---|---|---|
| `GET /grading-scales?term_code=dermatitis_radiation` | nurse | 已核可且啟用的量表與分級說明 |
| `POST /symptoms/records/{id}/review` | nurse | 既有；新增 `professional_grades: [{grading_system, grading_version, term_code, finding, grade?, body_site_code?, body_site_text?, note?}]`，與審閱同一交易、同一份已簽署評估 |
| `GET /patients/{pid}/professional-grades?term_code=&from=&to=` | nurse（配對）| 分級歷程／趨勢（含已更正標示）|
| `POST /professional-grades/{id}/amend`、`/mark-error`、`GET …/history` | nurse | 沿用 `services/corrections.py` 的 `register_routes` |

- 病人 v1 看不到（403／不出現在病人回應）；管理者 v1 不可存取。
- 稽核：CREATE、AMEND、MARK_ERROR；不記錄分級說明全文以外的敏感內容。
- 不寫入 `symptom_record_values.ctcae_grade`，病人的答案永遠不動。

### B5. 需要決定的事
1. 標準與版本（CTCAE v5.0／更新版／RTOG），是否兩者並存。
2. 分級說明中文版本由誰翻譯與核可。
3. 部位清單與是否左右分開。
4. 是否需要草稿（例如需要醫師覆核）；若需要，再加 `draft` 狀態與簽署 API。
5. 「沒有反應」記錄方式（`absent`）是否符合院方習慣。
