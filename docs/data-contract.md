<!-- [skill: go-team-standards · 文档场景] work-boss 資料契約 -->
# 資料契約

任何 agent 只要遵守本契約就能與監工協作。格式變更要同步改本文件與 `tests/`。

## 1. 任務卡 `tasks/T*.md`

Markdown + YAML front matter（只支援 `key: value` 單行）。

```markdown
---
id: T002
title: 任務標題
status: todo        # todo | doing | review | done | blocked
owner:              # agent 標識，未認領留空
est_hours: 1.5      # 預估工時；缺省按 1
blocked_by: []
created: 2026-10-15
due: 2026-10-16
---
## 驗收標準
- [ ] 條目一
- [ ] 條目二
```

- 監工的 backlog 取 `status` 為 `todo`／`doing` 的卡；每張卡的未勾選 `- [ ]`（最多 8 條）成為子任務。
- **`est_hours` 一定要寫**。缺省按 1h，大任務被當成 1h 會讓計畫工時嚴重偏低、觸發誤判的「需求不足」。
- **`images`（可選）**：參考圖路徑，用 `|` 分隔，路徑相對倉庫根目錄，例如 `images: 参考图组/20261017/a.jpg|参考图组/20261017/b.jpg`。監工建 Lark 任務時會把這些圖**作為附件上傳到任務**，並在任務描述裡列出檔名；單張上傳失敗只記錄、不影響任務建立；順延的任務不重複上傳。正文裡也可以用 `![說明](../路徑)` 嵌圖，看板會顯示。
- **`status: cancelled`**：任務卡已作廢。不再進 backlog，其未完成的 Lark 任務也不再被順延。`progress`／`status` 自動回寫**不會**改動它。作廢應在卡片正文註明被什麼取代。
- 建議把大任務**切片**：每張卡 1～3 小時，有 3～6 條可驗收項（變成 Lark 子任務）、對應截圖（`images`）和明確的「不做什麼」。
- 完成與否的**權威**是 Lark 任務，不是這個檔。
- **監工會回寫兩個欄位**（只改這兩行，正文與註解不動）：
  - `progress: 6/7`：Lark 上 `ref` 等於本卡 `id` 的主任務，已完成數／總數（同一個任務跨天只算一次）。
  - `status`：只往前走。有主任務完成 → `doing`；全部完成 → `review`；**不降級，不碰 `done`／`blocked`**，`done` 必須由人確認。
  - 驗收項（`- [ ]`）**不會**被自動勾選：Lark 任務標題與驗收項沒有一一對應關係。

## 2. 計畫檔 `plans/YYYY-MM-DD.json`

```json
{
  "date": "2026-10-16",
  "goal": "今日目標",
  "items": [
    {
      "id": "T002-buttons",
      "ref": "T002",
      "title": "任務名稱",
      "slot": "09:00-10:30",
      "est_hours": 1.5,
      "done_std": "可驗收的一句話",
      "subtasks": ["子任務一", "子任務二"],
      "carried": false,
      "carry_reason": "",
      "guid": ""
    }
  ]
}
```

| 欄位 | 必填 | 說明 |
|---|---|---|
| `id` | 是 | 穩定唯一；同一個 `id` 在當日只會建一次任務（冪等） |
| `title` | 是 | 工作項名稱 |
| `ref` | 否 | 任務卡編號，顯示在標題前綴 `[T002]` |
| `slot` | 否 | `HH:MM-HH:MM`；缺省或 `null` 顯示「待排」，到期取當日 18:00 |
| `est_hours` | 否 | 缺省 1 |
| `subtasks` | 否 | 字串；或 `{"guid","title"}`（沿用已存在的子任務並改期） |
| `carried` / `carry_reason` / `guid` / `url` | 否 | **由監工寫入**：順延項。有 `guid` 的項目早間會「改期」而不是新建，且只改標題與起止時間、**不動描述** |
| `from_requirement` | 否 | 監工寫入：這項來自使用者對「需求不足」卡片的回覆 |
| `images` | 否 | 參考圖路徑陣列（相對倉庫根目錄）。由任務卡的 `images` 帶入；新建任務時作為附件上傳 |

檔案頂層還有兩個欄位決定**誰能覆蓋它**：

| 欄位 | 含義 |
|---|---|
| `generated_by` | `"supervisor"`＝監工生成，可被監工重新生成（20:30、白天同步、00:00 定稿都會刷新）；**其他值或缺省＝人／agent 寫的，監工絕不整檔覆蓋**，只會往後追加使用者補充的需求 |
| `draft` | `true`＝草案，00:00 收尾時會定稿為 `false` |

