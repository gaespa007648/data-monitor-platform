# Data Monitor Platform

即時資料分析與監控系統，採 FastAPI 提供 API、MariaDB 持久化資料、Streamlit 提供互動介面。

目前提供使用者註冊與 JWT 登入、Admin／User／Viewer 權限、自訂 Schema 的數據集、單筆建立與 CSV／JSON 批次匯入、資料篩選與統計圖表、Excel 下載、即時監控與 WebSocket 模擬生成器。Streamlit 另提供資料檢視、Admin 專用的使用者角色管理、系統狀態與操作日誌。既有固定四欄位資料 API 仍保留相容；完整功能與限制請見 [系統架構](docs/architecture.md)。

## 技術棧

| 項目 | 技術 |
| --- | --- |
| 後端 | Python 3.12、FastAPI、Uvicorn、Pydantic |
| 資料庫 | MariaDB 11.7.2、InnoDB、utf8mb4 |
| 資料存取 | SQLAlchemy 2 Async ORM、asyncmy、連線池 |
| 遷移 | Alembic，一次性 migrate 服務 |
| 認證 | JWT HS256（60 分鐘）、Argon2 密碼雜湊、admin/user/viewer 角色 |
| 前端 | Streamlit 多頁面、Session State、HTTPX、WebSocket 用戶端 |
| 容器 | 多階段 Dockerfile、Docker Compose、named volume |

應用資料讀寫均使用 ORM，沒有原生 SQL 查詢。Alembic 的 schema operations 負責產生 DDL。版本與部署限制請見 [系統架構](docs/architecture.md)。

## 專案結構

```text
compose.yaml                 # 唯一一份容器編排設定
.env.example                 # 本機設定範本，不含正式憑證
data/                        # migrate 初始化時使用的 Iris 與 Vega Stocks 範例 CSV
backend/
  Dockerfile                 # 共用於 api 與 migrate 的多階段映像
  app/                       # 設定、ORM、JWT、API、初始化流程
  migrations/                # Alembic schema 版本
frontend/
  Dockerfile                 # Streamlit 多階段映像
  app.py                     # 註冊登入、產品簡介與頁面導覽
  dataset_ui.py              # 數據集建立、輸入、匯入與檢視
  analysis_ui.py             # 資料篩選、統計與 Excel 下載
  monitor_ui.py              # 即時監控與模擬生成器
  system_admin_ui.py         # 使用者、系統狀態與操作日誌
docs/                        # 對外架構、操作與文件管理說明
```

## 本地運行步驟

需要 Docker Engine／Docker Desktop（Linux containers）與 Docker Compose v2.24 以上；使用容器運行不需在主機安裝 Python 或 MariaDB。先啟動 Docker Desktop，在專案根目錄執行：

```powershell
Copy-Item .env.example .env
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 180
docker compose ps -a
```

macOS／Linux 複製指令改用 `cp .env.example .env`，其餘 Compose 指令相同。第一次需下載映像與建構套件，可能需要數分鐘。已存在 `.env` 時不要重新覆寫，請保留既有 DB 密碼。

Compose 啟動時，`migrate` 會檢查 `data/example-Iris.csv` 與 `data/example-vega-stocks.csv` 對應的數據集。若缺少完整名稱為 `example-iris` 或 `example-vega-stocks` 的數據集，就會自動建立 Schema 並匯入 CSV 資料；已有同名數據集時會跳過，不覆蓋既有內容。登入 Streamlit 後可在「管理數據集」選擇並查看這兩份範例。

### 啟動前設定檢查：`docker compose config --quiet`

這行指令會解析 `compose.yaml`、套用 `.env` 與目前 shell 的環境變數，檢查 YAML 格式、Compose 設定結構、服務相依關係及必要變數是否可解析。`--quiet` 表示不印出展開後的設定；成功時通常沒有輸出，結束碼為 0。它不會建構映像、建立或啟動容器。錯誤訊息與檢查方式請見 [操作與疑難排解](docs/operations.md)。

### 啟動後檢查

