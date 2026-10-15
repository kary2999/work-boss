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
- 完成與否的**權威**是 Lark 任務，不是這個檔；卡片 status 由 owner／人維護。

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
| `carried` / `carry_reason` / `guid` | 否 | **由監工收尾時寫入**：順延項。有 `guid` 的項目早間會「改期」而不是新建 |

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

## 6. 冪等與重入

- `morning`：已在狀態檔中的 `id` 不再建立；無新增時不重發卡片（`--force` 可強制）。
- `close`：已存在當日 `rollover` 日誌則跳過。
- 次日計畫檔已存在時**不覆蓋**。
- `evening` 與 `chase` **不是**冪等：重複運行會重複發卡片，所以同一時間只能啟用一種排程（launchd 或過渡的 Claude 排程）。
