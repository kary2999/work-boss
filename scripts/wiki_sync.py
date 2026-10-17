#!/usr/bin/env python3
"""把「工作日志看板」维护到 Lark 知识库的一个页面里（整页重写，只用 REST）。

用法：
  python3 scripts/wiki_sync.py              同步到 Lark 知识库页面（首次会在配置的父页面下新建子页面）
  python3 scripts/wiki_sync.py --dry-run    只在本地渲染，打印各段 Markdown 的大小，不碰 Lark
  python3 scripts/wiki_sync.py --print      把渲染出的 Markdown 全部打印出来
  python3 scripts/wiki_sync.py --out 看板.md  写成本地 Markdown 文件，可在 Lark 里“导入”手动使用（没有写权限时的备用办法）

配置（config/lark.json，私有）：
  "wiki": {"space_id": "…", "parent_node_token": "…", "title": "工作日志看板"}
页面标识保存在 .supervisor/wiki.json。机器人需要对父页面有「可编辑」权限，见 docs/setup-macos.md。
页面内容由本脚本整页重写，不要在页面里手改；父页面原有的正文与子页面不会被动。
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUP = os.path.join(ROOT, ".supervisor")
MAX_LOGS = 10
LOG_CHARS = 6000          # 单篇日志最多放这么多字，防止个别日志撑爆页面
STATUS = {"todo": "待办", "doing": "进行中", "review": "待验收", "done": "已完成", "blocked": "阻塞", "cancelled": "已作废"}
PHASE = {"P-1": "P-1 下单核心", "P-2": "P-2 持仓与记录", "P-3": "P-3 辅助", "P-4": "P-4 行情"}


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def collect(root=ROOT):
    """复用本地看板的数据口径。"""
    spec = importlib.util.spec_from_file_location("build_dashboard", os.path.join(root, "scripts", "build_dashboard.py"))
    bd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bd)
    bd.ROOT = root
    sup = os.path.join(root, ".supervisor")
    asks = _load(os.path.join(sup, "asks.json"), {})
    return {"today": dt.date.today().isoformat(), "tasks": bd.tasks(), "logs": bd.worklogs(), "plans": bd.plans(),
            "min_hours": bd.min_hours(), "progress": _load(os.path.join(sup, "progress.json"), {"days": {}}).get("days", {}),
            "asks": asks}


# ---------- Markdown 渲染（纯函数，有单元测试） ----------
def cell(x):
    return re.sub(r"\s+", " ", str(x if x is not None else "")).replace("|", "\\|").strip()


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join(cell(x) for x in r) + " |")
    return "\n".join(out)


def next_day(d):
    return (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()


def demote(md, by=3):
    """日志里的标题降级，避免盖过页面的结构标题。"""
    def f(m):
        return "#" * min(len(m.group(1)) + by, 9) + " " + m.group(2)
    return re.sub(r"^(#{1,6})\s+(.*)$", f, md, flags=re.M)


def render_chunks(data, now=None, max_logs=MAX_LOGS):
    """返回若干段 Markdown。每段单独转换、单独写入，避免单次请求过大。"""
    now = now or dt.datetime.now()
    today = data["today"]
    tmr = next_day(today)
    chunks = []

    tasks = data["tasks"]
    cnt = {}
    for t in tasks:
        cnt[t["status"]] = cnt.get(t["status"], 0) + 1
    pg = data["progress"].get(today)
    plan_tmr = next((p for p in data["plans"] if p["date"] == tmr), None)
    absent = [l for l in data["logs"] if l.get("absent")]
    lines = ["> 本页由“监工”自动生成，每次监工运行后整页重写，请勿手改。最近更新：%s" % now.strftime("%Y-%m-%d %H:%M"), "", "## 概览", ""]
    lines.append("- 今日（%s）：%s" % (today, "主任务 %d/%d，子任务 %d/%d（来自 Lark 勾选）" % (
        pg["main_done"], pg["main_total"], pg["sub_done"], pg["sub_total"]) if pg else "尚无同步"))
    if plan_tmr:
        low = plan_tmr["total_hours"] < data["min_hours"]
        lines.append("- 明日计划（%s）：%gh，%s（下限 %gh）%s" % (
            tmr, plan_tmr["total_hours"], "**需求不足**" if low else "需求充足", data["min_hours"], "，草案" if plan_tmr["draft"] else "，已定稿"))
    else:
        lines.append("- 明日计划（%s）：**还没有计划文件**" % tmr)
    lines.append("- 旷工记录：%d 次" % len(absent))
    lines.append("- 任务卡：" + "、".join("%s %d" % (STATUS.get(k, k), v) for k, v in cnt.items()))
    chunks.append("\n".join(lines))

    plans = [p for p in data["plans"] if p["date"] >= today][:3] or data["plans"][:1]
    pl = ["## 计划", ""]
    for p in plans:
        pl += ["### %s（%gh，%s）" % (p["date"], p["total_hours"], "草案" if p["draft"] else "已定稿"), "",
               table(["时段", "任务", "估时", "备注"], [
                   [i["slot"], ("[%s] " % i["ref"] if i["ref"] else "") + i["title"], "%gh" % float(i["est_hours"]),
                    ("顺延" + ("：" + i["reason"] if i["reason"] else "") if i["carried"] else "") +
                    ("；你补充的需求" if i["req"] else "")] for i in p["items"]]) if p["items"] else "（没有项目）", ""]
    chunks.append("\n".join(pl))

    days = sorted(data["progress"], reverse=True)[:7]
    pr = ["## 每日进度（最近 %d 天）" % len(days), ""]
    for d in days:
        g = data["progress"][d]
        pr += ["### %s　主任务 %d/%d　子任务 %d/%d　催促 %d 次%s" % (
            d, g["main_done"], g["main_total"], g["sub_done"], g["sub_total"], g["reminders"], "　**旷工**" if g["absent"] else ""), "",
               table(["状态", "时段", "任务", "子任务", "你写的原因"], [
                   ["✅" if i["completed"] else "⬜", i["slot"] or "", ("[%s] " % i["ref"] if i["ref"] else "") + i["title"],
                    "%d/%d" % (i["subs_done"], i["subs_total"]), "；".join(i.get("reasons") or [])] for i in g["items"]]), ""]
    if days:
        chunks.append("\n".join(pr))

    by = {}
    for t in tasks:
        by.setdefault(t["phase"] or "其他", []).append(t)
    for ph in sorted(by, key=lambda k: (k == "其他", k)):
        rows = [[t["id"], t["page"], t["title"], STATUS.get(t["status"], t["status"]), t["progress"] or "", t["est_hours"] and "%sh" % t["est_hours"] or ""]
                for t in by[ph]]
        head = "## 任务卡\n\n" if ph == sorted(by, key=lambda k: (k == "其他", k))[0] else ""
        chunks.append("%s### %s（%d 张）\n\n%s" % (head, PHASE.get(ph, ph), len(rows), table(["编号", "页面", "标题", "状态", "Lark 进度", "估时"], rows)))

    logs = data["logs"][:max_logs]
    if logs:
        chunks.append("## 工作日志（最近 %d 篇，新的在前）" % len(logs))
    for l in logs:
        body = l["body"].strip()
        body = re.sub(r"^#\s+.*\n+", "", body, count=1)  # 去掉正文第一行的一级标题，已写在三级标题里
        if len(body) > LOG_CHARS:
            body = body[:LOG_CHARS] + "\n\n（以下省略，完整内容见本地 worklog/%s）" % l["file"]
        chunks.append("### %s %s · %s · %s\n\n%s" % (l["date"], l["time"], l["agent"], l["task"], demote(body)))
    return merge_chunks(chunks)


def merge_chunks(chunks, limit=3500):
    """把相邻的小段合并，减少 API 调用次数；单段超过 limit 的保持独立。"""
    out, cur = [], ""
    for c in chunks:
        if cur and len(cur) + len(c) + 2 > limit:
            out.append(cur)
            cur = c
        else:
            cur = (cur + "\n\n" + c) if cur else c
    if cur:
        out.append(cur)
    return out


# ---------- Lark 写入 ----------
class WikiError(RuntimeError):
    pass


def _lark():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import lark_ops
    return lark_ops


def _check(r, what):
    if r.get("code") != 0:
        if r.get("code") == 131006:
            raise WikiError("%s失败：机器人对该页面没有编辑权限（%s）。请把应用添加为该页面或知识库的“可编辑”成员，见 docs/setup-macos.md「知识库同步」" % (what, r.get("msg")))
        raise WikiError("%s失败：code=%s %s" % (what, r.get("code"), r.get("msg")))
    return r.get("data") or {}


def ensure_page(L, token, cfg):
    """返回 (obj_token, node_token)。首次在父页面下新建子页面并记住。"""
    p = os.path.join(SUP, "wiki.json")
    st = _load(p, {})
    if st.get("obj_token"):
        return st["obj_token"], st.get("node_token")
    w = cfg["wiki"]
    d = _check(L.call("POST", "/open-apis/wiki/v2/spaces/%s/nodes" % w["space_id"],
                      {"obj_type": "docx", "parent_node_token": w["parent_node_token"], "node_type": "origin",
                       "title": w.get("title", "工作日志看板")}, token), "新建知识库子页面")
    n = d["node"]
    os.makedirs(SUP, exist_ok=True)
    json.dump({"node_token": n["node_token"], "obj_token": n["obj_token"], "space_id": w["space_id"]}, open(p, "w"))
    return n["obj_token"], n["node_token"]


def clean_blocks(blocks):
    """convert 返回的块不能直接给 descendant：要去掉 parent_id 与表格的 merge_info，否则报 1770001。"""
    out = []
    for b in blocks:
        b = dict(b)
        b.pop("parent_id", None)
        if b.get("block_type") == 31 and isinstance(b.get("table"), dict):
            prop = dict(b["table"].get("property") or {})
            prop.pop("merge_info", None)
            b["table"] = dict(b["table"], property=prop)
        out.append(b)
    return out


def publish(chunks, cfg, sleep=0.4):
    L = _lark()
    token = L.token()
    doc, node = ensure_page(L, token, cfg)
    base = "/open-apis/docx/v1/documents/%s" % doc
    # 1) 清空现有正文
    d = _check(L.call("GET", base + "/blocks/%s/children?page_size=500" % doc, token=token), "读取页面内容")
    n = len(d.get("items") or [])
    if n:
        _check(L.call("DELETE", base + "/blocks/%s/children/batch_delete" % doc, {"start_index": 0, "end_index": n}, token), "清空页面")
        time.sleep(sleep)
    # 2) 逐段：Markdown → 块 → 追加
    pos = 0
    for md in chunks:
        c = _check(L.call("POST", "/open-apis/docx/v1/documents/blocks/convert", {"content_type": "markdown", "content": md}, token), "转换 Markdown")
        first, blocks = c.get("first_level_block_ids") or [], c.get("blocks") or []
        if not first:
            continue
        _check(L.call("POST", base + "/blocks/%s/descendant?document_revision_id=-1" % doc,
                      {"children_id": first, "descendants": clean_blocks(blocks), "index": pos}, token), "写入页面")
        pos += len(first)
        time.sleep(sleep)
    return doc, node, pos


def main():
    ap = argparse.ArgumentParser(description="把工作日志看板同步到 Lark 知识库")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print", action="store_true", dest="show")
    ap.add_argument("--out", help="把渲染好的 Markdown 写成本地文件（可在 Lark 里用“导入”手动贴进去）")
    a = ap.parse_args()
    data = collect()
    chunks = render_chunks(data)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write("\n\n".join(chunks) + "\n")
        print("已写入 %s（%d 字）" % (a.out, sum(len(c) for c in chunks)))
        return 0
    if a.dry_run or a.show:
        for i, c in enumerate(chunks):
            print(("\n----- 第 %d 段 -----\n%s" % (i + 1, c)) if a.show else "第 %2d 段：%5d 字，%d 行" % (i + 1, len(c), c.count("\n") + 1))
        print("共 %d 段，%d 字" % (len(chunks), sum(len(c) for c in chunks)))
        return 0
    cfg = _load(os.path.join(ROOT, "config", "lark.json"), {})
    if not cfg.get("wiki"):
        print("config/lark.json 里没有 wiki 配置，跳过", file=sys.stderr)
        return 0
    try:
        doc, node, n = publish(chunks, cfg)
    except WikiError as e:
        print(str(e), file=sys.stderr)
        return 2
    print("已同步到知识库页面 node=%s doc=%s，写入 %d 个块组" % (node, doc, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
