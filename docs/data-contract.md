<!-- [skill: go-team-standards · 文档场景] work-boss 数据契约 -->
# 数据契约

任何 agent 只要遵守本契约就能与监工协作。格式变更要同步改本文件与 `tests/`。

## 1. 任务卡 `tasks/T*.md`

Markdown + YAML front matter（只支持 `key: value` 单行）。

```markdown
---
id: T002
title: 任务标题
status: todo        # todo | doing | review | done | blocked
owner:              # agent 标识，未认领留空
est_hours: 1.5      # 预估工时；缺省按 1
blocked_by: []
created: 2026-10-15
due: 2026-10-16
---
## 验收标准
- [ ] 条目一
- [ ] 条目二
```

- 监工的 backlog 取 `status` 为 `todo`／`doing` 的卡；每张卡的未勾选 `- [ ]`（最多 8 条）成为子任务。
- **`est_hours` 一定要写**。缺省按 1h，大任务被当成 1h 会让计划工时严重偏低、触发误判的“需求不足”。
- **`images`（可选）**：参考图路径，用 `|` 分隔，路径相对仓库根目录，例如 `images: 参考图组/20261017/a.jpg|参考图组/20261017/b.jpg`。监工建 Lark 任务时会把这些图**作为附件上传到任务**，并在任务描述里列出文件名；单张上传失败只记录、不影响任务建立；顺延的任务不重复上传。正文里也可以用 `![说明](../路径)` 嵌图，看板会显示。
- **`status: cancelled`**：任务卡已作废。不再进 backlog，其未完成的 Lark 任务也不再被顺延。`progress`／`status` 自动回写**不会**改动它。作废应在卡片正文注明被什么取代。
- 建议把大任务**切片**：每张卡 1～3 小时，有 3～6 条可验收项（变成 Lark 子任务）、对应截图（`images`）和明确的“不做什么”。
- 完成与否的**权威**是 Lark 任务，不是这个文件。
- **监工会回写两个栏位**（只改这两行，正文与注解不动）：
  - `progress: 6/7`：Lark 上 `ref` 等于本卡 `id` 的主任务，已完成数／总数（同一个任务跨天只算一次）。
  - `status`：只往前走。有主任务完成 → `doing`；全部完成 → `review`；**不降级，不碰 `done`／`blocked`**，`done` 必须由人确认。
  - 验收项（`- [ ]`）**不会**被自动勾选：Lark 任务标题与验收项没有一一对应关系。

## 2. 计划文件 `plans/YYYY-MM-DD.json`

```json
{
  "date": "2026-10-16",
  "goal": "今日目标",
  "items": [
    {
      "id": "T002-buttons",
      "ref": "T002",
      "title": "任务名称",
      "slot": "09:00-10:30",
      "est_hours": 1.5,
      "done_std": "可验收的一句话",
      "subtasks": ["子任务一", "子任务二"],
      "carried": false,
      "carry_reason": "",
      "guid": ""
    }
  ]
}
```

| 栏位 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | 稳定唯一；同一个 `id` 在当日只会建一次任务（幂等） |
| `title` | 是 | 工作项名称 |
| `ref` | 否 | 任务卡编号，显示在标题前缀 `[T002]` |
| `slot` | 否 | `HH:MM-HH:MM`；缺省或 `null` 显示“待排”，到期取当日 18:00 |
| `est_hours` | 否 | 缺省 1 |
| `subtasks` | 否 | 字符串；或 `{"guid","title"}`（沿用已存在的子任务并改期） |
| `carried` / `carry_reason` / `guid` / `url` | 否 | **由监工写入**：顺延项。有 `guid` 的项目早间会“改期”而不是新建，且只改标题与起止时间、**不动描述** |
| `from_requirement` | 否 | 监工写入：这项来自用户对“需求不足”卡片的回复 |
| `images` | 否 | 参考图路径阵列（相对仓库根目录）。由任务卡的 `images` 带入；新建任务时作为附件上传 |

文件顶层还有两个栏位决定**谁能覆盖它**：

| 栏位 | 含义 |
|---|---|
| `generated_by` | `"supervisor"`＝监工生成，可被监工重新生成（20:30、白天同步、00:00 定稿都会刷新）；**其他值或缺省＝人／agent 写的，监工绝不整个文件覆盖**，只会往后追加用户补充的需求 |
| `draft` | `true`＝草案，00:00 收尾时会定稿为 `false` |

> 你或 agent 要手动调整监工生成的计划，**先把 `generated_by` 改成 `"human"`**，否则下一次同步会把你的修改洗掉。

