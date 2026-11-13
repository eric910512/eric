# Phase 1a～1c 正式部署 Runbook（**未來操作計畫，本次未執行**）

> 適用：從聊天室版本（`71e9d8e`，revision `b5cd01c11e1d`）部署到 Phase 1a～1c 完整版本（revision `b8c3f1d7e2a6`）。
> **完整版本＝審查通過時 `feature/skin-follow-up` 的最新 commit**（2026-11-12 起為 `db1c422` 之後再加 6 組照片儲存修正；migration 未變）。本文件刻意不寫入該 hash，避免 commit 引用自己；部署時以 `git rev-parse feature/skin-follow-up` 取得，並記錄到事件紀錄，以下稱 `<FULL>`。
> 建議策略：**C — 先部署「橋接版本」（`71e9d8e` + 四個 migration 檔），驗收後再部署完整版本**。若未核准橋接版本，見文末「策略 A 差異」。
> 稽核依據：`docs/phase1abc-predeployment-audit.md`。每一步都要記錄操作者、時間、結果到事件紀錄。

## Phase 0：部署授權與維護窗口
- **前置**：稽核報告已審查；§13 核可項目（院方：1a 文字、1c 流程與簽署效力）已取得。
- **操作**：指定負責人與第二確認人；選定低使用時段；通知使用者（教學帳號使用者）可能短暫中斷；確認可聯絡 Render 帳號擁有者。
- **預期／驗收**：書面授權（commit、push、Render deploy 各自明確授權）。
- **失敗處理**：任何核可缺少 → 停止。
- **人工授權**：需要。

## Phase 1：部署前狀態確認（唯讀）
- **操作**：
  1. Render Dashboard：`cancer-care-api`、`cancer-care-web` 目前部署 commit 應為 `71e9d8e`；Auto-Deploy = Off；部署分支 = main。
  2. 環境變數（只看不改）：`FLASK_ENV=staging`、`SEED_DEMO_DATA=false`、未設定 `PHOTO_UPLOADS_ENABLED` / `PHOTO_STORAGE_BACKEND`（或為 false / 空）。**不要重新套用 Blueprint**（`render.yaml` 內 `SEED_DEMO_DATA="true"`）。
  3. 正式 revision：部署 log 或唯讀 `SELECT version_num FROM alembic_version;` → 必須是 `b5cd01c11e1d`。
  4. 本機：`git fetch`；`origin/main` = `71e9d8e`；`feature/skin-follow-up` = 已審查的 `<FULL>`（與事件紀錄一致）；`flask db heads` = `b8c3f1d7e2a6`。
- **驗收**：全部相符。**失敗處理**：任何不符 → 停止，回到稽核。**人工授權**：Render 唯讀檢視需要有權限者。

## Phase 2：備份與隔離還原驗證
- **前置**：Phase 1 通過。
- **操作**：
  1. Render Dashboard → `cancer-care-db` → Recovery：記錄 PITR 是否可用、可復原的時間窗；建立**當日** Export。
  2. 在**隔離**的 PostgreSQL（本機或專用測試庫，不可是正式庫）還原該 Export（`pg_restore`）。
  3. 檢查：`alembic_version` = `b5cd01c11e1d`；`users`、`patient_profiles`、`nurse_patient_assignments`、`chat_messages`、`chat_read_states`、`symptom_records` 筆數與 Dashboard／API 所見一致；50 組訓練帳號存在。
  4. 在還原後的隔離庫執行 `bash backend/tests/predeploy/run_isolated_checks.sh` 的升級部分（或以該庫為基準跑 `upgrade_path_check.py`），確認真實資料也能升級。
  5. 記錄還原所需時間；匯出檔存放在受控位置，用畢依規定刪除。
- **驗收**：還原成功、筆數一致、升級通過。
- **失敗處理**：無法還原 → 停止（No-Go）。
- **人工授權**：需要（下載正式資料的匯出屬敏感操作，需依院方規定授權與保管）。

## Phase 3：部署前測試（本機／隔離）
- **操作**：`python backend/tests/run_all.py`；`node frontend/e2e/run.mjs`；`node frontend/e2e/run.mjs --prod`；`bash backend/tests/predeploy/run_isolated_checks.sh 71e9d8e <FULL>`；`bash backend/tests/predeploy/run_bridge_checks.sh <橋接 backend 目錄> 71e9d8e <FULL>`；`python backend/tests/object_storage/photo_object_storage_check.py`（moto 模擬）；`node --test "frontend/tests/unit/*.test.mjs"`；前端 build。
- **預期**：後端全過；完整 E2E 只有已知 3 項 mock `labs` 失敗；隔離檢查 ALL PASSED。
- **失敗處理**：任何新失敗 → 停止。**人工授權**：不需要。

