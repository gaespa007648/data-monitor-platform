---
name: data-monitor-project-management
description: 維護 data-monitor-platform 的 README、docs、架構說明與專案文件分類。在此專案調整文件組織或更新部署及架構文件時使用；不套用到其他專案。
---

# Data Monitor Platform 專案管理

先確認工作目錄是 data-monitor-platform，讀取專案根目錄的 `AGENTS.md` 與 `docs/documentation-policy.md`。若從使用者 skills 目錄使用本技能，以上路徑皆相對於目標專案根目錄，不是技能安裝目錄。

## 文件邊界

- `artifact/` 為開發者自行管理的內部文件：不主動讀取來形成正式規格，不搬移、刪除、重新整理或改寫。使用者明確指定處理內部文件時，限於授權範圍；不要因此將內容直接公開。
- README 是對外入口，包含專案介紹、技術棧、運行步驟、Docker 指令、API 文件與測試帳號等實用資訊。不記錄開發過程、修復歷史、實測結果，不引用或連結 artifact。
- README 的專案補充文件必須位於 `docs/`，例如架構圖、架構說明、操作與疑難排解。docs 不依賴 artifact，讀者不需要存取內部文件即可理解及操作。
- 架構依目前原始碼、單一 `compose.yaml`、Dockerfile、`.env.example` 核對；區分目前功能與限制，不把草稿或尚未實作的計畫描述成現況。

## 維護方式

以使用者最新需求與目前程式行為為準。README 保留入口及必要步驟，較長的技術說明整理到對應 docs 文件；第三方官方參考集中於該文件，API 執行期連結可直接保留在 README。架構需要圖示時使用可攜的 Mermaid，標示實際容器、網路與持久化位置。

不要把內部驗證紀錄搬到 docs 來規避分類。不要自行更動 artifact 的 ignore 或追蹤規則，也不要把此技能當成修改應用、啟停容器或清除資料的額外授權。

完成前檢查 README 與 docs 沒有指向 artifact 的連結或其內容引用、沒有開發實測紀錄、沒有真實憑證與本機絕對路徑；確認相對連結存在、文件描述符合原始碼。純文件變更以內容與連結檢查為主，不因此重建或操作服務。
