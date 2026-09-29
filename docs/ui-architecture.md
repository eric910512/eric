# Cancer Care Platform — UI Architecture Design (v1.1)

> 狀態：設計文件；Phase 1 部分畫面已實作（見「實作狀態」）
> 更新日期：2026-09-29（Sprint 6：病人端完整功能）
> 參考圖：`docs/design-reference/cancer-dashboard-example.jpg`
> 資料來源：`docs/database-design.md`（**v3.1**）、`docs/api-design.md`（**v1.1**）

## 實作狀態（2026-09-29）

已實作的畫面與元件（`frontend/src/`）；本文件其他內容仍是設計。

| 區域 | 已實作 |
|---|---|
| 登入 | `Login.vue`，依角色導向（病人 `/patient`、護理師 `/nurse`、管理者 `/admin`） |
| 設定新密碼（`/change-password`） | 使用初始密碼登入後一律先到這裡；設定完成前其他頁面都會導回（API 同樣回 `403 PASSWORD_CHANGE_REQUIRED`） |
| 病人首頁（`/patient`） | 依 `GET /api/v1/dashboard/layout` 的 Layout JSON 渲染（請求失敗時使用前端內建副本）：`patient-summary`（pinned）→ `risk-summary` → `today-schedule` → `treatment-progress` → `symptom-quick-report` → `latest-vitals` → `lab-summary` → `symptom-trend` → `my-notifications`（通知顯示處理狀態，不顯示護理師備註） |
| 病人「健康紀錄」 | 分頁「照護時間軸」（`/patient/timeline`，預設）與「記錄生命徵象」（`/patient/vitals`） |
| 病人底部導覽 | 全部已實作：症狀回報（`/patient/symptoms`：今日回報＋我的回報）、健康紀錄、首頁、通知（`/patient/notifications`：未讀數、點開即已讀、全部已讀、通知內容與處理狀態）、我的（`/patient/me`：基本資料、照護注意事項、聯絡醫院、修改密碼、登出）。病人頁面不顯示護理師姓名、內部處理說明、SOAP 或內部風險；390px 無橫向捲動 |
| 護理端總覽（`/nurse/:patientId?`） | 照護重點、未處理通知、生命徵象異常、我的個案（排序：高風險優先 / 最久未回報 / 病歷代碼）、選取病人的 Banner、風險判斷、治療進度、最新生命徵象、檢驗數據（`LabPanel`，含登錄）、症狀趨勢、症狀審閱、病人照護時間軸 |
| 護理端通知中心（`/nurse/notifications`） | 待處理 / 處理中 / 已完成；查看 → 接手 → 處理中 → 完成 |
| 護理端病人管理（`/nurse/patients`、`/nurse/patients/:id`） | 目前指派給自己的病人；新增病人（代碼自動產生，可同時建立帳號並顯示一次初始密碼）；病人資料頁：基本資料編輯、照護注意事項、診斷、照護團隊（唯讀）、登入帳號。未指派或指派已結束的病人顯示「找不到這位病人」 |
| 化療療程（Sprint 2） | 病人資料頁的「化療療程」（`ChemoPanel`）：建立療程（依處方自動排定 Cycle）、Cycle 開始 / 完成 / 延後、登錄給藥（重送不會重複）、給藥歷史（已更正 / 標示錯誤仍保留）、更正與標示錯誤、停止療程；護理師可寫、管理者唯讀。病人：首頁「化療進度」的「查看療程與給藥紀錄」→ `/patient/treatment`（療程、每次療程、給藥紀錄；不顯示護理師姓名與反應紀錄） |
| 治療行程（Sprint 3） | 病人資料頁的「治療行程」（`AppointmentsPanel`）：新增 / 修改（含準備事項）、報到、完成、改期（原行程保留為「已改期」）、取消；建立療程時可同時建立化療注射行程，延後 Cycle 可同步改期。護理端總覽「今日行程」來自 API。病人：首頁「今日行程」顯示已報到 / 已完成狀態與黃底準備事項；「我的療程」有「接下來的行程」；時間軸新增「行程」事件 |
| 管理端（`/admin/patients`、`/admin/patients/:id`、`/admin/nurses`） | 所有病人（可篩選尚未指派）、新增病人、指派 / 結束指派護理師（含主責）、建立護理師帳號（初始密碼只顯示一次） |
| 提醒（Sprint 8） | 病人資料頁的「提醒」（`RemindersPanel`，護理師與管理者）：寫提醒（標題、內容、重要性一般 / 請特別注意、立即送出或指定時間，病人時區）、「排程中」（病人還看不到）、「已送出」（處理狀態，接手 → 開始處理 → 完成，完成需填內部處理說明）。病人未開通帳號時不能寫。病人在「通知」看到到時間的提醒（不含處理者與內部說明） |
| 登入裝置（Authentication Hardening） | `AccountSessions`（`/account/sessions`，所有角色；頂端列「登入裝置」、病人「我的」）：目前登入的裝置（這個裝置標示）、登出某個裝置、登出其他所有裝置。「登出」會同時結束伺服器端 session。Access token 到期前自動換新（refresh token 為 HttpOnly cookie，頁面讀不到）；過期時換頁或 API 請求會先 refresh 再繼續，session 已結束才回登入頁「登入已逾時」。管理端「帳號狀態」：顯示登入中的裝置數、強制登出、重設密碼（需再按「確定重設」，初始密碼只顯示一次） |
| 管理者後台（Sprint 7） | `AdminHome`（`/admin`，管理者首頁）：未處理風險警示 / 待審症狀數（唯讀）與帳號、照護團隊計數，各連到處理頁（尚未指派 → `/admin/patients?assigned=false`）。`AdminAccounts`（`/admin/accounts`）：全部角色帳號，依角色 / 狀態 / 關鍵字篩選；停用（需再按「確定停用」）、重新啟用、解除登入鎖定；自己的帳號不顯示操作；新增管理者（初始密碼只顯示一次）。`AdminAudit`（`/admin/audit`）：類別 / 動作 / 對象 / 結果 / 日期篩選、分頁、變更內容（`changes`）展開。`AdminRules`（`/admin/rules`）：風險規則啟用 / 停用、調整門檻與等級、試算（不發送）；症狀量表調整題目順序與必填（版本 +1，病人表單隨之更新）。`AdminSettings`（`/admin/settings`）：機構資訊與安全政策（唯讀）。側欄 `StaffNav` 依序：管理總覽、病人與照護團隊、護理師帳號、帳號狀態、稽核紀錄、風險規則與量表、系統設定。管理者不修改臨床紀錄 |
| 護理評估（Sprint 4） | 病人資料頁「護理評估」（`AssessmentsPanel`）：新增草稿（SOAP、ECOG、整體狀況、風險、化療準備、問題與措施）、修改 / 簽署自己的草稿、已簽署評估的「修正」（新版本，原評估保留並可看版本紀錄）、項目狀態追蹤；側欄「護理評估」（`/nurse/assessments`）列出自己的待簽署草稿與最近簽署。病人只在時間軸看到「護理師已完成評估」 |
| 紀錄修正與待審清單（Sprint 5） | 側欄「待審清單」（`/nurse/reviews`）：待審症狀、異常生命徵象、異常檢驗、待處理通知（跨病人，點選開啟病人）。病人資料頁「紀錄修正」（`RecordsPanel`）：症狀 / 生命徵象 / 檢驗的更正（需原因，原紀錄保留）、標示錯誤、修正歷史 |
| 護理端側欄 | 照護總覽、通知與警示、待審清單、病人管理、護理評估皆已實作 |

