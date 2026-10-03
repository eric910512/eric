# Cancer Care Platform — Frontend

Vue 3 + Vite + Vue Router + Pinia + Axios + Tailwind CSS v4。

有兩種模式：

| 模式 | 設定 | 說明 |
|---|---|---|
| 示範模式（預設） | `VITE_USE_MOCK=true` 或不設定 | 全部使用假資料（`src/mock/`），登入也是假的，不需要後端 |
| API 模式 | `VITE_USE_MOCK=false` | 所有已實作的功能都呼叫後端 `/api/v1`（Vite 轉到 `http://127.0.0.1:5000`） |

## 執行

需要 Node.js 20.19 以上。

```bash
cd frontend
npm install
npm run dev
```

開啟 <http://localhost:5173>，會先進入登入頁。登入後依角色導向：病人 → `/patient`，護理師 → `/nurse`。

測試帳號（密碼皆為 `Demo@1234`；開發模式下登入頁有快速填入按鈕）：

| 帳號 | 角色 | 示範模式 | API 模式 |
|---|---|---|---|
| `patient01@demo.local` | 病人 | P00001：一般狀況 | 後端 seed 的 P00001 |
| `patient02@demo.local` | 病人 | P00002：骨髓抑制期發燒（高風險示範） | 無此帳號 |
| `nurse01@demo.local` | 護理師 | 4 位假病人 | 後端 seed：負責 P00001 |

API 模式啟動方式（後端需先 `flask --app run db upgrade` 與 `flask --app run seed dev`）：

```bash
# 終端機 1（backend/）
flask --app run run --port 5000
# 終端機 2（frontend/）— Windows PowerShell 用 $env:VITE_USE_MOCK="false"; npm run dev
VITE_USE_MOCK=false npm run dev
```

其他指令：`npm run build`（輸出到 `dist/`）、`npm run preview`。

### 環境變數（建置時寫入，見 `.env.example`）

| 變數 | 說明 |
|---|---|
| `VITE_USE_MOCK` | `false` = API 模式。API 模式的 build **不包含任何 mock 程式或示範資料**（mock 只透過 `loadMock()` 動態載入，API build 會移除） |
| `VITE_API_BASE_URL` | 通常不設定：API 固定走**同源**相對路徑 `/api/v1`（`npm run dev` 由 Vite proxy 轉到 `http://127.0.0.1:5000`；production 由 Render Static Site 的 `/api/*` rewrite 轉給後端）。若設定只能是 `/` 開頭的路徑，跨網域網址會讓 build 失敗（refresh cookie 必須是第一方 cookie） |
| `VITE_API_TIMEOUT_MS` | 請求逾時，預設 10000；部署到會休眠的主機時設 60000 |

部署（Render Static Site：`/api/*` rewrite 到 API 服務、其他路徑 rewrite 到 `index.html`）見專案根目錄 `DEPLOYMENT.md`。

### 登入與 session

- Access token（15 分鐘）與使用者資料存在分頁的 `sessionStorage`；**refresh token 是後端設定的 HttpOnly cookie**（`SameSite=Strict`、`Path=/api/v1/auth`），JavaScript 讀不到，也不會出現在任何回應內容或 web storage。
- 到期前自動 refresh（`stores/auth.js` 的 `scheduleRefresh`）；API 回 `401 TOKEN_EXPIRED` 時 `api/client.js` 的 interceptor 先呼叫 `POST /auth/refresh`（同時只有一個），成功後重送原本的 request；換頁時已過期則 router 先 refresh。refresh 失敗（登出、被結束、改密碼、管理者撤銷、session 到期）→ 登入頁「登入已逾時」。
- 「登出」呼叫 `POST /auth/logout`（結束伺服器端 session、清除 cookie）。
- Mock 模式沒有 cookie：mock token 內含 mock session id 與到期時間，到期時 mock handler 回 `401 TOKEN_EXPIRED`，`api/call.js` 以 `mockRefresh` 換新 token 後重試；session 被結束後回登入頁——行為與 API 模式相同。

機構設定與首頁版面：優先使用 `GET /api/v1/settings/public` 與 `GET /api/v1/dashboard/layout`（mock 模式由 `src/mock/config.js` 回傳相同格式）；請求失敗（網路錯誤、5xx 等）時才使用內建副本 `src/config/defaults.js`，頁面照常顯示。

