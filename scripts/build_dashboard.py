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


def main():
    data = {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "tasks": tasks(), "logs": worklogs()}
    tpl = read(os.path.join(ROOT, "scripts", "dashboard_template.html"))
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("/*__DATA__*/null", payload)
    out = os.path.join(ROOT, "docs", "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("已生成", out, "任務 %d 個，日誌 %d 份" % (len(data["tasks"]), len(data["logs"])))


if __name__ == "__main__":
    main()
