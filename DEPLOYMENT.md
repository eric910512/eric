# Deployment — Render staging / demo

目標：在 Render 建立 staging / demo 環境，讓手機、其他電腦、不同網路都能操作 Phase 1。**只使用合成的示範資料，不放真實病人資料。**

目錄：1 架構 · 2 設定檔與指令 · 3 GitHub / GitLab repository · 4 Render Blueprint（建立服務）· 5 環境變數 · 6 API rewrite、service 名稱後綴、PROXY_FIX_HOPS · 7 第一次部署與 smoke test · 8 Seed 策略（關閉 SEED_DEMO_DATA）· 9 Refresh Token / same-origin 注意事項 · 10 Free 方案限制 · 11 啟動安全檢查與 health check · 12 本機 production 模擬 · 13 問題排除

## 1. 架構

```
瀏覽器（手機 / 電腦）
   │ HTTPS，只連線到一個 origin：https://cancer-care-web.onrender.com
   ▼
cancer-care-web   Render Static Site：Vue production build（frontend/dist）
   ├── /api/*  ──rewrite（proxy）──► cancer-care-api   Render Web Service：Flask + Gunicorn（FLASK_ENV=staging）
   │                                    │ internal connection string
   │                                    ▼
   │                                 cancer-care-db   Render PostgreSQL 17
   └── /*      rewrite 到 /index.html（Vue Router history mode；有對應檔案的路徑如 /assets/* 直接回檔案）
```

- **Same-origin**：瀏覽器只跟前端網域溝通。前端呼叫相對路徑 `/api/v1`，由 Static Site 的 `/api/*` rewrite 轉給 API 服務。`onrender.com` 在 Public Suffix List 上，兩個 `*.onrender.com` 服務彼此是 cross-site，所以瀏覽器不直接呼叫 API 網域（詳見 §9）。
- 三個資源在同一個 region（`singapore`，離台灣最近）。資料庫只接受內部連線（`ipAllowList: []`）。
- SQLite（`instance/app.db`）只用於本機開發；staging / production 的 `DATABASE_URL` 不是 PostgreSQL 時會拒絕啟動（Render 的檔案系統每次部署後清空）。

## 2. 設定檔與指令

| 檔案 | 用途 |
|---|---|
| `render.yaml` | Render Blueprint：資料庫、API、靜態網站、環境變數、`/api/*` 與 SPA rewrite、header |
| `backend/requirements.txt` | 後端執行期套件（固定版本，含 `gunicorn`、`psycopg2-binary`） |
| `backend/gunicorn.conf.py` | Gunicorn：`0.0.0.0:$PORT`、gthread、`WEB_CONCURRENCY`（預設 2）× 4 threads、timeout 60 秒 |
| `backend/scripts/render_start.sh` | 啟動：`flask db upgrade` →（`SEED_DEMO_DATA=true` 時）`flask seed dev` → `gunicorn run:app`（LF 換行，`.gitattributes` 固定） |
| `scripts/post-deploy-smoke.mjs` | 部署後從外部驗證（§7） |
| `frontend/.env.example`、`backend/.env.example` | 環境變數說明（不含 secret） |

| | 指令 |
|---|---|
| Backend Build | `pip install -r requirements.txt`（Root Directory：`backend`） |
| Backend Start | `bash scripts/render_start.sh` |
| Frontend Build | `cd frontend && npm ci && npm run build` |
| Frontend Publish Directory | `frontend/dist` |
| Health Check Path | `/api/v1/health` |

Migration 在 start command 執行（Free 方案沒有 pre-deploy command 與 Shell）。Alembic 只套用尚未執行的 migration，不會產生新的 migration。改用付費方案後可把 `flask --app run db upgrade` 移到 Pre-Deploy Command。

## 3. GitHub / GitLab repository

目前專案**不是 git repository**；Render Blueprint 只能從 GitHub / GitLab（或 Bitbucket）repository 部署。