尚未實作：CareTopic、問卷排程、衛教、拖拉版面；護理端與管理端版面仍為固定畫面（`/dashboard/layout` 目前只提供病人首頁）。

## 變更摘要（v1 → v1.1）

| 項目 | 變更 |
|---|---|
| 資料來源版本 | 對齊 Database Design v3.1、API Design v1.1 |
| Phase 1 Dashboard | 病人首頁改為 `today-schedule` / `treatment-progress` / `symptom-quick-report` / `latest-vitals` / `my-notifications`；**照護主題模組（CareTopic）移到 Phase 2**（依賴衛教與問卷排程） |
| Component ↔ Table | v1 標為「缺」的資料表都已併入 DB v3.1，對照表改成依 Phase 標示；未納入 DB 的只剩拍照評估與聊天室 |
| Dashboard Layout | 改依 API v1.1 §8 的 **Layout JSON 契約**：Phase 1 沒有拖拉，但首頁一律由 `GET /api/v1/dashboard/layout` 回傳的 JSON 渲染 |
| Widget 命名 | 改為 kebab-case，與 API `widget_code` 一致（`today_schedule` → `today-schedule`） |
| 底部導覽 | 依 Phase 1 功能調整；「資源」Phase 2 才出現，聊天室不在規劃範圍 |