自动排时段：工作窗口 09:00–12:00、14:00–18:00，工时向上取整到 15 分钟，单项不拆分；日工时上限 7h，顺延项无条件保留，超出上限的 backlog 记入 `deferred`。

## 3. 日志 `worklog/`

- 文件名：`YYYYMMDD-HHMM-<agent>-<task-id>.md`；只新增。
- 监工生成：

| 文件名 | 时机 |
|---|---|
| `YYYYMMDD-2030-supervisor-progress.md` | 晚间询问时 |
| `YYYYMMDD-HHMM-supervisor-reply.md` | 完成／有说明／有回复而停止追问时 |
| `YYYYMMDD-2400-supervisor-rollover.md` | 收尾：完成度、顺延清单、次日计划去向 |
| `YYYYMMDD-2400-supervisor-absent.md` | 收尾判定旷工时 |
| `YYYYMMDD-HHMM-supervisor-error.md` | 任一环节失败 |

## 4. 运行状态 `.supervisor/`（只有监工脚本写）

| 文件 | 内容 |
|---|---|
| `tasks-YYYYMMDD.json` | 当日已建任务：`[{id,guid,summary,slot,est_hours,url,carried,subtasks:[{guid,summary}]}]` |
| `YYYYMMDD.json` | 提问状态：`{date, question_ids[], replied, reminders}` |
| `progress.json` | **进度快照**：`{days:{日期:{main_done,main_total,sub_done,sub_total,items[],reminders,replied,absent,updated}}}`。每个命令结束时刷新；缺失的历史日会自动回填。看板与任务卡同步都读它 |
| `asks.json` | “需求不足”主动询问记录：`{目标日:{mids[],count,last}}` |
| `needs.json` | 用户回复补充的需求：`{items:[{id,title,est_hours,target,mid}], ingested:[已读消息id]}` |
| `wiki.json` | Lark 知识库页面标识：`{node_token, obj_token, space_id}`，首次同步时创建（见 docs/setup-macos.md 第 8 节） |
| `logs/` | launchd 的 stdout／stderr |

## 5. 判定规则（chase／close 共用）

```
对每个主任务：
  completed        = Lark 任务已勾选
  reasons          = 未完成时，creator 为用户的评论
全部 completed                  → 停止追问
有用户对提问消息的回复         → 停止追问
所有未完成项都有 reasons          → 停止追问，记为“有说明”
否则                            → 对“未完成且无说明”的项发催促

收尾（00:00）：
  旷工 = 无 completed 且 无 reasons 且 无用户回复
  顺延 = 所有未完成主任务（含其未完成子任务）
```

## 5b. 需求充足性（20:30、白天同步、23:00 追问后、00:00 收尾、早间都会检查）

```
目标日 = 要检查的那一天（晚上检查明天，早上检查今天）
总工时 = 该日计划所有项的 est_hours 之和
总工时 < min_plan_hours（默认 4h，config/lark.json 可改）→ “需求不足”
```

- 触发后在 Lark 发“明日／今日需求不足”卡片：列出已排项与工时，说明三种补法。
- **你对这张卡片点“回复”，每行写一条需求**（行内可写 `2h`／`1.5小时` 表示估时，缺省 1h；`@机器人` 与行首序号会被忽略）。监工下次运行读取回复，并入目标日计划（排在现有时段之后），并记入 `needs.json`。
- 限流：同一天最多主动要 **3 次**，两次至少间隔 **90 分钟**；00:00 收尾可再要 1 次（不受间隔限制）。
- 工时**超载**（> 7.5h）只在看板标“超载”，不会在 Lark 发消息。

## 5c. 每个命令结束时固定做的事（post_run）

1. 补齐缺失的历史日进度 → 写 `progress.json`
2. 依 `progress.json` 回写任务卡 `progress`／`status`
3. 读取用户对“需求不足”卡片的回复，并入计划
4. 维护次日计划草案（只覆盖 `generated_by=supervisor` 的文件）；需求不足就要需求
5. 刷新 `docs/dashboard.html`（失败不影响主流程）

## 6. 幂等与重入

- `morning`：已在状态文件中的 `id` 不再建立；无新增时不重发卡片（`--force` 可强制）。
- `close`：已存在当日 `rollover` 日志则跳过。
- 次日计划文件：`generated_by=supervisor` 的可被重新生成；人／agent 写的**不覆盖**。
- `evening` 与 `chase` **不是**幂等：重复运行会重复发卡片，所以同一时间只能启用一种定时方式（launchd 或过渡的 Claude 定时任务）。
