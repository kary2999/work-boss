<!-- [skill: go-team-standards · 文档场景] work-boss 维护规则 -->
# 维护规则

## 1. 谁维护什么

| 范围 | 规则 |
|---|---|
| `scripts/supervisor.py`、`scripts/lark_ops.py` | 改动必须带测试；改行为要同步 `docs/data-contract.md` |
| `launchd/*.template` | 改触发时间要同步 `docs/concept.md` 与 `docs/setup-macos.md` 的时间表 |
| `AGENTS.md` | 协作规则变更，需用户确认并留说明 |
| `docs/` | 与代码不一致时，以代码为准，并立即修文档 |
| 私有数据（日志、任务卡、计划、状态、配置） | **不入库**，见第 3 节 |

## 2. 改动流程

1. 开分支：`agent/<agent>/<topic>` 或 `fix/<topic>`。
2. 先写（或改）测试，再改实作。
3. 本地验证三件事，缺一不提交：
   ```bash
   python3 -m unittest discover -s tests          # 全绿
   python3 scripts/supervisor.py <cmd> --dry-run  # 涉及流程的改动要试运行
   bash scripts/check_private.sh                  # 私有数据与标识检查
   ```
4. 在 `docs/what-was-done.md` 的“变更记录”追加一条：改了什么、为什么、怎么验证、已知风险。
5. 合入 `main`；对外推送（GitHub）由维护者或有明确授权的流程执行。

## 3. 隐私规则（最重要）

**绝不入库**：

- `worklog/*`（`_TEMPLATE.md` 除外）——你的工作日志，包含工作内容与你写的评论
- `tasks/T*.md`、`plans/*.json`（模板除外）——工作内容
- `.supervisor/`——任务 guid、提问状态
- `config/lark.json`、`PROJECT.md`、任何密钥与 env 文件
- 任何真实的 `oc_` / `ou_` / `cli_` 标识

机制：`.gitignore` 排除 + `scripts/check_private.sh` 在推送前扫描。若不慎提交，**先停止推送**，再用 `git rm --cached` 与历史清理处理；已推送到公开仓库的内容视为已泄漏，需要轮换对应密钥。

**截图**：使用说明用的截图放 `docs/assets/`，且仅限用户明确同意的画面；放进去前确认没有人名、群名、密钥、真实任务内容之外不该公开的信息。

## 4. 发布与版本

- 版本号用语义化：`MAJOR.MINOR.PATCH`；改数据契约的不相容变更要升 MAJOR。
- 每次发布打 tag，并在 `docs/what-was-done.md` 记录。

## 5. 定时任务变更

- 改 launchd 时间：改 `launchd/*.template` → 重新运行 `scripts/install_launchd.sh`。
- 要暂停监工：`bash scripts/install_launchd.sh --uninstall`；恢复再安装。
- **同一时间只能有一种定时方式在调用脚本**（见 docs/setup-macos.md 第 5 节）。

## 6. 常见故障

| 现象 | 原因与处理 |
|---|---|
| 群里没收到早间卡片 | 先看 `.supervisor/logs/morning.err.log` 与 `worklog/*-supervisor-error.md`；常见：外接卷权限、应用权限未发布、`plans/今日.json` 不存在 |
| 安装时自测失败 | 见 docs/setup-macos.md 的“外接卷与隐私权限” |
| `230027 need scope: im:message.group_msg` | 后台开通该权限并发布版本 |
| 卡片重复 | 同时启用了两种定时任务；停用其中一种 |
| 顺延后出现重复任务 | 次日计划文件是别的 agent 写的、未带 `guid`；让该 agent 并入顺延项，或用 `plan-carry` 对照 rollover 日志手工补 |
| 任务列表被子任务刷屏 | 子任务默认不指派；若是旧任务，可在 Lark 内手动取消指派 |
| 旷工判定可疑 | 看 rollover／absent 日志里的追问次数；少于 7 次通常是电脑休眠 |
| 看板或任务卡进度没变 | 看板顶部“数据生成于”＝最近一次监工运行时间；手动 `python3 scripts/supervisor.py sync` 立刻刷新。若定时任务没在跑，见 docs/setup-macos.md；`build_dashboard.py` 单独运行**不会**读 Lark，只重排现有数据 |
| 误报“需求不足” | 先查任务卡有没有 `est_hours`；再看 `plans/` 当日 `total_hours` 与看板“计划”分页；必要时调 `min_plan_hours` |
| 一直没收到“需求不足”卡片 | 计划工时是否真的低于下限；`.supervisor/asks.json` 的 `count`／`last` 是否触发了限流（每天 3 次、间隔 90 分钟） |
| 回复了需求却没进计划 | 必须对**那张卡片**点“回复”（不是在群里发新消息）；下次运行才读取；若目标日计划是别人写的（`generated_by` 非 supervisor），需求会被追加在后面而不是改动既有项 |
| `sync` 很慢（首次） | 首次会回填所有历史日的进度，Lark 单次请求约 1 秒；之后每次只读当天，约 15～40 秒 |

## 7. 不要做的事

- 不要手改 `.supervisor/*.json`（除非按“重置某一天”）。唯一的例外是修正由数据错误造成的坏状态，且要在 `docs/what-was-done.md` 留记录。
- 不要把任务卡的 `status` 手动改成 `done` 以外的“自动值”：`progress`／`status` 由监工回写，手改会在下次同步被覆盖或造成混乱（想把 `blocked`、`done` 设上去是可以的，监工不会动它们）。
- 不要编辑 `generated_by` 为 `supervisor` 的计划文件而不先改掉 `generated_by`，否则下次同步会洗掉修改。
- 不要新增缺 `est_hours` 的任务卡：缺省 1h 会让需求充足性判断失真。
- 不要在脚本里写死 chat_id、open_id、密钥。
- 不要为了“更即时”给共用机器人开长连接或改回调。
- 不要让监工去“判断工作做得好不好”。