---

## 0. 參考圖摘要

參考圖是**病人端手機首頁**（放射腫瘤科、放療情境），由上而下：院所標頭 → 病人摘要 → 今日行程 → 治療進度 → 主題照護模組（放射線皮膚炎）→ 底部導覽列。

| 參考圖（放療） | 本平台（化療） | Phase |
|---|---|---|
| 放射線治療進度 18 / 33 次 | 化療 Cycle 進度 3 / 6，加上目前 cycle day、骨髓抑制期提醒 | 1 |
| 今日：放射線治療、電腦斷層定位（空腹、報到） | 今日：化療注射、抽血、門診、檢查，以及檢查前準備 | 1 |
| 病歷號 3087678 | 改顯示 `patient_code`（DB §3 Demo 政策） | 1 |
| 過敏史 / 右手禁治療 | 結構化注意事項 `patient_care_alerts` | 1 |
| 放射線皮膚炎評估（衛教 + 問卷 + 開放時段） | 依處方而定的照護主題（神經病變、口腔黏膜炎、手足症候群） | **2** |
| 皮膚炎拍照評估 | 未納入 DB 設計 | 未規劃 |
| 聊天室 | 未納入 DB 設計 | 未規劃 |

---

## 1. UI Layout 結構

### 1.1 病人首頁區塊（Phase 1）

```
┌─────────────────────────────────────┐
│ ① AppHeader                          │  固定：Home icon + 機構 / 科別
│                                      │  ← GET /api/v1/settings/public
├─────────────────────────────────────┤
│ ② Pinned：patient-summary            │  固定，不可收合 / 移除
│   頭像、姓名 / 年齡 / 性別 / patient_code │
│   CareAlertChip（過敏、右手禁治療）     │
├─────────────────────────────────────┤
│ ③ DashboardRenderer（可捲動）          │  ← GET /api/v1/dashboard/layout
│  ┌───────────────────────────────┐  │
│  │ today-schedule      [LiveClock]│  │
│  │  ScheduleItem ×N  [QuickContact]│  │
│  │  PrepInstructionPanel（黃底）   │  │
│  ├───────────────────────────────┤  │
│  │ treatment-progress    [醫師]   │  │
│  │  診斷 ▶▶ Cycle 3 / 6 ▶▶ Day 9  │  │
│  │  骨髓抑制期提醒 / 免責說明       │  │
│  ├───────────────────────────────┤  │
│  │ symptom-quick-report           │  │
│  │  今日是否已回報 / [開始回報]     │  │
│  ├───────────────────────────────┤  │
│  │ latest-vitals                  │  │
│  │  體溫 / 心跳 / 血壓 / 體重（標色）│  │
│  ├───────────────────────────────┤  │
│  │ my-notifications               │  │
│  │  最新 3 則提醒                  │  │
│  └───────────────────────────────┘  │
├─────────────────────────────────────┤
│ ④ BottomNavBar                       │  固定（見 §1.4）
└─────────────────────────────────────┘
```

> Phase 2 會在 `treatment-progress` 之後插入 `care-topics`（參考圖的「皮膚炎評估」區塊），由版面設定決定，前端不用改。

### 1.2 版面規則（從參考圖歸納）