## 結構

```
src/
├── components/          Dashboard 元件（每個對應一個 widget）
│   ├── PatientSummary.vue      病人摘要（card / banner 兩種樣式）
│   ├── RiskSummary.vue         病人：今日健康風險摘要
│   ├── TodaySchedule.vue       今日行程、報到提醒、請假專線
│   ├── TreatmentProgress.vue   化療進度與 Cycle 日程條（注射日、骨髓抑制期、今天）
│   ├── LatestVitals.vue        最新生命徵象（依 API 回傳的 flag 標色）
│   ├── LabSummary.vue          病人：最近檢驗結果（白話名稱、正常 / 偏低 / 過低、注意事項）
│   ├── LabPanel.vue            護理師：檢驗數據（參考範圍、判讀、趨勢、警示狀態、歷次紀錄、登錄表單）
│   ├── SymptomTrend.vue        症狀趨勢折線圖（SVG，沒有紀錄的日子留空）
│   ├── NotificationCard.vue    我的通知
│   ├── SymptomQuickReport.vue  今天是否已回報＋每日症狀回報表單（疼痛、噁心、疲倦、發燒）
│   ├── SymptomField.vue        依題目定義產生的題目元件（0–10 分量表、是 / 否）
│   ├── NurseNotificationList.vue  護理師通知列表（未處理 / 已處理、標示已讀、處理）
│   ├── NotificationDetail.vue  通知中心：單則通知內容與下一步動作
│   ├── NurseNav.vue            護理端側欄導覽
│   ├── StaffNav.vue            管理端導覽
│   ├── AppointmentsPanel.vue   治療行程（護理師：新增 / 修改 / 報到 / 完成 / 改期 / 取消）
│   ├── AssessmentsPanel.vue    護理評估（草稿 / 簽署 / 修正 / 版本紀錄 / 項目狀態）
│   ├── RecordsPanel.vue        紀錄修正（症狀 / 生命徵象 / 檢驗：更正、標示錯誤、修正歷史）
│   ├── ChemoPanel.vue          化療療程（護理師：療程 / Cycle / 給藥登錄 / 更正；管理者唯讀）
│   ├── OneTimePassword.vue     初始密碼（只顯示一次，不寫入 store / storage）
│   ├── PatientTimeline.vue     照護時間軸（病人端與護理端共用）
│   ├── HealthRecordsTabs.vue   病人「健康紀錄」分頁（照護時間軸 / 記錄生命徵象）
│   ├── SymptomReviewPanel.vue  症狀審閱（待審閱 / 已審閱）
│   ├── SymptomReviewCard.vue   單筆症狀回報的審閱卡片
│   ├── NoteComposer.vue        處理說明輸入框＋常用句
│   ├── VitalAbnormalList.vue   護理師：生命徵象異常列表（可直接處理警示）
│   ├── VitalInput.vue          生命徵象數值輸入（單位、錯誤、即時範圍提示）
│   ├── PatientBottomNav.vue    病人端底部導覽
│   ├── WidgetFrame.vue         所有 widget 共用外框（loading / empty / error）
│   └── AppIcon.vue             圖示（一律搭配文字）
├── views/
│   ├── PatientDashboard.vue    病人首頁（依 Layout JSON 渲染）
│   ├── NurseDashboard.vue      護理端總覽
│   ├── NotificationCenter.vue  護理端通知中心（/nurse/notifications）
│   ├── VitalSignsEntry.vue     病人：記錄生命徵象（/patient/vitals）
│   ├── CareTimeline.vue        病人：照護時間軸（/patient/timeline）
│   ├── PatientList.vue         病人管理（護理端 /nurse/patients）/ 病人與照護團隊（管理端 /admin/patients）
│   ├── PatientDetail.vue       病人資料、注意事項、診斷、照護團隊（管理者指派 / 結束）、登入帳號
│   ├── PatientSymptoms.vue     病人：症狀回報（/patient/symptoms）
│   ├── PatientNotifications.vue 病人：通知（/patient/notifications）
│   ├── PatientProfile.vue      病人：我的（/patient/me）
│   ├── MyTreatment.vue         病人：我的療程（/patient/treatment）
│   ├── ReviewQueue.vue         護理師：待審清單（/nurse/reviews）
│   ├── NursingAssessments.vue  護理師：我的待簽署草稿與最近簽署（/nurse/assessments）
│   ├── AdminHome.vue           管理端：管理總覽（/admin，管理者首頁）
│   ├── AdminAccounts.vue       管理端：帳號狀態（/admin/accounts：停用 / 啟用 / 解除鎖定、新增管理者）
│   ├── AdminAudit.vue          管理端：稽核紀錄（/admin/audit）
│   ├── AdminRules.vue          管理端：風險規則與症狀量表（/admin/rules）
│   ├── AdminSettings.vue       管理端：系統設定（/admin/settings，唯讀）
│   ├── AdminNurses.vue         管理端：護理師帳號（/admin/nurses）
│   ├── ChangePassword.vue      設定新密碼（/change-password，首次登入必經）
│   ├── Login.vue               登入
│   └── ComingSoon.vue          尚未開發的功能
├── dashboard/registry.js       widget_code → 元件（Component Registry）
├── mock/                       假資料，格式與後端 API 回應相同
├── stores/auth.js              登入狀態
├── stores/dashboard.js         Dashboard 資料（mock 或 API）
├── stores/timeline.js          照護時間軸分頁資料
├── stores/nurse.js             護理師通知、症狀審閱、檢驗資料與登錄
├── stores/patients.js          病人與照護團隊管理（mock 或 API，錯誤一律為 { status, code, message, details }）
├── stores/appointments.js      治療行程
├── stores/patientPortal.js     病人：通知、我的回報、個人資料
├── stores/records.js           紀錄更正與待審清單
├── stores/nursing.js           護理評估
├── stores/chemotherapy.js      化療療程、Cycle、給藥紀錄
├── api/call.js                 store 共用的 API / mock 呼叫（錯誤格式一致）
├── utils/password.js           密碼規則（與後端相同，最終由後端判斷）
├── api/client.js               Axios（baseURL `/api/v1`，自動帶 JWT）
├── utils/format.js             時間 / 日期格式（依病人時區 Asia/Taipei）
└── styles/main.css             Tailwind 與設計 token
```