## Phase 4：Migration 執行計畫（橋接版本，schema-first）
- **前置**：已授權建立橋接 commit。
- **操作**：
  1. 橋接候選：分支 `feature/phase1abc-schema-bridge`（worktree `D:\app-worktrees\phase1abc-schema-bridge`，基礎 `71e9d8e`），只含四個 migration 檔（`c3e9a1f4b7d2`、`e7a2c4f9d1b3`、`a9d4e2b7c5f1`、`b8c3f1d7e2a6`，內容與完整版本 `<FULL>` 相同）。本機橋接 commit：`d6c157a`（前三個）＋ `9c2f522`（第四個，照片 PostgreSQL 儲存；2026-11-12）。**先確認 Render 目前執行的 commit 就是 `71e9d8e`**（2026-11-12 已由 Dashboard 確認 Live Commit = `71e9d8e`）；若不是，以實際部署的 commit 為基礎重建橋接版本並重跑驗證。部署前再跑一次 `bash backend/tests/predeploy/run_bridge_checks.sh <橋接 backend 目錄> <部署中的 commit> <FULL>`，必須 ALL BRIDGE CHECKS PASSED。
  2. 經授權後讓 main 指向橋接 commit 並 push。
  3. Render：手動部署 `cancer-care-api`（Web **不**部署）。
- **預期**：部署 log 依序出現 `Running upgrade b5cd01c11e1d -> c3e9a1f4b7d2`、`-> e7a2c4f9d1b3`、`-> a9d4e2b7c5f1`、`-> b8c3f1d7e2a6`，之後 gunicorn 啟動、health 200。**不得**出現 `seed dev` 的輸出。
- **驗收**：health 200；聊天室冒煙（Phase 7 聊天部分）通過；唯讀確認 revision = `b8c3f1d7e2a6`。
- **失敗處理**：migration 失敗 → 交易回滾、資料庫留在 `b5cd01c11e1d`（第 2、3 個 migration 失敗皆已驗證）、舊實例續用；必要時重新部署 `71e9d8e`（在 `b5cd01c11e1d` 可正常啟動，已驗證）→ 停止並調查（不需資料庫處理）。
- **停止條件**：部署 log 出現 `b5cd01c11e1d -> c3e9a1f4b7d2` 以外的起點（例如 `f1a7c3d2b9e4 ->`），代表正式 revision 與預期不同 → 調查（橋接版本仍可完成，但必須先釐清原因）。
- **人工授權**：commit／push／deploy 各需授權。

## Phase 5：API 部署（完整版本）
- **前置**：Phase 4 驗收通過；觀察至少一段時間無異常。
- **操作**：main 合併 `feature/skin-follow-up`（`<FULL>`；四個 migration 檔與橋接 commit 新增的檔案內容相同，`git merge-tree` 無衝突）並 push；手動部署 `cancer-care-api`。
- **預期**：log 中 `flask db upgrade` **沒有** `Running upgrade`（已在 head）；build 安裝 Pillow 與 boto3／botocore 等套件（照片關閉時不會載入 boto3）；health 200。
- **驗收**：`GET /api/v1/symptom-photos/settings`（以訓練帳號）→ `upload_enabled: false`；Phase 7 API 冒煙通過。
- **失敗處理**：啟動失敗 → Render 保留舊實例（橋接版本，可正常重啟）；修正後重新部署（forward fix）。程式問題需要回復 → 重新部署**橋接 commit**（已驗證可在新 schema 啟動）。
- **人工授權**：需要。

## Phase 6：Frontend 部署
- **前置**：Phase 5 通過（新前端呼叫新 API；不可先於 API）。
- **操作**：手動部署 `cancer-care-web`（同一個 main commit）。
- **驗收**：Phase 7 畫面冒煙通過；舊版前端快取可能短暫存在（hash 檔名，重新整理即可）。
- **失敗處理**：重新部署前一個 Web 版本（Web 與資料庫無關，可安全回復）。
- **人工授權**：需要。

## Phase 7：Smoke Test（只用授權的教學帳號與測試資料）
- **聊天室**：教學病人送出訊息 → 對應教學護理師收件匣未讀 → 回覆 → 病人看到「放射腫瘤科護理師」；另一組教學病人看不到（404）；歷史訊息仍在。
- **每日回報**：教學病人送出一次；第二次 409。
- **Phase 1a**：教學病人送出文字皮膚自評；畫面顯示「照片上傳目前未開放」。
- **Phase 1b**：照片設定 `upload_enabled: false`；上傳 API 503。
- **Phase 1c**：教學護理師審閱並建立追蹤 → 追蹤管理看到 → 結案（新的已簽署評估）；病人「照護追蹤」唯讀並顯示 119 說明；另一組護理師 404。
- **帳號**：50 組教學帳號仍可登入（抽樣）；示範帳號密碼未被重設（若 `SEED_DEMO_DATA=false`）。
- **驗收**：全部通過。**失敗處理**：依影響決定 forward fix 或回到橋接版本（Phase 10）。**人工授權**：使用教學帳號需授權；不得操作真實病人資料。