| 規則 | 觀察 | 設計規範 |
|---|---|---|
| 單欄卡片堆疊 | 圓角卡片由上往下排 | Mobile 單欄；Layout JSON `grid.columns=1`，只使用 `position.y` |
| 卡片標題列 | 左標題、右 meta，下方分隔線 | `SectionHeader`（title + meta slot + divider） |
| 語意色 | 藍＝資訊、黃底紅字＝需要病人行動、綠＝完成、灰＝不可操作 | design token：`info` / `action-required` / `success` / `disabled`；另加 `critical`（紅）給生命徵象危急值 |
| 大字、大圖示 | | 內文 ≥ 16px、觸控目標 ≥ 48px、icon 一律搭配文字 |
| 固定頭尾 | | 由 App Shell 負責 |

### 1.3 各角色的 Layout Shell

| Shell | 使用者 | 結構 | `GET /api/v1/dashboard/layout` |
|---|---|---|---|
| `PatientMobileShell` | 病人 | Header + 捲動內容 + BottomNav | `context=overview`，`grid.columns=1` |
| `ClinicalDesktopShell` | 護理師 | Sidebar + Topbar + 內容 | 總覽：`context=overview`；單一病人：`context=patient&patient_id=…`（`patient-summary` 以 Banner 形式固定在頂端） |
| `AdminDesktopShell` | 管理者 | Sidebar + Topbar + 內容 | `context=overview`，`grid.columns=12` |

### 1.4 底部導覽（病人端）

| Phase | 項目（左 → 右） | 對應頁面 / API |
|---|---|---|
| 1 | 症狀回報 / 健康紀錄 / **首頁** / 通知 / 我的 | 症狀表單（`/api/v1/symptoms/forms/{code}`）/ 生命徵象與趨勢（`/api/v1/vital-signs`、`/trends`）/ Dashboard / `/api/v1/notifications` / 帳號設定（`/api/v1/auth/me`） |
| 2 | 症狀回報 / 健康紀錄 / **首頁** / 通知 / 資源 | 「我的」移到 Header 選單；「資源」= `/api/v1/education/materials` |

> **實作狀態（Patient Care Timeline sprint）**：「健康紀錄」包含兩個分頁：「照護時間軸」（`/patient/timeline`，預設，`GET /api/v1/patients/me/timeline`）與「記錄生命徵象」（`/patient/vitals`）。護理端病人資料頁也嵌入同一個 `PatientTimeline` 元件（內容依角色由 API 決定）。

> 導覽屬於路由結構，依角色寫在前端設定中，**不**由 Layout API 決定。通知項目上的紅點來自 `GET /api/v1/notifications/unread-count`（每 60 秒輪詢）。

---

## 2. Component 拆分

### 2.1 分三層

| 層 | 職責 | 取得資料 | 範例 |
|---|---|---|---|
| **Layout** | 頁面骨架、導覽 | `settings/public`、`auth/me` | AppShell、AppHeader、BottomNavBar |
| **Widget** | 一個完整功能區塊；**只呼叫 Layout JSON 指定的 `data_endpoint`** | 一個 widget data API | TodayScheduleWidget |
| **Shared UI** | 純呈現元件，只吃 props | 不取資料 | SectionCard、ProgressArrowBar |

### 2.2 元件樹（病人端，Phase 1）

```
PatientMobileShell
├── AppHeader                              (Layout)  ← settings/public
│   ├── HomeIconButton
│   └── OrganizationTitle
├── DashboardRenderer                      ← GET /api/v1/dashboard/layout
│   ├── PinnedArea
│   │   └── WidgetFrame ─ PatientSummaryWidget        [patient-summary]
│   │       ├── PatientAvatar
│   │       ├── PatientIdentityLine
│   │       └── CareAlertList → CareAlertChip ×N
│   └── WidgetGrid（依 items[].position 排列）
│       ├── WidgetFrame ─ TodayScheduleWidget         [today-schedule]
│       │   ├── SectionHeader (meta=<LiveClock/>)
│       │   ├── ScheduleItem ×N → AppointmentTypeIcon + TimeLabel
│       │   ├── QuickContactButton
│       │   └── PrepInstructionPanel → InstructionItem ×N
│       ├── WidgetFrame ─ TreatmentProgressWidget     [treatment-progress]
│       │   ├── SectionHeader (meta=<ClinicianTag/>)
│       │   ├── DiagnosisLabel
│       │   ├── ProgressArrowBar
│       │   ├── CycleDayIndicator（Day N、骨髓抑制期）
│       │   └── DisclaimerText
│       ├── WidgetFrame ─ SymptomQuickReportWidget    [symptom-quick-report]
│       │   ├── TodayReportStatus
│       │   └── ActionTile → 症狀表單頁
│       ├── WidgetFrame ─ LatestVitalsWidget          [latest-vitals]
│       │   └── VitalTile ×N（值 + 量測時間 + 標色）
│       └── WidgetFrame ─ MyNotificationsWidget       [my-notifications]
│           └── NotificationItem ×3 → 通知列表頁
└── BottomNavBar                           (Layout)
    └── NavItem ×5（通知項目帶 UnreadBadge）
```

