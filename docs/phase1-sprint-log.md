# Phase 1 Sprint Log

Each sprint: design read → implementation → backend tests → mock / API / production / 390px E2E → migration check → docs → regression. Test counts are for the whole suite at the end of the sprint.

## Sprint 1 — 病人與照護團隊管理（Completed 2026-09-29）

- **Implemented**: patient CRUD with system codes `P00001…` (collision retry), login accounts with one-time temporary passwords, first-login password change (`403 PASSWORD_CHANGE_REQUIRED`), care alerts, diagnoses, nurse assignments (admin assigns / ends; ended nurse gets 404 immediately), nurse accounts (admin). Frontend: 病人管理 / 病人與照護團隊 / 病人資料 / 護理師帳號 / 設定新密碼.
- **Migration**: none.
- **Tests**: backend 12 suites / 488 checks; E2E dev 12 / 335, prod 13 / 356.
- **Known limitations / decisions pending**: a nurse-created patient is visible to that nurse only after an admin assigns them; nurse temporary passwords are system-generated (not admin-chosen); no password reset / account disable yet (Auth Hardening); access tokens stay valid until expiry after a password change.

## Sprint 2 — 化療療程與給藥（Completed 2026-09-29）

- **Implemented**: drugs / regimens (admin writes), plans (auto-scheduled cycles), cycle start / complete / delay, discontinue, medication records with Idempotency-Key, amend (new record, original `amended`) and mark-error, full correction history for staff, patient-safe views. Frontend: `ChemoPanel` in 病人資料, patient 我的療程 (`/patient/treatment`), link from 化療進度. Mock: `src/mock/chemotherapy.js` feeds the mock timeline / dashboard.
- **Design deviation (per product instruction)**: starting a cycle does **not** recalculate existing records' `cycle_day`; only records written afterwards belong to the new cycle.
- **Integration**: Dashboard treatment-progress and caseload, Patient Timeline (cycle start / end, final medications) and Risk Engine (nadir window, cycle of new records) use the new data without logic changes.
- **Fixed along the way**: staff timeline showed person objects (`administered_by`, `assessed_by`) as raw JSON in API mode; mock lab history failed for patients created in mock mode.
- **Migration**: none (correction reasons are kept in `audit_logs`).
- **Tests**: backend 13 suites / 568 checks; E2E dev 13 / 381, prod 14 / 402.
- **Known limitations**: infusion appointments on plan creation and appointment rescheduling on cycle delay come with Sprint 3; mock seeded data is anchored to 2026-09-24 while chemo pages compute days from today.

## Sprint 3 — 治療行程（Completed 2026-09-29）

- **Implemented**: appointments with preparation steps (create / edit / check-in / complete / cancel with reason / reschedule creating a new linked row), patient 今日行程 status + reminders, nurse today list (`today-appointments` widget, now used by the nurse overview in API mode), timeline `APPOINTMENT` events (8th type; the 7 existing types unchanged), plan infusion appointments and cycle-delay rescheduling (Sprint 2 known limitation closed). Frontend: `AppointmentsPanel`, 接下來的行程 in 我的療程, appointment rendering in the timeline, status badge in 今日行程. Mock: `src/mock/appointments.js`.
- **Audit gap fixed during the sprint**: appointments created automatically (plan infusions, rows created by a cycle delay) now each get their own CREATE entry.
- **Tests updated for the new event type**: the backend and E2E timeline checks expected exactly 7 event types; once the seed appointment's time passes an APPOINTMENT event appears, so they now require the 7 original types and allow APPOINTMENT.
- **Migration**: none.
- **Tests**: backend 14 suites / 616 checks; E2E dev 14 / 419, prod 15 / 440.
- **Known limitations**: check-in / completion times are only in the audit log; no `no_show` endpoint.

## Sprint 4 — 護理評估（Completed 2026-09-29）

