# JWT 認證機制

JWT（JSON Web Token）用於證明 API 請求的使用者身分。本專案由 FastAPI 發出及驗證權杖，Streamlit 保存登入 session 並在呼叫 API 時附帶權杖。

## 格式與內容

JWT 由三段以句點分隔的內容組成：`Header.Payload.Signature`。Header 與 Payload 採 Base64URL 編碼，可被讀取；簽章用於驗證內容未被竄改，並不加密內容。因此權杖中不放密碼或密碼雜湊。

| 部分 | 本專案內容 |
| --- | --- |
| Header | 類型 JWT、簽章演算法 HS256 |
| Payload | 使用者識別、簽發與到期時間、簽發者及接收對象 |
| Signature | 以 `.env` 的 JWT_SECRET 進行 HMAC-SHA256 簽章 |

Payload 包含以下必要 claims：

| Claim | 意義 | 本專案設定 |
| --- | --- | --- |
| sub | 使用者識別 | User id 的字串形式 |
| iat | 簽發時間 | UTC Unix 時間戳，單位為秒 |
| exp | 到期時間 | 簽發時間加 60 分鐘 |
| iss | 簽發者 | data-monitor-platform |
| aud | 接收對象 | data-monitor-api |

權杖有效期固定為 **60 分鐘（3600 秒）**，不是滑動到期；呼叫 API 不會延長有效期。登入回應提供 access_token、token_type=bearer 與 expires_in=3600。

## 簽發與驗證流程

1. 使用者向 `POST /api/v1/auth/tokens` 提交帳密。後端以 Argon2 驗證資料庫密碼雜湊，確認帳號啟用後簽發 JWT。
2. 後續請求以 `Authorization: Bearer <access_token>` 傳送權杖。
3. 後端只接受 HS256，使用 JWT_SECRET 驗證簽章，要求上述 claims 存在，並由 PyJWT 檢查時間與 iss、aud 是否符合設定。
4. 將 sub 轉為使用者 id，透過 ORM 重新查詢帳號，確認仍存在且啟用。
5. 通過身分驗證後，依資料庫中的目前角色判斷操作權限；角色不寫入 JWT。

缺少或無效權杖、權杖過期、帳號不存在或停用，回傳 **401 Unauthorized**。登入有效但權限不足，例如 viewer 查詢系統管理的使用者列表或修改角色，回傳 **403 Forbidden**。

## 有效期與目前限制

目前沒有 refresh token 或權杖撤銷機制，過期後需重新登入。Streamlit 登入後會將 JWT 存入同一瀏覽器來源的 localStorage；重新連線時取回並呼叫 `/api/v1/users/me` 驗證，驗證通過才恢復登入。登出或驗證失敗時會清除瀏覽器儲存的 JWT。共用電腦應主動登出，瀏覽器儲存的權杖也會受同來源腳本存取；對外部署須使用 HTTPS 並防範 XSS。變更密碼不會使先前簽發的 JWT 立即失效。帳號刪除或停用則會在後續請求的資料庫檢查中遭拒。

JWT_SECRET 至少需 32 個字元，由後端保密；不可與 JWT 一起公開。對外連線需使用 HTTPS，因為持有有效 Bearer token 者即可代表該使用者發出請求。

參考：[PyJWT 官方說明](https://pyjwt.readthedocs.io/en/stable/usage.html)。
