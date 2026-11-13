# Phase 1a～1c 部署前稽核（Pre-deployment Audit）

> 日期：2026-11-12。範圍：Phase 1a（皮膚自我評估）、1b（照片，預設關閉）、1c（護理師追蹤管理）。
> 本次**未**部署、未 commit／push／merge、未連線或修改 Render 與正式 PostgreSQL、未啟用照片功能。
> 標示：**VERIFIED** = 本次以實際證據驗證；**INFERRED** = 依程式碼／設定推論；**UNKNOWN** = 尚無足夠證據（需人工確認）。
> **版本說明（2026-11-12 補充）**：§1～§16 記錄的是當時的完整版本 `db1c422`。之後 `feature/skin-follow-up` 又加入 6 組照片儲存修正（見 §17），migration 沒有變更。現在的「完整版本」＝該分支審查通過時的最新 commit，本文件不寫入其 hash，避免 commit 引用自己。
> 隔離驗證工具：`backend/tests/predeploy/`（`run_isolated_checks.sh`、`baseline_build.py`、`upgrade_path_check.py`；記憶體中的 PGlite PostgreSQL，結束即丟棄）。

## 1. Executive Summary
- **程式與 migration 本身已可部署**（VERIFIED，隔離 PostgreSQL）：由「已部署聊天室版本建立的模擬正式資料」逐步升級 1a → 1b → 1c，受保護資料（使用者、病人、配對、聊天、症狀紀錄、護理評估、每日回報表單）逐位元組不變；無 schema drift；照片在 staging 設定下預設關閉；Phase 1c 流程、聊天室、每日回報、隔離權限都正常。
- **最大風險是「部署後回到舊版程式」**（VERIFIED）：資料庫升級到 Phase 1c 後，舊版（`71e9d8e`）的啟動腳本 `flask db upgrade` 會失敗（`Can't locate revision identified by 'a9d4e2b7c5f1'`），API 無法啟動。已在跑的舊版行程仍可服務，但 Free 方案休眠後喚醒、重啟都會再跑啟動腳本 → 服務中斷（INFERRED）。**「重新部署舊 commit」不是可用的回復方案。**
- **已驗證的替代回復方案**：「橋接版本」= 已部署的聊天室程式 + 只加入三個新 migration 檔。它能在新 schema 上正常啟動（VERIFIED），可作為 schema-first 的第一步與回復目標。
- **目前結論：NO-GO**。阻擋原因不是程式，而是正式環境狀態未驗證：Render 實際部署 commit、正式 Alembic revision、`SEED_DEMO_DATA` 與 Auto-Deploy 現值、備份可還原性皆為 UNKNOWN；橋接版本尚未建立；Phase 1a 臨床文字與 Phase 1c 臨床流程尚未取得院方核可。

## 2. Current Deployment Baseline
| 項目 | 值 | 狀態 |
|---|---|---|
| 本機 `main` / `origin/main` | `71e9d8e`（Merge feature/patient-nurse-chat） | VERIFIED（`git fetch --dry-run` 無更新）|
| Render API 實際執行 commit | 使用者表示聊天室已部署；commit 未查證 | **UNKNOWN**（health API 不回傳版本；本機無 Render CLI / API 金鑰）|
| Render Web 實際執行 commit | 同上 | **UNKNOWN** |
| 正式 Alembic revision | 推論為 `b5cd01c11e1d`（聊天室）| **UNKNOWN**（無法唯讀連線；見第 5 節）|
| 預定部署版本 | `feature/skin-follow-up` = `db1c422`（可 fast-forward 自 `71e9d8e`）| VERIFIED |
| 本機 Migration head | `a9d4e2b7c5f1`（單一 head）| VERIFIED |

## 3. Git Audit（唯讀指令）
- Commit 關係（VERIFIED）：
  ```
  db1c422 Phase 1c ── 54e318f Phase 1b ── 212bc24 Phase 1a ── 71e9d8e main = origin/main（聊天室）
  ```
  merge-base(`main`, `feature/skin-follow-up`) = `71e9d8e`；feature 領先 3 個 commit、落後 0 → 線性，可 fast-forward。Working tree 乾淨（本稽核新增的文件與 `backend/tests/predeploy/` 除外，未 commit）。