**Phase 2 新增到病人首頁**

```
WidgetFrame ─ CareTopicWidget               [care-topics]
├── SectionHeader (title=config.topic)
├── EducationLinkButton ×N
└── QuestionnaireCard
    ├── WindowTimeline（療程第一週 / 第三週 / 結束第一週）
    └── StatusBadge（not_open / open / submitted / overdue / closed）
```

### 2.3 護理端元件（Phase 1）

| Widget | widget_code | 放在 |
|---|---|---|
| UnacknowledgedAlertsWidget | `unacknowledged-alerts` | 總覽 |
| TodayAppointmentsWidget | `today-appointments` | 總覽 |
| CaseloadWidget（依風險排序的個案清單） | `caseload` | 總覽 |
| PendingSymptomReviewsWidget | `pending-symptom-reviews` | 總覽 |
| PendingAssessmentSignoffWidget | `pending-assessment-signoff` | 總覽 |
| PatientBanner（`patient-summary` 的 Banner 版） | `patient-summary` | 單一病人（pinned） |
| TreatmentProgressWidget / LatestVitalsWidget / TodayScheduleWidget | 同病人端 | 單一病人 |

> 同一個 `widget_code` 在不同 Shell 可以有不同的呈現（例如 `patient-summary` 在手機是卡片，在桌面是 Banner）。Registry 依 `widget_code + shell` 選擇元件。

### 2.4 Shared UI 元件

| 元件 | Props（概念） | 用在 |
|---|---|---|
| `SectionCard` | variant: info / action-required / critical | 所有 widget 外框 |
| `SectionHeader` | title, meta slot | 所有 widget |
| `IconLabel` | icon, text, size | 行程、導覽 |
| `ProgressArrowBar` | completed, total, labels | 治療進度 |
| `StatusBadge` | status | 問卷（P2）、評估簽署、行程狀態 |
| `ActionTile` | icon, title, subtitle, to | 快速回報、衛教連結（P2） |
| `LiveClock` | serverTime, timezone | 今日行程 |
| `ClinicianTag` | name | 治療進度 |
| `CareAlertChip` | type, text, severity | 病人摘要、Banner |
| `VitalTile` | label, value, unit, flag, measuredAt | 最新生命徵象 |
| `TrendChart` | series, cycleMarkers | 症狀 / 生命徵象趨勢（兩支 trends API 格式相同） |
| `DynamicFormField` | definition（value_type、min/max、options） | 症狀表單：依 `GET /api/v1/symptoms/forms/{code}` 動態產生 |

### 2.5 WidgetFrame 統一契約

| 狀態 | 處理 |
|---|---|
| loading | Skeleton |
| empty | 空狀態文字（「今天沒有行程」） |
| error | 錯誤訊息 + 重試，不影響其他 widget |
| forbidden（403 / 404） | 不渲染 |
| unknown widget | `widget_code` 不在 Registry 中：顯示「此元件需要更新版本」佔位卡片 |
| auto refresh | 依 Layout JSON 的 `refresh_interval_sec` 重新取資料；頁面不在前景時暫停 |

---

## 3. Component ↔ Database Table 對照（DB v3.1）

### 3.1 病人首頁

