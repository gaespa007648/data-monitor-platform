# 專案管理規範

本檔適用 data-monitor-platform 全專案。

- `artifact/` 是開發者自行管理的內部文件，預設不讀取作為正式規格，不移動、刪除或修改內容；使用者明確要求處理其中資料時才依該次範圍操作。
- `README.md` 是對外分享入口，保留介紹、技術棧、運行／部署、API、帳號與必要操作限制。不加入開發進度、除錯歷史或測試實測結果，不連結或引用 `artifact/`。
- README 需要的專案補充文件一律放在 `docs/`。架構圖與架構文件描述目前實作，不以內部草稿或未完成規劃宣稱已有功能。
- `docs/` 必須自成一體，不依賴或引用 `artifact/`。以原始碼、`compose.yaml`、Dockerfile、`.env.example` 驗證內容；第三方參考整理在對應 docs 文件。
- 保留單一 `compose.yaml` 管理方式，不自行增加環境專用 Compose 檔。
- 文件更新不得公開 `.env` 實際值、密碼雜湊、JWT 或本機絕對路徑；使用可攜的相對連結。
- 修改文件管理、README 或架構文件時，使用 `$data-monitor-project-management` skill；版本化內容在 `.agents/skills/data-monitor-project-management/SKILL.md`，一般 Codex 環境可將該資料夾安裝至使用者 skills 目錄。

可分享的文件分類說明見 [文件管理規範](docs/documentation-policy.md)。