## Phase 8：監控與觀察
- 觀察 Render API log（500 錯誤、啟動次數、`Can't locate revision` 不應出現）、health、使用回報；Free 方案休眠喚醒後再做一次冒煙。
- 觀察期間不做其他部署或設定變更。

## Phase 9：Go／No-Go 決策
- 依觀察結果決定完成或回復；記錄決策者、時間、依據。

## Phase 10：失敗復原與事件紀錄
| 情境 | 處置 |
|---|---|
| Migration 失敗 | 自動回滾；資料庫不變；舊實例續用；調查後重試 |
| 完整版本啟動失敗 | 舊實例續用；forward fix；必要時重新部署橋接 commit |
| 完整版本有功能問題 | forward fix；或回到橋接 commit（資料庫維持 head，新功能資料保留但無介面）|
| 必須回到純聊天室版本 | 需另行授權：以新程式 `flask db downgrade b5cd01c11e1d`（追蹤資料存在時需 `ALLOW_DROP_CARE_FOLLOW_UPS=yes`、照片圖片存在時需 `ALLOW_DROP_PHOTO_BLOBS=yes`，**會刪除追蹤／照片／同意資料**；Free 方案需暫時更改 start command 或 shell）→ 再部署 `71e9d8e` |
| 資料毀損 | 以 Phase 2 驗證過的備份／PITR 還原到新資料庫，改指 `DATABASE_URL`；還原點之後資料遺失 |
- **絕對不要**：在資料庫已升級後直接重新部署 `71e9d8e`（啟動會失敗）；重新套用 Blueprint；設定 `SEED_DEMO_DATA=true`；啟用照片功能。
- 事件紀錄：時間軸、操作者、log 摘要（不含病人資料）、影響範圍、復原結果、後續改善。

## 策略 A 差異（若不建立橋接版本）
- 跳過 Phase 4；Phase 5 直接部署 `<FULL>`，log 應出現四個 `Running upgrade`。
- 回復選項只剩 forward fix、資料庫 downgrade（資料遺失）或還原；**不可**回到 `71e9d8e`。
- 部署與觀察期間建議 API 暫時使用不休眠的方案，降低舊實例重啟造成中斷的機率。

## 附錄：沿用已付費 `cancer-care-db` 的上線計畫（2026-11-12 決定，不另建測試資料庫）

### A. 本機演練（已完成：PostgreSQL 16.2 與 17.11）
- `python backend/tests/predeploy/rehearsal_pg.py`（預設 `71e9d8e` → `feature/phase1abc-schema-bridge` → `feature/skin-follow-up`）。用臨時 PostgreSQL 與合成資料，每一步都在獨立程序執行。
- 涵蓋：
  - 舊版建立模擬正式資料 → 橋接版升級 → 完整版（照片開啟）→ 回退橋接版 → 再回完整版；
  - 舊版拒絕已升級的資料庫；
  - 模擬 Render 匯出（`.dir.tar.gz`）並還原到新資料庫；
  - 第 4 個 migration 失敗。
- 結果：PostgreSQL 16.2 → 60/60；**PostgreSQL 17.11（與 Render 同一主版本）→ 60/60**（2026-11-12）。`photo_db_storage_check.py` 在 17.11 為 43/43（BYTEA／`STORAGE EXTERNAL`、移除與清理、並行配額含對照組、200 MiB → 507、`pg_dump`／`pg_restore`、降級保護）。
- PostgreSQL 17 執行方式：`PG_BIN=<pg17>/bin python backend/tests/predeploy/rehearsal_pg.py`（`photo_db_storage_check.py` 同樣支援 `PG_BIN`）。本次使用 EnterpriseDB 官方 `postgresql-17.11-5-windows-x64-binaries.zip`：SHA-256 `80379b2c…8f82f`；檔案沒有 Windows 程式碼簽章；只解出 `bin`／`lib`／`share`，沒有安裝 Windows 服務，也沒有建立永久資料庫。

### B. `cancer-care-db` 容量查核（唯讀，在 Dashboard 由有權限者操作；未確認前不開照片）
1. `cancer-care-db` → **Info**：記錄 Storage 配置大小（1 GB 或 5 GB 的倍數）與是否開啟 storage autoscaling。
2. **Metrics** → Disk usage：記錄目前用量與最近 30 天的成長。
3. Phase 2 的匯出檔大小可當作資料量的下限參考。
4. 判斷：**目前用量 + 配額 × 1.1（實測資料表額外約 4%，再留 WAL 空間）≤ 配置大小的 50%** 才可使用該配額。
   - 例：1 GB、目前用量 < 約 290 MiB → 200 MiB 可行。
   - 不符合時降低配額（例如 50～100 MiB），或維持關閉。增加儲存空間會增加月費（$0.30/GB，只能增加不能減少），不在目前方案內。