## 與設計文件的對應

- 病人首頁由 `src/config/defaults.js` 的 Layout JSON 渲染，格式同 `GET /api/v1/dashboard/layout`（`docs/api-design.md` §8.1）。元件用 `widget_code` 對應，未知的 `widget_code` 會顯示「需要更新」的佔位卡片，不會讓整頁壞掉（`docs/ui-architecture.md` §4）。
- `src/mock/patientDashboards.js` 的資料格式與 `GET /api/v1/dashboard/patient/<patient_id>` 回傳的 `data` 相同。
- 語意色：藍色＝一般資訊、黃底紅字＝需要病人行動、紅色＝危急、綠色＝完成；淡紫色只用於骨髓抑制期。

## 認證

- `src/stores/auth.js`：登入 / 登出，token 與使用者資料存在 sessionStorage（重新整理不會登出，關閉分頁即清除）。登出時會一併清空所有已載入的病人資料。
- `src/api/client.js`：Axios 自動加上 `Authorization: Bearer <token>`；API 回 401（token 過期或無效）時自動登出並回到登入頁。
- `src/router/index.js`：未登入導向 `/login?redirect=…`；登入後若 redirect 頁面不屬於自己的角色，改導向角色首頁；病人不能進 `/nurse`，護理師不能進 `/patient`。
- 尚未實作 refresh token：access token 15 分鐘後過期，需要重新登入。
- **首次登入**：`user.must_change_password` 為 true 時，router 只允許 `/change-password`；API 回 `403 PASSWORD_CHANGE_REQUIRED` 時也會導向該頁。設定完成後回到角色首頁。管理者首頁為 `/admin`（管理總覽）。

## 症狀回報

病人首頁「今天的症狀」卡片按「開始回報」後，會依 `GET /api/v1/symptoms/forms/daily_chemo_check` 產生表單。

- 回答「有發燒或畏寒」時，**送出前**就會顯示緊急提醒與撥打照護專線按鈕。
- 送出後顯示結果與觸發的提醒（危急提醒附撥號按鈕），並重新載入 Dashboard（今日已回報、症狀趨勢、通知）。
- 每次送出產生一個 `Idempotency-Key`；網路失敗重試時沿用同一個 key，修改答案後才換新的 key，因此重送不會產生重複紀錄。
- 示範模式下，送出會直接更新瀏覽器記憶體中的假資料（重新整理或換帳號後會還原）。

