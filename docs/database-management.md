# 資料庫遷移與連線池管理

本專案使用 MariaDB，透過 SQLAlchemy Async ORM 與 asyncmy 存取資料。Alembic 負責資料表結構的版本管理，SQLAlchemy Engine 負責資料庫連線的生命週期。

## Alembic 遷移管理

遷移是將資料表、欄位及約束的變更保存為有版本的操作，讓各部署環境可依相同順序升級資料庫。修改 ORM 模型本身不會自動更新既有資料表。

| 概念 | 用途 |
| --- | --- |
| revision／down_revision | 標示遷移版本及其前一版本，形成遷移鏈 |
| upgrade／downgrade | 定義往前升版及往回退版的 schema 操作 |
| alembic_version | 記錄資料庫目前已套用的遷移版本 |
| head | 遷移鏈的最新版本；本專案以單一線性遷移鏈管理 |
| ORM metadata | 描述期望的 schema，供遷移比對使用 |

遷移設定在 backend/alembic.ini，環境與版本檔在 backend/migrations。Alembic 透過 asyncmy 建立非同步連線，並以 run_sync 執行遷移操作；目前只支援線上遷移，不輸出離線 SQL 腳本。

Compose 啟動順序為 **db healthy → migrate 成功 → api 啟動**。一次性的 migrate 服務先執行相當於 `alembic upgrade head` 的升版，再執行選用的測試帳號初始化；兩者都成功才以結束碼 0 結束。API 不自行 create_all，也不由每個 API 程序各跑一次遷移。

schema 調整時，應新增遷移版本、檢查操作內容後再套用；自動產生的遷移仍須人工確認，尤其是欄位改名與資料轉換。已分享或套用的歷史版本應保留，後續變更以新版本表達。應用資料讀寫使用 ORM，schema 變更使用 Alembic operations。

MariaDB 的許多 DDL 會隱含提交，不能假設遷移失敗就會完整回滾。升版前須評估備份及相容性；downgrade 也不等於還原備份，移除欄位造成的資料遺失不會自動恢復。遷移期間避免多個流程同時修改 schema。

## SQLAlchemy 連線池

連線池讓多次資料庫工作重用既有連線，降低反覆連線的成本，並限制同時使用的連線數。Engine 管理連線池，AsyncSession 管理單次 ORM 工作與交易；Session 並不等於永久占用一條連線。

目前應用 Engine 採非同步適用的預設連線池，設定如下：

| 設定 | 值 | 意義 |
| --- | --- | --- |
| pool_size | 5 | 按需求建立，池內最多保留 5 條可重用連線，非啟動時立即開滿 |
| max_overflow | 5 | 池滿時最多額外建立 5 條連線，超額連線歸還時關閉 |
| pool_timeout | 30 秒 | 無可用連線時的最長等待時間，不是查詢執行逾時 |
| pool_pre_ping | true | 借出連線前檢查存活，必要時更換失效連線 |
| pool_recycle | 1800 秒 | 借出時汰換存活超過 30 分鐘的連線，不中斷正在執行的工作 |

每個應用 Engine 最多同時使用 **10 條連線**；增加程序或容器會各自建立連線池，資料庫容量需計入所有程序及遷移、管理等額外連線。遷移專用 Engine 使用 NullPool，連線用完即關閉，不維持長期連線池。

每個請求使用獨立 AsyncSession，離開工作範圍時關閉 Session 並歸還連線；未提交交易會回滾。寫入須明確提交交易，不跨並行工作共用 Session。應用關閉時會 dispose Engine 釋放池內連線。

pre_ping 與 recycle 可降低借到失效連線的機會，但不能救回執行中斷線的交易，也不會自動重試整筆工作。連線池耗盡時應檢查長交易、未釋放的 Session 與並行負載，而非只提高連線數。

參考：[Alembic 官方教學](https://alembic.sqlalchemy.org/en/latest/tutorial.html)、[SQLAlchemy 連線池說明](https://docs.sqlalchemy.org/en/20/core/pooling.html)。