- **Implemented**: nursing assessment drafts (author-only edit / sign), sign lock (`422 RECORD_LOCKED`), corrections as new draft versions (original `amended` on signing; `/versions` chain), item status follow-up, nurse's own list (drafts to sign), idempotent create / amend, audit (`CREATE` / `UPDATE` / `SIGN` / `AMEND`). Frontend: `AssessmentsPanel` in 病人資料, 護理評估 page (`/nurse/assessments`, replaces the 開發中 nav item). Mock: `src/mock/nursing.js` now feeds the mock timeline and nurse view; the mock symptom review creates a signed assessment like the API.
- **Integration**: timeline privacy (patients: signed assessments as a neutral line only), nurse-view drafts / latest assessment, Risk Engine assessment rule — all existing logic, unchanged.
- **Migration**: none.
- **Tests**: backend 15 suites / 660 checks; E2E dev 15 / 456, prod 16 / 477.
- **Known limitations**: vital signs / symptom records cannot be attached when creating an assessment (record them separately); the Risk Engine uses the latest final assessment including drafts (existing definition, not changed).

## Sprint 5 — 紀錄修正與待審清單（Completed 2026-09-29）

- **Implemented**: amend / mark-error / history for symptom records, vital signs and lab results (through each module's own create path; cycle, author, source and review state kept; original `amended` / `entered_in_error`), reconciliation of the original's open alerts (closed when no longer supported or re-raised; kept when still valid under cooldown; all closed on mark-error), idempotent amend, staff vitals list, abnormal labs list, cross-patient pending symptom reviews. Frontend: 待審清單 (`/nurse/reviews`, replaces the last 開發中 nurse nav item), 紀錄修正 panel. Mock: `src/mock/records.js` over the existing mock stores; mock patient symptom submissions now reach the nurse lists.
- **Fixes found by the tests**: the symptom record payload lacked `amends_id`; mock vital payloads lacked `pain_score`; correcting a report made before a question became required failed (the required check is skipped for corrections).
- **Migration**: none.
- **Tests**: backend 16 suites / 706 checks; E2E dev 16 / 488, prod 17 / 509.
- **Known limitations**: patients cannot correct their own reports yet; mock vital submissions still do not create nurse notifications (pre-existing).

## Sprint 6 — 病人端完整功能（Completed 2026-09-29）

- **Implemented**: `GET /notifications/unread-count`, `POST /notifications/read-all` (own, currently visible notifications only). Patient pages 症狀回報 (today's report + 我的回報), 通知 (unread count, open → read, 全部已讀, detail with handling status), 我的 (profile, care alerts, hospital contacts, 修改密碼 with 返回, 登出); every patient bottom-nav item is now a real page. Mock: `src/mock/patientNotifications.js`; mock symptom records get the patient view for patients.
- **Privacy**: no nurse names, internal notes, SOAP or internal risk on patient pages (checked in backend and E2E).
- **Migration**: none.
- **Tests**: backend 17 suites / 721 checks; E2E dev 17 / 523, prod 18 / 544 (390px checks in `portal`).
- **Known limitations**: the patient profile is read-only (changes through the nurse); no polling of the unread count yet (refreshed on page load / actions).

## Sprint 7 — 管理者後台（Completed 2026-09-29）

- **Implemented**: `GET /admin/overview`; `GET/POST /admin/users` for every role (`role`, `status`, `q`; create nurse or admin), `GET/PATCH /admin/users/{id}` (disable / enable — tokens stop working at once —, unlock, display name, nurse profile; cannot disable yourself or the last active admin; role / email / password cannot be changed here), `GET /admin/audit-logs` (filters, paging; the search itself is audited), `GET /admin/settings` (read-only, config file), alert rules (`GET/POST /notifications/alert-rules`, `GET/PATCH …/{id}`, `POST …/{id}/test` dry run), symptom form composition (`GET /symptoms/forms`, `PUT /symptoms/forms/{code}`, version +1). Frontend: 管理總覽 (`/admin`, the admin home), 帳號狀態, 稽核紀錄, 風險規則與量表, 系統設定; StaffNav. Mock: `src/mock/admin.js`, `src/mock/alertRules.js` (the mock symptom / vital / lab checks read the editable thresholds).
- **Decisions**: admins do not get role changes (the design table lists 改角色; not offered, so an admin cannot grant themselves clinical access). Rule / form changes apply to new records only; existing alerts and records are not recalculated (Risk Engine unchanged). The mock audit log only records the mock admin console's own actions.
- **Tests updated**: `patients.e2e` expected the admin to land on 病人與照護團隊; the admin home is now 管理總覽.
- **Migration**: none.
- **Tests**: backend 18 suites / 764 checks; E2E `admin` 75 checks (full regression: see Sprint 8).

## Sprint 8 — 手動 / 排程提醒（Completed 2026-09-29）

- **Implemented**: `POST /notifications` (reminder; nurse for assigned patients, admin; `scheduled_for` optional, 1 min – 1 year ahead; Idempotency-Key), `GET /notifications/scheduled?patient_id=` (staff; not yet due), derived `origin` = `manual` / `scheduled` / `alert_rule` (the requirement's `source`; the name `source` was already the trigger record) and `scheduled_for` on every notification. Reminders follow the same lifecycle as risk alerts (new → acknowledged → in_progress → resolved) on the staff side; quick resolve stays alert-only; the default notification list and the timeline still show risk alerts only. Patients see a reminder only once it is due, without handling details. Frontend: `RemindersPanel` on the patient page. Mock: `src/mock/reminders.js`.
- **Known limitations**: a scheduled reminder cannot be cancelled or edited before it is sent (needs a new column, not planned in the design); no background job / push — visibility is decided at query time.
- **Migration**: none.
- **Tests**: backend 19 suites / 805 checks (+ `test_reminders` 43); E2E `reminders` 45 checks; full regression after all three: backend 20 suites / 836 checks, E2E dev 20 suites / 679 checks, prod 21 suites / 700 checks (before the refresh-token work).

## Authentication Hardening（Completed 2026-09-29）

- **Implemented**: server-side sessions on the existing `auth_tokens` schema (one family per sign-in, hashed refresh row, 14 days); access tokens carry `sid` and are accepted only while the session is active. `POST /auth/logout` (server-side), `GET /auth/sessions`, `DELETE /auth/sessions/{id}`, `POST /auth/sessions/revoke-others`; password change ends every other session (current one continues); admin `GET /admin/users/{id}/sessions`, `POST …/revoke-sessions` (force sign-out), `POST …/password-reset` (new temporary password shown once, first-login flow, lockout cleared, sessions ended); disabling an account ends its sessions and re-enabling does not revive them; `active_sessions` on the admin account list. Frontend: 登入裝置 (`/account/sessions`), 登出 calls the server, admin 強制登出 / 重設密碼. Mock: mock sessions (token carries the session id; ended sessions → 401).
- **Audit**: LOGOUT; UPDATE users with `sessions_revoked` / `forced` / `password: "reset"`; never a password, temporary password or token.
- **Fixed during the work**: timeline store no longer throws when a timeline request finishes after sign-out (`$reset`) — found by the prod E2E run.
- **Refresh token + same-origin (decision: same-origin proxy)**: `POST /auth/refresh` with the `refresh_token` cookie (HttpOnly, Secure, SameSite=Strict, Path=/api/v1/auth, host-only; never in a body or web storage), rotation within the session (absolute 14-day expiry), replay detection (whole session revoked), `X-Requested-With` required. Login sets the cookie; logout works with an expired access token or the cookie alone and clears it. Frontend: proactive refresh, 401 TOKEN_EXPIRED → single-flight refresh → the original request is retried, router refreshes before sign-in. Same-origin: API base URL is the relative `/api/v1` (build refuses a cross-origin URL); Render Static Site rewrites `/api/*` to the API service; the E2E static server does the same. Mock: tokens carry the mock session and expiry (`mockRefresh`).
- **Working-tree integrity check (before the change)**: no truncated or non-UTF-8 files; the two 0-byte files are empty package `__init__.py` markers; three backend files had mixed CRLF/LF endings (appended blocks) and were normalised to CRLF without content change; all Python compiles, all E2E scripts pass `node --check`, the production build succeeds.
- **Tests changed because the required behaviour changed**: `auth.e2e` step 8 (an expired access token no longer signs out — it refreshes; signing in again is required once the session is over), `deploy.e2e` (API calls must now stay on the frontend origin instead of going to a separate API origin), `test_sessions` (a repeated logout with a dead token now returns 204 and ends nothing, because logout must work after the access token expired).
- **Pending**: public `password-reset/request` / `confirm` (no email / SMS delivery channel in Phase 1; admin password reset covers the need).
- **Migration**: none (`auth_tokens` sufficient, also for rotation). Suggested later: an index on `auth_tokens (user_id, family_id)` — every API request looks the session up.
- **Tests**: backend 21 suites / 869 checks (`test_sessions` 29, `test_refresh` 33); E2E `sessions` 34, `refresh` 27 checks; full regression: E2E dev 21 suites / 708 checks (mock 347, API 335), prod 22 suites / 730 checks (incl. `deploy` 22).