## 護理師審閱流程

護理端首頁：

- **未處理通知**：所有負責病人的風險警示，顯示目前狀態（已接手 / 處理中與負責人）。可「標示已讀」、按「處理」直接結案（處理說明為內部紀錄），或「在通知中心開啟」走完整流程；「已處理」分頁顯示處理人、時間與說明。
- **症狀審閱**（選取病人後）：「待審閱」卡片顯示每筆回報的分數（7 分以上標示）、是否發燒、觸發的警示；按「審閱」填寫處置說明、選擇電話追蹤 / 一般追蹤、可選 CTCAE 分級，並可一併把這筆回報的警示標為已處理。「已審閱」分頁顯示審閱人、時間與處置說明。
- 處理或審閱後，通知列表、個案清單、風險判斷都會重新載入。

## 通知中心

- **護理端「通知與警示」**（`/nurse/notifications`，側欄或首頁警示數量進入）：分頁「待處理」（待處理 + 已接手）、「處理中」、「已完成」，可依優先程度（危急 / 注意）和病人篩選；篩選與選取的通知記在網址上。
- 每筆通知顯示危急 / 注意、病人、觸發原因、時間、目前狀態與負責人，以及下一步按鈕。流程：**查看 → 接手 → 開始處理 → 完成**；完成時必須填寫處理說明（內部紀錄）。
- 通知內容：處理進度（收到通知 → 接手 → 處理中 → 完成，含操作者與時間）、病人資訊（診斷、療程天數、骨髓抑制期、注意事項）、觸發原因與規則、原始的症狀 / 生命徵象 / 檢驗資料、建議處理。
- 手機寬度時先顯示列表，點選後改為顯示內容，可「返回列表」。
- **病人端「我的通知」**：風險提醒下方顯示處理狀態（護理團隊已收到通知 / 護理師已接手 / 護理師正在處理 / 已處理完成）。「今天的健康狀況」也改為只顯示狀態；**不會顯示護理師的處理說明**。
- Mock 模式：`src/mock/nurseReview.js` 依後端規則模擬處理流程（狀態轉換、同步更新病人的通知、nurse-view 與個案清單）。示範資料存在瀏覽器記憶體中，重新整理後會還原。

## 生命徵象

- **病人**：首頁「最新生命徵象」卡片的「記錄生命徵象」，或「健康紀錄」的「記錄生命徵象」分頁，進入 `/patient/vitals`。可填體溫（含量測方式）、血壓（含量測部位）、心跳、血氧、呼吸、體重，以及「稍早量的」時間。輸入時就會依參考範圍提示偏高 / 偏低；體溫 38°C 以上立即顯示緊急提醒與撥號按鈕。有肢體限制時，血壓部位預設避開該側，選到限制側會以紅字提醒。儲存後顯示觸發的提醒，並更新首頁。
- **護理師**：首頁「生命徵象異常」列出負責病人超出範圍的量測（24 小時 / 3 天 / 7 天），標示危急 / 注意與骨髓抑制期；每則警示可直接填處理說明並標為已處理，與「未處理通知」共用同一套流程。

## 風險摘要

- **病人首頁**：病人資料卡下方的「今天的健康狀況」。依 risk engine 的結果顯示「請立即聯絡醫療團隊 / 今天有些狀況需要留意 / 護理團隊已處理 / 今天狀況穩定」，列出今天的症狀回報、生命徵象、提醒與護理師的處理說明，並提供下一步按鈕（撥打專線、跳到症狀回報、前往記錄生命徵象）。
- **護理端**：「我的個案」可選排序：高風險優先（預設）/ 最久未回報 / 病歷代碼；每位病人顯示排名、風險燈號與未處理（危急）警示數。預設打開排名第一的病人。

## 檢驗值