| UI 元素 | Widget / API | Tables（v3.1） | Phase |
|---|---|---|---|
| 機構 / 科別名稱 | `GET /api/v1/settings/public` | Phase 1–2：後端設定檔；Phase 3：`institution_settings` | 1 |
| 姓名 / 年齡 / 性別 / 代碼 | `patient-summary` | `patient_profiles`（`display_name`、`date_of_birth` → 年齡、`gender`、`patient_code`） | 1 |
| 過敏、肢體限制 | `patient-summary` | `patient_care_alerts` | 1 |
| 今日行程 | `today-schedule` | `appointments` | 1 |
| 檢查前準備（空腹、報到） | `today-schedule` | `appointment_instructions`（`is_highlighted`） | 1 |
| 請假專線 | `today-schedule`（`quick_contact`） | Phase 1–2：設定檔；Phase 3：`institution_settings` | 1 |
| 即時時鐘 | `today-schedule`（`server_time`） | — | 1 |
| 診斷名稱 | `treatment-progress` | `cancer_diagnoses` → `cancer_types` | 1 |
| Cycle 進度 | `treatment-progress` | `chemotherapy_plans.total_cycles` + `chemotherapy_cycles`（status 計數） | 1 |
| 目前 cycle day / 骨髓抑制期 | `treatment-progress` | `chemotherapy_cycles`（`actual_start_date`、`nadir_start_day`、`nadir_end_day`） | 1 |
| 主治醫師 | `treatment-progress` | `chemotherapy_plans.attending_physician_name` | 1 |
| 免責說明 | `treatment-progress`（`disclaimer_key`） | 設定檔 / `institution_settings` | 1 |
| 今日是否已回報症狀 | `symptom-quick-report` | `symptom_forms`、`symptom_records` | 1 |
| 最新生命徵象與標色 | `latest-vitals` | `vital_signs`；門檻來自設定檔（Phase 3：`institution_settings`） | 1 |
| 最新提醒 | `my-notifications` | `notifications` | 1 |
| 照護主題：衛教連結 | `care-topics` | `education_materials` | 2 |
| 照護主題：問卷與開放時段 | `care-topics` / `GET /api/v1/symptoms/questionnaires` | `symptom_forms`（`availability=scheduled`）、`symptom_form_schedules`、`symptom_records` | 2 |
| 照護主題依處方出現 | Layout（`scope=regimen`）或 `symptom_form_targets` | `dashboard_layouts.regimen_id`、`symptom_form_targets` | 2 |

### 3.2 其他頁面

| 頁面 / 元素 | API | Tables | Phase |
|---|---|---|---|
| 症狀回報表單 | `GET /api/v1/symptoms/forms/{code}`、`POST /api/v1/symptoms/records` | `symptom_forms`、`symptom_form_items`、`symptom_definitions`、`symptom_definition_options` → `symptom_records`、`symptom_record_values`、`symptom_record_value_options`、`idempotency_records` | 1 |
| 生命徵象填寫 | `POST /api/v1/vital-signs`、`GET /api/v1/vital-signs/reference-ranges` | `vital_signs`、`patient_care_alerts`（肢體限制提示）、`idempotency_records` | 1 |
| 趨勢圖 | `GET /api/v1/symptoms/trends`、`GET /api/v1/vital-signs/trends` | `symptom_record_values`、`vital_signs`、`chemotherapy_cycles` | 1 |
| 通知列表 | `GET /api/v1/notifications` | `notifications`、`alert_rules` | 1 |
| 護理評估（護理端） | `/api/v1/nursing-assessments` | `nursing_assessments`、`nursing_assessment_items`、`vital_signs`、`symptom_records` | 1 |
| 資源（衛教） | `GET /api/v1/education/materials` | `education_categories`、`education_materials`、`material_cancer_types` | 2 |
| Dashboard 拖拉編輯（護理端） | `PUT /api/v1/dashboard/layout` | `dashboard_layouts`、`dashboard_layout_items`、`dashboard_widgets` | 2 |

### 3.3 未納入 DB v3.1 的功能