1. 在 GitHub 或 GitLab 建立一個 repository（**建議 private**；雖然只有合成資料，仍包含完整程式與設計文件）。
2. 在專案根目錄（`D:\app`，`render.yaml` 所在位置）初始化並推送：`git init` → `git add .` → `git commit` → 設定 remote → `git push`。`render.yaml` 必須在 repository **根目錄**。
3. `.gitignore` 已排除：`.env`、`venv/`、`node_modules/`、`dist/`、`instance/`、`*.db`、`frontend/e2e/.artifacts/`（E2E 截圖、log、測試資料庫）、`*.log`、`__pycache__/`。會提交的是程式、migration、測試、`render.yaml`、README 與 `docs/`。**提交前請確認 `git status` 沒有 `.env`、`*.db`、`.artifacts`。**
4. Render 需要能讀取該 repository：在 Render Dashboard 連結 GitHub / GitLab 帳號，並授權該 repository。

## 4. Render Blueprint（建立三個服務）

1. Render Dashboard → **New → Blueprint** → 選 repository 與 branch。Render 讀取 `render.yaml`，列出：
   - `cancer-care-db` — PostgreSQL 17（Free），database `cancer_care`，user `cancer_care`。
   - `cancer-care-api` — Web Service（Python 3.11.9、Root Directory `backend`、Health Check `/api/v1/health`）。
   - `cancer-care-web` — Static Site（Node 24、`frontend/dist`、rewrite 與 header 規則）。
2. 輸入 `sync: false` 的變數（§5）：`CORS_ORIGINS`、`SEED_DEMO_PASSWORD`。
3. **Apply**。資料庫先建立，API 取得 `DATABASE_URL` 後 build → start（migration → seed → Gunicorn），Static Site build 後發布。
4. `autoDeploy: false`：之後推送新 commit 不會自動部署，需在 Dashboard 按 **Manual Deploy**。

## 5. 環境變數

**cancer-care-api**

| 變數 | 值 | 說明 |
|---|---|---|
| `FLASK_ENV` | `staging` | 與 production 相同的安全檢查，但允許載入合成示範資料。正式上線用 `production`（禁止示範資料） |
| `PYTHON_VERSION` | `3.11.9` | 與本機相同 |
| `DATABASE_URL` | 由 `cancer-care-db` 自動帶入 | 內部連線字串；`postgres://` 會自動轉成 `postgresql://` |
| `SECRET_KEY`、`JWT_SECRET_KEY` | Render 自動產生 | 兩者都必須 ≥ 32 字元、彼此不同、不能是預設值，否則拒絕啟動 |
| **`CORS_ORIGINS`** | **手動輸入**：前端的實際網址，例 `https://cancer-care-web.onrender.com` | 瀏覽器的 origin（只能 https，不可 `*`，**結尾不要加 `/`**，多個以逗號分隔）。沒設定 API 會拒絕啟動 |
| **`SEED_DEMO_PASSWORD`** | **手動輸入**：自己選的密碼（8–128 字元、含英文字母與數字） | 示範帳號的密碼。網站是公開的，**不要用預設的 `Demo@1234`**，也不要用真實帳號的密碼 |
| `SEED_DEMO_DATA` | `true`（第一次） | 見 §8：驗證完成後改 `false` |
| `PROXY_FIX_HOPS` | `1` | 見 §6 |
| `WEB_CONCURRENCY` | `2` | Gunicorn worker 數 |
| `JWT_ACCESS_TOKEN_MINUTES` | 不設定（預設 `15`） | Access token 有效分鐘數；前端會在到期前自動 refresh |
| **`EMAIL_PROVIDER`** | **手動輸入** `brevo`（啟用 Email 時） | 病人通知 Email。不設定 = `disabled`：不寄任何信，通知的 Email 記為 `skipped / not_configured`，App 通知照常，病人無法完成 Email 驗證。`brevo` = Brevo Transactional Email API。`capture`（開發測試用）在 staging / production 會被拒絕啟動 |
| **`EMAIL_API_KEY`** | **手動輸入**：Brevo API key（secret） | 只在 Dashboard → Environment 輸入；**不要寫進 `render.yaml`、程式、README 或任何檔案**。`EMAIL_PROVIDER=brevo` 時缺少會拒絕啟動 |
| **`EMAIL_FROM`** | **手動輸入**：Brevo 已驗證的寄件者 Email | 寄件者網域 / 地址必須先在 Brevo 驗證，否則 Brevo 拒收（寄送記為 `failed / PROVIDER_REJECTED`）。缺少會拒絕啟動 |
| `EMAIL_FROM_NAME` | 選填（預設「化療照護」） | 寄件者名稱 |
| **`APP_BASE_URL`** | **手動輸入**：前端 https 網址，例 `https://cancer-care-web.onrender.com`（結尾不要 `/`） | Email 驗證連結與通知 Email 的網址；`brevo` 時不是 https 會拒絕啟動 |
| `EMAIL_TIMEOUT_SECONDS` | 選填（預設 `10`） | 每次呼叫 Brevo 的 timeout（寄信在 request 中同步執行，沒有重試） |