- **病人首頁**：「最近檢驗結果」（在最新生命徵象之後）顯示 30 天內各項最新值，使用白話名稱（例如「嗜中性白血球（抵抗力）」），標示正常 / 偏低 / 過低，異常時附上日常注意事項（例如抵抗力低要避免出入人多的地方、發燒要立即就醫）；有危急值時整張卡片以紅框提醒。不顯示參考範圍數字。
- **護理端病人資料**：「檢驗數據」表格列出 WBC / ANC / Hb / PLT 的最新值、參考範圍與危急值、判讀、採檢時間與療程天數、趨勢小圖（綠色帶為參考範圍）；觸發的警示及處理狀態列在該項目下方，於「未處理警示」清單處理後會顯示處理人與說明。「歷次檢驗」依採檢時間列出每一組結果。
- **登錄檢驗**：填寫採檢時間與有結果的項目，輸入時即時顯示偏低 / 嚴重偏低；超出合理範圍時會顯示在欄位下方。送出時帶 `Idempotency-Key`（重試同一筆不會重複建立），完成後顯示觸發的警示，並更新風險判斷、個案排序與警示清單。
- Mock 模式：`src/mock/labs.js` 依照後端的範圍、判讀、白話說明、ANC 規則與 risk engine 條件模擬（P00001：正常的化療前 CBC；P00002：骨髓抑制期 CBC，ANC 1.2 偏低）。

## 照護時間軸

- **病人**：底部導覽「健康紀錄」預設開啟「照護時間軸」（`/patient/timeline`），上方分頁可切換到「記錄生命徵象」（`/patient/vitals`）。首頁卡片的「記錄生命徵象」仍直接進入量測頁。
- **護理端**：病人資料最下方的「病人照護時間軸」，內容較完整（處理說明、護理評估 SOAP、檢驗參考範圍、建議處理，並可「在通知中心開啟」）。
- `PatientTimeline.vue`：依日期分組（今天 / 昨天 / 日期，並標示療程第幾天），最新在最上面。每類事件使用固定的圖示與標籤：治療、病人回報、量測、檢驗、風險提醒、處理進度、護理處理；顏色只用在嚴重程度（危急 / 注意），化療與處理進度另有固定色。點一下展開詳細內容；內容已完整顯示的事件（例如病人看到的處理進度）不需展開。可依日期篩選，並「載入較早的紀錄」（cursor 分頁）。
- 顯示什麼由 API 決定；病人端不會拿到護理師的內部備註。
- Mock 模式：`src/mock/timeline.js` 依其他示範資料（症狀、檢驗、通知與其處理進度、生命徵象）即時組出時間軸，並套用相同的病人隱私規則。
- 時間格式：API 與 mock 一律使用 ISO 8601 UTC 毫秒格式（`2026-09-25T06:10:00.123Z`）。Mock 由 `src/mock/clock.js` 產生時間（不截到秒），處理步驟與後端相同規則：分開執行的步驟至少相差 1 毫秒，時間相同代表快速處理。排序一律依時間值比較，不用字串比較。

## 病人與照護團隊管理

- **護理師**：側欄「病人管理」（`/nurse/patients`）列出目前指派的病人；可新增病人（代碼由系統產生，可同時建立登入帳號）。新病人要等管理者指派後才會出現在名單。病人資料頁可編輯基本資料、照護注意事項、診斷；照護團隊唯讀。
- **管理者**：`/admin/patients` 看全部病人（可篩選尚未指派）、新增病人；病人資料頁可指派護理師（含主責）與結束指派（需再按一次確認）；`/admin/nurses` 建立護理師帳號。`/admin` 管理總覽；`/admin/accounts` 帳號狀態（停用立即生效、解除鎖定、新增管理者；不能停用自己）；`/admin/audit` 稽核查詢；`/admin/rules` 風險規則（門檻、啟用、試算）與症狀量表（順序、必填）；`/admin/settings` 系統設定（唯讀）。登入裝置：`/account/sessions`（頂端列「登入裝置」、病人「我的」；`src/stores/sessions.js`）；「登出」同時呼叫 `POST /auth/logout`。Mock 登入會建立 mock session（token 內含 session id），結束 session 後 mock 也回 401。提醒：病人資料頁「提醒」（`RemindersPanel`、`src/stores/reminders.js`；mock `src/mock/reminders.js`，員工端資料在 `src/mock/nurseReview.js`，到時間後送進病人通知）。Mock：`src/mock/admin.js`（總覽、稽核、設定、量表；mock 稽核只記錄 mock 管理後台自己的操作）、`src/mock/alertRules.js`（規則；mock 症狀 / 生命徵象 / 檢驗判斷讀取這裡的門檻與啟用狀態）。
- **初始密碼**只在建立後顯示一次（`OneTimePassword.vue`，可複製）；只存在該頁面的區域狀態，按「關閉」或離開頁面就消失，不寫入 store、sessionStorage 或網址。
- **403 / 404**：權限由後端判斷。病人不存在、未指派或指派已結束都顯示「找不到這位病人」（不區分，避免洩漏資訊），不顯示任何快取資料。
- **Mock 模式**：`src/mock/patients.js` 提供相同格式的回應與相同的存取規則（管理者全部、病人本人、護理師僅目前指派），其他 mock（Dashboard、時間軸、檢驗、症狀紀錄、通知）也經由 `src/mock/api.js` 套用同一套規則，錯誤同樣是 `404 NOT_FOUND`。病人、帳號與指派存在該分頁的 sessionStorage（示範用的 mock「伺服器」狀態），同一分頁重新整理後仍保留。Mock 新增的病人會有空白的 Dashboard（與 API 對新病人的回應相同）。

