<!-- [skill: go-team-standards · 文档场景] work-boss README -->
# work-boss（監工）

一套給個人與多個 AI agent 共用的「監工」系統：**每天把工作排成時段任務，晚上收集完成度，沒做完就催，到期不做就順延並重排明天。**

- 溝通通道：Lark／飛書（互動卡片＋原生任務，不要求你學新工具）
- 排程：macOS launchd，**確定性腳本**，不依賴任何 LLM
- 協作：計畫、任務、日誌都是倉庫檔案，任何 agent 讀寫檔案即可參與
- 原則：證據優先——完成只認 Lark 任務的勾選，監工從不替你宣稱完成

## 一圖看懂

```
08:30 發今日工作項 ─► 你/agent 執行（勾選或寫原因）─► 20:30 詢問進度
   ▲                                                      │
   └──────── 00:00 收尾：順延未完成、重排明天 ◄── 21:00–23:30 每30分鐘催促
```

## 文檔

| 想知道 | 看 |
|---|---|
| 為什麼這樣設計、名詞定義 | [docs/concept.md](docs/concept.md) |
| 一天裡你要做什麼 | [docs/usage.md](docs/usage.md) |
| agent 的協作規則 | [AGENTS.md](AGENTS.md) |
| 檔案格式與判定規則 | [docs/data-contract.md](docs/data-contract.md) |
| 安裝、launchd、權限 | [docs/setup-macos.md](docs/setup-macos.md) |
| 維護與隱私規則 | [docs/maintenance.md](docs/maintenance.md) |
| 做了什麼、踩過的坑 | [docs/what-was-done.md](docs/what-was-done.md) |

## 快速開始

```bash
cp config/lark.example.json config/lark.json     # 填 chat_id / owner_open_id / env_file
python3 scripts/lark_ops.py chats                # 列出機器人所在的群
python3 -m unittest discover -s tests            # 測試
cp plans/_TEMPLATE.json plans/$(date +%F).json   # 寫今天的計畫
python3 scripts/supervisor.py morning --dry-run   # 乾跑
bash scripts/install_launchd.sh                  # 安裝定時任務
```
## 结合Lark任务 进行排期

<img width="429" height="522" alt="image" src="https://github.com/user-attachments/assets/d27e2a73-eb22-4409-8a0f-16648a5d6e0a" />
<img width="741" height="742" alt="image" src="https://github.com/user-attachments/assets/4a3dcebc-ce49-4b64-9955-93ed55b18bdf" />

## 目錄

```
AGENTS.md  CLAUDE.md          協作約定（CLAUDE.md 引用 AGENTS.md）
scripts/   supervisor.py      監工執行器（morning/evening/chase/close/plan-carry/status）
           lark_ops.py        Lark REST 工具（發卡片、建任務、讀回覆…）
           build_dashboard.py 本地看板生成
           install_launchd.sh launchd 安裝／卸載（含自測）
           check_private.sh   推送前私有資料檢查
launchd/                      4 個 launchd plist 模板
tests/                        單元測試
tasks/ plans/ worklog/        私有資料（僅範本入庫）
docs/                         說明文檔
config/lark.example.json      配置範本
```

## 隱私

工作日誌、任務卡、計畫、運行狀態、Lark 配置與任何密鑰**都不在這個倉庫裡**。規則與檢查方式見 [docs/maintenance.md](docs/maintenance.md)。

## 現況

可運行的原型，已在單人、單群、單機器人的場景驗證。尚未驗證與限制見 [docs/what-was-done.md](docs/what-was-done.md) 第 5 節。