- 差異（`git diff --stat 71e9d8e db1c422`）：69 個檔案，+7722 / −44。1a：8 新增、23 修改；1b：19 新增、19 修改；1c：11 新增、21 修改。
- Migration：只新增 `c3e9a1f4b7d2`、`e7a2c4f9d1b3`、`a9d4e2b7c5f1`；**既有 migration 無任何修改**（VERIFIED）。
- 受保護路徑無任何差異（VERIFIED）：聊天室模組／model／migration、admin（含 50 組訓練帳號）、auth、`render.yaml`、`backend/scripts/`（含 `render_start.sh`）、前端聊天室元件。
- Seed：`seeds/dev.py` 只多呼叫 `seed_skin_self_assessment`（upsert 皮膚表單，與 migration 相同資料）。
- 相依套件：`backend/requirements.txt` 新增 `pillow==11.3.0`；未新增 `boto3`；`frontend/package.json` 無變更（VERIFIED）。
- 敏感資訊：無資料庫檔、照片、測試產物、`.env`、金鑰；新增內容中無正式密碼（`.smoke-pass` 比對 0 筆）、Token、身分證號或手機號碼格式（VERIFIED）。`git diff --check` 無問題。
- 新功能皆有測試：`test_skin_self_assessment`、`test_skin_photos`、`test_care_follow_ups`、E2E `skin-assessment`、`skin-photo`、`skin-follow-up`（VERIFIED）。

## 4. Render Audit
依 `render.yaml` 與程式（VERIFIED 為檔案內容；**正式環境實際值除另註外皆 UNKNOWN**）：

| 項目 | `render.yaml` / 程式 | 正式環境 |
|---|---|---|
| API service | `cancer-care-api`，python，plan `free`，region singapore，rootDir `backend` | 使用者 2026-11-05 表示 Auto-Deploy Off、分支 main；現值 UNKNOWN |
| Build | `pip install -r requirements.txt`（多安裝 Pillow）| — |
| Start | `bash scripts/render_start.sh`：`flask db upgrade` →（`SEED_DEMO_DATA=true` 時）`flask seed dev` → gunicorn | — |
| Health check | `/api/v1/health`（只回 status / database；不含版本）| — |
| Python | `PYTHON_VERSION=3.11.9` | — |
| `FLASK_ENV` | `staging` | UNKNOWN |
| `SEED_DEMO_DATA` | **Blueprint 檔內為 `"true"`**（給第一次部署用）| 使用者 2026-11-05 表示 `false`；現值 UNKNOWN |
| Web | `cancer-care-web`，static，`cd frontend && npm ci && npm run build`，`NODE_VERSION=24`，`/api/*` rewrite 到 API | Auto-Deploy Off（使用者 11-05）|
| PostgreSQL | `cancer-care-db`，`plan: free`、PG 17、singapore、`ipAllowList: []`（不允許外部連線）| 使用者表示已升級付費；方案、PITR UNKNOWN |
| 磁碟 | 無 persistent disk 設定；照片在 staging/production 拒絕本機磁碟 | INFERRED |

啟動流程評估：
- `render_start.sh` 有 `set -euo pipefail`：任何一步失敗 → 不啟動（VERIFIED，檔案內容）。
- **Migration 在 API 啟動時自動執行**（VERIFIED）。三個 migration 在**同一次** `flask db upgrade` 中為**同一個交易**：Phase 1c 失敗時 1a、1b 一併回滾，資料庫留在 `b5cd01c11e1d`（VERIFIED，隔離 PostgreSQL 強制失敗測試）。
- **Migration 成功但 API 啟動失敗**：資料庫已在新 revision。Render 一般會保留舊的執行中實例（INFERRED，Render 零停機部署行為，未在本帳號驗證）；舊實例程式能在新 schema 上服務（VERIFIED：聊天、症狀、dashboard、時間軸皆 200），但它下次重啟（Free 方案休眠後喚醒就會）執行舊的 `flask db upgrade` → `Can't locate revision` → 無法啟動（VERIFIED 失敗訊息；重啟時機 INFERRED）。**列為高風險。**
- **新程式在 schema 尚未升級時執行**：症狀相關 API 回 500（VERIFIED）。現行啟動順序（先 upgrade 再 gunicorn）避免這種情況；不可改成「先部署程式、之後再跑 migration」。
- `flask seed dev` 只在 `SEED_DEMO_DATA=true` 時執行：只 upsert 示範資料、重設 3 個示範帳號密碼並重新啟用；**不碰 50 組訓練帳號**（INFERRED，程式；訓練帳號測試通過）。**風險：若重新套用 Blueprint，檔內 `SEED_DEMO_DATA="true"` 可能覆蓋 Dashboard 的 `false`**（INFERRED）。
- 資源：三個 migration 只新增表與少量資料列，耗時與記憶體極小（INFERRED；隔離環境數秒內完成）。