## 化療療程與給藥

- **護理師**：病人資料頁的「化療療程」——建立療程（依處方週期自動排定 Cycle）、開始（Day 1）/ 完成 / 延後 Cycle、登錄給藥、更正與標示錯誤、停止療程。給藥表單每次開啟產生一個 Idempotency-Key：網路重送不會新增第二筆；伺服器回 4xx 後改用新 key。
- **歷史**：更正會新增一筆紀錄，原紀錄保留並標示「已更正」；標示錯誤的紀錄也保留。時間軸只顯示有效紀錄。
- **病人**：首頁「化療進度」→「查看療程與給藥紀錄」（`/patient/treatment`）。
- **Mock**：`src/mock/chemotherapy.js` 與後端相同的規則與錯誤碼（含 Idempotency-Key 行為），並提供 mock 時間軸的 Cycle / 給藥事件與 Dashboard 的 treatment-progress。Known limitation：mock 首頁其他示範資料固定在 2026-09-24，療程頁的 Cycle 天數則依今天計算，兩者可能不同；有療程操作後，mock 首頁的化療進度會改依今天計算。

## 治療行程

- **護理師**：病人資料頁「治療行程」——新增（類型、時間、地點、內部備註、準備事項）、修改、報到（當天）、完成、改期（需原因，原行程保留為「已改期」）、取消（需原因）。護理端總覽的「今日行程」列出負責病人今天的行程。
- **病人**：首頁「今日行程」顯示已報到 / 已完成與黃底準備事項；「我的療程」→「接下來的行程」；時間軸的「行程」事件（不含內部備註）。
- **Mock**：`src/mock/appointments.js`（同樣的狀態規則）同時產生 mock 的今日行程、護理師今日清單與時間軸行程事件。示範病人的「今天」是 2026-09-24（其他 mock 資料的日期），mock 新增的病人使用今天。

## 護理評估

- **護理師**：病人資料頁「護理評估」新增草稿、修改與簽署（只限自己的草稿）；已簽署的評估用「修正」建立新版本（需原因），簽署後取代原評估，原評估保留在「版本紀錄」。問題與措施的狀態可隨時更新。側欄「護理評估」列出自己的待簽署草稿。
- **病人**：看不到 SOAP、草稿或風險，只在時間軸看到「護理師已完成評估」。
- **Mock**：`src/mock/nursing.js`（同樣的規則與錯誤碼），同時提供 mock 時間軸的評估事件與 nurse view（最新評估、待簽署、評估風險）；mock 的症狀審閱也會建立一份已簽署評估（與 API 相同）。

## 紀錄修正與待審清單

- **待審清單**（側欄）：待審症狀、72 小時內異常生命徵象、7 天內異常檢驗、待處理通知，都只含目前負責的病人。
- **紀錄修正**（病人資料頁）：更正會新增一筆並保留原紀錄（可看修正歷史）；標示錯誤的紀錄保留但不再使用。錯誤數值觸發的警示，更正後由伺服器依規則關閉。
- **Mock**：`src/mock/records.js` 以同樣的規則修改各 mock 模組的紀錄（症狀、生命徵象、檢驗），mock 時間軸的量測也改由同一份生命徵象清單產生；mock 中病人送出的症狀回報現在也會出現在護理師的清單中。Known limitation：mock 的生命徵象送出仍不會建立護理師通知（既有限制）。

## 病人端頁面

