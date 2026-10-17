<!-- [skill: go-team-standards · 文档场景] work-boss 維護規則 -->
# 維護規則

## 1. 誰維護什麼

| 範圍 | 規則 |
|---|---|
| `scripts/supervisor.py`、`scripts/lark_ops.py` | 改動必須帶測試；改行為要同步 `docs/data-contract.md` |
| `launchd/*.template` | 改觸發時間要同步 `docs/concept.md` 與 `docs/setup-macos.md` 的時間表 |
| `AGENTS.md` | 協作規則變更，需使用者確認並留說明 |
| `docs/` | 與程式碼不一致時，以程式碼為準，並立即修文檔 |
| 私有資料（日誌、任務卡、計畫、狀態、配置） | **不入庫**，見第 3 節 |

## 2. 改動流程

1. 開分支：`agent/<agent>/<topic>` 或 `fix/<topic>`。
2. 先寫（或改）測試，再改實作。
3. 本地驗證三件事，缺一不提交：
   ```bash
   python3 -m unittest discover -s tests          # 全綠
   python3 scripts/supervisor.py <cmd> --dry-run  # 涉及流程的改動要乾跑
   bash scripts/check_private.sh                  # 私有資料與標識檢查
   ```
4. 在 `docs/what-was-done.md` 的「變更記錄」追加一條：改了什麼、為什麼、怎麼驗證、已知風險。
5. 合入 `main`；對外推送（GitHub）由維護者或有明確授權的流程執行。

## 3. 隱私規則（最重要）

**絕不入庫**：

- `worklog/*`（`_TEMPLATE.md` 除外）——你的工作日誌，包含工作內容與你寫的評論
- `tasks/T*.md`、`plans/*.json`（範本除外）——工作內容
- `.supervisor/`——任務 guid、提問狀態
- `config/lark.json`、`PROJECT.md`、任何密鑰與 env 檔
- 任何真實的 `oc_` / `ou_` / `cli_` 標識

機制：`.gitignore` 排除 + `scripts/check_private.sh` 在推送前掃描。若不慎提交，**先停止推送**，再用 `git rm --cached` 與歷史清理處理；已推送到公開倉庫的內容視為已洩漏，需要輪換對應密鑰。

**截圖**：使用說明用的截圖放 `docs/assets/`，且僅限使用者明確同意的畫面；放進去前確認沒有人名、群名、密鑰、真實任務內容之外不該公開的資訊。

## 4. 發布與版本

- 版本號用語義化：`MAJOR.MINOR.PATCH`；改資料契約的不相容變更要升 MAJOR。
- 每次發布打 tag，並在 `docs/what-was-done.md` 記錄。

## 5. 排程變更

- 改 launchd 時間：改 `launchd/*.template` → 重新運行 `scripts/install_launchd.sh`。
- 要暫停監工：`bash scripts/install_launchd.sh --uninstall`；恢復再安裝。
- **同一時間只能有一種排程在呼叫腳本**（見 docs/setup-macos.md 第 5 節）。

## 6. 常見故障

| 現象 | 原因與處理 |
|---|---|
| 群裡沒收到早間卡片 | 先看 `.supervisor/logs/morning.err.log` 與 `worklog/*-supervisor-error.md`；常見：外接卷權限、應用權限未發布、`plans/今日.json` 不存在 |
| 安裝時自測失敗 | 見 docs/setup-macos.md 的「外接卷與隱私權限」 |
| `230027 need scope: im:message.group_msg` | 後台開通該權限並發布版本 |
| 卡片重複 | 同時啟用了兩種排程；停用其中一種 |
| 順延後出現重複任務 | 次日計畫檔是別的 agent 寫的、未帶 `guid`；讓該 agent 併入順延項，或用 `plan-carry` 對照 rollover 日誌手工補 |
| 任務列表被子任務刷屏 | 子任務默認不指派；若是舊任務，可在 Lark 內手動取消指派 |
| 旷工判定可疑 | 看 rollover／absent 日誌裡的追問次數；少於 7 次通常是電腦休眠 |
| 看板或任務卡進度沒變 | 看板頂部「資料生成於」＝最近一次監工運行時間；手動 `python3 scripts/supervisor.py sync` 立刻刷新。若排程沒在跑，見 docs/setup-macos.md；`build_dashboard.py` 單獨運行**不會**讀 Lark，只重排現有資料 |
| 誤報「需求不足」 | 先查任務卡有沒有 `est_hours`；再看 `plans/` 當日 `total_hours` 與看板「計畫」分頁；必要時調 `min_plan_hours` |
| 一直沒收到「需求不足」卡片 | 計畫工時是否真的低於下限；`.supervisor/asks.json` 的 `count`／`last` 是否觸發了限流（每天 3 次、間隔 90 分鐘） |
| 回覆了需求卻沒進計畫 | 必須對**那張卡片**點「回覆」（不是在群裡發新消息）；下次運行才讀取；若目標日計畫是別人寫的（`generated_by` 非 supervisor），需求會被追加在後面而不是改動既有項 |
| `sync` 很慢（首次） | 首次會回填所有歷史日的進度，Lark 單次請求約 1 秒；之後每次只讀當天，約 15～40 秒 |

## 7. 不要做的事

- 不要手改 `.supervisor/*.json`（除非按「重置某一天」）。唯一的例外是修正由資料錯誤造成的壞狀態，且要在 `docs/what-was-done.md` 留記錄。
- 不要把任務卡的 `status` 手動改成 `done` 以外的「自動值」：`progress`／`status` 由監工回寫，手改會在下次同步被覆蓋或造成混亂（想把 `blocked`、`done` 設上去是可以的，監工不會動它們）。
- 不要編輯 `generated_by` 為 `supervisor` 的計畫檔而不先改掉 `generated_by`，否則下次同步會洗掉修改。
- 不要新增缺 `est_hours` 的任務卡：缺省 1h 會讓需求充足性判斷失真。
- 不要在腳本裡寫死 chat_id、open_id、密鑰。
- 不要為了「更即時」給共用機器人開長連接或改回調。
- 不要讓監工去「判斷工作做得好不好」。
