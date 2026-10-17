<!-- [skill: go-team-standards · 文档场景] work-boss README -->
# work-boss（监工）

一套给个人与多个 AI agent 共用的“监工”系统：**每天把工作排成时段任务，晚上收集完成度，没做完就催，到期不做就顺延并重排明天。**

- 沟通通道：Lark／飞书（互动卡片＋原生任务，不要求你学新工具）
- 定时：macOS launchd，**确定性脚本**，不依赖任何 LLM
- 协作：计划、任务、日志都是仓库文件，任何 agent 读写文件即可参与
- 原则：证据优先——完成只认 Lark 任务的勾选，监工从不替你宣称完成
- 本地跟得上 Lark：进度每次运行都写回本地（任务卡、进度快照、看板自动刷新），次日计划从白天就持续维护
- 需求不足会主动要：计划工时低于下限，监工在 Lark 问你要需求，回复卡片就能补

## 一图看懂

```
08:30 发今日工作项 ─► 你/agent 执行（勾选或写原因）─► 20:30 询问进度
   ▲                                                      │
   └──────── 00:00 收尾：顺延未完成、重排明天 ◄── 21:00–23:30 每30分钟催促
```

## 文档

| 想知道 | 看 |
|---|---|
| 为什么这样设计、名词定义 | [docs/concept.md](docs/concept.md) |
| 一天里你要做什么 | [docs/usage.md](docs/usage.md) |
| agent 的协作规则 | [AGENTS.md](AGENTS.md) |
| 文件格式与判定规则 | [docs/data-contract.md](docs/data-contract.md) |
| 安装、launchd、权限 | [docs/setup-macos.md](docs/setup-macos.md) |
| 维护与隐私规则 | [docs/maintenance.md](docs/maintenance.md) |
| 做了什么、踩过的坑 | [docs/what-was-done.md](docs/what-was-done.md) |

## 快速开始

```bash
cp config/lark.example.json config/lark.json     # 填 chat_id / owner_open_id / env_file
python3 scripts/lark_ops.py chats                # 列出机器人所在的群
python3 -m unittest discover -s tests            # 测试
cp plans/_TEMPLATE.json plans/$(date +%F).json   # 写今天的计划
python3 scripts/supervisor.py morning --dry-run   # 试运行
bash scripts/install_launchd.sh                  # 安装定时任务
```
## 结合Lark任务 进行排期

<img width="429" height="522" alt="image" src="https://github.com/user-attachments/assets/d27e2a73-eb22-4409-8a0f-16648a5d6e0a" />
<img width="741" height="742" alt="image" src="https://github.com/user-attachments/assets/4a3dcebc-ce49-4b64-9955-93ed55b18bdf" />

## 目录

```
AGENTS.md  CLAUDE.md          协作约定（CLAUDE.md 引用 AGENTS.md）
scripts/   supervisor.py      监工执行器（morning/evening/chase/close/sync/plan-carry/status）
           lark_ops.py        Lark REST 工具（发卡片、建任务、读回复…）
           build_dashboard.py 本地看板生成
           install_launchd.sh launchd 安装／卸载（含自测）
           check_private.sh   推送前私有数据检查
launchd/                      4 个 launchd plist 模板
tests/                        单元测试
tasks/ plans/ worklog/        私有数据（仅模板入库）
docs/                         说明文档
config/lark.example.json      配置模板
```

## 隐私

工作日志、任务卡、计划、运行状态、Lark 配置与任何密钥**都不在这个仓库里**。规则与检查方式见 [docs/maintenance.md](docs/maintenance.md)。

## 现况

可运行的原型，已在单人、单群、单机器人的场景验证。尚未验证与限制见 [docs/what-was-done.md](docs/what-was-done.md) 第 5 节。