> 你或 agent 要手動調整監工生成的計畫，**先把 `generated_by` 改成 `"human"`**，否則下一次同步會把你的修改洗掉。

自動排時段：工作視窗 09:00–12:00、14:00–18:00，工時向上取整到 15 分鐘，單項不拆分；日工時上限 7h，順延項無條件保留，超出上限的 backlog 記入 `deferred`。

## 3. 日誌 `worklog/`

- 檔名：`YYYYMMDD-HHMM-<agent>-<task-id>.md`；只新增。
- 監工生成：

| 檔名 | 時機 |
|---|---|
| `YYYYMMDD-2030-supervisor-progress.md` | 晚間詢問時 |
| `YYYYMMDD-HHMM-supervisor-reply.md` | 完成／有說明／有回覆而停止追問時 |
| `YYYYMMDD-2400-supervisor-rollover.md` | 收尾：完成度、順延清單、次日計畫去向 |
| `YYYYMMDD-2400-supervisor-absent.md` | 收尾判定旷工時 |
| `YYYYMMDD-HHMM-supervisor-error.md` | 任一環節失敗 |

## 4. 運行狀態 `.supervisor/`（只有監工腳本寫）

| 檔案 | 內容 |
|---|---|
| `tasks-YYYYMMDD.json` | 當日已建任務：`[{id,guid,summary,slot,est_hours,url,carried,subtasks:[{guid,summary}]}]` |
| `YYYYMMDD.json` | 提問狀態：`{date, question_ids[], replied, reminders}` |
| `progress.json` | **進度快照**：`{days:{日期:{main_done,main_total,sub_done,sub_total,items[],reminders,replied,absent,updated}}}`。每個命令結束時刷新；缺失的歷史日會自動回填。看板與任務卡同步都讀它 |
| `asks.json` | 「需求不足」主動詢問記錄：`{目標日:{mids[],count,last}}` |
| `needs.json` | 使用者回覆補充的需求：`{items:[{id,title,est_hours,target,mid}], ingested:[已讀消息id]}` |
| `logs/` | launchd 的 stdout／stderr |

## 5. 判定規則（chase／close 共用）

```
對每個主任務：
  completed        = Lark 任務已勾選
  reasons          = 未完成時，creator 為使用者的評論
全部 completed                  → 停止追問
有使用者對提問消息的回覆         → 停止追問
所有未完成項都有 reasons          → 停止追問，記為「有說明」
否則                            → 對「未完成且無說明」的項發催促

收尾（00:00）：
  旷工 = 無 completed 且 無 reasons 且 無使用者回覆
  順延 = 所有未完成主任務（含其未完成子任務）
```

## 5b. 需求充足性（20:30、白天同步、23:00 追問後、00:00 收尾、早間都會檢查）

```
目標日 = 要檢查的那一天（晚上檢查明天，早上檢查今天）
總工時 = 該日計畫所有項的 est_hours 之和
總工時 < min_plan_hours（默認 4h，config/lark.json 可改）→ 「需求不足」
```

- 觸發後在 Lark 發「明日／今日需求不足」卡片：列出已排項與工時，說明三種補法。
- **你對這張卡片點「回覆」，每行寫一條需求**（行內可寫 `2h`／`1.5小時` 表示估時，缺省 1h；`@機器人` 與行首序號會被忽略）。監工下次運行讀取回覆，併入目標日計畫（排在現有時段之後），並記入 `needs.json`。
- 限流：同一天最多主動要 **3 次**，兩次至少間隔 **90 分鐘**；00:00 收尾可再要 1 次（不受間隔限制）。
- 工時**超載**（> 7.5h）只在看板標「超載」，不會在 Lark 發消息。

## 5c. 每個命令結束時固定做的事（post_run）

1. 補齊缺失的歷史日進度 → 寫 `progress.json`
2. 依 `progress.json` 回寫任務卡 `progress`／`status`
3. 讀取使用者對「需求不足」卡片的回覆，併入計畫
4. 維護次日計畫草案（只覆蓋 `generated_by=supervisor` 的檔）；需求不足就要需求
5. 刷新 `docs/dashboard.html`（失敗不影響主流程）

## 6. 冪等與重入

- `morning`：已在狀態檔中的 `id` 不再建立；無新增時不重發卡片（`--force` 可強制）。
- `close`：已存在當日 `rollover` 日誌則跳過。
- 次日計畫檔：`generated_by=supervisor` 的可被重新生成；人／agent 寫的**不覆蓋**。
- `evening` 與 `chase` **不是**冪等：重複運行會重複發卡片，所以同一時間只能啟用一種排程（launchd 或過渡的 Claude 排程）。
