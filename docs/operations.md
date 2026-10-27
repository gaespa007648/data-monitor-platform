# 操作與疑難排解

基本啟動、關閉與 API 位址見 [README](../README.md)，容器與網路關係見 [系統架構](architecture.md)。本文件提供可供部署者重複使用的操作說明。

## Compose 設定檢查

在專案根目錄執行 `docker compose config --quiet`，會解析 Compose 設定並代入環境變數，不建構或啟動容器。成功時通常沒有輸出，結束碼為 0；錯誤時通常顯示原因並以非 0 結束。

| 常見訊息片段（實際文字隨版本不同） | 檢查方式 |
| --- | --- |
| `no configuration file provided: not found` | 確認目前目錄有 `compose.yaml`，用 `Get-Location`、`Test-Path .\compose.yaml` 檢查 |
| `required variable DB_NAME is missing a value` | 確認 `.env` 存在、名稱正確，所指變數不為空；只在檔案不存在時從 `.env.example` 複製 |
| `required variable DB_PASSWORD is missing a value` | 檢查 DB_NAME、DB_USER、DB_PASSWORD、DB_ROOT_PASSWORD、JWT_SECRET 均有值 |
| `yaml: line ...`、`mapping values are not allowed in this context` | 檢查該行及前一行的冒號、引號和縮排，縮排使用空白 |
| `Additional property ... is not allowed` | 檢查鍵名、所在層級及 Compose 版本 |
| `depends on undefined service` | 檢查 depends_on 名稱與 services 定義一致 |
| `variable is not set. Defaulting to a blank string` | 此警告未必造成失敗，但仍須檢查漏設變數或 `$` 插值；`.env` 字面值可使用單引號，Compose YAML 字面 `$` 使用 `$$` |

shell 同名變數會優先於 `.env`，也需注意 `COMPOSE_FILE` 與 `--env-file` 是否指向其他設定。可使用不帶 `--quiet` 的 `docker compose config` 查看解析結果，但輸出可能含憑證，不宜公開貼出。

```powershell
docker compose config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Compose 設定檢查失敗，請修正後再啟動。"
}
docker compose up --build -d --wait --wait-timeout 180
```

此指令不會驗證資料庫密碼能否登入、應用密碼長度、JWT secret 最少 32 字元、連接埠是否占用或映像能否下載。啟動問題請查看：

```powershell
docker compose ps -a
docker compose logs --tail 100 db migrate api frontend
```

## 測試帳號密碼同步

Streamlit 不自帶帳號，使用 FastAPI 驗證 MariaDB 中的 User。`.env` 中的 `SEED_DEMO_USERS=true` 會啟用 admin／viewer 的建立及密碼同步，兩組密碼都必須至少 12 字元，否則 migrate 顯示：

```text
ValueError: Demo passwords must contain at least 12 characters
```

修改 `DEMO_ADMIN_PASSWORD` 或 `DEMO_VIEWER_PASSWORD` 後，重新執行初始化以同步資料庫：

```powershell
docker compose up --build -d --wait --wait-timeout 180 --force-recreate migrate api frontend
```

若仍無法登入，確認 shell 沒有覆寫設定、migrate 已 Exited (0)、seed 已啟用、帳號未停用。設定變更不會調整原角色或重新啟用帳號；也不會撤銷已發出的 JWT。

## 資料庫連線帳密與資料保留

`DB_PASSWORD` 是 MariaDB 應用連線密碼，與上述使用者登入密碼不同。資料庫初始化環境變數只對空資料目錄生效；若變更既有 MariaDB 密碼，需受控更新 DB 帳號與應用設定，不能只改 `.env`。

一般 `docker compose down --timeout 30` 保留 volume。不要為了解決登入問題執行 `down -v`；這會刪除所有資料。更改 COMPOSE_PROJECT_NAME 或 Docker context 也可能使部署連到另一組資料 volume。

## 官方參考

- [docker compose config](https://docs.docker.com/reference/cli/docker/compose/config/)