瀏覽 [Streamlit](http://localhost:8501) 登入。API 位於 [http://localhost:8000](http://localhost:8000/docs)，根路徑 `/` 沒有另外定義頁面，請使用 `/docs` 或健康檢查端點。

`.env` 可調整 `API_PORT`、`STREAMLIT_PORT`、DB 帳密、JWT secret 與測試帳號開關。修改 host port 後，README 中的連結也需使用對應 port。容器內 frontend 始終透過 `http://api:8000` 連線，DB 使用 `db:3306`；MariaDB 不對主機公開連接埠。


## Docker 部署與維運指令

```powershell
# 建構、遷移、啟動；成功時 db、api、frontend 為 healthy，migrate 為 Exited (0)
docker compose up --build -d --wait --wait-timeout 180

# 查看狀態與日誌
docker compose ps -a
docker compose logs --tail 100 api migrate frontend db

# 停止但保留容器
docker compose stop
docker compose start

# 關閉並移除本專案容器／網路，保留資料 volume
docker compose down --timeout 30

# 重新啟動，沿用資料
docker compose up -d --wait --wait-timeout 180
```

啟動順序為 `db healthy → migrate 成功 → api healthy → frontend`；遷移失敗會阻止 API 啟動。服務的 healthcheck 只回報健康，不會自行重啟仍存活的 unhealthy 程序。

更新程式或 schema 時，使用下列流程確保 migrate 本次確實執行，避免沿用先前已結束的容器：

```powershell
docker compose down --timeout 30
docker compose build
docker compose up -d --wait --wait-timeout 180
```

### 資料持久化

MariaDB 資料位於 `${COMPOSE_PROJECT_NAME}_mariadb_data` named volume，掛載到 `/var/lib/mysql`。預設實際名稱為 `data-monitor-platform_mariadb_data`。映像重建、容器重建與一般 `docker compose down` 都保留使用者資料及 Alembic revision。

**`docker compose down -v` 會永久刪除本專案資料 volume，只限確定要清空本機資料時使用。** 不要更改 project name 後誤以為原資料消失；不同 project name 會使用不同 volume。Volume 不是備份，正式使用前仍需建立異機備份及還原流程。

MariaDB 初始化帳密只在空資料目錄生效；修改 `.env` 中的 DB_PASSWORD 不會同步修改既有資料庫帳號。應用測試帳號由另一個 ORM 初始化流程管理：`SEED_DEMO_USERS=true` 時會建立不存在的帳號，並同步既有 admin 與 viewer 的密碼；不修改既有角色或啟用狀態。

`migrate` 每次啟動會以完整名稱檢查 `example-iris` 與 `example-vega-stocks`；缺少時才從 `data/` 的 CSV 建立 Schema 並匯入資料，已有同名數據集則不修改或補匯資料。這項範例初始化不受 `SEED_DEMO_USERS` 開關影響；詳細欄位與日期格式見 [使用者與資料管理](docs/user-and-data-management.md)。

目前為本機初始化設定，僅將 API 與 Streamlit 綁定 `127.0.0.1`。正式對外部署前需更換所有範本密碼與 JWT secret、設定 `SEED_DEMO_USERS=false`、移除既有測試帳號、加入 TLS 及更細的 DB 權限。目前共用 DB 帳號包含 migration 所需 schema 權限；尚未拆分 runtime／migration 帳號。`.env` 已排除版控，但環境變數仍可被有 Docker 管理權限的人檢視。

## API 文件與端點

- [Swagger UI](http://localhost:8000/docs)
- [ReDoc](http://localhost:8000/redoc)
- [OpenAPI JSON](http://localhost:8000/openapi.json)
- [Liveness](http://localhost:8000/health/live)
- [Readiness（含 ORM 資料庫查詢）](http://localhost:8000/health/ready)

| 方法與路徑 | 功能 |
| --- | --- |
| POST `/api/v1/users` | 公開註冊，固定建立 User 角色 |
| POST `/api/v1/auth/tokens` | JSON 帳密登入，取得 JWT |
| GET `/api/v1/users/me` | 查看目前登入者 |
| GET `/api/v1/users` | 管理員取得使用者清單，viewer 回傳 403 |
| PATCH `/api/v1/users/{id}/role` | Admin 修改使用者角色 |
| GET `/api/v1/system/database` | Admin 查看 MariaDB 連線、快取及資料表大小等指標 |
| GET `/api/v1/system/audit-logs` | Admin 分頁查詢操作稽核紀錄 |
| GET／POST `/api/v1/datasets` | 數據集清單與建立 |
| GET `/api/v1/datasets/{id}` | 數據集及目前 Schema |
| POST `/api/v1/datasets/{id}/fields` | 只新增欄位，保留既有欄位 |
| GET／POST `/api/v1/datasets/{id}/records` | API 每頁最多 20 筆的排序檢視（Streamlit 固定 10 筆）／單筆建立 |
| DELETE `/api/v1/datasets/{id}/records/{record_id}` | 建立者或 Admin 刪除數據集資料列 |
| POST `/api/v1/datasets/{id}/imports` | CSV／JSON 預檢與冪等批次匯入；CSV 可設定是否跳過首列欄名 |
| GET／POST `/api/v1/monitors` | 查詢及建立即時監控設定 |
| POST `/api/v1/monitors/{id}/start`、`/stop` | 啟用或停止監控 |
| GET `/api/v1/monitors/{id}/snapshot`、`/alerts` | 讀取圖表資料與異常告警 |
| WebSocket `/api/v1/generator/ws` | 驗證後每秒模擬寫入一筆資料並推送結果 |
| GET／POST `/api/v1/categories` | 查詢分類；僅 Admin 可建立分類 |
| GET／POST `/api/v1/records` | 資料分頁查詢／建立 |
| GET／PATCH／DELETE `/api/v1/records/{id}` | 讀取／更新／刪除記錄；寫入須符合角色與擁有者權限 |
| POST `/api/v1/record-imports` | CSV／JSON 預檢與冪等批次匯入 |
| GET／POST `/api/v1/import-schemas` | 選取或建立匯入 Schema 範本，支援選填型別與版本管理 |

固定四欄位的 `categories`、`records`、`record-imports`、`import-schemas` API 保留給既有用戶端，新的 Streamlit 流程使用數據集 API。Swagger 先呼叫登入端點，複製 `access_token` 至 Authorize 的 bearer token 欄位，即可測試受保護 API。權杖不包含密碼雜湊，API 回應也不回傳該欄位。

## 測試帳號資訊

`.env.example` 預設 `SEED_DEMO_USERS=true`，migrate 成功後透過 ORM 建立以下本機帳號：

| 帳號 | 預設密碼 | 權限 |
| --- | --- | --- |
| admin | `AdminDemo123!` | 管理全部資料、使用者角色與系統管理資訊；可建立分類 |
| viewer | `ViewerDemo123!` | 查看自身資訊、產品簡介與共享資料；不可寫入 |

也可在 Streamlit「註冊」頁建立自己的 User 帳號，無固定測試密碼。User 可讀取共享數據集，建立自己的數據集及資料；Admin 可管理全部數據集。既有固定欄位 API 的修改／刪除權限仍依建立者判斷。操作方式與格式請見 [使用者與資料管理](docs/user-and-data-management.md)。

**測試帳號的密碼至少需要 12 個字元**：當 `SEED_DEMO_USERS=true` 時，`.env` 中的 `DEMO_ADMIN_PASSWORD` 與 `DEMO_VIEWER_PASSWORD` 都必須符合此限制；帳號名稱沒有至少 12 個字元的要求。目前初始化流程會先驗證這兩個密碼，再檢查帳號是否存在，因此即使資料庫已有帳號，設定值仍須符合限制。

若任一密碼不足 12 個字元，`migrate` 容器會顯示以下錯誤並以結束碼 1 結束，後續 API 與 Streamlit 也不會啟動：

```text
ValueError: Demo passwords must contain at least 12 characters
```

請修正 `.env` 中的密碼後，重新執行 `docker compose up --build -d --wait --wait-timeout 180`。`docker compose config --quiet` 不會驗證此應用程式層級的密碼長度限制。

當 `SEED_DEMO_USERS=true` 時，`DEMO_ADMIN_PASSWORD` 與 `DEMO_VIEWER_PASSWORD` 分別是 admin 與 viewer 密碼的設定來源：每次執行 migrate 初始化流程，若既有帳號密碼不同，會透過 ORM 更新其 Argon2 雜湊；密碼相同時不重寫。不會變更帳號的 id、角色或啟用狀態。`SEED_DEMO_USERS=false` 時不建立或同步測試帳號。

修改 `.env` 的 `DEMO_ADMIN_PASSWORD` 或 `DEMO_VIEWER_PASSWORD` 後，執行下列指令套用設定並確認服務健康；明確重建 migrate 容器，確保初始化流程重新執行：

```powershell
docker compose up --build -d --wait --wait-timeout 180 --force-recreate migrate api frontend
```

不需刪除 MariaDB volume。Streamlit 登出僅清除目前 session；目前未實作 refresh token 與後端登出撤銷，變更密碼前已發出的 access token 可使用至其原到期時間，新簽發的權杖有效期為 60 分鐘。登入與註冊有單程序 IP 速率限制。

## 延伸文件

- [Streamlit 前端操作手冊](docs/streamlit-user-guide.md)：系統管理、數據集、CSV／JSON 匯入、資料分析與即時監控操作。
- [系統架構與架構圖](docs/architecture.md)：容器職責、通訊、認證與資料儲存。
- [使用者與資料管理](docs/user-and-data-management.md)：角色、資料操作與 CSV／JSON 匯入格式。
- [JWT 認證機制](docs/jwt-authentication.md)：權杖格式、有效期與驗證流程。
- [資料庫遷移與連線池管理](docs/database-management.md)：Alembic 版本管理與 SQLAlchemy 連線池核心概念。
- [操作與疑難排解](docs/operations.md)：設定檢查、啟動問題與測試帳號密碼同步。
- [文件管理規範](docs/documentation-policy.md)：對外文件範圍與維護原則。
