<!-- [skill: go-team-standards · 文档场景] work-boss 多 agent 协作约定 -->
# AGENTS.md

本文件是**所有 agent（Claude Code、Codex、Cursor、Hermes 等）与人类**在此仓库工作时的唯一协作约定。开工前读完。

> work-boss 是一套“监工”系统：把每天的工作排成时段任务，晚上收集完成度，没做完就催、到期不做就顺延并重排明天。
> 概念见 [docs/concept.md](docs/concept.md)，数据格式见 [docs/data-contract.md](docs/data-contract.md)。

## 1. 先读这些

1. 本文件
2. `PROJECT.md`（若存在：当前项目的背景；它是私有文件，不入库）
3. `plans/` 今日计划与 `tasks/` 中与你相关的任务卡
4. `worklog/` 最近 3 份日志（含监工自动生成的 `*-supervisor-*.md`）

## 2. 目录与谁能写

| 路径 | 内容 | 谁写 | 入库 |
|---|---|---|---|
| `tasks/T*.md` | 任务卡（目标／验收项／估时／状态） | agent 与人；监工只回写 `progress`、`status` | 否（私有） |
| `plans/YYYY-MM-DD.json` | 当日时段计划，监工据此建 Lark 任务 | agent、人；`generated_by=supervisor` 的由监工维护 | 否（私有） |
| `worklog/*.md` | 工作日志，只增不改 | agent、人、监工 | **否（私有）** |
| `.supervisor/` | 监工运行状态（任务 guid、提问状态、日志） | **只有监工脚本** | 否 |
| `config/lark.json` | Lark 群与负责人标识 | 人 | 否 |
| `scripts/`、`launchd/`、`docs/`、`tests/` | 系统本体 | 维护者（见 docs/maintenance.md） | 是 |
| `*/_TEMPLATE.*`、`config/lark.example.json` | 模板 | 维护者 | 是 |

## 3. 任务协议

1. 任务卡放 `tasks/`，从 `tasks/_TEMPLATE.md` 复制；`status`：`todo → doing → review → done`，异常 `blocked`。
2. **认领**：把 `owner` 改成自己的标识、`status` 改成 `doing`，并**立即提交**。未提交的认领视为无效。
3. 已被他人认领（`doing`）的任务，不得改其范围内的文件；需要协调就在日志或任务卡 `blocked_by` 留言。
4. 验收项（`- [ ]`）由 owner 勾选；`done` 需人类确认。
5. 估时写在 `est_hours`，监工用它排时段。

## 4. 计划协议（任何 agent 都可以当“计划者”）

- 在 `plans/YYYY-MM-DD.json` 写当日计划（格式见 `plans/_TEMPLATE.json`）。`id` 要稳定且唯一；子任务用字符串阵列。**写计划文件时把 `generated_by` 设为 `"human"` 或你的 agent 名**，监工就不会整个文件覆盖它。
- 文件顶层 `generated_by` 为 `"supervisor"` 的是**监工自动维护的草案**：20:30、白天同步、00:00 都会重新生成。你想手动改它，**先把 `generated_by` 改掉**，否则下次同步会洗掉你的改动。
- 没有计划文件时，监工会用“用户补充的需求 + 任务卡 backlog”自动生成；两者都空才发“今日无计划”并在 Lark 要需求。
- 任务卡**一定要写 `est_hours`**：缺省按 1h，会让计划工时偏低、误报“需求不足”。
- 当日计划工时低于下限（默认 4h）时，监工会在 Lark 主动要需求；用户回复的内容会自动并入计划，不需要 agent 处理。

## 5. 日志协议

- 文件名 `YYYYMMDD-HHMM-<agent>-<task-id>.md`，从 `worklog/_TEMPLATE.md` 复制。
- **只新增，不修改既有文件**；更正以新日志说明。
- 必须如实记录：做了什么、为什么、怎么验证、**没做／没验证的**、风险、下一步。
- 写给“完全不知道前情的下一个 agent”。
- 监工自动生成的文件名带 `supervisor`（`progress`／`reply`／`rollover`／`absent`／`error`），不要手改。

## 6. 与监工的关系

- 完成与否**以 Lark 任务的勾选为准**，监工不会替你宣称完成，也不会把“没证据”当完成。
- 监工会把 Lark 进度写回本地：任务卡 front matter 的 `progress`、`status`（只往前走：`todo→doing→review`，**不会设 `done`**，不降级）。**你不需要也不应该手动维护这两个栏位**；验收项 `- [ ]` 仍由 owner 勾选。
- 本地看板 `docs/dashboard.html` 由监工在每次运行后自动刷新；读它之前先看页面顶部的“数据生成于”。
- 未完成请在 Lark 任务的**评论**写原因（有原因＝有回应，不算旷工）。
- **不要手动改 `.supervisor/`**；要重置请见 docs/maintenance.md。
- 定时由 macOS launchd 或过渡的 Claude 定时任务调用 `scripts/supervisor.py`，**同一时间只能启用一种**，否则会重复发消息。

## 7. 并行与提交

- 每个 agent 用独立 git worktree 与分支 `agent/<agent>/<task-id>`；禁止 `git push --force`、禁止改写已合入 `main` 的历史。
- 提交消息：`<type>(<scope>): <摘要>`，type 取 `feat|fix|docs|refactor|test|chore`。
- 提交前跑 `python3 -m unittest discover -s tests` 与 `bash scripts/check_private.sh`。

## 8. 隐私红线（违反就是事故）

- **不得提交**：`worklog/*`（模板除外）、`tasks/T*.md`、`plans/*.json`（模板除外）、`.supervisor/`、`config/lark.json`、`PROJECT.md`、任何密钥、任何 `oc_`／`ou_`／`cli_` 开头的真实标识。
- 密钥只能放在仓库之外的 env 文件，由 `config/lark.json` 的 `env_file` 指向，运行时读取。
- 不得输出、记录或转贴密钥；不得修改共用机器人所属的其他系统（如 Hermes）的任何配置。
- 对外发送（Lark 消息、GitHub 推送）属于对外动作：由维护者或有明确授权的流程执行。

## 9. 工作准则

- **文字一律使用简体中文**：任务卡、计划、日志、文档、代码里的提示文案、Lark 任务与卡片都用简体，用语按中国大陆习惯（项目、文件、默认、回复、模板、定时任务，不用“专案、档案、预设、回覆、范本、排程”）。新增或修改文字时直接写简体；繁体内容用 `ICU Hant-Hans` 转换后再校正词汇。
- **不臆测**：没看过的代码、没跑过的命令不下结论；不确定就写“不确定／待验证”。
- **找根因**：修 bug 不只消除症状。
- **验证后再说完成**；没跑的步骤在日志与回复里明说。
- **诚实交付**：有问题如实承认，跳过的步骤明说，不粉饰。
- 不可逆操作（删数据、对外发送、改生产设置）先向用户确认。

## 10. 变更本文件

修改 `AGENTS.md` 属协作规则变更：须由用户确认，并在 `docs/what-was-done.md` 或日志留一份说明。