## 5. PostgreSQL Revision Audit
- 正式 revision：**UNKNOWN**。本機無 Render CLI／API 金鑰；資料庫 `ipAllowList: []` 不允許外部連線；依規定不為查詢而修改網路或權限。**未執行任何正式查詢。**
- 推論（INFERRED）：聊天室部署時應已執行 `f1a7c3d2b9e4 -> b5cd01c11e1d`，正式 revision 應為 `b5cd01c11e1d`。**不可據此部署**。
- 建議唯讀確認方式（由有權限者執行，擇一）：
  1. Render Dashboard → `cancer-care-api` → 聊天室那次部署的 Logs：尋找 `Running upgrade f1a7c3d2b9e4 -> b5cd01c11e1d`，以及其後每次啟動 `flask db upgrade` 沒有其他 `Running upgrade`。
  2. Render Dashboard 提供的資料庫連線工具（若有，不變更 IP 白名單）執行唯讀 `SELECT version_num FROM alembic_version;`，只記錄結果。
- 本機 head `a9d4e2b7c5f1`；若正式為 `b5cd01c11e1d`，缺少 3 個 migration，無分支、無多個 head（VERIFIED，本機）；schema drift：本機 PostgreSQL 驗證無 drift（VERIFIED），**正式 schema 是否與 `b5cd01c11e1d` 一致 UNKNOWN**。
- 特殊資料轉換：無（1a 只新增表單資料；1b、1c 只新增表；不轉換 `next_follow_up_at`）（VERIFIED）。

## 6. Migration Risk Assessment（隔離 PostgreSQL，**模擬基準**）
模擬基準由**已部署的聊天室程式（`71e9d8e`）**建立：demo seed、50 組訓練帳號（admin API）、聊天訊息與已讀狀態、每日回報與審閱。**這不是正式資料庫。**

| | 內容 | 驗證 |
|---|---|---|
| **1a `c3e9a1f4b7d2`** | 只新增表單資料（1 分類、3 題、6 項目；重用 3 題每日回報題目，只查找不修改）；已存在則略過，可重複執行；downgrade 在有回報時保留資料 | 升級成功、受保護資料不變、表單 6 項（VERIFIED）。代碼衝突：以 code 查找，既有同名列不覆寫（INFERRED）|
| **1b `e7a2c4f9d1b3`** | 新增 `symptom_photos`、`patient_consents`（FK RESTRICT、CHECK、部分唯一索引）；無不可逆操作；downgrade 刪兩表 metadata（影像檔不在 DB）| 升級成功、表與索引存在、受保護資料不變（VERIFIED）。照片關閉時可升級與啟動（VERIFIED）|
| **1c `a9d4e2b7c5f1`** | 新增 `care_follow_ups`、`care_follow_up_events`；部分唯一索引（`WHERE status = 'open'`，PostgreSQL 已確認為 partial）；CHECK、FK（含 nursing_assessments）；有資料時 downgrade 拒絕 | 升級成功、索引與約束存在、無 drift、downgrade 有資料時拒絕（VERIFIED）。不修改 `next_follow_up_at`；舊日期以讀取相容（VERIFIED，後端測試）|

升級後 production-like 冒煙（staging 設定、照片預設關閉）全部通過（VERIFIED）：照片設定 `disabled_by_config`、上傳 503；訓練病人送出文字皮膚自評；訓練護理師審閱並建立追蹤、結案（新的已簽署評估）；其他配對護理師 404；病人唯讀追蹤；聊天送出、收件匣未讀、升級前歷史保留、跨病人 404；每日回報仍一天一次。

## 7. Backup and Restore Readiness
| 項目 | 狀態 |
|---|---|
| PITR 是否啟用、保留時間、最新可復原時間 | **UNKNOWN** |
| 既有匯出 | 使用者表示 2026-11-05 有 Export（部署當時必然已過期，之後的聊天等資料不在內）|
| 備份包含 schema、資料、聊天、訓練帳號與配對 | INFERRED（`pg_dump` 類匯出應包含）；未驗證 |
| 可還原性 | **「備份存在，但可還原性尚未驗證。」** |
| 存取控制與加密 | UNKNOWN |
| 還原所需時間 | UNKNOWN |
| 還原後資料遺失 | 還原點之後的所有寫入（聊天訊息、回報、追蹤）都會遺失（INFERRED）|

部署前必須的備份驗證程序見 Runbook Phase 2（需授權：建立當日 Export、在**隔離**環境還原、比對 `alembic_version` 與資料筆數、確認 PITR 時間窗）。

