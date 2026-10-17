#!/usr/bin/env python3
"""監工群收發工具。只用 REST；密鑰運行時從 config/lark.json 指向的 env 檔讀取，不輸出、不落盤。

用法:
  lark_ops.py send "文字"                 發到監工群，輸出 message_id
  lark_ops.py reply <message_id> "文字"   回覆指定消息（reply_in_thread）
  lark_ops.py chats                       列出機器人所在的群（找 chat_id 用）
  lark_ops.py poll [N]                    讀取群裡最近 N 條（默認 20），輸出 JSON 行
  lark_ops.py replies <message_id>        列出直接回覆指定消息的消息（按 parent_id / root_id 判斷）
  lark_ops.py card --title T [--subtitle S] [--template blue|green|orange|red|grey] --md "markdown"
                   [--footer "小字"] [--button "文字|https://網址"]...   發互動卡片（不產生回調）
  lark_ops.py subtask-create <parent_guid> --summary S [--desc D] [--due "YYYY-MM-DD HH:MM"] [--no-assignee]   建子任務
  lark_ops.py task-update <guid> [--summary S] [--desc D] [--start "…"] [--due "…"]   改標題／描述／起止時間（含子任務）
  lark_ops.py task-comments <guid>...     讀任務評論（使用者在任務裡寫的未完成原因）
  lark_ops.py task-create --summary S [--desc D] [--start "YYYY-MM-DD HH:MM"] [--due "YYYY-MM-DD HH:MM"|YYYY-MM-DD] [--assignee ou_xxx]
                                          建 Lark 任務（默認負責人取 config 的 owner_open_id）
  lark_ops.py task-get <guid>...          查任務是否已完成（輪詢，不需要回調）
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.environ.get("WORK_BOSS_CONFIG", os.path.join(ROOT, "config", "lark.json"))
if not os.path.exists(CFG_PATH):
    sys.exit("找不到配置檔 %s；請複製 config/lark.example.json 為 config/lark.json 並填寫" % CFG_PATH)
CFG = json.load(open(CFG_PATH, encoding="utf-8"))


def load_env(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^(?:export\s+)?([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip().strip("'\"")
    return out


ENV = load_env(CFG["env_file"])
DOMAIN = ENV.get(CFG["domain_key"], "").lower()
BASE = "https://open.larksuite.com" if "lark" in DOMAIN and "feishu" not in DOMAIN else "https://open.feishu.cn"


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"code": e.code, "msg": str(e)}
    except Exception as e:
        return {"code": -1, "msg": repr(e)}


def token():
    r = call("POST", "/open-apis/auth/v3/tenant_access_token/internal",
             {"app_id": ENV[CFG["app_id_key"]], "app_secret": ENV[CFG["app_secret_key"]]})
    if r.get("code") != 0:
        sys.exit("token 失敗: code=%s msg=%s" % (r.get("code"), r.get("msg")))
    return r["tenant_access_token"]


def text_of(msg):
    try:
        c = json.loads(msg.get("body", {}).get("content", "{}"))
        return c.get("text", "") if msg.get("msg_type") == "text" else "<%s>" % msg.get("msg_type")
    except Exception:
        return ""


def fetch(t, n):
    r = call("GET", "/open-apis/im/v1/messages?container_id_type=chat&container_id=%s&sort_type=ByCreateTimeDesc&page_size=%d"
             % (CFG["chat_id"], min(max(n, 1), 50)), token=t)
    if r.get("code") != 0:
        sys.exit("讀取失敗: code=%s msg=%s" % (r.get("code"), r.get("msg")))
    return (r.get("data") or {}).get("items") or []


def brief(m):
    s = m.get("sender", {})
    return {"message_id": m.get("message_id"), "create_time": m.get("create_time"),
            "sender_type": s.get("sender_type"), "sender_id": s.get("id"),
            "parent_id": m.get("parent_id"), "root_id": m.get("root_id"),
            "msg_type": m.get("msg_type"), "text": text_of(m)}


def build_card(title, template, md, footer=None, buttons=None, subtitle=None):
    """卡片 JSON 2.0，只含展示元素與 open_url 按鈕（不產生回調，不需要事件訂閱）。"""
    header = {"title": {"tag": "plain_text", "content": title}, "template": template}
    if subtitle:
        header["subtitle"] = {"tag": "plain_text", "content": subtitle}
    els = [{"tag": "markdown", "content": md}]
    for label, url in buttons or []:
        els.append({"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": "primary",
                    "behaviors": [{"type": "open_url", "default_url": url}]})
    if footer:
        els.append({"tag": "hr"})
        els.append({"tag": "markdown", "content": "<font color='grey'>%s</font>" % footer})
    return {"schema": "2.0", "config": {"wide_screen_mode": True}, "header": header, "body": {"elements": els}}


def opt(args, name, default=None):
    """取 --name 後面的值；可重複的選項用 optall。"""
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def optall(args, name):
    return [args[i + 1] for i, x in enumerate(args) if x == name and i + 1 < len(args)]


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    t = token()
    if a[0] == "card":
        def unesc(s):
            return s.replace("\\n", "\n") if s else s

        card = build_card(opt(a, "--title", "監工"), opt(a, "--template", "orange"), unesc(opt(a, "--md", "")),
                          unesc(opt(a, "--footer")), [tuple(b.split("|", 1)) for b in optall(a, "--button") if "|" in b],
                          opt(a, "--subtitle"))
        r = call("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id",
                 {"receive_id": CFG["chat_id"], "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)}, t)
        if r.get("code") != 0:
            sys.exit("發卡片失敗: code=%s msg=%s" % (r.get("code"), r.get("msg")))
        print(json.dumps({"message_id": r["data"]["message_id"]}))
        return
    if a[0] == "task-create":
        body = {"summary": opt(a, "--summary", ""), "description": opt(a, "--desc", "")}
        due = opt(a, "--due")
        start = opt(a, "--start")
        if due or start:
            import datetime

            def stamp(s):
                if len(s) > 10:
                    return {"timestamp": str(int(datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp() * 1000)),
                            "is_all_day": False}
                return {"timestamp": str(int(datetime.datetime.strptime(s, "%Y-%m-%d").timestamp() * 1000)),
                        "is_all_day": True}
            if due:
                body["due"] = stamp(due)
            if start:
                body["start"] = stamp(start)
        who = opt(a, "--assignee", CFG.get("owner_open_id"))
        if who:
            body["members"] = [{"id": who, "type": "user", "role": "assignee"}]
        r = call("POST", "/open-apis/task/v2/tasks?user_id_type=open_id", body, t)
        if r.get("code") != 0:
            sys.exit("建任務失敗: code=%s msg=%s" % (r.get("code"), r.get("msg")))
        tk = r["data"]["task"]
        print(json.dumps({"guid": tk.get("guid"), "url": tk.get("url")}, ensure_ascii=False))
        return
    if a[0] == "chats":
        r = call("GET", "/open-apis/im/v1/chats?page_size=50", token=t)
        if r.get("code") != 0:
            sys.exit("列群失敗: code=%s msg=%s" % (r.get("code"), r.get("msg")))
        for c in (r.get("data") or {}).get("items") or []:
            print(json.dumps({"name": c.get("name"), "chat_id": c.get("chat_id")}, ensure_ascii=False))
        return
    if a[0] == "task-update" and len(a) >= 2:
        import datetime
        task, fields = {}, []
        for flag, key in (("--summary", "summary"), ("--desc", "description")):
            v = opt(a, flag)
            if v is not None:
                task[key] = v.replace("\\n", "\n")
                fields.append(key)
        for flag, key in (("--start", "start"), ("--due", "due")):
            v = opt(a, flag)
            if v:
                fmt = "%Y-%m-%d %H:%M" if len(v) > 10 else "%Y-%m-%d"
                task[key] = {"timestamp": str(int(datetime.datetime.strptime(v, fmt).timestamp() * 1000)),
                             "is_all_day": len(v) <= 10}
                fields.append(key)
        if not fields:
            sys.exit("task-update 需要 --summary／--desc／--start／--due 其中之一")
        r = call("PATCH", "/open-apis/task/v2/tasks/%s?user_id_type=open_id" % a[1],
                 {"task": task, "update_fields": fields}, t)
        if r.get("code") != 0:
            sys.exit("改任務失敗: code=%s msg=%s" % (r.get("code"), r.get("msg")))
        print(json.dumps({"guid": a[1], "updated": fields}))
        return
    if a[0] == "subtask-create" and len(a) >= 3:
        import datetime
        body = {"summary": opt(a, "--summary", ""), "description": opt(a, "--desc", "")}
        due = opt(a, "--due")
        if due:
            fmt = "%Y-%m-%d %H:%M" if len(due) > 10 else "%Y-%m-%d"
            body["due"] = {"timestamp": str(int(datetime.datetime.strptime(due, fmt).timestamp() * 1000)),
                           "is_all_day": len(due) <= 10}
        who = opt(a, "--assignee", CFG.get("owner_open_id"))
        if who and "--no-assignee" not in a:
            body["members"] = [{"id": who, "type": "user", "role": "assignee"}]
        r = call("POST", "/open-apis/task/v2/tasks/%s/subtasks?user_id_type=open_id" % a[1], body, t)
        if r.get("code") != 0:
            sys.exit("建子任務失敗: code=%s msg=%s" % (r.get("code"), r.get("msg")))
        sub = r["data"]["subtask"]
        print(json.dumps({"guid": sub.get("guid"), "url": sub.get("url")}, ensure_ascii=False))
        return
    if a[0] == "task-comments" and len(a) >= 2:
        from concurrent.futures import ThreadPoolExecutor

        def one_comments(g):
            r = call("GET", "/open-apis/task/v2/comments?resource_type=task&resource_id=%s&page_size=50&user_id_type=open_id" % g, token=t)
            if r.get("code") != 0:
                return [{"guid": g, "error": "code=%s msg=%s" % (r.get("code"), r.get("msg"))}]
            rows = []
            for c in (r.get("data") or {}).get("items") or []:
                cr = c.get("creator") or {}
                rows.append({"guid": g, "comment_id": c.get("id"), "created_at": c.get("created_at"),
                             "creator_type": cr.get("type"), "creator_id": cr.get("id"), "content": c.get("content")})
            return rows
        with ThreadPoolExecutor(max_workers=8) as ex:
            for rows in ex.map(one_comments, a[1:]):
                for row in rows:
                    print(json.dumps(row, ensure_ascii=False))
        return
    if a[0] == "task-get" and len(a) >= 2:
        from concurrent.futures import ThreadPoolExecutor

        def one_get(g):
            r = call("GET", "/open-apis/task/v2/tasks/%s?user_id_type=open_id" % g, token=t)
            if r.get("code") != 0:
                return {"guid": g, "error": "code=%s msg=%s" % (r.get("code"), r.get("msg"))}
            tk = r["data"]["task"]
            done = tk.get("completed_at") not in (None, "", "0", 0)
            return {"guid": g, "summary": tk.get("summary"), "completed": done, "completed_at": tk.get("completed_at")}
        with ThreadPoolExecutor(max_workers=8) as ex:  # 並行讀取，保持輸出順序與輸入一致
            for row in ex.map(one_get, a[1:]):
                print(json.dumps(row, ensure_ascii=False))
        return
    if a[0] == "send" and len(a) >= 2:
        r = call("POST", "/open-apis/im/v1/messages?receive_id_type=chat_id",
                 {"receive_id": CFG["chat_id"], "msg_type": "text", "content": json.dumps({"text": a[1]}, ensure_ascii=False)}, t)
        if r.get("code") != 0:
            sys.exit("發送失敗: code=%s msg=%s" % (r.get("code"), r.get("msg")))
        print(json.dumps({"message_id": r["data"]["message_id"]}))
    elif a[0] == "reply" and len(a) >= 3:
        r = call("POST", "/open-apis/im/v1/messages/%s/reply" % a[1],
                 {"msg_type": "text", "content": json.dumps({"text": a[2]}, ensure_ascii=False), "reply_in_thread": True}, t)
        if r.get("code") != 0:
            sys.exit("回覆失敗: code=%s msg=%s" % (r.get("code"), r.get("msg")))
        print(json.dumps({"message_id": r["data"]["message_id"]}))
    elif a[0] == "poll":
        for m in reversed(fetch(t, int(a[1]) if len(a) > 1 else 20)):
            print(json.dumps(brief(m), ensure_ascii=False))
    elif a[0] == "replies" and len(a) >= 2:
        for m in reversed(fetch(t, 50)):
            if a[1] in (m.get("parent_id"), m.get("root_id")) and m.get("message_id") != a[1]:
                print(json.dumps(brief(m), ensure_ascii=False))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
