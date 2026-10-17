#!/usr/bin/env python3
"""把「工作日志看板」维护到 Lark 知识库的一个页面里（整页重写，只用 REST）。

用法：
  python3 scripts/wiki_sync.py              同步到 Lark 知识库：“工作计划”页面下含看板、任务卡（每张一页，带参考图）、工作日志（每篇一页）；内容没变的页面会跳过
  python3 scripts/wiki_sync.py --dry-run    只在本地渲染，列出各页的大小与图片数，不碰 Lark（--force 忽略哈希全部重写）
  python3 scripts/wiki_sync.py --print      把渲染出的 Markdown 全部打印出来
  python3 scripts/wiki_sync.py --out 看板.md  写成本地 Markdown 文件，可在 Lark 里“导入”手动使用（没有写权限时的备用办法）

配置（config/lark.json，私有）：
  "wiki": {"space_id": "…", "parent_node_token": "留空＝知识库根目录"}
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
    tasks = bd.tasks()
    for t in tasks:
        meta, _ = bd.split_front(open(os.path.join(root, "tasks", t["file"]), encoding="utf-8").read())
        t["images"] = [x.strip() for x in meta.get("images", "").split("|") if x.strip()]
    return {"today": dt.date.today().isoformat(), "tasks": tasks, "logs": bd.worklogs(), "plans": bd.plans(),
            "min_hours": bd.min_hours(), "progress": _load(os.path.join(sup, "progress.json"), {"days": {}}).get("days", {}),
            "asks": asks, "needs": _load(os.path.join(sup, "needs.json"), {"items": []}).get("items", [])}


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


def _overview(data, now):
    today, tmr = data["today"], next_day(data["today"])
    cnt = {}
    for t in data["tasks"]:
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
    return "\n".join(lines)


def _plans(data):
    today = data["today"]
    plans = [p for p in data["plans"] if p["date"] >= today][:3] or data["plans"][:1]
    pl = ["## 计划", ""]
    for p in plans:
        pl += ["### %s（%gh，%s）" % (p["date"], p["total_hours"], "草案" if p["draft"] else "已定稿"), "",
               table(["时段", "任务", "估时", "备注"], [
                   [i["slot"], ("[%s] " % i["ref"] if i["ref"] else "") + i["title"], "%gh" % float(i["est_hours"]),
                    ("顺延" + ("：" + i["reason"] if i["reason"] else "") if i["carried"] else "") +
                    ("；你补充的需求" if i["req"] else "")] for i in p["items"]]) if p["items"] else "（没有项目）", ""]
    return "\n".join(pl)


def _progress(data, days=7):
    days = sorted(data["progress"], reverse=True)[:days]
    if not days:
        return None
    pr = ["## 每日进度（最近 %d 天）" % len(days), ""]
    for d in days:
        g = data["progress"][d]
        pr += ["### %s　主任务 %d/%d　子任务 %d/%d　催促 %d 次%s" % (
            d, g["main_done"], g["main_total"], g["sub_done"], g["sub_total"], g["reminders"], "　**旷工**" if g["absent"] else ""), "",
               table(["状态", "时段", "任务", "子任务", "你写的原因"], [
                   ["✅" if i["completed"] else "⬜", i["slot"] or "", ("[%s] " % i["ref"] if i["ref"] else "") + i["title"],
                    "%d/%d" % (i["subs_done"], i["subs_total"]), "；".join(i.get("reasons") or [])] for i in g["items"]]), ""]
    return "\n".join(pr)


def _cards(data, with_head=True):
    by = {}
    for t in data["tasks"]:
        by.setdefault(t["phase"] or "其他", []).append(t)
    order = sorted(by, key=lambda k: (k == "其他", k))
    out = []
    for ph in order:
        rows = [[t["id"], t["page"], t["title"], STATUS.get(t["status"], t["status"]), t["progress"] or "", t["est_hours"] and "%sh" % t["est_hours"] or ""]
                for t in by[ph]]
        head = "## 任务卡\n\n" if with_head and ph == order[0] else ""
        out.append("%s### %s（%d 张）\n\n%s" % (head, PHASE.get(ph, ph), len(rows), table(["编号", "页面", "标题", "状态", "Lark 进度", "估时"], rows)))
    return out


def _log_body(l, limit=LOG_CHARS):
    body = l["body"].strip()
    body = re.sub(r"^#\s+.*\n+", "", body, count=1)  # 去掉正文第一行的一级标题
    if limit and len(body) > limit:
        body = body[:limit] + "\n\n（以下省略，完整内容见本地 worklog/%s）" % l["file"]
    return body


def render_chunks(data, now=None, max_logs=MAX_LOGS):
    """返回若干段 Markdown（单页版，供 --out 导出）。每段单独转换、单独写入，避免单次请求过大。"""
    now = now or dt.datetime.now()
    chunks = [_overview(data, now), _plans(data)]
    pr = _progress(data)
    if pr:
        chunks.append(pr)
    chunks += _cards(data)
    logs = data["logs"][:max_logs]
    if logs:
        chunks.append("## 工作日志（最近 %d 篇，新的在前）" % len(logs))
    for l in logs:
        chunks.append("### %s %s · %s · %s\n\n%s" % (l["date"], l["time"], l["agent"], l["task"], demote(_log_body(l))))
    return merge_chunks(chunks)


# ---------- 多页结构：工作计划（总览）→ 看板 / 任务卡（→ 每张任务详情）/ 工作日志（→ 每篇日志） ----------
HUB_TITLE = "工作计划"
MAX_LOG_PAGES = 60
LOG_PAGE_CHARS = 20000
IMG_MD = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$", re.M)


def card_page(t, now):
    """单张任务卡的详情页：属性表 + 正文（去掉图片行，图片另外作为图片块上传）。"""
    meta = table(["属性", "内容"], [["状态", STATUS.get(t["status"], t["status"])], ["Lark 进度", t["progress"] or "—"],
                                    ["估时", t["est_hours"] and "%sh" % t["est_hours"] or "—"], ["阶段", PHASE.get(t["phase"], t["phase"] or "—")],
                                    ["页面", t["page"] or "—"], ["依赖", t["blocked_by"] or "—"], ["验收项", "%d/%d" % (t["done"], t["total"])]])
    body = IMG_MD.sub("", t["body"]).strip()
    body = re.sub(r"^#\s+.*\n+", "", body, count=1)
    return ["> 本页由“监工”自动生成，请勿手改。最近更新：%s\n\n%s" % (now.strftime("%Y-%m-%d %H:%M"), meta)] + \
        merge_chunks([demote(body, by=1)]) + (["## 参考图\n\n共 %d 张，见下方。" % len(t["images"])] if t.get("images") else [])


def _plan_table(p):
    if not p["items"]:
        return "（没有项目）"
    return table(["时段", "任务", "估时", "子任务", "备注"], [
        [i["slot"], ("[%s] " % i["ref"] if i["ref"] else "") + i["title"], "%gh" % float(i["est_hours"]), i["subs"],
         ("顺延" + ("：" + i["reason"] if i["reason"] else "") if i["carried"] else "") + ("；你补充的需求" if i["req"] else "")]
        for i in p["items"]]) + "\n\n合计 %gh ｜ 来源：%s%s" % (p["total_hours"], p["generated_by"], (" ｜ 未排入：" + "、".join(p["deferred"])) if p["deferred"] else "")


def _chip(p, min_hours):
    return ("草案" if p["draft"] else "已定稿") + ("，**需求不足**" if p["total_hours"] < min_hours else "")


def overview_page(data, now):
    """对应本地看板的“概览”页签：指标、提醒、今日／明日计划、任务卡、最近日志、旷工。"""
    today, tmr = data["today"], next_day(data["today"])
    pg = data["progress"].get(today)
    cp = next((p for p in data["plans"] if p["date"] == today), None)
    tp = next((p for p in data["plans"] if p["date"] == tmr), None)
    absent = [l for l in data["logs"] if l.get("absent")]
    today_logs = len([l for l in data["logs"] if l["date"] == today])
    out = ["> 本页由“监工”自动生成，请勿手改。最近更新：%s" % now.strftime("%Y-%m-%d %H:%M"),
           table(["今日主任务（Lark 勾选）", "明日计划工时（下限 %gh）" % data["min_hours"], "今日日志", "旷工记录"],
                 [["%d / %d" % (pg["main_done"], pg["main_total"]) if pg else "—", "%gh" % tp["total_hours"] if tp else "—", today_logs, len(absent)]])]
    if tp is None:
        out.append("**明日（%s）还没有计划文件。**" % tmr)
    elif tp["total_hours"] < data["min_hours"]:
        out.append("**明日需求不足**：已排 %d 项、约 %gh，低于下限 %gh。请在 Lark 回复需求。" % (len(tp["items"]), tp["total_hours"], data["min_hours"]))
    if data.get("needs"):
        out.append("**你补充的需求**（已并入计划）：" + "、".join("%s（%sh → %s）" % (n["title"], n["est_hours"], n["target"]) for n in data["needs"]))
    cnt = {}
    for t in data["tasks"]:
        cnt[t["status"]] = cnt.get(t["status"], 0) + 1
    out.append("**任务卡**：" + "　".join("%s %d" % (STATUS[k], cnt[k]) for k in STATUS if k in cnt))
    if cp:
        out.append("## 今日计划（%s）\n\n%s" % (_chip(cp, data["min_hours"]), _plan_table(cp)))
    if tp:
        out.append("## 明日计划（%s）\n\n%s" % (_chip(tp, data["min_hours"]), _plan_table(tp)))
    out.append("## 任务卡\n\n" + (table(["编号", "标题", "状态", "Lark 进度", "截止", "验收项"], [
        [t["id"], t["title"], STATUS.get(t["status"], t["status"]), t["progress"] or "", t["due"] or "", "%d / %d" % (t["done"], t["total"])]
        for t in data["tasks"] if t["status"] != "cancelled"]) if data["tasks"] else "还没有任务卡"))
    recent = data["logs"][:5]
    out.append("## 最近日志\n\n" + ("\n".join("- %s %s · %s · %s" % (l["date"], l["time"], l["agent"], l["task"]) for l in recent) if recent else "还没有日志"))
    if absent:
        out.append("## 旷工记录\n\n" + "\n".join("- %s %s" % (l["date"], l["task"]) for l in absent))
    return merge_chunks(out)


def build_pages(data, now=None):
    """页面结构与本地 docs/dashboard.html 的五个页签一致：概览／进度／计划／任务卡／日志。
    返回有序页面列表 [{key, parent, title, chunks, images}]，父页面一定排在子页面之前。"""
    now = now or dt.datetime.now()
    pages = []
    pages.append({"key": "hub", "parent": None, "title": HUB_TITLE, "images": [], "chunks": [
        "> 本页由“监工”自动生成，请勿手改。最近更新：%s\n\n与本地 docs/dashboard.html 同样的五个页签，对应下面五个子页面：\n\n- 概览：指标、提醒、今日与明日计划、任务卡、最近日志、旷工\n- 进度：每天的任务完成情况与你写的原因\n- 计划：全部日期的时段计划\n- 任务卡：按状态分组，每张卡一页（含参考图）\n- 工作日志：每篇日志一页" % now.strftime("%Y-%m-%d %H:%M")]})
    pages.append({"key": "overview", "parent": "hub", "title": "概览", "chunks": overview_page(data, now), "images": []})
    days = sorted(data["progress"], reverse=True)
    pages.append({"key": "progress", "parent": "hub", "title": "进度", "images": [],
                  "chunks": merge_chunks([_progress(data, days=len(days))]) if days else ["还没有进度同步。"]})
    pg = [p for p in data["plans"]]
    pages.append({"key": "plans", "parent": "hub", "title": "计划", "images": [], "chunks": merge_chunks(
        ["### %s%s（%s）\n\n%s" % (p["date"], "（今日）" if p["date"] == data["today"] else "", _chip(p, data["min_hours"]), _plan_table(p)) for p in pg]) or ["还没有计划。"]})
    by = {}
    for t in data["tasks"]:
        by.setdefault(t["status"], []).append(t)
    groups = ["### %s（%d）\n\n%s" % (STATUS[k], len(by[k]), table(["编号", "页面", "标题", "Lark 进度", "估时"], [
        [t["id"], t["page"], t["title"], t["progress"] or "", t["est_hours"] and "%sh" % t["est_hours"] or ""] for t in by[k]])) for k in STATUS if k in by and k != "cancelled"]
    pages.append({"key": "cards", "parent": "hub", "title": "任务卡", "chunks": merge_chunks(groups) or ["还没有任务卡。"], "images": []})
    for t in data["tasks"]:
        if t["status"] == "cancelled":
            continue
        pages.append({"key": "card:" + t["id"], "parent": "cards", "title": "%s %s" % (t["id"], t["title"]),
                      "chunks": card_page(t, now), "images": list(t.get("images") or [])})
    logs = data["logs"][:MAX_LOG_PAGES]
    idx = table(["时间", "来源", "类型", "旷工"], [["%s %s" % (l["date"], l["time"]), l["agent"], l["task"], "是" if l.get("absent") else ""] for l in logs])
    pages.append({"key": "logs", "parent": "hub", "title": "工作日志", "images": [],
                  "chunks": ["> 最近 %d 篇，新的在前；每篇日志是本页的一个子页面。\n\n%s" % (len(logs), idx)]})
    for l in logs:
        pages.append({"key": "log:" + l["file"], "parent": "logs", "title": "%s %s · %s · %s" % (l["date"], l["time"], l["agent"], l["task"]),
                      "chunks": merge_chunks([demote(_log_body(l, LOG_PAGE_CHARS), by=1)]) or ["（空）"], "images": []})
    return pages


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


RATE_CODES = (99991400, 99991663, 429, 1770024, 1061045)


def lcall(L, method, path, body=None, token=None, tries=5):
    """带限流重试的 Lark 调用：遇到频率限制就退避重试。"""
    for i in range(tries):
        r = L.call(method, path, body, token)
        if r.get("code") not in RATE_CODES and r.get("code") != -1:
            return r
        time.sleep(1.5 * (i + 1))
    return r


def _check(r, what):
    if r.get("code") != 0:
        if r.get("code") == 131006:
            raise WikiError("%s失败：机器人对该页面没有编辑权限（%s）。请把应用添加为该页面或知识库的“可编辑”成员，见 docs/setup-macos.md「知识库同步」" % (what, r.get("msg")))
        raise WikiError("%s失败：code=%s %s" % (what, r.get("code"), r.get("msg")))
    return r.get("data") or {}


def _state_path():
    return os.path.join(SUP, "wiki.json")


def load_state(space_id):
    """.supervisor/wiki.json：{"space_id", "pages": {key: {"node","obj","hash"}}}。兼容只有单页的旧格式。"""
    st = _load(_state_path(), {})
    if "pages" in st and "board" in st["pages"]:       # 旧版“看板”页并入新的“计划”页
        st["pages"]["plans"] = st["pages"].pop("board")
        st["pages"]["plans"]["hash"] = ""
    if "pages" not in st:
        pages = {}
        if st.get("obj_token"):
            pages["board"] = {"node": st.get("node_token"), "obj": st["obj_token"], "hash": ""}
        st = {"space_id": space_id, "pages": pages}
    return st


def save_state(st):
    os.makedirs(SUP, exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _state_path())


def ensure_node(L, token, st, spec, parent_node):
    """页面不存在就新建；返回 state 里的记录。"""
    rec = st["pages"].get(spec["key"])
    if rec and rec.get("obj"):
        return rec
    body = {"obj_type": "docx", "node_type": "origin", "title": spec["title"]}
    if parent_node:                       # 没有父节点就建在知识库根目录
        body["parent_node_token"] = parent_node
    d = _check(L.call("POST", "/open-apis/wiki/v2/spaces/%s/nodes" % st["space_id"], body, token),
               "新建知识库页面「%s」" % spec["title"])
    n = d["node"]
    rec = {"node": n["node_token"], "obj": n["obj_token"], "hash": "", "title": spec["title"]}
    st["pages"][spec["key"]] = rec
    save_state(st)
    return rec


def move_node(L, token, st, rec, new_parent):
    _check(L.call("POST", "/open-apis/wiki/v2/spaces/%s/nodes/%s/move" % (st["space_id"], rec["node"]),
                  {"target_parent_token": new_parent}, token), "移动页面")


def rename_node(L, token, st, rec, title):
    _check(L.call("POST", "/open-apis/wiki/v2/spaces/%s/nodes/%s/update_title" % (st["space_id"], rec["node"]), {"title": title}, token), "改页面标题")
    rec["title"] = title


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


def page_hash(spec):
    import hashlib
    chunks = [re.sub(r"最近更新：\d{4}-\d{2}-\d{2} \d{2}:\d{2}", "最近更新：-", c) for c in spec["chunks"]]  # 时间戳不算内容变化
    h = hashlib.sha1(json.dumps([spec["title"], chunks], ensure_ascii=False).encode())
    for p in spec["images"]:
        ap = os.path.join(ROOT, p)
        try:
            h.update(("%s:%d" % (p, os.path.getsize(ap))).encode())
        except OSError:
            h.update(("%s:missing" % p).encode())
    return h.hexdigest()


def upload_image(L, token, doc, block_id, path):
    """给图片块上传图片素材并绑定。知识库文档必须带 drive_route_token，否则报 1061044。"""
    import mimetypes
    import urllib.error
    import urllib.request
    import uuid
    data = open(path, "rb").read()
    name = os.path.basename(path)
    b = "----workboss" + uuid.uuid4().hex
    fields = {"file_name": name, "parent_type": "docx_image", "parent_node": block_id, "size": str(len(data)),
              "extra": json.dumps({"drive_route_token": doc})}
    head = "".join('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v) for k, v in fields.items())
    head += '--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: %s\r\n\r\n' % (
        b, "image" + os.path.splitext(path)[1].lower(), mimetypes.guess_type(path)[0] or "image/jpeg")
    body = head.encode("utf-8") + data + ("\r\n--%s--\r\n" % b).encode()
    req = urllib.request.Request(L.BASE + "/open-apis/drive/v1/medias/upload_all", data=body, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=" + b)
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            res = json.loads(e.read().decode())
        except Exception:
            res = {"code": e.code, "msg": str(e)}
    except Exception as e:
        res = {"code": -1, "msg": repr(e)}
    ft = (res.get("data") or {}).get("file_token")
    if res.get("code") != 0 or not ft:
        raise WikiError("上传图片 %s 失败：code=%s %s" % (name, res.get("code"), res.get("msg")))
    _check(lcall(L, "PATCH", "/open-apis/docx/v1/documents/%s/blocks/%s?document_revision_id=-1" % (doc, block_id),
                  {"replace_image": {"token": ft}}, token), "绑定图片")


def write_doc(L, token, doc, chunks, images, sleep=0.1):
    """整页重写：清空 → 逐段 Markdown 转块写入 → 末尾追加图片块。"""
    base = "/open-apis/docx/v1/documents/%s" % doc
    d = _check(lcall(L, "GET", base + "/blocks/%s/children?page_size=500" % doc, None, token), "读取页面内容")
    n = len(d.get("items") or [])
    if n:
        _check(lcall(L, "DELETE", base + "/blocks/%s/children/batch_delete" % doc, {"start_index": 0, "end_index": n}, token), "清空页面")
        time.sleep(sleep)
    pos = 0
    for md in chunks:
        c = _check(lcall(L, "POST", "/open-apis/docx/v1/documents/blocks/convert", {"content_type": "markdown", "content": md}, token), "转换 Markdown")
        first, blocks = c.get("first_level_block_ids") or [], c.get("blocks") or []
        if not first:
            continue
        _check(lcall(L, "POST", base + "/blocks/%s/descendant?document_revision_id=-1" % doc,
                      {"children_id": first, "descendants": clean_blocks(blocks), "index": pos}, token), "写入页面")
        pos += len(first)
        time.sleep(sleep)
    miss = []
    for p in images:
        ap = os.path.join(ROOT, p)
        if not os.path.isfile(ap):
            miss.append(p)
            continue
        r = _check(lcall(L, "POST", base + "/blocks/%s/children?document_revision_id=-1" % doc,
                          {"children": [{"block_type": 27, "image": {}}], "index": pos}, token), "新建图片块")
        upload_image(L, token, doc, r["children"][0]["block_id"], ap)
        pos += 1
        time.sleep(sleep)
    return pos, miss


def publish_pages(pages, cfg, only_changed=True, log=print, workers=6):
    """先按顺序建好/移好所有页面节点（父在前），再并行写内容；内容没变的跳过。返回 (写入页数, 跳过页数, 缺失图片列表)。"""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    L = _lark()
    token = L.token()
    w = cfg["wiki"]
    st = load_state(w["space_id"])
    root_parent = w.get("parent_node_token") or None   # 留空＝知识库根目录
    lock = threading.Lock()
    todo, skipped = [], 0
    for spec in pages:
        parent = root_parent if not spec["parent"] else st["pages"][spec["parent"]]["node"]
        existed = spec["key"] in st["pages"]
        rec = ensure_node(L, token, st, spec, parent)
        if existed and rec.get("title") != spec["title"]:
            rename_node(L, token, st, rec, spec["title"])
        h = page_hash(spec)
        if only_changed and rec.get("hash") == h:
            skipped += 1
        else:
            todo.append((spec, rec, h))

    def work(item):
        spec, rec, h = item
        _, miss = write_doc(L, token, rec["obj"], spec["chunks"], spec["images"])
        with lock:
            rec["hash"] = h
            save_state(st)
        return spec["title"], miss

    missing, errors = [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, it): it for it in todo}
        for f in as_completed(futs):
            try:
                title, miss = f.result()
                missing += miss
                log("  已写入：%s" % title)
            except WikiError as e:
                errors.append("%s：%s" % (futs[f][0]["title"], e))
    if errors:
        raise WikiError("%d 页写入失败：%s" % (len(errors), "；".join(errors[:3])))
    return len(todo), skipped, missing


def main():
    ap = argparse.ArgumentParser(description="把工作日志看板同步到 Lark 知识库")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print", action="store_true", dest="show")
    ap.add_argument("--force", action="store_true", help="忽略内容哈希，所有页面都重写")
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
        pages = build_pages(data)
        if a.dry_run:
            for sp in pages:
                print("%-28s ← %-6s %2d 段 %6d 字 %d 图" % (sp["title"][:26], sp["parent"] or "(根)", len(sp["chunks"]), sum(len(c) for c in sp["chunks"]), len(sp["images"])))
            print("共 %d 页，%d 张图" % (len(pages), sum(len(sp["images"]) for sp in pages)))
            return 0
        for i, c in enumerate(chunks):
            print(("\n----- 第 %d 段 -----\n%s" % (i + 1, c)) if a.show else "第 %2d 段：%5d 字，%d 行" % (i + 1, len(c), c.count("\n") + 1))
        print("共 %d 段，%d 字" % (len(chunks), sum(len(c) for c in chunks)))
        return 0
    cfg = _load(os.path.join(ROOT, "config", "lark.json"), {})
    if not cfg.get("wiki"):
        print("config/lark.json 里没有 wiki 配置，跳过", file=sys.stderr)
        return 0
    try:
        wrote, skipped, missing = publish_pages(build_pages(data), cfg, only_changed=not a.force)
    except WikiError as e:
        print(str(e), file=sys.stderr)
        return 2
    print("知识库同步完成：写入 %d 页，未变化跳过 %d 页" % (wrote, skipped))
    if missing:
        print("以下图片本地不存在，已跳过：" + "、".join(missing), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