## 8. Existing Feature Protection
- 聊天室：程式、model、migration 無差異（VERIFIED）；升級前後聊天訊息與已讀狀態逐位元組不變、升級後可送出／收件匣未讀／歷史保留／跨病人 404（VERIFIED，模擬基準）；聊天 E2E 109/109（VERIFIED，最近一次完整回歸）。
- 50 組訓練帳號：升級前後 users、patient_profiles、配對逐位元組不變（VERIFIED，模擬基準）；migration 不碰帳號；seed 不碰訓練帳號（INFERRED）。風險僅在 `SEED_DEMO_DATA=true` 時重設 3 個**示範**帳號密碼（不含訓練帳號）。
- 每日回報：表單資料不變、一天一次規則正常（VERIFIED）。
- 既有測試：後端 32/32 suites、1604 項；完整 E2E 開發 31/32、PostgreSQL 模擬 32/33（VERIFIED，最近一次）。**既有未修正問題（揭露，非本次範圍）**：mock 模式 `labs` 3 項失敗——病人端看不到最近檢驗結果、P00002 ANC 1.2 未標示偏低、patient02 未顯示偏低 ANC 的生活建議（API 模式通過）。

## 9. Photo Feature Safety
- 預設：base／staging／production `PHOTO_UPLOADS_ENABLED=false`、無儲存後端（VERIFIED，程式與隔離 staging）。
- staging／production 一律拒絕本機磁碟；物件儲存需全部 `PHOTO_OBJECT_*` 設定**且**安裝 `boto3`（未在 requirements）**且**院方核可的同意書版本 → 以目前程式無法在正式環境開啟（VERIFIED，`test_skin_photos` 的設定組合測試）。無 fallback。
- 未設定時 API 正常啟動；上傳 503；文字自評、審閱、追蹤、聊天正常（VERIFIED，隔離 staging）。無公開網址；影像只能經授權 API 讀取（VERIFIED，測試）。
- **風險**：若有人在 Render 設定 `PHOTO_UPLOADS_ENABLED=true`，仍因無合法後端而關閉（VERIFIED，設定測試）。

## 10. Deployment Strategy Comparison
| | A：一次部署 `db1c422` | B：分三次（1a → 1b → 1c）| C：Schema-first（橋接版本 → 完整版本）|
|---|---|---|---|
| 做法 | main fast-forward 到 `db1c422`，API 啟動時一次執行三個 migration | main 依序指向 `212bc24`、`54e318f`、`db1c422`，各自部署 | 先部署「`71e9d8e` + 三個 migration 檔」（需另建 commit），再部署 `db1c422` |
| Migration 風險 | 一次交易，失敗全部回滾（VERIFIED）| 三次，各自原子 | 在舊程式下執行，失敗全部回滾 |
| 回復 | 應用程式回到舊 commit **不可行**（VERIFIED）；只能 forward fix、DB downgrade 或還原 | 每一步都有同樣的舊版無法啟動問題 | **回到橋接版本可行**（VERIFIED，即使新程式已寫入資料）|
| 故障排查 | 三個功能同時上線，較難分辨 | 容易 | schema 與功能分開，容易 |
| 停機 | Free 方案冷啟動 + 一次 build | 三次 | 兩次 |
| 驗收範圍 | 一次全部 | 三次 | 第一步只驗聊天室與 schema；第二步驗全部功能 |
| 額外成本 | 無 | 三個維護窗口、三次備份、每一步都需回復方案 | 需建立一個橋接 commit（本次未授權）|
| 已知限制 | — | 不能讓前端先於 API；中間版本也需完整驗收 | 新程式不可在 schema 未升級時服務（VERIFIED 會 500）——C 正是避免這點；Web 必須在完整 API 之後部署 |

## 11. Rollback and Recovery Assessment
| 方式 | 何時可用 | 驗證 |
|---|---|---|
| **Forward fix**（修正後重新部署新版本）| 首選；資料庫已升級、只有程式問題時 | 新程式在 head 上重跑 `flask db upgrade` 為 no-op（VERIFIED）|
| **Application rollback 到橋接版本** | 新程式有問題、資料庫已在 head | 橋接版本啟動成功並可服務（VERIFIED，含新程式已寫入的 1a／1c 資料）|
| Application rollback 到 `71e9d8e` | **不可用**（資料庫已升級後）| 啟動失敗 `Can't locate revision`（VERIFIED）|
| Database downgrade（新程式執行 `flask db downgrade b5cd01c11e1d`）| 必須回到純聊天室版本時 | 有追蹤資料時拒絕；設 `ALLOW_DROP_CARE_FOLLOW_UPS=yes` 才執行；之後 `71e9d8e` 可啟動；**追蹤／照片／同意資料遺失**，皮膚表單資料保留（VERIFIED，隔離）。在 Render Free 上執行需要 shell 或暫時更改 start command（INFERRED；需授權）|
| Database restore（PITR／Export）| 資料毀損 | **UNKNOWN**；還原點之後資料全部遺失 |
| Migration 失敗 | 自動 | 交易回滾，資料庫不變（VERIFIED）；新實例不通過健康檢查（INFERRED）|

