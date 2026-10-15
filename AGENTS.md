<!-- [skill: go-team-standards · 文档场景] work-boss 多 agent 協作約定 -->
# AGENTS.md

本文件是**所有 agent（Claude Code、Codex、Cursor、Hermes 等）與人類**在此倉庫工作時的唯一協作約定。開工前讀完。

> work-boss 是一套「監工」系統：把每天的工作排成時段任務，晚上收集完成度，沒做完就催、到期不做就順延並重排明天。
> 概念見 [docs/concept.md](docs/concept.md)，資料格式見 [docs/data-contract.md](docs/data-contract.md)。

## 1. 先讀這些

1. 本文件
2. `PROJECT.md`（若存在：當前專案的背景；它是私有檔，不入庫）
3. `plans/` 今日計畫與 `tasks/` 中與你相關的任務卡
4. `worklog/` 最近 3 份日誌（含監工自動生成的 `*-supervisor-*.md`）

## 2. 目錄與誰能寫

| 路徑 | 內容 | 誰寫 | 入庫 |
|---|---|---|---|
| `tasks/T*.md` | 任務卡（目標／驗收項／狀態） | agent 與人 | 否（私有） |
| `plans/YYYY-MM-DD.json` | 當日時段計畫，監工據此建 Lark 任務 | agent、人、監工的順延 | 否（私有） |
| `worklog/*.md` | 工作日誌，只增不改 | agent、人、監工 | **否（私有）** |
| `.supervisor/` | 監工運行狀態（任務 guid、提問狀態、日誌） | **只有監工腳本** | 否 |
| `config/lark.json` | Lark 群與負責人標識 | 人 | 否 |
| `scripts/`、`launchd/`、`docs/`、`tests/` | 系統本體 | 維護者（見 docs/maintenance.md） | 是 |
| `*/_TEMPLATE.*`、`config/lark.example.json` | 範本 | 維護者 | 是 |

## 3. 任務協議

1. 任務卡放 `tasks/`，從 `tasks/_TEMPLATE.md` 複製；`status`：`todo → doing → review → done`，異常 `blocked`。
2. **認領**：把 `owner` 改成自己的標識、`status` 改成 `doing`，並**立即提交**。未提交的認領視為無效。
3. 已被他人認領（`doing`）的任務，不得改其範圍內的檔案；需要協調就在日誌或任務卡 `blocked_by` 留言。
4. 驗收項（`- [ ]`）由 owner 勾選；`done` 需人類確認。
5. 估時寫在 `est_hours`，監工用它排時段。

## 4. 計畫協議（任何 agent 都可以當「排程者」）

- 在 `plans/YYYY-MM-DD.json` 寫當日計畫（格式見 `plans/_TEMPLATE.json`）。`id` 要穩定且唯一；子任務用字串陣列。
- **已存在的計畫檔不要整個覆蓋**；要改就只改你負責的條目，其餘保持。
- 監工在 00:00 會把未完成項順延進次日計畫檔（若該檔已存在則**不覆蓋**，只在收尾日誌提示）。
- 沒有計畫檔時，早間監工只會發「今日無計畫」提示，不會自己編任務。

## 5. 日誌協議

- 檔名 `YYYYMMDD-HHMM-<agent>-<task-id>.md`，從 `worklog/_TEMPLATE.md` 複製。
- **只新增，不修改既有檔案**；更正以新日誌說明。
- 必須如實記錄：做了什麼、為什麼、怎麼驗證、**沒做／沒驗證的**、風險、下一步。
- 寫給「完全不知道前情的下一個 agent」。
- 監工自動生成的檔名帶 `supervisor`（`progress`／`reply`／`rollover`／`absent`／`error`），不要手改。

## 6. 與監工的關係

- 完成與否**以 Lark 任務的勾選為準**，監工不會替你宣稱完成，也不會把「沒證據」當完成。
- 未完成請在 Lark 任務的**評論**寫原因（有原因＝有回應，不算旷工）。
- **不要手動改 `.supervisor/`**；要重置請見 docs/maintenance.md。
- 定時由 macOS launchd 或過渡的 Claude 排程呼叫 `scripts/supervisor.py`，**同一時間只能啟用一種**，否則會重複發消息。

## 7. 並行與提交

- 每個 agent 用獨立 git worktree 與分支 `agent/<agent>/<task-id>`；禁止 `git push --force`、禁止改寫已合入 `main` 的歷史。
- 提交訊息：`<type>(<scope>): <摘要>`，type 取 `feat|fix|docs|refactor|test|chore`。
- 提交前跑 `python3 -m unittest discover -s tests` 與 `bash scripts/check_private.sh`。

## 8. 隱私紅線（違反就是事故）

- **不得提交**：`worklog/*`（範本除外）、`tasks/T*.md`、`plans/*.json`（範本除外）、`.supervisor/`、`config/lark.json`、`PROJECT.md`、任何密鑰、任何 `oc_`／`ou_`／`cli_` 開頭的真實標識。
- 密鑰只能放在倉庫之外的 env 檔，由 `config/lark.json` 的 `env_file` 指向，運行時讀取。
- 不得輸出、記錄或轉貼密鑰；不得修改共用機器人所屬的其他系統（如 Hermes）的任何配置。
- 對外發送（Lark 消息、GitHub 推送）屬於對外動作：由維護者或有明確授權的流程執行。

## 9. 工作準則

- **不臆測**：沒看過的程式碼、沒跑過的命令不下結論；不確定就寫「不確定／待驗證」。
- **找根因**：修 bug 不只消除症狀。
- **驗證後再說完成**；沒跑的步驟在日誌與回覆裡明說。
- **誠實交付**：有問題如實承認，跳過的步驟明說，不粉飾。
- 不可逆操作（刪資料、對外發送、改生產設定）先向使用者確認。

## 10. 變更本文件

修改 `AGENTS.md` 屬協作規則變更：須由使用者確認，並在 `docs/what-was-done.md` 或日誌留一份說明。
