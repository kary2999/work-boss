<!-- [skill: go-team-standards · 文档场景] work-boss 安装与 macOS 定时任务配置 -->
# 安装与 macOS 定时任务

## 1. 前置条件

| 项目 | 说明 |
|---|---|
| macOS | 使用 launchd（用户级，不需要 sudo） |
| Python 3 | 只用标准库，3.9 即可；`/usr/bin/python3` 可用 |
| Lark／飞书自建应用 | 启用**机器人**能力，并已把机器人加进“监工群” |
| 应用权限 | 见下表；开通后要**创建版本并发布**才生效 |
| 密钥文件 | 一个仓库之外的 env 文件，含 app id／secret／domain 三个键 |

### 应用权限（实际遇到过的报错与对应）

| 能力 | 报错时的线索 | 需要的权限 |
|---|---|---|
| 读取群列表 | `99991672 … im:chat` | `im:chat:readonly` 或 `im:chat` |
| 读取群消息（判断回复） | `230027 need scope: im:message.group_msg` | `im:message.group_msg`（以及 `im:message` 或 `im:message:readonly`） |
| 以机器人发消息／卡片 | — | `im:message:send_as_bot` |
| 建任务、子任务、改任务、读状态与评论 | — | 任务读写权限（task v2；未逐一核对权限名，缺权限时 API 会在 `msg` 里写明需要的 scope） |

> 与其他系统共用同一个机器人时：**不要**为本系统开第二条长连接或改事件订阅，本系统只用 REST。

## 2. 配置

```bash
cp config/lark.example.json config/lark.json
# 编辑 config/lark.json：chat_id、owner_open_id、env_file
python3 scripts/lark_ops.py chats        # 列出机器人所在的群，找到 chat_id
```

- `owner_open_id`：负责人（你）的 open_id。可从你在群里发的消息的 `sender_id` 取得：`python3 scripts/lark_ops.py poll 10`。
- `env_file` 指向的文件至少要有 `config/lark.json` 里 `app_id_key`／`app_secret_key`／`domain_key` 指定的三个键。domain 值含 `lark` 走 `open.larksuite.com`，否则走 `open.feishu.cn`。
- `config/lark.json` 已被 `.gitignore` 排除，不要提交。

## 3. 先试运行

```bash
python3 -m unittest discover -s tests                  # 单元测试
python3 scripts/supervisor.py status                    # 本地状态
python3 scripts/supervisor.py morning --dry-run         # 只印出将建立的任务与卡片，不写入
```

`--dry-run` 的读取类调用（任务状态、评论、回复）照常执行，写入类（建任务、发卡片、写日志、写状态）只印出。

## 4. 安装 launchd

```bash
bash scripts/install_launchd.sh            # 安装 morning / evening / chase / close / sync
launchctl list | grep workboss             # 确认已载入
bash scripts/install_launchd.sh --uninstall   # 卸载
```

| Label | 触发 |
|---|---|
| `com.workboss.morning` | 每天 08:30 |
| `com.workboss.evening` | 每天 20:30 |
| `com.workboss.chase` | 每天 21:00、21:30、22:00、22:30、23:00、23:30 |
| `com.workboss.close` | 每天 00:00（收尾昨日） |
| `com.workboss.sync` | 每天 10:05 到 19:05，每小时一次（同步进度、维护次日计划、刷新看板） |

可选配置（`config/lark.json`）：`"min_plan_hours": 4`——当日计划低于这个工时就在 Lark 主动要需求，缺省 4。也可用环境变量 `WORK_BOSS_MIN_HOURS` 临时覆盖。

日志在 `.supervisor/logs/*.log`。Mac 睡眠时错过的触发会在唤醒后补跑。

### 重要：外接卷与隐私权限

安装脚本会先做自测。**若仓库放在外接卷（如 `/Volumes/...`），launchd 背景环境通常读不到**，表现为 `Operation not permitted`，安装会被拦下。解决办法二选一：

- **A（推荐）**：把仓库放到家目录，例如 `~/Code/work-boss`，再安装。launchd 能读家目录（`Desktop`／`Documents`／`Downloads` 另有隐私保护，也别放那里）。
- **B**：系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 加入 Python（真实二进制通常在 `/Library/Developer/CommandLineTools/usr/bin/python3`），重新运行安装脚本。

> 这是在实际环境里验证过的现象：同一个 launchd 任务可以读家目录，读外接卷被拒。

## 5. 只启用一种定时方式

`evening` 与 `chase` 不是幂等的。若同时启用 launchd 与其他定时任务（例如某个 AI 客户端的定时任务）去调用同一个脚本，群里会收到重复卡片。安装 launchd 后，请停用其他定时任务。

## 6. 补产出与手动操作

```bash
python3 scripts/supervisor.py sync                          # 立刻同步：进度→本地、任务卡、次日计划、看板（约 15～40 秒；首次会回填历史日，较久）
python3 scripts/supervisor.py plan-carry --date 2026-10-15    # 只生成“顺延＋次日计划”，不发消息
python3 scripts/supervisor.py morning --date 2026-10-16 --force
python3 scripts/supervisor.py close --date 2026-10-15 --dry-run
python3 scripts/build_dashboard.py                           # 生成本地看板 docs/dashboard.html
```

## 7. 重置某一天

只在确定要重来时做（会导致该日任务被重建）：删除 `.supervisor/tasks-YYYYMMDD.json` 与 `.supervisor/YYYYMMDD.json`，并自行到 Lark 处理已建的任务。