## 12. Risk Matrix
| # | 風險 | 機率 | 影響 | 等級 | 目前控制 | 尚缺控制 | 證據 | 阻擋 |
|---|---|---|---|---|---|---|---|---|
| R1 | 正式 revision 與預期不一致 | 中 | 高 | **高** | migration 只新增、單一 head | 唯讀確認正式 revision | §5 UNKNOWN | **是** |
| R2 | 啟動時自動升級資料庫 | 確定 | 中 | 中 | 一次交易、失敗全回滾 | 部署前備份 | §4 VERIFIED | 否（需備份）|
| R3 | 應用程式回到舊 commit 無法啟動 | 高（若回復）| 高 | **高** | — | 建立並驗收橋接版本，或只用 forward fix | §11 VERIFIED | **是**（需決定回復策略）|
| R4 | 備份不可還原 | 未知 | 高 | **高** | 使用者表示有 11-05 Export | 部署前 Export + 隔離還原 + PITR 確認 | §7 UNKNOWN | **是** |
| R5 | 聊天室資料或功能受影響 | 低 | 高 | 中 | 無任何聊天變更；升級前後不變 | 部署後冒煙 | §8 VERIFIED（模擬）| 否 |
| R6 | 示範／訓練帳號被重設或重建 | 低～中 | 中 | 中 | migration 不碰帳號；seed 不碰訓練帳號 | 確認 `SEED_DEMO_DATA=false`；不要重新套用 Blueprint | §4 UNKNOWN 現值 | **是**（確認前）|
| R7 | 照片功能意外開啟 | 極低 | 高 | 低 | 程式層多重條件、無 boto3 | — | §9 VERIFIED | 否 |
| R8 | 未經核可的臨床流程上線 | 中 | 高 | **高** | 文字標示待審核 | 院方核可 1a 文字、1c 流程 | — | **是** |
| R9 | API／Web 版本不一致 | 中 | 中 | 中 | Auto-Deploy Off（11-05）| 先 API 後 Web；新 Web 不可配舊 API | INFERRED | 否 |
| R10 | 正式 schema drift | 低 | 高 | 中 | migration 全程管理 schema | 確認正式 revision；部署 log 檢查 | UNKNOWN | 與 R1 一併 |
| R11 | 權限或患者資料外洩 | 低 | 高 | 中 | 後端權限、404 不洩漏、稽核；測試涵蓋 | 部署後隔離冒煙 | VERIFIED（測試）| 否 |
| R12 | 部署失敗造成服務中斷 | 中 | 高 | **高** | 健康檢查、交易回滾 | 維護窗口、可用的回復目標（R3）、必要時暫時改付費方案避免休眠 | INFERRED | 與 R3 一併 |
| R13 | 既有 mock labs 3 項失敗 | 已存在 | 低（mock 模式）| 低 | 揭露 | 後續修正 | VERIFIED | 否 |

## 13. Go／No-Go Checklist
**技術項目**
- [ ] Render API／Web 實際部署 commit 已確認（UNKNOWN）
- [ ] 正式 Alembic revision 已確認為 `b5cd01c11e1d`（UNKNOWN）
- [x] Migration chain 單一 head、無衝突（VERIFIED）
- [x] PostgreSQL 隔離升級測試通過（VERIFIED，模擬基準）
- [x] 聊天室回歸通過（VERIFIED）
- [x] 50 組訓練帳號在升級中不變（VERIFIED，模擬基準）
- [ ] 正式 `SEED_DEMO_DATA=false`、Auto-Deploy Off 現值確認（UNKNOWN）
- [x] 照片功能關閉（VERIFIED）
- [ ] 備份可用且已在隔離環境還原（UNKNOWN）
- [ ] 經驗證的回復目標可部署（橋接候選已在隔離環境完整驗證，見 §16；commit 尚未建立）
- [x] 前後端 build、權限與安全測試通過（VERIFIED）

