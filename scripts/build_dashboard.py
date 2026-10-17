#!/usr/bin/env python3
"""掃描 tasks/ 與 worklog/，生成自包含的 docs/dashboard.html（離線可開，無外部依賴）。

用法：python3 scripts/build_dashboard.py
資料每次生成時嵌入頁面；要看最新狀態，重新執行本腳本後刷新頁面。
"""
import datetime
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def split_front(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^([A-Za-z_]+):\s*(.*?)\s*(#.*)?$", line)
        if mm:
            meta[mm.group(1)] = mm.group(2).strip()
    return meta, m.group(2)


def tasks():
    out = []
    d = os.path.join(ROOT, "tasks")
    for n in sorted(os.listdir(d)):
        if not n.endswith(".md") or n.startswith("_"):
            continue
        meta, body = split_front(read(os.path.join(d, n)))
        total = len(re.findall(r"^\s*- \[[ xX]\]", body, re.M))
        done = len(re.findall(r"^\s*- \[[xX]\]", body, re.M))
        out.append({"file": n, "id": meta.get("id", n), "title": meta.get("title", n),
                    "status": meta.get("status", "todo") or "todo", "owner": meta.get("owner", ""),
                    "progress": meta.get("progress", ""),
                    "due": meta.get("due", ""), "created": meta.get("created", ""),
                    "blocked_by": meta.get("blocked_by", ""), "done": done, "total": total, "body": body})
    return out


def worklogs():
    out = []
    d = os.path.join(ROOT, "worklog")
    for n in sorted(os.listdir(d), reverse=True):
        if not n.endswith(".md") or n.startswith("_"):
            continue
        m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{4})-([^-]+)-(.+)\.md$", n)
        if m:
            date = "%s-%s-%s" % m.group(1, 2, 3)
            hhmm = "%s:%s" % (m.group(4)[:2], m.group(4)[2:])
            agent, task = m.group(5), m.group(6)
        else:
            date, hhmm, agent, task = "", "", "", n[:-3]
        body = read(os.path.join(d, n))
        out.append({"file": n, "date": date, "time": hhmm, "agent": agent, "task": task, "body": body,
                    "absent": agent == "supervisor" and task == "absent"})
    return out


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def plans():
    out = []
    d = os.path.join(ROOT, "plans")
    if not os.path.isdir(d):
        return out
    for n in sorted(os.listdir(d), reverse=True):
        if not re.match(r"^\d{4}-\d{2}-\d{2}\.json$", n):
            continue
        p = load_json(os.path.join(d, n), None)
        if not p:
            continue
        items = p.get("items", [])
        out.append({"date": p.get("date", n[:-5]), "generated_by": p.get("generated_by", "人／agent"),
                    "draft": bool(p.get("draft")), "deferred": p.get("deferred", []),
                    "total_hours": round(sum(float(i.get("est_hours", 1)) for i in items), 2),
                    "items": [{"ref": i.get("ref", ""), "title": i.get("title", ""), "slot": i.get("slot") or "待排",
                               "est_hours": i.get("est_hours", 1), "carried": bool(i.get("carried")),
                               "reason": i.get("carry_reason", ""), "req": bool(i.get("from_requirement")),
                               "subs": len(i.get("subtasks", []))} for i in items]})
    return out


def min_hours():
    env = os.environ.get("WORK_BOSS_MIN_HOURS")
    if env is not None:
        return float(env)
    cfg = load_json(os.path.join(ROOT, "config", "lark.json"), {}) or {}
    return float(cfg.get("min_plan_hours", 4.0))


def main():
    sup = os.path.join(ROOT, ".supervisor")
    prog = load_json(os.path.join(sup, "progress.json"), {"days": {}})
    asks = load_json(os.path.join(sup, "asks.json"), {})
    needs = load_json(os.path.join(sup, "needs.json"), {"items": []})
    data = {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "today": datetime.date.today().isoformat(),
            "tasks": tasks(), "logs": worklogs(), "plans": plans(), "min_hours": min_hours(),
            "progress": prog.get("days", {}),
            "asks": {k: {"count": v.get("count", 0), "last": v.get("last", "")} for k, v in asks.items()},
            "needs": [{"title": n["title"], "est_hours": n["est_hours"], "target": n["target"]} for n in needs.get("items", [])]}
    tpl = read(os.path.join(ROOT, "scripts", "dashboard_template.html"))
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/null", payload)
    out = os.path.join(ROOT, "docs", "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("已生成", out, "任務卡 %d 張，日誌 %d 份，進度 %d 天，計畫 %d 份" % (
        len(data["tasks"]), len(data["logs"]), len(data["progress"]), len(data["plans"])))


if __name__ == "__main__":
    main()
