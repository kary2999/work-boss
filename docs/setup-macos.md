<!-- [skill: go-team-standards · 文档场景] work-boss 安裝與 macOS 排程配置 -->
# 安裝與 macOS 排程

## 1. 前置條件

| 項目 | 說明 |
|---|---|
| macOS | 使用 launchd（用戶級，不需要 sudo） |
| Python 3 | 只用標準庫，3.9 即可；`/usr/bin/python3` 可用 |
| Lark／飛書自建應用 | 啟用**機器人**能力，並已把機器人加進「監工群」 |
| 應用權限 | 見下表；開通後要**創建版本並發布**才生效 |
| 密鑰檔 | 一個倉庫之外的 env 檔，含 app id／secret／domain 三個鍵 |

### 應用權限（實際遇到過的報錯與對應）

| 能力 | 報錯時的線索 | 需要的權限 |
|---|---|---|
| 讀取群列表 | `99991672 … im:chat` | `im:chat:readonly` 或 `im:chat` |
| 讀取群消息（判斷回覆） | `230027 need scope: im:message.group_msg` | `im:message.group_msg`（以及 `im:message` 或 `im:message:readonly`） |
| 以機器人發消息／卡片 | — | `im:message:send_as_bot` |
| 建任務、子任務、改任務、讀狀態與評論 | — | 任務讀寫權限（task v2；未逐一核對權限名，缺權限時 API 會在 `msg` 裡寫明需要的 scope） |

> 與其他系統共用同一個機器人時：**不要**為本系統開第二條長連接或改事件訂閱，本系統只用 REST。

## 2. 配置

```bash
cp config/lark.example.json config/lark.json
# 編輯 config/lark.json：chat_id、owner_open_id、env_file
python3 scripts/lark_ops.py chats        # 列出機器人所在的群，找到 chat_id
```

- `owner_open_id`：負責人（你）的 open_id。可從你在群裡發的消息的 `sender_id` 取得：`python3 scripts/lark_ops.py poll 10`。
- `env_file` 指向的檔案至少要有 `config/lark.json` 裡 `app_id_key`／`app_secret_key`／`domain_key` 指定的三個鍵。domain 值含 `lark` 走 `open.larksuite.com`，否則走 `open.feishu.cn`。
- `config/lark.json` 已被 `.gitignore` 排除，不要提交。

## 3. 先乾跑

```bash
python3 -m unittest discover -s tests                  # 單元測試
python3 scripts/supervisor.py status                    # 本地狀態
python3 scripts/supervisor.py morning --dry-run         # 只印出將建立的任務與卡片，不寫入
```

`--dry-run` 的讀取類呼叫（任務狀態、評論、回覆）照常執行，寫入類（建任務、發卡片、寫日誌、寫狀態）只印出。

## 4. 安裝 launchd

```bash
bash scripts/install_launchd.sh            # 安裝 morning / evening / chase / close
launchctl list | grep workboss             # 確認已載入
bash scripts/install_launchd.sh --uninstall   # 卸載
```

| Label | 觸發 |
|---|---|
| `com.workboss.morning` | 每天 08:30 |
| `com.workboss.evening` | 每天 20:30 |
| `com.workboss.chase` | 每天 21:00、21:30、22:00、22:30、23:00、23:30 |
| `com.workboss.close` | 每天 00:00（收尾昨日） |

日誌在 `.supervisor/logs/*.log`。Mac 睡眠時錯過的觸發會在喚醒後補跑。

### 重要：外接卷與隱私權限

安裝腳本會先做自測。**若倉庫放在外接卷（如 `/Volumes/...`），launchd 背景環境通常讀不到**，表現為 `Operation not permitted`，安裝會被攔下。解決辦法二選一：

- **A（推薦）**：把倉庫放到家目錄，例如 `~/Code/work-boss`，再安裝。launchd 能讀家目錄（`Desktop`／`Documents`／`Downloads` 另有隱私保護，也別放那裡）。
- **B**：系統設定 → 隱私與安全性 → 完全磁碟存取權限 → 加入 Python（真實二進制通常在 `/Library/Developer/CommandLineTools/usr/bin/python3`），重新運行安裝腳本。

> 這是在實際環境裡驗證過的現象：同一個 launchd 任務可以讀家目錄，讀外接卷被拒。

## 5. 只啟用一種排程

`evening` 與 `chase` 不是冪等的。若同時啟用 launchd 與其他排程（例如某個 AI 客戶端的定時任務）去呼叫同一個腳本，群裡會收到重複卡片。安裝 launchd 後，請停用其他排程。

## 6. 補產出與手動操作

```bash
python3 scripts/supervisor.py plan-carry --date 2026-10-15    # 只生成「順延＋次日計畫」，不發消息
python3 scripts/supervisor.py morning --date 2026-10-16 --force
python3 scripts/supervisor.py close --date 2026-10-15 --dry-run
python3 scripts/build_dashboard.py                           # 生成本地看板 docs/dashboard.html
```

## 7. 重置某一天

只在確定要重來時做（會導致該日任務被重建）：刪除 `.supervisor/tasks-YYYYMMDD.json` 與 `.supervisor/YYYYMMDD.json`，並自行到 Lark 處理已建的任務。