5. 原因：磁碟用滿時 Render 會把資料庫標為 unhealthy 並暫停，聊天室與回報也會一起停擺。
6. 監控：正式 API 是 Free 方案，沒有 Shell，無法執行 `flask photos storage-report`。上線後以 Dashboard 的 Metrics（disk usage）與 API log 的 `photo storage at …%` 警示為主。

### C. 經驗證的備份與還原（正式部署前必須完成；不對真實資料做任何破壞性操作）
1. `cancer-care-db` → **Recovery**：
   - 記錄 PITR 可回復的時間窗（Hobby 為 3 天）；
   - **Create export**：pg_dump 目錄格式 `.dir.tar.gz`，保留 7 天。
2. 下載匯出檔：含真實病人資料，須依院方規定授權、加密保存、用畢刪除。在隔離的本機 PostgreSQL 17 解壓後，用 `pg_restore` 還原到**新的空白資料庫**（流程與 A 演練相同）。
3. 只做唯讀檢查：`alembic_version` = `b5cd01c11e1d`、各表筆數、50 組教學帳號存在。可選：在還原副本上跑橋接版升級（副本會在用畢後刪除）。
4. 若不允許下載：改用 PITR 還原到**新**的 Render 資料庫做驗證（存在期間按秒計費），驗證後刪除。不得覆蓋原資料庫。

### D. 正式部署步驟（每一步都需另行授權；API 啟動時自動執行 migration：`render_start.sh` 為 `set -euo pipefail` → `flask db upgrade` → gunicorn）
0. 前置：
   - B、C 完成；
   - Auto-Deploy = Off、`SEED_DEMO_DATA=false`；
   - 確認 Render Live Commit = `71e9d8e`；
   - 安排維護時段並通知使用者。
1. **橋接版**：
   - main fast-forward 到 `9c2f522`（只多 4 個 migration 檔）→ push → **只**手動部署 `cancer-care-api`。
   - log 應依序出現 4 行 `Running upgrade`，從 `b5cd01c11e1d` 到 `b8c3f1d7e2a6`；health 200；聊天室與每日回報冒煙通過。
   - 失敗時：交易回滾、資料庫維持 `b5cd01c11e1d`（演練已驗證），新實例不啟動，舊實例繼續服務（INFERRED，Render 行為）。
2. 觀察一段時間。
3. **完整版**：
   - main 合併 `feature/skin-follow-up`（merge-tree 無衝突）→ push → 部署 `cancer-care-api`：log 不應有 `Running upgrade`。
   - 照片 API settings 應回 `upload_enabled: false`（`disabled_by_config`）。
   - 再部署 `cancer-care-web`。
4. **照片啟用**（之後另一個獨立步驟），條件：
   - 容量查核通過；
   - 院方核可同意書（目前程式中的版本 `skin-photo-2026-11-draft-1` 是草稿：需先把核可文字寫進 `consent.py` 並另發版本）；
   - 權限冒煙通過。

   然後設定 `PHOTO_STORAGE_BACKEND=database`、`PHOTO_DB_QUOTA_BYTES=<查核後的值>`、`PHOTO_CONSENT_APPROVED_VERSION=<核可版本>`、`PHOTO_UPLOADS_ENABLED=true` 並重新啟動；以教學帳號與合成圖片冒煙測試。

### E. 回退方案
| 情況 | 處置 |
|---|---|
| 橋接版 migration 失敗 | 不需處理資料庫（已回滾）；必要時重新部署 `71e9d8e` |
| 完整版有問題 | 重新部署橋接版 `9c2f522`：演練已驗證可啟動，所有新功能資料（含照片圖片）保持不變 |
| 照片功能有問題 | `PHOTO_UPLOADS_ENABLED=false` 並重新啟動：停止上傳，已存照片仍可讀；需要完全隱藏時再移除 `PHOTO_STORAGE_BACKEND` |
| 照片接近配額或磁碟偏高 | 停止上傳（同上），評估配額與保存政策 |
| 資料毀損或誤刪 | PITR 還原到**新**資料庫（3 天內）→ 驗證 → 改指 `DATABASE_URL`；還原時間點之後的資料會遺失 |
| 磁碟已滿（資料庫被暫停）| resume → 增加儲存空間（產生費用）→ 停止照片上傳 |
| **絕對不要** | 在已升級的資料庫上部署 `71e9d8e`；在有資料時執行 migration 降級；`SEED_DEMO_DATA=true`；套用 Blueprint |