| 功能 | 參考圖位置 | 狀態 |
|---|---|---|
| 拍照評估（皮膚、口腔黏膜） | 底部導覽「皮膚炎評估」 | 未規劃；需要附件表並另做隱私與權限設計 |
| 聊天室 | 底部導覽「聊天室」 | 未規劃；牽涉即時通訊與回覆時效責任 |

---

## 4. Dashboard Layout API 與動態元件

### 4.1 渲染流程

```
登入 / 進入首頁
  → GET /api/v1/dashboard/layout?context=overview
  → 檢查 schema_version（不支援 → 降級畫面）
  → 渲染 pinned[]（固定區）
  → 依 items[].position 排序並渲染 items[]
      每個 item：
        Component Registry[widget_code] → Vue 元件（找不到 → 佔位卡片）
        WidgetFrame 呼叫 item.data_endpoint（附 patient_id、instance_key）
        將 item.config 傳給元件
        依 refresh_interval_sec 自動更新
```

### 4.2 Layout JSON 契約（摘要，完整定義見 API v1.1 §8.1）

| 欄位 | 前端用途 |
|---|---|
| `schema_version` | 契約版本檢查 |
| `source` | 除錯用：`code_default`（Phase 1）/ `system` / `role` / `regimen` / `user`（Phase 2） |
| `grid` | `columns`、`row_height`、`gap`；病人端 `columns=1` |
| `pinned[]` | 固定區塊（`patient-summary`），不可收合、移動或移除 |
| `items[].widget_code` | 查 Component Registry |
| `items[].data_endpoint` | WidgetFrame 取資料的 API |
| `items[].position` | `{x, y, w, h}`；手機只使用 `y` 排序 |
| `items[].config` | 元件參數 |
| `items[].refresh_interval_sec` | 自動更新間隔 |
| `items[].collapsible` / `removable` | 卡片上的操作按鈕 |
| `permissions` | Phase 1 固定 `can_edit=false`、`can_reorder=false`：**不顯示編輯模式** |

### 4.3 Phase 1 預設版面（由後端程式產生，`source=code_default`）

| 角色 / context | columns | pinned | items（依序） |
|---|---|---|---|
| patient / overview | 1 | `patient-summary` | `today-schedule` → `treatment-progress` → `symptom-quick-report` → `latest-vitals` → `my-notifications` |
| nurse / overview | 12 | — | `unacknowledged-alerts`(w6) + `today-appointments`(w6) → `caseload`(w12) → `pending-symptom-reviews`(w6) + `pending-assessment-signoff`(w6) |
| nurse / patient | 12 | `patient-summary`（Banner） | `treatment-progress`(w6) + `latest-vitals`(w6) → `today-schedule`(w12) |
| admin / overview | 12 | — | `user-overview`(w12) |

### 4.4 Component Registry（前端 widget_code → 元件）

| widget_code | Mobile 元件 | Desktop 元件 | data_endpoint | Phase |
|---|---|---|---|---|
| `patient-summary` | PatientSummaryWidget | PatientBanner | `/api/v1/dashboard/widgets/patient-summary/data` | 1 |
| `today-schedule` | TodayScheduleWidget | 同左 | `/api/v1/dashboard/widgets/today-schedule/data` | 1 |
| `treatment-progress` | TreatmentProgressWidget | 同左 | `/api/v1/dashboard/widgets/treatment-progress/data` | 1 |
| `symptom-quick-report` | SymptomQuickReportWidget | — | `/api/v1/dashboard/widgets/symptom-quick-report/data` | 1 |
| `latest-vitals` | LatestVitalsWidget | 同左 | `/api/v1/dashboard/widgets/latest-vitals/data` | 1 |
| `my-notifications` | MyNotificationsWidget | — | `/api/v1/dashboard/widgets/my-notifications/data` | 1 |
| `caseload` | — | CaseloadWidget | `/api/v1/dashboard/widgets/caseload/data` | 1 |
| `unacknowledged-alerts` | — | UnacknowledgedAlertsWidget | `/api/v1/dashboard/widgets/unacknowledged-alerts/data` | 1 |
| `pending-symptom-reviews` | — | PendingSymptomReviewsWidget | `/api/v1/dashboard/widgets/pending-symptom-reviews/data` | 1 |
| `pending-assessment-signoff` | — | PendingAssessmentSignoffWidget | `/api/v1/dashboard/widgets/pending-assessment-signoff/data` | 1 |
| `today-appointments` | — | TodayAppointmentsWidget | `/api/v1/dashboard/widgets/today-appointments/data` | 1 |
| `user-overview` | — | UserOverviewWidget | `/api/v1/dashboard/widgets/user-overview/data` | 1 |
| `care-topics` | CareTopicWidget | 同左 | `/api/v1/dashboard/widgets/care-topics/data` | 2 |
| `questionnaire-status` | QuestionnaireStatusWidget | — | `/api/v1/dashboard/widgets/questionnaire-status/data` | 2 |
| `education-shortcuts` | EducationShortcutsWidget | — | `/api/v1/dashboard/widgets/education-shortcuts/data` | 2 |
| `system-usage` | — | SystemUsageWidget | `/api/v1/dashboard/widgets/system-usage/data` | 2 |