- 底部導覽的「症狀回報」「通知」「我的」都已完成；首頁通知數字與通知頁一致（已讀 / 全部已讀後同步）。
- 病人只看到自己的資料，不顯示護理師姓名、內部處理說明或評估內容。
- **Mock**：`src/mock/patientNotifications.js` 以病人首頁通知 widget 的項目提供同樣格式的通知 API；mock 的症狀紀錄清單對病人套用與 API 相同的病人視角（不含審閱者與審閱內容）。

## 病人基本資料與 Email 通知（2026-10-02）

- **病人「我的」**（`PatientProfile`）：基本資料——通知 Email（與登入帳號無關）與驗證狀態、「驗證 Email」、身高、新增體重（經 `dashboard.submitVitalSigns` 送 `POST /vital-signs`，帶 Idempotency-Key）、BMI、「接收 Email 通知」（驗證後才能開啟）、「儲存」；「體重紀錄」。驗證連結頁 `VerifyEmail`（`/patient/verify-email#token=…`，需登入，讀到 token 後立即從網址列移除）。Store：`src/stores/patientPortal.js`（`fetchBasic`、`saveBasic`、`fetchWeights`、`requestVerification`、`confirmVerification`）。
- **護理端 / 管理端病人資料頁**：「病人登入帳號」顯示通知 Email（遮罩）、驗證、Email 通知；「提醒與通知」（`RemindersPanel`）的「發送通知」說明病人是否會收到 Email，並在送出後與每則提醒顯示 Email 狀態（與處理狀態分開）。
- **Mock**：`src/mock/profile.js`（與 `app/modules/patient/profile.py`、`delivery.py` 相同的規則、錯誤碼與回應格式；體重來自 mock 生命徵象清單）、`src/mock/email.js`（**MockEmailService**：不寄信，寄件匣存在分頁的 sessionStorage `ccp.mock.email-outbox`，`mockEmailOutbox()` 可讀；收件人 local part 以 `+fail` / `+timeout` 結尾的通知 Email 模擬失敗，與後端 capture provider 相同）。通知 Email 只寄摘要，不含通知內容。Mock 模式永遠視為「有 Email 服務」；API 模式依後端 `EMAIL_PROVIDER`（staging / production 預設 `disabled`：不寄信，畫面顯示「系統目前尚未開放 Email 寄送」）。

## E2E 測試

```bash
npm install
npm run test:e2e          # 開發環境：Vite（mock 與 API 模式）+ Flask + SQLite
npm run test:e2e:prod     # production 模擬：API build + 靜態網站 rewrite + FLASK_ENV=staging + PostgreSQL（PGlite），另含 deploy suite
node e2e/run.mjs labs     # 只跑包含 labs 的 suite 群組
```