**核可項目**
- [ ] 院方核可 Phase 1a 病人端文字、安全提示（`docs/skin-self-assessment-clinical-review.md`）
- [ ] 院方核可 Phase 1c 追蹤流程、簽署效力（`docs/skin-follow-up-implementation.md` §11）
- [ ] 維護窗口與部署授權

**必須停止的情況**：正式 revision 不明或不是 `b5cd01c11e1d`；備份無法確認；`SEED_DEMO_DATA` 不是 `false`；部署 log 出現非預期的 `Running upgrade`；任何聊天資料可能遺失；回復目標未就緒；核可未取得。

## 14. Open Questions and Required Approvals
**需要您（有 Render 權限者）唯讀確認**
1. API 與 Web 目前部署的 commit（Render Dashboard → Events / Deploys）。
2. 正式 Alembic revision（§5 方法）。
3. `SEED_DEMO_DATA`、`FLASK_ENV`、Auto-Deploy、部署分支的現值；之後**不要**重新套用 Blueprint。
4. PostgreSQL 方案、PITR 是否啟用與保留時間、可否建立當日 Export。
5. 是否同意建立「橋接版本」commit 作為 schema-first 與回復目標（需另行授權 commit / push）。
6. 部署期間是否暫時把 API 改為不休眠的方案（避免舊實例重啟風險），或接受風險。

**需要院方核可**：Phase 1a 臨床文字與安全提示、Phase 1c 臨床流程與簽署效力、（未來）照片相關的資料治理（照片仍維持關閉）。

## 15. Final Recommendation
- **目前：NO-GO**，直到 §13 的 UNKNOWN 與核可項目完成。
- **建議策略：C（橋接版本 → 完整版本）**。理由：(1) 已驗證「回到舊 commit」不可行，C 提供已驗證可啟動的回復目標；(2) migration 在舊程式下執行，聊天室持續服務，schema 問題與功能問題分開；(3) 第二步新程式啟動時無 migration 要跑。代價是需要另建一個只含三個 migration 檔的 commit。
- 若不同意建立橋接版本，次佳為 **A（一次部署）**，但必須接受「只能 forward fix 或資料庫 downgrade／還原」，並在部署期間避免 API 休眠，且備份還原必須先驗證。
- 不建議 B：三個窗口都有相同的回復問題，卻不增加安全性。


---

## 16. Bridge Release 候選版本驗證（2026-11-12 補充）

> 候選分支 `feature/phase1abc-schema-bridge`（git worktree：`D:\app-worktrees\phase1abc-schema-bridge`），基礎 `71e9d8e`。**未 commit**：只有三個新增、未追蹤的 migration 檔。
> `71e9d8e` 是本機 `main`／`origin/main`，**是否就是 Render 目前執行的 commit 仍為 UNKNOWN**（§2）；橋接方案以它為基礎，必須在 Runbook Phase 1 確認。
> 工具：`backend/tests/predeploy/run_bridge_checks.sh`（+ `bridge_check.py`）；在記憶體中的 PGlite PostgreSQL 執行。

### 16.1 橋接版本內容與 migration 依賴（VERIFIED）
- 內容：`71e9d8e` + `c3e9a1f4b7d2`、`e7a2c4f9d1b3`、`a9d4e2b7c5f1` 三個 migration 檔，與 `db1c422` 逐位元組相同；**沒有**任何 Phase 1a～1c 的程式、model、前端或設定。
- 依賴：三個 migration 只 import `alembic`、`sqlalchemy`、`datetime`、`os`，不 import 任何 app 程式；外鍵只指向聊天室版本已存在的表（`patient_profiles`、`users`、`symptom_records`、`nursing_assessments`）；`c3e9a1f4b7d2` 只以 code 查找 `rt_daily_report` 的既有題目。
- Alembic `env.py` 的 model metadata 只用於 autogenerate，`upgrade` 不比對 model（INFERRED，程式）。橋接版本的 model 沒有新表，`flask db check` 會顯示差異，但不影響執行（INFERRED）。
- 舊程式沒有刪除病人／使用者／症狀紀錄的路徑，新表的 RESTRICT 外鍵不會擋到舊功能（INFERRED，程式搜尋）。
- 套件：橋接版本的 `requirements.txt` 與已部署版本相同（不需要 Pillow）；在 Pillow、boto3 都無法載入的情況下，橋接 migration 仍可完成（VERIFIED）。完整版本只多 `pillow==11.3.0`。

