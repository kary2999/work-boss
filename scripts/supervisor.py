#!/usr/bin/env python3
"""work-boss 監工執行器：確定性腳本，不依賴任何 LLM，供 macOS launchd（或 cron）定時呼叫。

子命令:
  morning   [--date D]   依 plans/D.json 建 Lark 任務（含子任務、順延項改期不重建）並發卡片
  evening   [--date D]   收集今日完成度，寫 progress 日誌，發進度詢問卡片
  chase     [--date D]   檢查是否完成／已說明原因／已回覆；否則發催促卡片
  close     [--date D]   收尾 D（默認昨日）：判定旷工、順延未完成項、生成次日 plans/ 草案
  plan-carry [--date D]  只做「順延並生成次日計畫」，不發消息、不寫旷工（用於補產出）
  status    [--date D]   只讀本地檔案，顯示今日任務與提問狀態

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


def pack_slots(items, windows=None, hard_end="22:00"):
    """依序把 items（需含 est_hours）排進工作時段，返回新列表（含 slot，排不下為 None）。"""
    windows = windows or WINDOWS
    wins = [(hm_to_min(a), hm_to_min(b)) for a, b in windows]
    wi, cursor = 0, wins[0][0]
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


def build_next_plan(results, backlog, next_day, cap=DAY_CAP_HOURS):
    """順延未完成項（排最前，沿用原 Lark 任務）+ 從 backlog 補到工時上限，再排時段。"""
    carried = []
    for r in results:
        if r["completed"]:
            continue
        ref, title = parse_summary(r["summary"])
        subs = []
        for s in r.get("subtasks_open", []):
            subs.append({"guid": s["guid"], "title": s["summary"]})
        carried.append({
            "id": r.get("id") or (ref + "-" + title)[:40],
            "ref": ref, "title": title, "est_hours": r.get("est_hours", 1.0),
            "done_std": r.get("done_std", ""), "carried": True,
            "carry_reason": "；".join(r["reasons"]) if r["reasons"] else "",
            "guid": r["guid"], "subtasks": subs,
        })
    total = sum(float(c["est_hours"]) for c in carried)
    carried_refs = {c["ref"] for c in carried if c["ref"]}
    fresh, deferred = [], []
    for b in backlog:
        if b["ref"] in carried_refs:
            continue
        if total + float(b["est_hours"]) <= cap:
            fresh.append(b)
            total += float(b["est_hours"])
        else:
            deferred.append(b["ref"] or b["title"])
    items = pack_slots(carried + fresh)
    return {"date": next_day, "goal": "", "generated_by": "supervisor", "total_hours": round(total, 2),
            "deferred": deferred, "items": items}


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
        items.append({"id": meta.get("id", os.path.basename(p)), "ref": meta.get("id", ""),
                      "title": meta.get("title", os.path.basename(p)), "est_hours": est,
                      "done_std": "", "subtasks": subs})
    return items


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


def collect_results(state, client, with_comments=True):
    res = []
    for e in state:
        g = client.task_get(e["guid"])
        subs_open, subs_done = [], 0
        for s in e.get("subtasks", []):
            if client.task_get(s["guid"])["completed"]:
                subs_done += 1
            else:
                subs_open.append(s)
        reasons = []
        if with_comments and not g["completed"]:
            for c in client.comments(e["guid"]):
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


def cmd_morning(day, client, dry, force=False):
    plan = load_json(p_plan(day))
    if not plan:
        client.card("今日無工作計畫", "grey",
                    "**%s 沒有 plans/%s.json**\n請任一 agent 或本人補上計畫檔後，手動執行 `supervisor.py morning`。" % (day, day),
                    subtitle=day, footer="監工 · 計畫檔格式見 plans/_TEMPLATE.json")
        print("無計畫檔，已發提示卡片")
        return
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
        desc += HINT
        entry = {"id": it["id"], "summary": summary, "slot": slot_label, "est_hours": it.get("est_hours", 1.0),
                 "done_std": it.get("done_std", ""), "carried": bool(it.get("carried")), "subtasks": []}
        if it.get("guid"):  # 順延項：沿用原任務，只改期，不重複建立
            client.task_update(it["guid"], summary=summary, desc=desc, start=start, due=due)
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
    state = load_json(p_state_tasks(day), [])
    if not state:
        print("今日無任務狀態，略過")
        return
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


def cmd_chase(day, client, dry):
    state = load_json(p_state_tasks(day), [])
    if not state:
        return
    q = load_json(p_state_q(day), {"date": day, "question_ids": [], "replied": False, "reminders": 0})
    if q.get("replied"):
        return
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
        return finish("今日工作項已全部完成", "主任務 %d / %d 已勾選完成。" % (done, m))
    if user_replies:
        return finish("已記錄今日回報", "已收到你對提問消息的回覆，已寫入日誌。未完成項將於 00:00 順延。")
    pending = [r for r in res if not r["completed"] and not r["reasons"]]
    if not pending:
        return finish("已記錄未完成原因", "未完成項都已有評論說明，將於 00:00 順延並重排明日工作。")
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


def carry_and_plan(day, client, dry, write_plan=True):
    """收集 day 的最終結果，順延並生成次日計畫。返回 (results, plan, wrote)。"""
    state = load_json(p_state_tasks(day), [])
    res = collect_results(state, client)
    nxt = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
    plan = build_next_plan(res, read_backlog(), nxt)
    wrote = False
    if write_plan:
        if os.path.exists(p_plan(nxt)):
            print("plans/%s.json 已存在，不覆蓋（請 agent 自行併入順延項）" % nxt)
        elif dry:
            print("[dry-run] 將寫 plans/%s.json：\n%s" % (nxt, json.dumps(plan, ensure_ascii=False, indent=1)))
        else:
            save_json(p_plan(nxt), plan)
            wrote = True
    return res, plan, wrote


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


def cmd_status(day):
    state = load_json(p_state_tasks(day), [])
    q = load_json(p_state_q(day), None)
    print("日期 %s｜任務 %d 項｜提問狀態 %s" % (day, len(state), q))
    for e in state:
        print("  %s %s（子任務 %d）" % (e.get("slot"), e["summary"], len(e.get("subtasks", []))))
    print("計畫檔 plans/%s.json：%s" % (day, "存在" if os.path.exists(p_plan(day)) else "不存在"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="work-boss 監工執行器")
    ap.add_argument("cmd", choices=["morning", "evening", "chase", "close", "plan-carry", "status"])
    ap.add_argument("--date", help="YYYY-MM-DD；morning/evening/chase/status 默認今日，close/plan-carry 默認昨日")
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
