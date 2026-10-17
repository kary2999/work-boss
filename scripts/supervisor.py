#!/usr/bin/env python3
"""work-boss 監工執行器：確定性腳本，不依賴任何 LLM，供 macOS launchd（或 cron）定時呼叫。

子命令:
  morning   [--date D]   依 plans/D.json 建 Lark 任務（含子任務、順延項改期不重建）並發卡片
  evening   [--date D]   收集今日完成度，寫 progress 日誌，發進度詢問卡片
  chase     [--date D]   檢查是否完成／已說明原因／已回覆；否則發催促卡片
  close     [--date D]   收尾 D（默認昨日）：判定旷工、順延未完成項、定稿次日 plans/、需求不足再要一次
  sync      [--date D]   白天同步：進度寫回本地、維護次日計畫草案、需求不足時在 Lark 主動要需求、刷新看板
  plan-carry [--date D]  只做「順延並生成次日計畫」，不發消息、不寫旷工（用於補產出）
  status    [--date D]   只讀本地檔案，顯示今日任務與提問狀態

每個會與 Lark 互動的命令結束後，都會：①把進度寫進 .supervisor/progress.json 並同步任務卡
（status／progress）；②讀取使用者對「需求不足」卡片的回覆並併入計畫；③刷新 docs/dashboard.html。

通用選項: --dry-run（不呼叫 Lark 寫入、不寫本地檔，只印出將做什麼；讀取類呼叫仍會執行）、--no-notify
資料契約見 docs/data-contract.md。
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS = [sys.executable, "-I", os.path.join(ROOT, "scripts", "lark_ops.py")]
SUP = os.path.join(ROOT, ".supervisor")
WORKLOG = os.path.join(ROOT, "worklog")
PLANS = os.path.join(ROOT, "plans")
TASKS = os.path.join(ROOT, "tasks")
WINDOWS = [("09:00", "12:00"), ("14:00", "18:00")]
DAY_CAP_HOURS = 7.0
DEFAULT_MIN_HOURS = 4.0   # 當日計畫低於此工時視為「需求不足」；可用 config 的 min_plan_hours 或環境變數覆蓋
ASK_MAX = 3               # 對同一天的「需求不足」最多主動要幾次（收尾的最後一次不計）
ASK_INTERVAL_MIN = 90     # 兩次主動要需求之間的最短間隔（分鐘）
DASHBOARD = True          # 命令結束後是否刷新看板；測試時關閉
HINT = "【完成請直接勾選；未完成請在『評論』說明原因】"


# ---------- 純函數（有單元測試） ----------
def hm_to_min(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def min_to_hm(m):
    return "%02d:%02d" % (m // 60, m % 60)


def split_slot(slot):
    """'09:00-10:00' 或 '09:00–10:00' → ('09:00','10:00')；None/待排 → (None, None)"""
    if not slot or slot == "待排":
        return None, None
    a, b = re.split(r"[-–—]", slot)
    return a.strip(), b.strip()


def pack_slots(items, windows=None, hard_end="22:00", start_after=None):
    """依序把 items（需含 est_hours）排進工作時段，返回新列表（含 slot，排不下為 None）。
    start_after（分鐘）：從這個時間點之後開始排，用於往已有計畫後面追加。"""
    windows = windows or WINDOWS
    wins = [(hm_to_min(a), hm_to_min(b)) for a, b in windows]
    wi, cursor = 0, wins[0][0]
    if start_after is not None:
        cursor = start_after
        while wi < len(wins) and wins[wi][1] <= cursor:
            wi += 1
    out = []
    for it in items:
        need = int(-(-float(it.get("est_hours", 1.0)) * 60 // 15) * 15)  # 向上取整到 15 分鐘
        placed = None
        while wi < len(wins):
            s, e = wins[wi]
            cursor = max(cursor, s)
            if cursor + need <= e:
                placed = (cursor, cursor + need)
                cursor += need
                break
            if need > e - s and cursor == s:  # 比整個視窗還長：從視窗起點硬排，不拆分
                if cursor + need <= hm_to_min(hard_end):
                    placed = (cursor, cursor + need)
                    cursor += need
                    # 後續視窗若已被覆蓋則前移
                    while wi + 1 < len(wins) and wins[wi + 1][0] < cursor:
                        wi += 1
                    break
            wi += 1
            if wi < len(wins):
                cursor = wins[wi][0]
        new = dict(it)
        new["slot"] = "%s-%s" % (min_to_hm(placed[0]), min_to_hm(placed[1])) if placed else None
        out.append(new)
    return out


SUMMARY_RE = re.compile(r"^\[(?P<ref>[^\]]+)\]\s+(?:\d{2}:\d{2}\s*[-–—]\s*\d{2}:\d{2}|待排)\s+(?P<title>.*)$")


def parse_summary(s):
    m = SUMMARY_RE.match(s or "")
    if m:
        return m.group("ref"), m.group("title")
    return "", s or ""


def decide_absent(results, user_reply_count):
    """旷工 = 沒有任何主任務完成、沒有任何使用者評論、對提問也沒有回覆。"""
    responded = (any(r["completed"] for r in results)
                 or any(r["reasons"] for r in results)
                 or user_reply_count > 0)
    return not responded


def parse_needs_text(text):
    """使用者對「需求不足」卡片的回覆 → [(標題, 估時)]。每行一條；行內的「2h／1.5小時」是估時，缺省 1h。"""
    text = re.sub(r"@_user_\d+", "", text or "")
    out = []
    for line in text.replace("；", "\n").replace(";", "\n").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.、)）])\s*", "", line).strip()
        if not line:
            continue
        est = 1.0
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|H|小時|小时)", line)
        if m:
            est = min(max(float(m.group(1)), 0.25), 8.0)
            line = (line[:m.start()] + line[m.end():]).strip(" ，,、:：-")
        if line:
            out.append((line[:80], est))
    return out[:10]


def plan_total_hours(plan):
    return round(sum(float(i.get("est_hours", 1.0)) for i in (plan or {}).get("items", [])), 2)


def set_front(text, key, value):
    """改（或補）任務卡 front matter 裡的 key，保留行尾註解與正文；無變化時原樣返回。"""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return text
    lines = m.group(1).split("\n")
    found = False
    for i, line in enumerate(lines):
        mm = re.match(r"^%s:\s*(\S*)(\s*#.*)?$" % re.escape(key), line)
        if mm:
            lines[i] = "%s: %s%s" % (key, value, (mm.group(2) or ""))
            found = True
            break
    if not found:
        pos = next((i + 1 for i, line in enumerate(lines) if line.startswith("status:")), len(lines))
        lines.insert(pos, "%s: %s" % (key, value))
    return "---\n" + "\n".join(lines) + "\n---\n" + m.group(2)


def card_status(current, done, total):
    """依 Lark 進度推導任務卡 status。只往前走：不降級，不碰 done/blocked，done 必須由人確認。"""
    if current in ("review", "done", "blocked", "cancelled"):
        return current
    if total > 0 and done == total:
        return "review"
    if done > 0:
        return "doing"
    return current


def ask_allowed(entry, now, final=False):
    """對同一天『需求不足』的主動詢問是否允許：次數與間隔限制；收尾（final）可再要一次。"""
    entry = entry or {}
    count = entry.get("count", 0)
    if final:
        return count < ASK_MAX + 1
    if count >= ASK_MAX:
        return False
    last = entry.get("last")
    if last and (now - dt.datetime.fromisoformat(last)).total_seconds() < ASK_INTERVAL_MIN * 60:
        return False
    return True


def build_next_plan(results, backlog, next_day, cap=DAY_CAP_HOURS, extra=None, cancelled=None):
    """順延未完成項（排最前，沿用原 Lark 任務）+ 使用者主動補充的需求 + backlog，補到工時上限，再排時段。
    cancelled：已作廢的任務卡 id，其未完成的 Lark 任務不再順延。"""
    carried = []
    for r in results:
        if r["completed"]:
            continue
        ref, title = parse_summary(r["summary"])
        if cancelled and ref in cancelled:
            continue
        subs = []
        for s in r.get("subtasks_open", []):
            subs.append({"guid": s["guid"], "title": s["summary"]})
        carried.append({
            "id": r.get("id") or (ref + "-" + title)[:40],
            "ref": ref, "title": title, "est_hours": r.get("est_hours", 1.0),
            "done_std": r.get("done_std", ""), "carried": True,
            "carry_reason": "；".join(r["reasons"]) if r["reasons"] else "",
            "guid": r["guid"], "url": r.get("url", ""), "subtasks": subs,
        })
    total = sum(float(c["est_hours"]) for c in carried)
    carried_refs = {c["ref"] for c in carried if c["ref"]}
    fresh, deferred = [], []
    asked = [{"id": n["id"], "ref": "REQ", "title": n["title"], "est_hours": n["est_hours"], "done_std": "",
              "subtasks": [], "from_requirement": True} for n in (extra or [])]
    for b in asked + list(backlog):
        if b["ref"] in carried_refs:
            continue
        if total + float(b["est_hours"]) <= cap:
            fresh.append(b)
            total += float(b["est_hours"])
        else:
            deferred.append(b["title"] if b["ref"] in ("", "REQ") else b["ref"])
    items = pack_slots(carried + fresh)
    return {"date": next_day, "goal": "", "generated_by": "supervisor", "draft": True,
            "total_hours": round(total, 2), "deferred": deferred, "items": items}


# ---------- 檔案與狀態 ----------
def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def compact(d):
    return d.replace("-", "")


def p_state_tasks(day):
    return os.path.join(SUP, "tasks-%s.json" % compact(day))


def p_state_q(day):
    return os.path.join(SUP, "%s.json" % compact(day))


def p_plan(day):
    return os.path.join(PLANS, "%s.json" % day)


def p_progress():
    return os.path.join(SUP, "progress.json")


def p_asks():
    return os.path.join(SUP, "asks.json")


def p_needs():
    return os.path.join(SUP, "needs.json")


def min_plan_hours():
    env = os.environ.get("WORK_BOSS_MIN_HOURS")
    if env is not None:
        return float(env)
    cfg = load_json(os.path.join(ROOT, "config", "lark.json"), {}) or {}
    return float(cfg.get("min_plan_hours", DEFAULT_MIN_HOURS))


def write_worklog(name, text, dry):
    path = os.path.join(WORKLOG, name)
    if dry:
        print("[dry-run] 寫日誌 %s\n%s" % (name, text))
        return path
    os.makedirs(WORKLOG, exist_ok=True)
    if os.path.exists(path):  # 日誌只新增，不改既有
        return path
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def read_backlog():
    """tasks/*.md（非範本）中 status 為 todo/doing 的任務卡 → 計畫條目。"""
    items = []
    for p in sorted(glob.glob(os.path.join(TASKS, "*.md"))):
        if os.path.basename(p).startswith("_"):
            continue
        text = open(p, encoding="utf-8").read()
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
        if not m:
            continue
        meta = {}
        for line in m.group(1).splitlines():
            mm = re.match(r"^([A-Za-z_]+):\s*(.*?)\s*(#.*)?$", line)
            if mm:
                meta[mm.group(1)] = mm.group(2).strip()
        if meta.get("status", "todo") not in ("todo", "doing"):
            continue
        subs = re.findall(r"^\s*- \[ \]\s+(.*)$", m.group(2), re.M)[:8]
        try:
            est = float(meta.get("est_hours", "1"))
        except ValueError:
            est = 1.0
        imgs = [x.strip() for x in meta.get("images", "").split("|") if x.strip()]
        items.append({"id": meta.get("id", os.path.basename(p)), "ref": meta.get("id", ""),
                      "title": meta.get("title", os.path.basename(p)), "est_hours": est,
                      "done_std": "", "subtasks": subs, "images": imgs})
    return items


def read_cancelled_refs():
    """status 為 cancelled 的任務卡 id：已作廢，不再進 backlog，也不再被順延。"""
    out = set()
    for p in sorted(glob.glob(os.path.join(TASKS, "*.md"))):
        if os.path.basename(p).startswith("_"):
            continue
        m = re.match(r"^---\n(.*?)\n---", open(p, encoding="utf-8").read(), re.S)
        if not m:
            continue
        meta = dict((k.strip(), v.strip().split("#")[0].strip()) for k, _, v in
                    (ln.partition(":") for ln in m.group(1).splitlines() if ":" in ln))
        if meta.get("status") == "cancelled" and meta.get("id"):
            out.add(meta["id"])
    return out


# ---------- Lark 客戶端 ----------
class LarkCLI(object):
    """透過 scripts/lark_ops.py 呼叫 Lark（只用 REST，不開長連接）。"""

    def _run(self, args):
        p = subprocess.run(OPS + args, capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout).strip()[:300])
        out = []
        for line in p.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                out.append(json.loads(line))
        return out

    def task_create(self, summary, desc, start, due):
        a = ["task-create", "--summary", summary, "--desc", desc, "--due", due]
        if start:
            a += ["--start", start]
        return self._run(a)[-1]

    def subtask_create(self, parent, summary, due, assign=False):
        a = ["subtask-create", parent, "--summary", summary, "--due", due]
        if not assign:
            a.append("--no-assignee")
        return self._run(a)[-1]

    def task_update(self, guid, summary=None, desc=None, start=None, due=None):
        a = ["task-update", guid]
        for flag, v in (("--summary", summary), ("--desc", desc), ("--start", start), ("--due", due)):
            if v is not None:
                a += [flag, v]
        return self._run(a)[-1]

    def task_get(self, guid):
        r = self._run(["task-get", guid])[-1]
        if "error" in r:
            raise RuntimeError(r["error"])
        return r

    def attach(self, guid, paths):
        return self._run(["attach", guid] + paths)

    def task_get_many(self, guids):
        out = {}
        for i in range(0, len(guids), 40):  # 分批，避免命令列過長
            for r in self._run(["task-get"] + guids[i:i + 40]):
                if "error" in r:
                    raise RuntimeError("%s: %s" % (r.get("guid"), r["error"]))
                out[r["guid"]] = r
        return out

    def comments_many(self, guids):
        out = dict((g, []) for g in guids)
        for i in range(0, len(guids), 40):
            for r in self._run(["task-comments"] + guids[i:i + 40]):
                if "error" not in r:
                    out.setdefault(r["guid"], []).append(r)
        return out

    def comments(self, guid):
        return [c for c in self._run(["task-comments", guid]) if "error" not in c]

    def replies(self, mid):
        return self._run(["replies", mid])

    def card(self, title, template, md, subtitle=None, footer=None):
        a = ["card", "--title", title, "--template", template, "--md", md]
        if subtitle:
            a += ["--subtitle", subtitle]
        if footer:
            a += ["--footer", footer]
        return self._run(a)[-1]["message_id"]


class DryLark(LarkCLI):
    """--dry-run：讀取類呼叫照常執行；寫入類只印出。"""

    def __init__(self):
        self.n = 0

    def _fake(self, what):
        self.n += 1
        print("[dry-run] %s" % what)
        return "dry-%d" % self.n

    def task_create(self, summary, desc, start, due):
        return {"guid": self._fake("建任務 %s（%s → %s）" % (summary, start, due)), "url": "https://example.invalid/task"}

    def subtask_create(self, parent, summary, due, assign=False):
        return {"guid": self._fake("  建子任務 %s" % summary), "url": ""}

    def task_update(self, guid, summary=None, desc=None, start=None, due=None):
        return {"guid": guid, "updated": [self._fake("改任務 %s → %s（%s → %s）" % (guid, summary, start, due))]}

    def attach(self, guid, paths):
        self._fake("  掛附件 %s → %s" % ([os.path.basename(p) for p in paths], guid))
        return [{"file": p, "ok": True} for p in paths]

    def card(self, title, template, md, subtitle=None, footer=None):
        print("[dry-run] 發卡片 [%s] %s %s\n%s\n-- %s" % (template, title, subtitle or "", md, footer or ""))
        return self._fake("card")


def notify(text, dry):
    if dry or os.environ.get("WORK_BOSS_NO_NOTIFY"):
        return
    try:
        subprocess.run(["osascript", "-e", 'display notification "%s" with title "監工"' % text.replace('"', "'")[:180]],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# ---------- 業務流程 ----------
def slot_range(day, slot):
    a, b = split_slot(slot)
    if not a:
        return None, "%s 18:00" % day
    return "%s %s" % (day, a), "%s %s" % (day, b)


def _get_many(client, guids):
    if hasattr(client, "task_get_many"):
        return client.task_get_many(guids)
    return dict((g, client.task_get(g)) for g in guids)


def _comments_many(client, guids):
    if hasattr(client, "comments_many"):
        return client.comments_many(guids)
    return dict((g, client.comments(g)) for g in guids)


def collect_results(state, client, with_comments=True):
    guids = []
    for e in state:
        guids.append(e["guid"])
        guids += [s["guid"] for s in e.get("subtasks", [])]
    got = _get_many(client, guids)  # 批量讀：一次命令讀一批，避免每個任務都新開進程換令牌
    open_mains = [e["guid"] for e in state if not got[e["guid"]]["completed"]]
    cmts = _comments_many(client, open_mains) if (with_comments and open_mains) else {}
    res = []
    for e in state:
        g = got[e["guid"]]
        subs_open, subs_done = [], 0
        for s in e.get("subtasks", []):
            if got[s["guid"]]["completed"]:
                subs_done += 1
            else:
                subs_open.append(s)
        reasons = []
        if with_comments and not g["completed"]:
            for c in cmts.get(e["guid"], []):
                if c.get("creator_type") == "user" and (c.get("content") or "").strip():
                    reasons.append(c["content"].strip())
        res.append({"id": e.get("id"), "guid": e["guid"], "summary": e["summary"], "slot": e.get("slot"),
                    "url": e.get("url", ""), "est_hours": e.get("est_hours", 1.0), "done_std": e.get("done_std", ""),
                    "completed": g["completed"], "subtasks_total": len(e.get("subtasks", [])),
                    "subtasks_done": subs_done, "subtasks_open": subs_open, "reasons": reasons})
    return res


def md_table(rows, header):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def attach_images(client, guid, images):
    """把計畫項的參考圖作為附件掛到 Lark 任務。路徑相對倉庫根目錄。
    單張失敗不影響整體（任務已建好）；返回成功掛上的檔名。"""
    paths = [p if os.path.isabs(p) else os.path.join(ROOT, p) for p in images]
    ok = []
    try:
        for row in client.attach(guid, paths):
            if row.get("ok"):
                ok.append(os.path.basename(row["file"]))
            else:
                print("附件失敗：%s %s" % (row.get("file"), row.get("error")), file=sys.stderr)
    except Exception as e:
        print("附件上傳異常：%s" % str(e)[:200], file=sys.stderr)
    return ok


def cmd_morning(day, client, dry, force=False):
    _morning_core(day, client, dry, force)
    post_run(day, client, dry, None, "morning")


def _morning_core(day, client, dry, force=False):
    new_needs = ingest_requirements(client, day, dry)  # 夜裡使用者對「需求不足」卡片的回覆
    plan = load_json(p_plan(day))
    if plan is None:
        plan = refresh_plan(day, [], client, dry, final=True)  # 沒有計畫檔：用補充需求與 backlog 自動生成
    elif new_needs:
        plan = append_needs(plan, new_needs, day, dry)
    if not plan or not plan.get("items"):
        client.card("今日無工作計畫", "grey",
                    "**%s 沒有可排的工作**（沒有計畫檔、沒有補充需求、backlog 也是空的）。\n請回覆下一張「需求不足」卡片，或補任務卡／計畫檔。" % day,
                    subtitle=day, footer="監工 · 計畫檔格式見 plans/_TEMPLATE.json")
        maybe_ask(client, plan or {"items": []}, day, dry, final=False)
        print("無可排的工作，已發提示並要需求")
        return
    maybe_ask(client, plan, day, dry, final=False)
    state = load_json(p_state_tasks(day), [])
    have = {e.get("id") for e in state}
    created, rescheduled = 0, 0
    for it in plan["items"]:
        if it["id"] in have:
            continue
        start, due = slot_range(day, it.get("slot"))
        ref = it.get("ref") or it["id"]
        slot_label = (it.get("slot") or "待排").replace("-", "–")
        summary = "[%s] %s %s" % (ref, slot_label, it["title"])
        desc = "時段：%s %s（約 %sh）\n" % (day, slot_label, it.get("est_hours", 1))
        if it.get("done_std"):
            desc += "完成標準：%s\n" % it["done_std"]
        if it.get("carried"):
            desc += "【順延項】%s\n" % (it.get("carry_reason") or "昨日未完成")
        if it.get("images"):
            desc += "參考圖（已作為附件上傳）：%s\n" % "、".join(os.path.basename(p) for p in it["images"])
        desc += HINT
        entry = {"id": it["id"], "summary": summary, "slot": slot_label, "est_hours": it.get("est_hours", 1.0),
                 "done_std": it.get("done_std", ""), "carried": bool(it.get("carried")), "subtasks": []}
        if it.get("guid"):  # 順延項：沿用原任務，只改期，不重複建立
            # 順延項只改標題與起止時間，不動描述：描述裡可能有人或 agent 補充的規範與說明
            client.task_update(it["guid"], summary=summary, start=start, due=due)
            entry["guid"], entry["url"] = it["guid"], it.get("url", "")
            for s in it.get("subtasks", []):
                if isinstance(s, dict) and s.get("guid"):
                    client.task_update(s["guid"], due=due)
                    entry["subtasks"].append({"guid": s["guid"], "summary": s.get("title", "")})
                else:
                    t = s["title"] if isinstance(s, dict) else s
                    st = client.subtask_create(it["guid"], t, due)
                    entry["subtasks"].append({"guid": st["guid"], "summary": t})
            rescheduled += 1
        else:
            t = client.task_create(summary, desc, start, due)
            entry["guid"], entry["url"] = t["guid"], t.get("url", "")
            for s in it.get("subtasks", []):
                title = s["title"] if isinstance(s, dict) else s
                st = client.subtask_create(t["guid"], title, due)
                entry["subtasks"].append({"guid": st["guid"], "summary": title})
            if it.get("images"):
                entry["attached"] = attach_images(client, t["guid"], it["images"])
            created += 1
        state.append(entry)
        if not dry:
            save_json(p_state_tasks(day), state)
    if not (created or rescheduled or force):
        print("今日任務已建立，無新增項；不重發卡片")
        return
    rows = []
    total = 0.0
    for e in state:
        total += float(e.get("est_hours", 0))
        mark = "（順延）" if e.get("carried") else ""
        title = parse_summary(e["summary"])[1]
        link = "[打開](%s)" % e["url"] if e.get("url") else ""
        rows.append((e["slot"], title + mark, "%gh" % float(e.get("est_hours", 0)), len(e["subtasks"]), link))
    md = "**今日目標**：%s\n\n%s\n\n**預計總工時**：約 %.2fh（新建 %d、順延改期 %d）\n\n完成請在 Lark 任務勾選，做不完請在任務『評論』寫原因。" % (
        plan.get("goal") or "按計畫完成下列工作", md_table(rows, ["時段", "任務", "估時", "子任務", "連結"]), total, created, rescheduled)
    client.card("今日工作項", "orange", md, subtitle=day,
                footer="監工 · 20:30 詢問進度；到期未完成將順延並重排明日工作")
    notify("今日工作項已發出（%d 項，約 %.1fh）" % (len(state), total), dry)


def progress_summary(results):
    m = len(results)
    done = sum(1 for r in results if r["completed"])
    st = sum(r["subtasks_total"] for r in results)
    sd = sum(r["subtasks_done"] for r in results)
    return done, m, sd, st


def cmd_evening(day, client, dry):
    res = _evening_core(day, client, dry)
    post_run(day, client, dry, res, "evening")


def _evening_core(day, client, dry):
    state = load_json(p_state_tasks(day), [])
    if not state:
        print("今日無任務狀態，略過")
        return None
    res = collect_results(state, client)
    done, m, sd, st = progress_summary(res)
    rows = []
    for r in res:
        status = "✅ 已完成" if r["completed"] else ("📝 有說明" if r["reasons"] else "⬜ 未完成")
        note = "；".join(x[:40] for x in r["reasons"]) if r["reasons"] else ""
        link = "[打開](%s)" % r["url"] if r["url"] else ""
        rows.append((r["slot"], parse_summary(r["summary"])[1], status, note, "%d/%d" % (r["subtasks_done"], r["subtasks_total"]), link))
    color = "green" if done == m else ("orange" if done else "red")
    md = "**完成度** 主任務 %d / %d · 子任務 %d / %d\n\n%s\n\n**請處理**：未完成請在 Lark 點擊完成，或在任務『評論』說明原因。" % (
        done, m, sd, st, md_table(rows, ["時段", "任務", "狀態", "備註", "子任務", "連結"]))
    mid = client.card("今日進度詢問", color, md, subtitle=day,
                      footer="監工 · 每 30 分鐘催促至 24:00；到期仍未完成將順延並重排明日工作")
    q = load_json(p_state_q(day), {"date": day, "question_ids": [], "replied": False, "reminders": 0})
    q["question_ids"].append(mid)
    if done == m:
        q["replied"] = True
    if not dry:
        save_json(p_state_q(day), q)
    lines = ["# %s 晚間進度（監工自動生成）" % day, "",
             "- 完成度：主任務 %d/%d，子任務 %d/%d" % (done, m, sd, st), "- 依據：Lark 任務勾選狀態與使用者評論", ""]
    for r in res:
        lines.append("- [%s] %s｜%s%s" % ("x" if r["completed"] else " ", r["summary"],
                                         "已完成" if r["completed"] else "未完成",
                                         ("｜說明：" + "；".join(r["reasons"])) if r["reasons"] else ""))
    write_worklog("%s-2030-supervisor-progress.md" % compact(day), "\n".join(lines) + "\n", dry)
    notify("晚間進度：完成 %d/%d 項" % (done, m), dry)
    return res


def cmd_chase(day, client, dry):
    res = _chase_core(day, client, dry)
    post_run(day, client, dry, res, "chase")


def _chase_core(day, client, dry):
    state = load_json(p_state_tasks(day), [])
    if not state:
        return None
    q = load_json(p_state_q(day), {"date": day, "question_ids": [], "replied": False, "reminders": 0})
    if q.get("replied"):
        return None
    res = collect_results(state, client)
    done, m, sd, st = progress_summary(res)
    hhmm = dt.datetime.now().strftime("%H%M")
    user_replies = []
    for mid in q["question_ids"]:
        user_replies += [r for r in client.replies(mid) if r.get("sender_type") == "user"]

    def finish(title, md):
        lines = ["# %s %s 回報記錄（監工自動生成）" % (day, hhmm), "", "- 完成度：主任務 %d/%d，子任務 %d/%d" % (done, m, sd, st)]
        for r in res:
            lines.append("- %s｜%s%s" % (r["summary"], "已完成" if r["completed"] else "未完成",
                                        ("｜說明：" + "；".join(r["reasons"])) if r["reasons"] else ""))
        for r in user_replies:
            lines.append("- 回覆：%s" % r.get("text", ""))
        write_worklog("%s-%s-supervisor-reply.md" % (compact(day), hhmm), "\n".join(lines) + "\n", dry)
        q["replied"] = True
        if not dry:
            save_json(p_state_q(day), q)
        client.card(title, "green", md, subtitle=day, footer="監工 · 今日追問結束")

    if done == m:
        finish("今日工作項已全部完成", "主任務 %d / %d 已勾選完成。" % (done, m))
        return res
    if user_replies:
        finish("已記錄今日回報", "已收到你對提問消息的回覆，已寫入日誌。未完成項將於 00:00 順延。")
        return res
    pending = [r for r in res if not r["completed"] and not r["reasons"]]
    if not pending:
        finish("已記錄未完成原因", "未完成項都已有評論說明，將於 00:00 順延並重排明日工作。")
        return res
    q["reminders"] = q.get("reminders", 0) + 1
    rows = [(r["slot"], parse_summary(r["summary"])[1], "%d/%d" % (r["subtasks_done"], r["subtasks_total"]),
             "[打開](%s)" % r["url"] if r["url"] else "") for r in pending]
    md = "**未完成 %d / 共 %d 項**\n\n%s\n\n請在 Lark 任務中**點擊完成**；做不完請在評論寫原因。" % (
        len(pending), m, md_table(rows, ["時段", "任務", "子任務", "連結"]))
    mid = client.card("第 %d 次催促：請點擊完成" % q["reminders"], "red", md,
                      subtitle=dt.datetime.now().strftime("%H:%M"),
                      footer="監工 · 到期仍未完成將順延並重新制定明日任務")
    q["question_ids"].append(mid)
    if not dry:
        save_json(p_state_q(day), q)
    return res


def next_day(day):
    return (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()


def refresh_plan(target, results, client, dry, final=False):
    """（重新）生成 target 日的計畫。只覆蓋「監工自己生成」的計畫檔；
    人或其他 agent 寫的（generated_by 不是 supervisor）原樣保留。返回生效的計畫。"""
    existing = load_json(p_plan(target))
    if existing is not None and existing.get("generated_by") != "supervisor":
        return existing
    needs = [n for n in load_json(p_needs(), {"items": []})["items"] if n["target"] == target]
    plan = build_next_plan(results, read_backlog(), target, extra=needs, cancelled=read_cancelled_refs())
    plan["draft"] = not final
    if dry:
        print("[dry-run] 將寫 plans/%s.json（%s，約 %sh，%d 項）" % (
            target, "定稿" if final else "草案", plan["total_hours"], len(plan["items"])))
    else:
        save_json(p_plan(target), plan)
    return plan


def append_needs(plan, new_needs, target, dry):
    """把使用者補充的需求追加到已有計畫（只追加，不動既有項），排在現有時段之後。"""
    have = {i["id"] for i in plan["items"]}
    add = [{"id": n["id"], "ref": "REQ", "title": n["title"], "est_hours": n["est_hours"], "done_std": "",
            "subtasks": [], "from_requirement": True} for n in new_needs if n["id"] not in have]
    if not add:
        return plan
    used = [hm_to_min(split_slot(i.get("slot"))[1]) for i in plan["items"] if split_slot(i.get("slot"))[1]]
    plan = dict(plan)
    plan["items"] = plan["items"] + pack_slots(add, start_after=max(used) if used else None)
    if not dry:
        save_json(p_plan(target), plan)
    return plan


def ingest_requirements(client, target, dry):
    """讀取使用者對「需求不足」卡片的回覆，每行一條需求，記入 .supervisor/needs.json。返回新增條目。"""
    asks = load_json(p_asks(), {})
    needs = load_json(p_needs(), {"items": [], "ingested": []})
    new = []
    for mid in (asks.get(target) or {}).get("mids", []):
        for r in client.replies(mid):
            if r.get("sender_type") != "user" or r.get("message_id") in needs["ingested"]:
                continue
            for i, (title, est) in enumerate(parse_needs_text(r.get("text", ""))):
                item = {"id": "REQ-%s-%s-%d" % (compact(target), (r.get("message_id") or "x")[-6:], i),
                        "title": title, "est_hours": est, "target": target, "mid": r.get("message_id")}
                needs["items"].append(item)
                new.append(item)
            needs["ingested"].append(r.get("message_id"))
    if new and not dry:
        save_json(p_needs(), needs)
    elif new:
        print("[dry-run] 讀到 %d 條補充需求：%s" % (len(new), [n["title"] for n in new]))
    return new


def maybe_ask(client, plan, target, dry, final=False):
    """target 日的計畫工時低於下限就在 Lark 主動要需求（有次數與間隔限制）。返回是否發了卡片。"""
    minh = min_plan_hours()
    total = plan_total_hours(plan)
    if total >= minh:
        return False
    asks = load_json(p_asks(), {})
    entry = asks.get(target) or {"mids": [], "count": 0}
    now = dt.datetime.now()
    if not ask_allowed(entry, now, final):
        return False
    items = (plan or {}).get("items", [])
    rows = [(i.get("slot") or "待排", i["title"], "%gh" % float(i.get("est_hours", 1))) for i in items]
    md = ("**%s 目前只排了 %d 項、約 %.2fh，低於下限 %.1fh。**\n\n%s\n\n"
          "**怎麼補**（任選一種）\n"
          "1. **對本卡片點「回覆」**，每行寫一條需求，行內可寫「2h」表示估時，不寫默認 1h\n"
          "2. 在 `tasks/` 新增任務卡（寫上 `est_hours`）\n"
          "3. 讓 agent 寫 `plans/%s.json`") % (
        target, len(items), total, minh,
        md_table(rows, ["時段", "已排", "估時"]) if rows else "（目前沒有任何已排項）", target)
    mid = client.card("%s需求不足" % ("今日" if target == dt.date.today().isoformat() else "明日"),
                      "red" if total == 0 else "orange", md, subtitle=target,
                      footer="監工 · 你的回覆會被讀取並併入計畫；沒有需求就沒有任務可排")
    entry["mids"].append(mid)
    entry["count"] = entry.get("count", 0) + 1
    entry["last"] = now.isoformat(timespec="seconds")
    asks[target] = entry
    if not dry:
        save_json(p_asks(), asks)
    notify("%s需求不足（%.1fh < %.1fh），已在 Lark 要需求" % (target, total, minh), dry)
    return True


def sync_progress(day, res, dry):
    """把 day 的 Lark 進度寫進 .supervisor/progress.json（看板與任務卡同步的依據）。"""
    q = load_json(p_state_q(day), {}) or {}
    items = []
    for r in res:
        items.append({"id": r.get("id"), "ref": parse_summary(r["summary"])[0], "title": parse_summary(r["summary"])[1],
                      "slot": r.get("slot"), "guid": r["guid"], "url": r.get("url", ""), "completed": r["completed"],
                      "subs_done": r["subtasks_done"], "subs_total": r["subtasks_total"], "reasons": r["reasons"]})
    done, m, sd, st = progress_summary(res)
    entry = {"main_done": done, "main_total": m, "sub_done": sd, "sub_total": st, "items": items,
             "reminders": q.get("reminders", 0), "replied": bool(q.get("replied")),
             "absent": os.path.exists(os.path.join(WORKLOG, "%s-2400-supervisor-absent.md" % compact(day))),
             "updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
    prog = load_json(p_progress(), {"days": {}})
    prog["days"][day] = entry
    if dry:
        print("[dry-run] 進度 %s：主任務 %d/%d，子任務 %d/%d" % (day, done, m, sd, st))
    else:
        save_json(p_progress(), prog)
    return prog


def backfill_progress(client, dry, skip_day=None):
    """把有任務狀態檔、但還沒進度記錄的歷史日補進 progress.json（首次啟用或漏跑時）。"""
    prog = load_json(p_progress(), {"days": {}})
    for path in sorted(glob.glob(os.path.join(SUP, "tasks-*.json"))):
        m = re.match(r"tasks-(\d{4})(\d{2})(\d{2})\.json$", os.path.basename(path))
        if not m:
            continue
        d = "%s-%s-%s" % m.groups()
        if d == skip_day or d in prog["days"]:
            continue
        state = load_json(path, [])
        if state:
            sync_progress(d, collect_results(state, client), dry)


def sync_task_cards(prog, dry):
    """依 progress.json 回寫 tasks/*.md 的 status 與 progress（按 front matter 的 id 對應 ref）。"""
    agg = {}
    for d in sorted(prog["days"]):
        for it in prog["days"][d]["items"]:
            if it.get("ref"):
                agg.setdefault(it["ref"], {})[it["guid"]] = it["completed"]
    changed = []
    for path in sorted(glob.glob(os.path.join(TASKS, "*.md"))):
        if os.path.basename(path).startswith("_"):
            continue
        text = open(path, encoding="utf-8").read()
        m = re.match(r"^---\n(.*?)\n---", text, re.S)
        if not m:
            continue
        meta = dict((k.strip(), v.strip().split("#")[0].strip()) for k, _, v in
                    (ln.partition(":") for ln in m.group(1).splitlines() if ":" in ln))
        guids = agg.get(meta.get("id", ""))
        if not guids:
            continue
        done, total = sum(1 for v in guids.values() if v), len(guids)
        new = set_front(text, "progress", "%d/%d" % (done, total))
        new = set_front(new, "status", card_status(meta.get("status", "todo"), done, total))
        if new != text:
            changed.append("%s → %d/%d" % (os.path.basename(path), done, total))
            if not dry:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new)
    if changed:
        print("同步任務卡：" + "；".join(changed))
    return changed


def refresh_dashboard(dry):
    if dry or not DASHBOARD:
        return
    try:
        subprocess.run([sys.executable, "-X", "utf8", "-I", os.path.join(ROOT, "scripts", "build_dashboard.py")],
                       capture_output=True, timeout=60, cwd=ROOT)
    except Exception:
        pass  # 看板刷新失敗不能拖垮監工


def post_run(day, client, dry, res, phase):
    """每個命令收尾：進度寫回本地 → 任務卡同步 → 讀補充需求 → 維護次日計畫 → 需求不足就要 → 刷新看板。"""
    state = load_json(p_state_tasks(day), [])
    if res is None and state:
        res = collect_results(state, client)
    backfill_progress(client, dry, skip_day=day)
    if res is not None:
        prog = sync_progress(day, res, dry)
        sync_task_cards(prog, dry)
    else:
        sync_task_cards(load_json(p_progress(), {"days": {}}), dry)
    if phase in ("evening", "chase", "sync", "close"):
        nxt = next_day(day)
        ingest_requirements(client, nxt, dry)
        if phase == "close":
            plan = load_json(p_plan(nxt)) or {"items": []}
        else:
            plan = refresh_plan(nxt, res or [], client, dry, final=False)
        maybe_ask(client, plan, nxt, dry, final=(phase == "close"))
    refresh_dashboard(dry)


def carry_and_plan(day, client, dry, write_plan=True):
    """收集 day 的最終結果，順延並生成（定稿）次日計畫。返回 (results, plan, wrote)。"""
    state = load_json(p_state_tasks(day), [])
    res = collect_results(state, client)
    nxt = next_day(day)
    ingest_requirements(client, nxt, dry)
    existing = load_json(p_plan(nxt))
    if not write_plan:
        plan = build_next_plan(res, read_backlog(), nxt)
        return res, plan, False
    if existing is not None and existing.get("generated_by") != "supervisor":
        print("plans/%s.json 是別人寫的，不覆蓋（請 agent 自行併入順延項）" % nxt)
        plan = build_next_plan(res, read_backlog(), nxt)
        return res, plan, False
    plan = refresh_plan(nxt, res, client, dry, final=True)
    return res, plan, not dry


def cmd_close(day, client, dry):
    rollover = "%s-2400-supervisor-rollover.md" % compact(day)
    if os.path.exists(os.path.join(WORKLOG, rollover)) and not dry:
        print("已收尾過 %s，略過" % day)
        return
    state = load_json(p_state_tasks(day))
    if state is None:
        write_worklog("%s-2400-supervisor-error.md" % compact(day),
                      "# %s 無法收尾\n\n昨日沒有任務狀態檔（未建立任務或監工未運行），無法判定旷工。\n" % day, dry)
        return
    res, plan, wrote = carry_and_plan(day, client, dry)
    q = load_json(p_state_q(day), {"question_ids": []})
    user_replies = 0
    for mid in q.get("question_ids", []):
        user_replies += sum(1 for r in client.replies(mid) if r.get("sender_type") == "user")
    absent = decide_absent(res, user_replies)
    done, m, sd, st = progress_summary(res)
    lines = ["# %s 收尾（監工自動生成）" % day, "",
             "- 完成度：主任務 %d/%d，子任務 %d/%d" % (done, m, sd, st),
             "- 旷工：%s" % ("是（無完成、無評論、無回覆）" if absent else "否"), "", "## 順延清單", ""]
    carried = [i for i in plan["items"] if i.get("carried")]
    for c in carried:
        lines.append("- %s %s（原因：%s）" % (c.get("ref", ""), c["title"], c.get("carry_reason") or "未說明"))
    if not carried:
        lines.append("- 無")
    lines += ["", "## 次日計畫 plans/%s.json" % plan["date"], "",
              "- 總工時約 %sh；未排入：%s" % (plan["total_hours"], "、".join(plan["deferred"]) or "無"),
              "- 檔案%s" % ("已寫入" if wrote else "未寫入（已存在或 dry-run）")]
    write_worklog(rollover, "\n".join(lines) + "\n", dry)
    if absent:
        write_worklog("%s-2400-supervisor-absent.md" % compact(day),
                      "# 旷工（未回報）\n\n- 日期：%s\n- 判定：無任一主任務完成、無使用者評論、對提問無回覆\n"
                      "- 提問與催促次數：%d\n- 說明：僅代表未回應；若次數少於 7，可能因電腦休眠導致追問不足，可信度較低\n"
                      % (day, len(q.get("question_ids", []))), dry)
    color = "red" if absent else ("green" if done == m else "orange")
    rows = [(c.get("ref", ""), c["title"], c.get("carry_reason") or "未說明") for c in carried[:8]]
    md = "**完成度**：主任務 %d/%d · 子任務 %d/%d\n**旷工**：%s\n\n%s" % (
        done, m, sd, st, "是" if absent else "否",
        md_table(rows, ["編號", "順延項", "原因"]) if rows else "全部完成，無順延。")
    client.card("昨日收尾", color, md, subtitle=day, footer="監工 · 08:30 將按順延後的計畫建立今日任務")
    notify("昨日收尾：完成 %d/%d，順延 %d 項%s" % (done, m, len(carried), "，旷工" if absent else ""), dry)
    post_run(day, client, dry, res, "close")


def cmd_sync(day, client, dry):
    """白天同步：不發進度卡片，只更新本地進度、維護次日計畫草案、需求不足才要需求、刷新看板。"""
    state = load_json(p_state_tasks(day), [])
    res = collect_results(state, client) if state else None
    post_run(day, client, dry, res, "sync")


def cmd_status(day):
    state = load_json(p_state_tasks(day), [])
    q = load_json(p_state_q(day), None)
    print("日期 %s｜任務 %d 項｜提問狀態 %s" % (day, len(state), q))
    for e in state:
        print("  %s %s（子任務 %d）" % (e.get("slot"), e["summary"], len(e.get("subtasks", []))))
    print("計畫檔 plans/%s.json：%s" % (day, "存在" if os.path.exists(p_plan(day)) else "不存在"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="work-boss 監工執行器")
    ap.add_argument("cmd", choices=["morning", "evening", "chase", "close", "sync", "plan-carry", "status"])
    ap.add_argument("--date", help="YYYY-MM-DD；morning/evening/chase/sync/status 默認今日，close/plan-carry 默認昨日")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--force", action="store_true", help="morning：即使無新增也重發卡片")
    a = ap.parse_args(argv)
    if a.no_notify:
        os.environ["WORK_BOSS_NO_NOTIFY"] = "1"
    today = dt.date.today()
    if a.date:
        day = a.date
    elif a.cmd in ("close", "plan-carry"):
        day = (today - dt.timedelta(days=1)).isoformat()
    else:
        day = today.isoformat()
    if a.cmd == "status":
        cmd_status(day)
        return 0
    client = DryLark() if a.dry_run else LarkCLI()
    try:
        if a.cmd == "morning":
            cmd_morning(day, client, a.dry_run, a.force)
        elif a.cmd == "evening":
            cmd_evening(day, client, a.dry_run)
        elif a.cmd == "chase":
            cmd_chase(day, client, a.dry_run)
        elif a.cmd == "close":
            cmd_close(day, client, a.dry_run)
        elif a.cmd == "sync":
            cmd_sync(day, client, a.dry_run)
        elif a.cmd == "plan-carry":
            carry_and_plan(day, client, a.dry_run)
    except Exception as e:  # 失敗要留痕並盡力通知，不能靜默
        msg = "%s %s 失敗：%s" % (day, a.cmd, str(e)[:300])
        print(msg, file=sys.stderr)
        if not a.dry_run:
            write_worklog("%s-%s-supervisor-error.md" % (compact(day), dt.datetime.now().strftime("%H%M")),
                          "# 監工執行失敗\n\n- 命令：%s\n- 錯誤：%s\n" % (a.cmd, str(e)[:500]), False)
            try:
                LarkCLI().card("監工執行失敗", "red", "**%s**\n錯誤已寫入 worklog。" % a.cmd, subtitle=day)
            except Exception:
                pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