### 16.2 隔離 PostgreSQL 驗證（VERIFIED，模擬基準由 `71e9d8e` 建立）
| # | 驗證 | 結果 |
|---|---|---|
| 1 | 聊天室版本建立模擬資料（demo seed、50 組訓練帳號、聊天、每日回報與審閱）| 完成 |
| 2 | 橋接版本 `flask db upgrade` → `a9d4e2b7c5f1` | 通過 |
| 3 | 聊天室（送出、未讀、跨病人 404）與每日回報（201、同日 409）| 通過 |
| 4 | 50 組訓練帳號與配對：升級前後逐位元組不變；抽樣 6 個帳號可登入 | 通過 |
| 5 | 新表、部分唯一索引、CHECK、外鍵存在 | 通過 |
| 6 | **沒有執行 seed**：users（含密碼雜湊、updated_at）逐位元組不變；啟動 log 無 `Seed complete` | 通過 |
| 7 | 橋接版本啟動腳本（第 1 步）重跑：no-op | 通過 |
| 8 | 完整版本 `db1c422` 接續啟動：沒有 migration 要跑 | 通過 |
| 9 | 完整版本 → **回到橋接版本**：啟動成功 | 通過 |
| 10 | 完整版本已寫入 1a／1b／1c 資料（皮膚自評、照片同意、審閱＋追蹤、結案＋下一次追蹤）後，橋接版本：病人與護理師的舊功能 API 全部 200、聊天可送出；**新功能資料逐位元組不變**（未刪除、未改寫，只是舊畫面無法操作）；再切回完整版本，追蹤鏈與同意都還在 | 通過 |
| — | 對照：資料庫已到 head 時，純 `71e9d8e` 啟動失敗（`Can't locate revision identified by 'a9d4e2b7c5f1'`）| 符合預期 |
| — | 橋接版本自身後端測試：29/29 suites、1285 項（與聊天室版本相同，migration 鏈多三個版本）| 通過 |
| — | 已部署的前端與 E2E 套件（`71e9d8e`）對橋接版本 API 的 PostgreSQL 正式環境模擬：29/30 suites、1185 項；唯一失敗為既有 3 項 mock `labs`（與聊天室版本當時相同）| 通過 |

### 16.3 額外風險驗證
1. **PostgreSQL DDL 交易邊界**：`env.py` 未設定 `transaction_per_migration`，一次 `flask db upgrade` 的所有 migration 在同一個交易（INFERRED，程式）；PostgreSQL 支援交易式 DDL。
2. **跨 revision 原子性（VERIFIED）**：第 2 個（`e7a2c4f9d1b3`）或第 3 個（`a9d4e2b7c5f1`）migration 失敗時，log 顯示前面的 migration 已執行，但資料庫**停在 `b5cd01c11e1d`**，1a 的表單資料與其他新表全部回滾。
3. **失敗後恢復聊天室服務（VERIFIED）**：上述失敗後，資料庫仍是聊天室版本，`71e9d8e` 可正常啟動。Render 端：新實例不通過健康檢查時保留舊實例（INFERRED，Render 行為未在本帳號驗證）。
4. **啟動時 `flask db upgrade` 失敗**：腳本 `set -e` → 不啟動 gunicorn（VERIFIED，檔案內容；失敗路徑由 2 驗證）。
5. **Blueprint／Seed**：橋接版本的 `render.yaml` 與已部署版本相同（仍為 `SEED_DEMO_DATA="true"` 的 Blueprint 預設）。實測 `SEED_DEMO_DATA=true` 時：**訓練帳號與配對不變**，但 **3 個示範帳號的密碼雜湊被改寫**（VERIFIED）。正式環境現值 UNKNOWN；不可重新套用 Blueprint。
6. **正式 revision 不是 `b5cd01c11e1d` 時**（VERIFIED，隔離）：
   - 在 `f1a7c3d2b9e4`（聊天室 migration 未執行）→ 橋接版本會一併套用聊天室 migration，結束於 `a9d4e2b7c5f1`。這代表正式環境與預期不同，**應先停止並調查**，不應直接繼續。
   - 未知的 revision → 橋接版本拒絕啟動，資料庫不變；**停止**。
   - 因此 Runbook Phase 1 必須先確認正式 revision。

### 16.4 對 Go／No-Go 的影響
- 已解決：§13「經驗證的回復目標」的**技術驗證**（橋接版本可作為回復目標）。
- 仍待：建立本機橋接 commit（需授權）與其 push／部署授權；§13 其他 UNKNOWN（Render 實際 commit、正式 revision、`SEED_DEMO_DATA`／Auto-Deploy 現值、備份還原）與院方核可。**整體仍為 NO-GO。**