> Registry 是前端與後端唯一共用的清單：後端的 widget 清單（Phase 1 在程式內，Phase 2 在 `dashboard_widgets`）與前端 Registry 必須使用相同的 `widget_code`。

### 4.5 哪些區塊做成 Widget

| 區塊 | 做成 Widget？ | 理由 |
|---|---|---|
| AppHeader、BottomNavBar | ❌ | Layout 骨架 |
| 病人摘要 | ✅ 但固定在 `pinned` | 有獨立資料來源，但屬於病人安全資訊，不可移動或移除 |
| 今日行程、治療進度、快速回報、最新生命徵象、我的通知 | ✅ | 自成一體、只依賴一個 data endpoint |
| 照護主題（參考圖皮膚炎區塊） | ✅✅（Phase 2） | 版型固定是「衛教＋問卷」，換 `config.topic_code` 就能產生不同主題 |

### 4.6 Phase 1 → Phase 2 的差異

| 項目 | Phase 1 | Phase 2 |
|---|---|---|
| 版面來源 | 後端程式（`source=code_default`） | `dashboard_*` 資料表；載入順序 user → regimen → role → system |
| 拖拉 / 編輯 | 無（`permissions.can_edit=false`） | 護理師可以編輯個人版面（`PUT /api/v1/dashboard/layout`）；病人端仍不開放 |
| 病人首頁照護主題 | 無 | 依處方版面（`scope=regimen`）自動加入 `care-topics` |
| 前端需要修改的部分 | — | 只新增編輯模式 UI 與 Phase 2 元件；**DashboardRenderer、WidgetFrame、既有 widget 都不用改** |

---

## 5. 設計注意事項

| 項目 | 說明 |
|---|---|
| 個資顯示 | 病人端顯示 `display_name` + `patient_code`；不顯示病歷號（`patient_contacts` 屬 Phase 2，且不提供給病人端 widget） |
| 醫療免責 | 進度、風險類元件附上免責文字（`disclaimer_key` 對應設定檔 / `institution_settings`） |
| 時間 | 依 `meta.timezone` 顯示在地時間；LiveClock 用 `server_time` 校正裝置時間 |
| 送出表單（Idempotency） | 症狀、生命徵象表單**每按一次送出產生一個 `Idempotency-Key`**；網路失敗自動重試時沿用同一個 key；使用者改了內容再送出要換新的 key。收到 `Idempotent-Replayed: true` 時照一般成功處理 |
| 送出後的即時回饋 | `POST /symptoms/records`、`POST /vital-signs` 回傳的 `flags` / `triggered_alerts` 要立即以 `critical` / `action-required` 樣式顯示（例如「骨髓抑制期發燒，請立即聯絡醫療團隊」） |
| 肢體限制 | 生命徵象表單中，量血壓部位預設避開 `patient_care_alerts` 標示的肢體 |
| 可近性 | 大字級、高對比、icon + 文字、觸控目標 ≥ 48px；顏色不能是唯一的資訊 |
| 離線 / 弱網 | 快取最後一次成功的 layout 與 `today-schedule` 資料；離線時顯示快取內容並標示「資料時間」 |