**cancer-care-web**（建置時寫入，修改後需要重新部署）

| 變數 | 值 |
|---|---|
| `VITE_USE_MOCK` | `false`（完全使用真實 API；production bundle 不含任何 mock 資料） |
| `VITE_API_TIMEOUT_MS` | `60000`（Free API 休眠後喚醒約需 1 分鐘） |
| `NODE_VERSION` | `24` |
| （不需要 API 網址） | API 為同源 `/api/v1`。**不要設定 `VITE_API_BASE_URL`**；若設定只能是 `/` 開頭的路徑，跨網域網址會讓 build 失敗 |

## 6. API rewrite destination、service 名稱後綴、PROXY_FIX_HOPS

**API rewrite destination**：`render.yaml` 的 `cancer-care-web.routes` 第一條：

```yaml
- type: rewrite
  source: /api/*
  destination: https://cancer-care-api.onrender.com/api/*
```

它必須在 SPA 的 `/*` → `/index.html` 規則**之前**。Render 的 rewrite 不會轉址（網址列不變），而是從 destination 取得內容回給瀏覽器。

**Render 加了後綴時**：服務名稱在 `onrender.com` 必須唯一；若已被使用，Render 會產生例如 `cancer-care-api-x1y2.onrender.com`。建立後到各服務頁面看實際網址：

1. **API 網址不同** → 修改 `render.yaml` 的 destination（或在 Dashboard：cancer-care-web → Redirects/Rewrites 修改該規則），再重新部署 Static Site。
2. **前端網址不同** → 修改 API 的 `CORS_ORIGINS` 為前端的實際網址（Dashboard：cancer-care-api → Environment），API 會重新啟動。
3. 以實際網址重跑 smoke test（§7）。

**PROXY_FIX_HOPS**：Flask 信任的 `X-Forwarded-For` proxy 層數，用於稽核紀錄與「登入裝置」的用戶端 IP。預設 `1`（Render 自己的 proxy）。請求還會先經過 Static Site 的 rewrite，可能多一層。第一次部署後登入，到「登入裝置」看 IP：若顯示的不是自己的 IP（而是 Render 的內部位址），把 `PROXY_FIX_HOPS` 改成 `2`。不要設得比實際層數大（用戶端可偽造多出來的部分）。

## 7. 第一次部署與 smoke test

1. 完成 §3–§5，等三個服務都顯示 **Live**（API 的 log 應看到 `flask db upgrade`、`Seed complete`、Gunicorn 啟動）。
2. 直接檢查：`https://<api>/api/v1/health` 與經過前端的 `https://<web>/api/v1/health` 都應回 `{"database":"ok","status":"ok"}`（後者代表同源 proxy 正常）。
3. 執行 smoke test（本機需要 Node 20 以上，不需要安裝套件）：

   ```bash
   # PowerShell: $env:SMOKE_PASSWORD="<SEED_DEMO_PASSWORD>"; node scripts/post-deploy-smoke.mjs --web https://<web> --api https://<api>
   SMOKE_PASSWORD='<SEED_DEMO_PASSWORD>' node scripts/post-deploy-smoke.mjs --web https://<web> --api https://<api>
   # 加上 --wait-expiry 會等 access token 真的過期（約 15 分鐘）再驗證 refresh 流程
   ```

   驗證（全部經由前端網域）：前端 HTTPS、SPA rewrite、assets、安全 header、bundle 不含 API 網址與 mock 資料；`/api/v1/health`（proxy GET）；登入（proxy POST）；`Set-Cookie: refresh_token` 與其屬性（HttpOnly、Secure、SameSite=Strict、Path=/api/v1/auth、沒有 Domain）；access token；`Authorization` 轉送；refresh（`Cookie` 轉送、輪替、同一 session）與缺 CSRF 標頭被拒；refresh 後的 API；（`--wait-expiry`）過期 → `401 TOKEN_EXPIRED` → refresh → API；登出（POST）與清除 cookie；登出後 refresh token 與 access token 都被拒。
   密碼只從 `SMOKE_PASSWORD` 環境變數讀取；輸出只有檢查名稱、狀態碼、錯誤代碼與 cookie 屬性名稱，**不會印出密碼、access token、refresh token 或完整 cookie**。全部通過時 exit code 0。預設帳號 `patient01@demo.local`（`--email` 可改）；腳本只登入一次，不會觸發登入鎖定。