---

## 17. 完整版本更新：照片儲存修正（2026-11-12 補充）

`db1c422` 之後，`feature/skin-follow-up` 加入以下 commit（依序；hash 以 `git log 71e9d8e..feature/skin-follow-up` 為準）：
1. `build(photo): pin boto3 for private object storage`
2. `fix(photo): bound S3 timeouts and retries, return 503 on storage failures`
3. `test(photo): add simulated S3/KMS object storage check`
4. `fix(photo): retry uploads safely after storage errors`
5. `docs(photo): document object storage, failure handling and lifecycle`
6. `chore(deploy): add phase1abc pre-deployment audit, runbook and isolated checks`（本文件、Runbook、`backend/tests/predeploy/`）

影響評估：
- Migration：三個 migration 檔與 `db1c422`、橋接 commit `d6c157a` 逐位元組相同（VERIFIED，`git rev-parse <commit>:<path>` 比對）。橋接方案不變。
- 合併：`git merge-tree` 模擬橋接 commit 與完整版本合併，無衝突（VERIFIED）。
- 套件：完整版本多 `boto3`／`botocore` 等 7 個套件（Render build 變大）。照片關閉時不會載入 boto3（VERIFIED：staging／production 設定啟動後 `boto3`、`botocore` 都不在 `sys.modules`）。
- 行為：照片功能仍預設關閉，`render.yaml` 沒有設定 `PHOTO_UPLOADS_ENABLED`（VERIFIED）。聊天室、其他 API、migration、seed、`render.yaml`、`render_start.sh` 都沒有變更（VERIFIED，`git diff --name-only db1c422..`）。
- 隔離相容性：commit 後以 `run_isolated_checks.sh 71e9d8e feature/skin-follow-up` 與 `run_bridge_checks.sh <橋接 backend> 71e9d8e feature/skin-follow-up` 重新驗證，結果記錄在事件紀錄（不寫入本文件，避免 commit 引用自己）。
- Go／No-Go：§13 與 §16.4 的 UNKNOWN 與院方核可事項不變，**整體仍為 NO-GO**（正式部署）。

## 18. 照片改存 PostgreSQL：第四個 migration（2026-11-12 補充）

- 決定：照片優先存在已付費的 Render PostgreSQL（`PHOTO_STORAGE_BACKEND=database`），暫不用 AWS S3；設計與實測見 `docs/skin-photo-deployment.md` §8。
- 新 migration `b8c3f1d7e2a6`（`symptom_photo_blobs`），接在 `a9d4e2b7c5f1` 之後；新 head = `b8c3f1d7e2a6`（VERIFIED，`flask db heads`）。只新增一張表，既有資料表與資料列不變。
- 橋接版本：已在橋接分支加入同一個檔案，commit `9c2f522`（接在 `d6c157a` 之後）。完整版本與橋接版本的 11 個 migration 以 git blob 比對完全相同（VERIFIED）。若只部署 `d6c157a`，完整版本把資料庫升到 `b8c3f1d7e2a6` 之後就無法回復到它（`Can't locate revision`，與 §16 相同機制），因此部署的橋接版本必須是 `9c2f522`。
- 隔離驗證（VERIFIED，PGlite）：
  - `run_isolated_checks.sh 71e9d8e <工作目錄>`：34/34，ALL PASSED。
  - `run_bridge_checks.sh <橋接 worktree> 71e9d8e <工作目錄>`：ALL BRIDGE CHECKS PASSED。新增「第 4 個 migration 失敗」情境：資料庫停在 `b5cd01c11e1d`，`71e9d8e` 可重新啟動。
- 真實 PostgreSQL 16（本機臨時，合成資料；Render 為 17）：`backend/tests/predeploy/photo_db_storage_check.py` 39/39，涵蓋：
  - BYTEA 與 `STORAGE EXTERNAL`；
  - 並行配額：有鎖不超額，對照組無鎖會超額；
  - 200 MiB 配額 → 507；
  - `pg_dump`／`pg_restore` 後圖片一致；
  - 降級保護。
- 啟動流程：`render_start.sh` 不變，第四個 migration 在 API 啟動時由 `flask db upgrade` 一併執行，與前三個屬於同一個交易（VERIFIED，原子性情境）。
- 照片功能仍預設關閉；正式啟用需要 `PHOTO_UPLOADS_ENABLED=true`、`PHOTO_STORAGE_BACKEND=database`、`PHOTO_DB_QUOTA_BYTES`、院方核可的同意書版本（**本次都未設定**）。