- runner 會自行啟動所有伺服器，使用專用 port（mock 5273、API 模式前端 5274、後端 5100、PostgreSQL 55433），不影響平常開著的開發伺服器；每個 suite 群組使用全新、已 seed 的資料庫（SQLite 檔案在 `e2e/.artifacts/`，**不會碰到 `backend/instance/app.db`**）。
- 每個 suite 都同時測試 mock 模式與 API 模式：`auth`、`symptom` + `review`、`vitals` + `risk`、`notifications`、`labs`、`timeline`、`mobile`（390px）、`config`（Sprint 0 契約與 fallback）、`refresh`（Refresh token 回歸，後端 access token 1 分鐘：登入 → cookie 屬性（HttpOnly / Secure / SameSite=Strict / Path / host-only）→ JavaScript 讀不到 → 到期前主動 refresh → 過期 token：換頁先 refresh、request 401 → refresh → 重送成功 → 登出後舊 refresh token 不能用 → 改密碼後其他裝置不能 refresh 並回登入頁 → 管理者撤銷後不能 refresh → 缺 CSRF 標頭被拒；mock 模式同樣流程）、`sessions`（Authentication Hardening：登入裝置清單 → 登出其他裝置（其他 token 立即失效）→ 登出結束伺服器 session → 改密碼結束其他 session → 管理者強制登出、重設密碼（初始密碼一次、首次登入流程）→ 稽核不含密碼 → 390px → mock / API 結構比對）、`profile-email`（病人基本資料 + Email 通知：病人 390px 填 Email / 身高 / 體重 → 儲存 → BMI 23.7 → 驗證 Email（從 mock 寄件匣 / 後端 capture 檔案取連結，API 模式以完整頁面載入開啟連結）→ 連結不能重用 → 開啟 Email 通知 → 護理師看到遮罩 Email → 發送通知 → App 通知 + 摘要 Email → Email 失敗（`+fail`）通知仍送出 → 接手 → 病人收到 App 通知 → 權限（403 / 404）→ API：稽核不含 Email / token、時間軸無新事件、Risk Engine 讀到病人體重 → mock / API 結構比對；`--prod`（沒有 Email 服務）的 API 模式改驗證：無法驗證、寄送 `skipped / not_configured`、App 通知照常）、`reminders`（Sprint 8：護理師寫立即 / 排程提醒 → 病人只看到已到時間的 → 1 分鐘後的排程提醒出現 → 接手 → 開始處理 → 完成（內部說明病人看不到）→ 驗證、權限、稽核 → 390px → mock / API 結構比對）、`admin`（Sprint 7：非管理者 403 → 管理總覽 → 新增管理者 → 停用（不能登入）/ 啟用 / 登入鎖定與解除 → 風險規則門檻變更套用到新紀錄、停用、試算 → 量表順序 / 必填 → 稽核篩選、不含密碼 → 系統設定 → 390px → mock / API 結構比對）、`portal`（Sprint 6：390px 病人端通知 / 症狀回報 / 我的、未讀與全部已讀、隱私、mock / API 結構比對）、`corrections`（Sprint 5：待審清單 → 更正發燒數值 → 修正歷史 → 警示關閉 → Dashboard → 症狀更正 → 檢驗標示錯誤 → 390px → mock / API 結構比對）、`assessments`（Sprint 4：草稿 → 修改 → 簽署 → 風險 → 修正版本 → 項目追蹤 → 病人只看到中性文字 → 390px → mock / API 結構比對）、`appointments`（Sprint 3：新增行程 → 今日清單 → 報到 → 病人今日行程 / 接下來的行程 → 完成 → 改期保留歷史 → 時間軸 → 療程注射行程與延後同步改期 → 390px → mock / API 結構比對）、`chemotherapy`（Sprint 2：建立療程 → 開始 Cycle → 給藥（重送一筆）→ Dashboard / 時間軸 → 更正 / 標示錯誤 → 病人檢視 → 390px → mock / API 結構比對）、`patients`（Sprint 1 完整流程：建立病人 → 初始密碼 → 指派 → 首次登入改密碼 → 症狀回報 → 結束指派 → 原護理師 404 → 新護理師可存取；390px；mock / API 回應結構比對）、`mock-clock`（mock 時間精度），`--prod` 另加 `deploy`（深層連結、同源 `/api` proxy、瀏覽器只連線前端網域、bundle 不含 API 網址）。prod 模擬的靜態伺服器（`e2e/lib/static-server.mjs`）與 `render.yaml` 相同：`/api/*` 轉給後端，其他路徑回檔案或 `index.html`。
- 需要：Chrome（找不到時設定 `CHROME_PATH`）、Python venv（`../venv`，或設定 `PYTHON`）。截圖與 log 在 `e2e/.artifacts/`（已排除於版本控制）。
- 所有伺服器固定綁定 `127.0.0.1`（Vite 的 `localhost` 在部分系統只綁 IPv6 `[::1]`）；每個伺服器 ready 後才開始測試，每個 suite 開始前也會再確認所有伺服器仍在回應，並列出實際的 URL、PID 與 ready 時間。
- 測試不依賴網際網路：E2E 用的 Chrome 會讓本機以外的主機直接解析失敗，外部資源（例如 `index.html` 的 Google Fonts）立即改用系統字型，避免網路或 DNS 異常時頁面一直等待而逾時。
- `--prod` 的 production build 輸出到系統暫存資料夾（不在 Vite 專案目錄內，避免 mock 模式的開發伺服器因檔案變動而重新整理頁面）。
- Email：開發環境的後端使用 `EMAIL_PROVIDER=capture`，每封信寫成 `e2e/.artifacts/email-outbox/*.json`（每個 suite 群組清空）；`--prod` 不設定（`disabled`），不會寄出任何信。
- 單一 suite 也可以手動對已啟動的伺服器執行：`E2E_MOCK_URL`、`E2E_WEB_URL`、`E2E_API_URL`（見 `e2e/lib/env.mjs`）。

## 尚未串接的部分

- 護理端「今日行程」沒有後端 API，API 模式下不顯示。