4. 瀏覽器實際操作：開啟前端網址，用示範帳號（`patient01@demo.local`、`nurse01@demo.local`、`admin01@demo.local`，密碼為 `SEED_DEMO_PASSWORD`）登入；手機也試一次。
5. 若 smoke test 在 proxy 或 cookie 相關項目失敗，見 §13。

## 8. Seed 策略（SEED_DEMO_DATA）

- `SEED_DEMO_DATA=true` **只用於第一次 demo / verification**：每次 API 啟動都會執行 `flask seed dev`，建立 / 更新合成示範資料，並把三個示範帳號的密碼重設為 `SEED_DEMO_PASSWORD`、重新啟用。Free 方案休眠後喚醒也算一次啟動，所以在這段期間做的密碼變更、停用帳號等操作，下次啟動會被示範資料蓋回。
- **smoke test 與瀏覽器驗證完成後，改成 `false`**：Dashboard → cancer-care-api → Environment → `SEED_DEMO_DATA` = `false` → Save（API 會重新啟動，之後不再 seed，已經存在的示範資料與帳號保留）。之後若要重新整理示範資料，再暫時改回 `true`。
- **不要對正式資料執行 seed**：示範資料只用於 staging / demo。`FLASK_ENV=production` 時 `flask seed dev` 會拒絕執行（`ALLOW_DEMO_SEED=False`）；若 production 誤設 `SEED_DEMO_DATA=true`，啟動腳本會因此失敗而不會啟動，不會寫入示範資料。不要把 staging 資料庫拿來放真實病人資料。

## 9. Refresh Token 與 same-origin /api proxy 注意事項

- Refresh token 只存在 cookie：`refresh_token; HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth; Expires=<session 到期>`，**沒有 `Domain`**（只屬於前端網域）。不在任何 response body，也不在 `localStorage` / `sessionStorage`，頁面 JavaScript 讀不到。Access token（15 分鐘）在分頁的 `sessionStorage`。
- 這個設計**依賴 `/api/*` 走前端網域**：Render 的 rewrite 必須原樣轉送請求的 `Authorization` 與 `Cookie`，以及回應的 `Set-Cookie`。Render 文件只說明 rewrite 的 destination 可以是公開網址，沒有說明標頭轉送細節——**請以 §7 的 smoke test 確認**（`Set-Cookie`、`Authorization forwarded`、`Refresh (Cookie forwarded)` 三組）。
- 不要把前端改成直接呼叫 API 網域，也不要改用 `SameSite=None`：兩個 `*.onrender.com` 是 cross-site，Strict cookie 不會送出，而 `None` 會被阻擋第三方 cookie 的瀏覽器拒絕。
- `CORS_ORIGINS` 仍需設為前端網址（production 啟動檢查要求）。同源請求不需要 CORS credentials（`supports_credentials=False`）。
- Session：每次登入是一個獨立 session（14 天，refresh 不會延長）。登出、登出其他裝置、改密碼、管理者強制登出 / 重設密碼 / 停用帳號後，該 session 立即失效且不能再 refresh。
- 自訂網域（之後若使用）：前端與 `/api` 仍在同一個網域即可，cookie 不需要改。

## 10. Free 方案限制

