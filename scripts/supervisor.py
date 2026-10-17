#!/usr/bin/env python3
"""work-boss 监工执行器：确定性脚本，不依赖任何 LLM，供 macOS launchd（或 cron）定时调用。

子命令:
  morning   [--date D]   依 plans/D.json 建 Lark 任务（含子任务、顺延项改期不重建）并发卡片
  evening   [--date D]   收集今日完成度，写 progress 日志，发进度询问卡片
  chase     [--date D]   检查是否完成／已说明原因／已回复；否则发催促卡片
  close     [--date D]   收尾 D（默认昨日）：判定旷工、顺延未完成项、定稿次日 plans/、需求不足再要一次
  sync      [--date D]   白天同步：进度写回本地、维护次日计划草案、需求不足时在 Lark 主动要需求、刷新看板
  plan-carry [--date D]  只做“顺延并生成次日计划”，不发消息、不写旷工（用于补产出）
  status    [--date D]   只读本地文件，显示今日任务与提问状态

每个会与 Lark 互动的命令结束后，都会：①把进度写进 .supervisor/progress.json 并同步任务卡
（status／progress）；②读取用户对“需求不足”卡片的回复并并入计划；③刷新 docs/dashboard.html。

通用选项: --dry-run（不调用 Lark 写入、不写本地文件，只印出将做什么；读取类调用仍会执行）、--no-notify
数据契约见 docs/data-contract.md。
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
DEFAULT_MIN_HOURS = 4.0   # 当日计划低于此工时视为“需求不足”；可用 config 的 min_plan_hours 或环境变量覆盖
ASK_MAX = 3               # 对同一天的“需求不足”最多主动要几次（收尾的最后一次不计）
ASK_INTERVAL_MIN = 90     # 两次主动要需求之间的最短间隔（分钟）
DASHBOARD = True          # 命令结束后是否刷新看板；测试时关闭
HINT = "【完成请直接勾选；未完成请在‘评论’说明原因】"


# ---------- 纯函数（有单元测试） ----------
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
    """依序把 items（需含 est_hours）排进工作时段，返回新列表（含 slot，排不下为 None）。
    start_after（分钟）：从这个时间点之后开始排，用于往已有计划后面追加。"""
    windows = windows or WINDOWS
    wins = [(hm_to_min(a), hm_to_min(b)) for a, b in windows]
    wi, cursor = 0, wins[0][0]
    if start_after is not None:
        cursor = start_after
        while wi < len(wins) and wins[wi][1] <= cursor:
            wi += 1
    out = []
    for it in items:
        need = int(-(-float(it.get("est_hours", 1.0)) * 60 // 15) * 15)  # 向上取整到 15 分钟
        placed = None
        while wi < len(wins):
            s, e = wins[wi]
            cursor = max(cursor, s)
            if cursor + need <= e:
                placed = (cursor, cursor + need)
                cursor += need
                break
            if need > e - s and cursor == s:  # 比整个窗口还长：从窗口起点硬排，不拆分
                if cursor + need <= hm_to_min(hard_end):
                    placed = (cursor, cursor + need)
                    cursor += need
                    # 后续窗口若已被覆盖则前移
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
    """旷工 = 没有任何主任务完成、没有任何用户评论、对提问也没有回复。"""
    responded = (any(r["completed"] for r in results)
                 or any(r["reasons"] for r in results)
                 or user_reply_count > 0)
    return not responded


def parse_needs_text(text):
    """用户对“需求不足”卡片的回复 → [(标题, 估时)]。每行一条；行内的“2h／1.5小时”是估时，缺省 1h。"""
    text = re.sub(r"@_user_\d+", "", text or "")
    out = []
    for line in text.replace("；", "\n").replace(";", "\n").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.、)）])\s*", "", line).strip()
        if not line:
            continue
        est = 1.0
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|H|小时|小时)", line)
        if m:
            est = min(max(float(m.group(1)), 0.25), 8.0)
            line = (line[:m.start()] + line[m.end():]).strip(" ，,、:：-")
        if line:
            out.append((line[:80], est))
    return out[:10]


def plan_total_hours(plan):
    return round(sum(float(i.get("est_hours", 1.0)) for i in (plan or {}).get("items", [])), 2)


def set_front(text, key, value):
    """改（或补）任务卡 front matter 里的 key，保留行尾注解与正文；无变化时原样返回。"""
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
    """依 Lark 进度推导任务卡 status。只往前走：不降级，不碰 done/blocked，done 必须由人确认。"""
    if current in ("review", "done", "blocked", "cancelled"):
        return current
    if total > 0 and done == total:
        return "review"
    if done > 0:
        return "doing"
    return current


def ask_allowed(entry, now, final=False):
    """对同一天‘需求不足’的主动询问是否允许：次数与间隔限制；收尾（final）可再要一次。"""
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
    """顺延未完成项（排最前，沿用原 Lark 任务）+ 用户主动补充的需求 + backlog，补到工时上限，再排时段。
    cancelled：已作废的任务卡 id，其未完成的 Lark 任务不再顺延。"""
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


# ---------- 文件与状态 ----------
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
        print("[dry-run] 写日志 %s\n%s" % (name, text))
        return path
    os.makedirs(WORKLOG, exist_ok=True)
    if os.path.exists(path):  # 日志只新增，不改既有
        return path
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def read_backlog():
    """tasks/*.md（非模板）中 status 为 todo/doing 的任务卡 → 计划条目。"""
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
    """status 为 cancelled 的任务卡 id：已作废，不再进 backlog，也不再被顺延。"""
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


# ---------- Lark 客户端 ----------
class LarkCLI(object):
    """通过 scripts/lark_ops.py 调用 Lark（只用 REST，不开长连接）。"""

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
        for i in range(0, len(guids), 40):  # 分批，避免命令行过长
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
    """--dry-run：读取类调用照常执行；写入类只印出。"""

    def __init__(self):
        self.n = 0

    def _fake(self, what):
        self.n += 1
        print("[dry-run] %s" % what)
        return "dry-%d" % self.n

    def task_create(self, summary, desc, start, due):
        return {"guid": self._fake("建任务 %s（%s → %s）" % (summary, start, due)), "url": "https://example.invalid/task"}

    def subtask_create(self, parent, summary, due, assign=False):
        return {"guid": self._fake("  建子任务 %s" % summary), "url": ""}

    def task_update(self, guid, summary=None, desc=None, start=None, due=None):
        return {"guid": guid, "updated": [self._fake("改任务 %s → %s（%s → %s）" % (guid, summary, start, due))]}

    def attach(self, guid, paths):
        self._fake("  挂附件 %s → %s" % ([os.path.basename(p) for p in paths], guid))
        return [{"file": p, "ok": True} for p in paths]

    def card(self, title, template, md, subtitle=None, footer=None):
        print("[dry-run] 发卡片 [%s] %s %s\n%s\n-- %s" % (template, title, subtitle or "", md, footer or ""))
        return self._fake("card")


def notify(text, dry):
    if dry or os.environ.get("WORK_BOSS_NO_NOTIFY"):
        return
    try:
        subprocess.run(["osascript", "-e", 'display notification "%s" with title "监工"' % text.replace('"', "'")[:180]],
                       capture_output=True, timeout=10)
    except Exception:
        pass


# ---------- 业务流程 ----------
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
    got = _get_many(client, guids)  # 批量读：一次命令读一批，避免每个任务都新开进程换令牌
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
    """把计划项的参考图作为附件挂到 Lark 任务。路径相对仓库根目录。
    单张失败不影响整体（任务已建好）；返回成功挂上的文件名。"""
    paths = [p if os.path.isabs(p) else os.path.join(ROOT, p) for p in images]
    ok = []
    try:
        for row in client.attach(guid, paths):
            if row.get("ok"):
                ok.append(os.path.basename(row["file"]))
            else:
                print("附件失败：%s %s" % (row.get("file"), row.get("error")), file=sys.stderr)
    except Exception as e:
        print("附件上传异常：%s" % str(e)[:200], file=sys.stderr)
    return ok


def cmd_morning(day, client, dry, force=False):
    _morning_core(day, client, dry, force)
    post_run(day, client, dry, None, "morning")


def _morning_core(day, client, dry, force=False):
    new_needs = ingest_requirements(client, day, dry)  # 夜里用户对“需求不足”卡片的回复
    plan = load_json(p_plan(day))
    if plan is None:
        plan = refresh_plan(day, [], client, dry, final=True)  # 没有计划文件：用补充需求与 backlog 自动生成
    elif new_needs:
        plan = append_needs(plan, new_needs, day, dry)
    if not plan or not plan.get("items"):
        client.card("今日无工作计划", "grey",
                    "**%s 没有可排的工作**（没有计划文件、没有补充需求、backlog 也是空的）。\n请回复下一张“需求不足”卡片，或补任务卡／计划文件。" % day,
                    subtitle=day, footer="监工 · 计划文件格式见 plans/_TEMPLATE.json")
        maybe_ask(client, plan or {"items": []}, day, dry, final=False)
        print("无可排的工作，已发提示并要需求")
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
        desc = "时段：%s %s（约 %sh）\n" % (day, slot_label, it.get("est_hours", 1))
        if it.get("done_std"):
            desc += "完成标准：%s\n" % it["done_std"]
        if it.get("carried"):
            desc += "【顺延项】%s\n" % (it.get("carry_reason") or "昨日未完成")
        if it.get("images"):
            desc += "参考图（已作为附件上传）：%s\n" % "、".join(os.path.basename(p) for p in it["images"])
        desc += HINT
        entry = {"id": it["id"], "summary": summary, "slot": slot_label, "est_hours": it.get("est_hours", 1.0),
                 "done_std": it.get("done_std", ""), "carried": bool(it.get("carried")), "subtasks": []}
        if it.get("guid"):  # 顺延项：沿用原任务，只改期，不重复建立
            # 顺延项只改标题与起止时间，不动描述：描述里可能有人或 agent 补充的规范与说明
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
        print("今日任务已建立，无新增项；不重发卡片")
        return
    rows = []
    total = 0.0
    for e in state:
        total += float(e.get("est_hours", 0))
        mark = "（顺延）" if e.get("carried") else ""
        title = parse_summary(e["summary"])[1]
        link = "[打开](%s)" % e["url"] if e.get("url") else ""
        rows.append((e["slot"], title + mark, "%gh" % float(e.get("est_hours", 0)), len(e["subtasks"]), link))
    md = "**今日目标**：%s\n\n%s\n\n**预计总工时**：约 %.2fh（新建 %d、顺延改期 %d）\n\n完成请在 Lark 任务勾选，做不完请在任务‘评论’写原因。" % (
        plan.get("goal") or "按计划完成下列工作", md_table(rows, ["时段", "任务", "估时", "子任务", "链接"]), total, created, rescheduled)
    client.card("今日工作项", "orange", md, subtitle=day,
                footer="监工 · 20:30 询问进度；到期未完成将顺延并重排明日工作")
    notify("今日工作项已发出（%d 项，约 %.1fh）" % (len(state), total), dry)


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
        print("今日无任务状态，略过")
        return None
    res = collect_results(state, client)
    done, m, sd, st = progress_summary(res)
    rows = []
    for r in res:
        status = "✅ 已完成" if r["completed"] else ("📝 有说明" if r["reasons"] else "⬜ 未完成")
        note = "；".join(x[:40] for x in r["reasons"]) if r["reasons"] else ""
        link = "[打开](%s)" % r["url"] if r["url"] else ""
        rows.append((r["slot"], parse_summary(r["summary"])[1], status, note, "%d/%d" % (r["subtasks_done"], r["subtasks_total"]), link))
    color = "green" if done == m else ("orange" if done else "red")
    md = "**完成度** 主任务 %d / %d · 子任务 %d / %d\n\n%s\n\n**请处理**：未完成请在 Lark 点击完成，或在任务‘评论’说明原因。" % (
        done, m, sd, st, md_table(rows, ["时段", "任务", "状态", "备注", "子任务", "链接"]))
    mid = client.card("今日进度询问", color, md, subtitle=day,
                      footer="监工 · 每 30 分钟催促至 24:00；到期仍未完成将顺延并重排明日工作")
    q = load_json(p_state_q(day), {"date": day, "question_ids": [], "replied": False, "reminders": 0})
    q["question_ids"].append(mid)
    if done == m:
        q["replied"] = True
    if not dry:
        save_json(p_state_q(day), q)
    lines = ["# %s 晚间进度（监工自动生成）" % day, "",
             "- 完成度：主任务 %d/%d，子任务 %d/%d" % (done, m, sd, st), "- 依据：Lark 任务勾选状态与用户评论", ""]
    for r in res:
        lines.append("- [%s] %s｜%s%s" % ("x" if r["completed"] else " ", r["summary"],
                                         "已完成" if r["completed"] else "未完成",
                                         ("｜说明：" + "；".join(r["reasons"])) if r["reasons"] else ""))
    write_worklog("%s-2030-supervisor-progress.md" % compact(day), "\n".join(lines) + "\n", dry)
    notify("晚间进度：完成 %d/%d 项" % (done, m), dry)
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
        lines = ["# %s %s 回报记录（监工自动生成）" % (day, hhmm), "", "- 完成度：主任务 %d/%d，子任务 %d/%d" % (done, m, sd, st)]
        for r in res:
            lines.append("- %s｜%s%s" % (r["summary"], "已完成" if r["completed"] else "未完成",
                                        ("｜说明：" + "；".join(r["reasons"])) if r["reasons"] else ""))
        for r in user_replies:
            lines.append("- 回复：%s" % r.get("text", ""))
        write_worklog("%s-%s-supervisor-reply.md" % (compact(day), hhmm), "\n".join(lines) + "\n", dry)
        q["replied"] = True
        if not dry:
            save_json(p_state_q(day), q)
        client.card(title, "green", md, subtitle=day, footer="监工 · 今日追问结束")

    if done == m:
        finish("今日工作项已全部完成", "主任务 %d / %d 已勾选完成。" % (done, m))
        return res
    if user_replies:
        finish("已记录今日回报", "已收到你对提问消息的回复，已写入日志。未完成项将于 00:00 顺延。")
        return res
    pending = [r for r in res if not r["completed"] and not r["reasons"]]
    if not pending:
        finish("已记录未完成原因", "未完成项都已有评论说明，将于 00:00 顺延并重排明日工作。")
        return res
    q["reminders"] = q.get("reminders", 0) + 1
    rows = [(r["slot"], parse_summary(r["summary"])[1], "%d/%d" % (r["subtasks_done"], r["subtasks_total"]),
             "[打开](%s)" % r["url"] if r["url"] else "") for r in pending]
    md = "**未完成 %d / 共 %d 项**\n\n%s\n\n请在 Lark 任务中**点击完成**；做不完请在评论写原因。" % (
        len(pending), m, md_table(rows, ["时段", "任务", "子任务", "链接"]))
    mid = client.card("第 %d 次催促：请点击完成" % q["reminders"], "red", md,
                      subtitle=dt.datetime.now().strftime("%H:%M"),
                      footer="监工 · 到期仍未完成将顺延并重新制定明日任务")
    q["question_ids"].append(mid)
    if not dry:
        save_json(p_state_q(day), q)
    return res


def next_day(day):
    return (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()


def refresh_plan(target, results, client, dry, final=False):
    """（重新）生成 target 日的计划。只覆盖“监工自己生成”的计划文件；
    人或其他 agent 写的（generated_by 不是 supervisor）原样保留。返回生效的计划。"""
    existing = load_json(p_plan(target))
    if existing is not None and existing.get("generated_by") != "supervisor":
        return existing
    needs = [n for n in load_json(p_needs(), {"items": []})["items"] if n["target"] == target]
    plan = build_next_plan(results, read_backlog(), target, extra=needs, cancelled=read_cancelled_refs())
    plan["draft"] = not final
    if dry:
        print("[dry-run] 将写 plans/%s.json（%s，约 %sh，%d 项）" % (
            target, "定稿" if final else "草案", plan["total_hours"], len(plan["items"])))
    else:
        save_json(p_plan(target), plan)
    return plan


def append_needs(plan, new_needs, target, dry):
    """把用户补充的需求追加到已有计划（只追加，不动既有项），排在现有时段之后。"""
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
    """读取用户对“需求不足”卡片的回复，每行一条需求，记入 .supervisor/needs.json。返回新增条目。"""
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
        print("[dry-run] 读到 %d 条补充需求：%s" % (len(new), [n["title"] for n in new]))
    return new


def maybe_ask(client, plan, target, dry, final=False):
    """target 日的计划工时低于下限就在 Lark 主动要需求（有次数与间隔限制）。返回是否发了卡片。"""
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
    md = ("**%s 目前只排了 %d 项、约 %.2fh，低于下限 %.1fh。**\n\n%s\n\n"
          "**怎么补**（任选一种）\n"
          "1. **对本卡片点“回复”**，每行写一条需求，行内可写“2h”表示估时，不写默认 1h\n"
          "2. 在 `tasks/` 新增任务卡（写上 `est_hours`）\n"
          "3. 让 agent 写 `plans/%s.json`") % (
        target, len(items), total, minh,
        md_table(rows, ["时段", "已排", "估时"]) if rows else "（目前没有任何已排项）", target)
    mid = client.card("%s需求不足" % ("今日" if target == dt.date.today().isoformat() else "明日"),
                      "red" if total == 0 else "orange", md, subtitle=target,
                      footer="监工 · 你的回复会被读取并并入计划；没有需求就没有任务可排")
    entry["mids"].append(mid)
    entry["count"] = entry.get("count", 0) + 1
    entry["last"] = now.isoformat(timespec="seconds")
    asks[target] = entry
    if not dry:
        save_json(p_asks(), asks)
    notify("%s需求不足（%.1fh < %.1fh），已在 Lark 要需求" % (target, total, minh), dry)
    return True


def sync_progress(day, res, dry):
    """把 day 的 Lark 进度写进 .supervisor/progress.json（看板与任务卡同步的依据）。"""
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
        print("[dry-run] 进度 %s：主任务 %d/%d，子任务 %d/%d" % (day, done, m, sd, st))
    else:
        save_json(p_progress(), prog)
    return prog


def backfill_progress(client, dry, skip_day=None):
    """把有任务状态文件、但还没进度记录的历史日补进 progress.json（首次启用或漏跑时）。"""
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
    """依 progress.json 回写 tasks/*.md 的 status 与 progress（按 front matter 的 id 对应 ref）。"""
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
        print("同步任务卡：" + "；".join(changed))
    return changed


def refresh_dashboard(dry):
    if dry or not DASHBOARD:
        return
    try:
        subprocess.run([sys.executable, "-X", "utf8", "-I", os.path.join(ROOT, "scripts", "build_dashboard.py")],
                       capture_output=True, timeout=60, cwd=ROOT)
    except Exception:
        pass  # 看板刷新失败不能拖垮监工


def refresh_wiki(dry):
    """配置了 wiki 才同步到 Lark 知识库页面；失败只提示，不影响主流程。"""
    if dry or not DASHBOARD:
        return
    cfg = load_json(os.path.join(ROOT, "config", "lark.json"), {}) or {}
    if not cfg.get("wiki"):
        return
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", "-I", os.path.join(ROOT, "scripts", "wiki_sync.py")],
                           capture_output=True, text=True, timeout=240, cwd=ROOT)
        if r.returncode != 0:
            print("知识库同步失败：" + (r.stderr or r.stdout).strip()[:300], file=sys.stderr)
    except Exception as e:
        print("知识库同步异常：%s" % str(e)[:200], file=sys.stderr)


def post_run(day, client, dry, res, phase):
    """每个命令收尾：进度写回本地 → 任务卡同步 → 读补充需求 → 维护次日计划 → 需求不足就要 → 刷新看板。"""
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
    if phase in ("morning", "evening", "sync", "close"):  # chase 每 30 分钟一次，不同步知识库
        refresh_wiki(dry)


def carry_and_plan(day, client, dry, write_plan=True):
    """收集 day 的最终结果，顺延并生成（定稿）次日计划。返回 (results, plan, wrote)。"""
    state = load_json(p_state_tasks(day), [])
    res = collect_results(state, client)
    nxt = next_day(day)
    ingest_requirements(client, nxt, dry)
    existing = load_json(p_plan(nxt))
    if not write_plan:
        plan = build_next_plan(res, read_backlog(), nxt)
        return res, plan, False
    if existing is not None and existing.get("generated_by") != "supervisor":
        print("plans/%s.json 是别人写的，不覆盖（请 agent 自行并入顺延项）" % nxt)
        plan = build_next_plan(res, read_backlog(), nxt)
        return res, plan, False
    plan = refresh_plan(nxt, res, client, dry, final=True)
    return res, plan, not dry


def cmd_close(day, client, dry):
    rollover = "%s-2400-supervisor-rollover.md" % compact(day)
    if os.path.exists(os.path.join(WORKLOG, rollover)) and not dry:
        print("已收尾过 %s，略过" % day)
        return
    state = load_json(p_state_tasks(day))
    if state is None:
        write_worklog("%s-2400-supervisor-error.md" % compact(day),
                      "# %s 无法收尾\n\n昨日没有任务状态文件（未建立任务或监工未运行），无法判定旷工。\n" % day, dry)
        return
    res, plan, wrote = carry_and_plan(day, client, dry)
    q = load_json(p_state_q(day), {"question_ids": []})
    user_replies = 0
    for mid in q.get("question_ids", []):
        user_replies += sum(1 for r in client.replies(mid) if r.get("sender_type") == "user")
    absent = decide_absent(res, user_replies)
    done, m, sd, st = progress_summary(res)
    lines = ["# %s 收尾（监工自动生成）" % day, "",
             "- 完成度：主任务 %d/%d，子任务 %d/%d" % (done, m, sd, st),
             "- 旷工：%s" % ("是（无完成、无评论、无回复）" if absent else "否"), "", "## 顺延清单", ""]
    carried = [i for i in plan["items"] if i.get("carried")]
    for c in carried:
        lines.append("- %s %s（原因：%s）" % (c.get("ref", ""), c["title"], c.get("carry_reason") or "未说明"))
    if not carried:
        lines.append("- 无")
    lines += ["", "## 次日计划 plans/%s.json" % plan["date"], "",
              "- 总工时约 %sh；未排入：%s" % (plan["total_hours"], "、".join(plan["deferred"]) or "无"),
              "- 文件%s" % ("已写入" if wrote else "未写入（已存在或 dry-run）")]
    write_worklog(rollover, "\n".join(lines) + "\n", dry)
    if absent:
        write_worklog("%s-2400-supervisor-absent.md" % compact(day),
                      "# 旷工（未回报）\n\n- 日期：%s\n- 判定：无任一主任务完成、无用户评论、对提问无回复\n"
                      "- 提问与催促次数：%d\n- 说明：仅代表未回应；若次数少于 7，可能因电脑休眠导致追问不足，可信度较低\n"
                      % (day, len(q.get("question_ids", []))), dry)
    color = "red" if absent else ("green" if done == m else "orange")
    rows = [(c.get("ref", ""), c["title"], c.get("carry_reason") or "未说明") for c in carried[:8]]
    md = "**完成度**：主任务 %d/%d · 子任务 %d/%d\n**旷工**：%s\n\n%s" % (
        done, m, sd, st, "是" if absent else "否",
        md_table(rows, ["编号", "顺延项", "原因"]) if rows else "全部完成，无顺延。")
    client.card("昨日收尾", color, md, subtitle=day, footer="监工 · 08:30 将按顺延后的计划建立今日任务")
    notify("昨日收尾：完成 %d/%d，顺延 %d 项%s" % (done, m, len(carried), "，旷工" if absent else ""), dry)
    post_run(day, client, dry, res, "close")


def cmd_sync(day, client, dry):
    """白天同步：不发进度卡片，只更新本地进度、维护次日计划草案、需求不足才要需求、刷新看板。"""
    state = load_json(p_state_tasks(day), [])
    res = collect_results(state, client) if state else None
    post_run(day, client, dry, res, "sync")


def cmd_status(day):
    state = load_json(p_state_tasks(day), [])
    q = load_json(p_state_q(day), None)
    print("日期 %s｜任务 %d 项｜提问状态 %s" % (day, len(state), q))
    for e in state:
        print("  %s %s（子任务 %d）" % (e.get("slot"), e["summary"], len(e.get("subtasks", []))))
    print("计划文件 plans/%s.json：%s" % (day, "存在" if os.path.exists(p_plan(day)) else "不存在"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="work-boss 监工执行器")
    ap.add_argument("cmd", choices=["morning", "evening", "chase", "close", "sync", "plan-carry", "status"])
    ap.add_argument("--date", help="YYYY-MM-DD；morning/evening/chase/sync/status 默认今日，close/plan-carry 默认昨日")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--force", action="store_true", help="morning：即使无新增也重发卡片")
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
    except Exception as e:  # 失败要留痕并尽力通知，不能静默
        msg = "%s %s 失败：%s" % (day, a.cmd, str(e)[:300])
        print(msg, file=sys.stderr)
        if not a.dry_run:
            write_worklog("%s-%s-supervisor-error.md" % (compact(day), dt.datetime.now().strftime("%H%M")),
                          "# 监工执行失败\n\n- 命令：%s\n- 错误：%s\n" % (a.cmd, str(e)[:500]), False)
            try:
                LarkCLI().card("监工执行失败", "red", "**%s**\n错误已写入 worklog。" % a.cmd, subtitle=day)
            except Exception:
                pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