- **Web Service 休眠**：15 分鐘沒有流量會休眠，下一次請求約 1 分鐘才喚醒。前端 timeout 已設 60 秒；smoke test 會等待最多 3 分鐘。第一次開啟頁面若失敗，稍等後重新整理。經過 Static Site rewrite 的請求在 API 喚醒期間是否會先逾時，第一次部署時請觀察。不想等可改 `starter`。
- **PostgreSQL 30 天**：Free PostgreSQL 建立 **30 天後到期**（到期後資料刪除），容量 1 GB。要長期展示請在建立 Blueprint 前把 `render.yaml` 的 `plan: free` 改成付費方案（例如 `basic-256mb`），或到期前在 Dashboard 升級。到期重建時，第一次啟動請暫時把 `SEED_DEMO_DATA` 設回 `true`。
- Free 沒有 Shell、one-off job、persistent disk；示範資料由 `SEED_DEMO_DATA=true` 在啟動時載入。

## 11. 啟動安全檢查與 health check

`create_app()` 發現下列任一情況就拒絕啟動（錯誤訊息不含 secret）：

- `SECRET_KEY` / `JWT_SECRET_KEY` 是預設值、少於 32 字元、兩者相同，或 `JWT_SECRET_KEY` 不在環境變數中
- `DEBUG` 開啟（包含 `FLASK_DEBUG=1`）
- `DATABASE_URL` 不是 PostgreSQL
- `CORS_ORIGINS` 沒設定、含 `*`，或不是 https（本機模擬允許 `http://localhost`）
- `EMAIL_PROVIDER` 是 `capture`（開發測試用）或不支援的值；`EMAIL_PROVIDER=brevo` 時缺 `EMAIL_API_KEY` / `EMAIL_FROM`，或 `APP_BASE_URL` 不是 https

`GET /api/v1/health` → `200 {"status": "ok", "database": "ok"}`；資料庫無法連線時回 `503`，Render 不會把流量導到壞掉的部署。`/api/*` 的 CORS preflight 一律回 200（是否允許由 `CORS_ORIGINS` 決定）。

## 12. 本機 production 模擬

`cd frontend && node e2e/run.mjs --prod`：

- 後端 `FLASK_ENV=staging`、PostgreSQL（PGlite，記憶體資料庫）、隨機 secret、`CORS_ORIGINS` = 前端網址（本機以 Flask 單執行緒伺服器代替 Gunicorn；Gunicorn 不支援 Windows，應用程式進入點 `run:app` 相同）。
- 前端以 `VITE_USE_MOCK=false` production build（bundle 內沒有 API 網址），放在模擬 `render.yaml` 規則的靜態伺服器（`frontend/e2e/lib/static-server.mjs`）：`/api/*` 轉給後端、其他路徑回檔案或 `/index.html`。`deploy` suite 檢查瀏覽器只連線到前端網域，`refresh` suite 檢查 refresh cookie 經過 proxy 設定與送出。
- smoke test 也可以對本機模擬執行：`SMOKE_PASSWORD=Demo@1234 node scripts/post-deploy-smoke.mjs --web http://127.0.0.1:<port> --allow-http`（`--allow-http` 只供本機使用）。

## 13. 問題排除

| 現象 | 可能原因 / 處理 |
|---|---|
| API 部署失敗，log 顯示 `Refusing to start in staging` | 依訊息修正環境變數（多半是 `CORS_ORIGINS` 沒設定、有結尾 `/` 或不是 https） |
| `https://<web>/api/v1/health` 回 HTML 或 404 | `/api/*` rewrite 沒有生效：確認它在 `/*` 規則之前、destination 是 API 的實際網址 |
| `https://<web>/api/v1/health` 逾時 / 502 | API 休眠中（等 1 分鐘再試）或 API 部署失敗（看 API log） |
| smoke test：`Set-Cookie ... reached the browser` 或 `Refresh (Cookie forwarded)` 失敗 | Render rewrite 沒有轉送 cookie 標頭。登入仍可用，但 access token 到期（15 分鐘）後需重新登入。不要改用 `SameSite=None` 或跨網域呼叫；請回報結果再決定處理方式 |
| smoke test：`Authorization forwarded` 失敗 | rewrite 沒有轉送 `Authorization`，所有需登入的 API 都會失敗；請回報結果 |
| 登入回 `401` | 密碼與 `SEED_DEMO_PASSWORD` 不同，或 seed 尚未執行（看 API log 的 `Seed complete`） |
| 「登入裝置」IP 不是自己的 | `PROXY_FIX_HOPS` 改 `2`（§6） |
| 登入後改的密碼 / 停用的帳號被還原 | `SEED_DEMO_DATA` 仍是 `true`（§8） |
