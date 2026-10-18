#!/usr/bin/env python3
"""把需求池 requirements/pool.json 生成成自包含的平铺页面 docs/pool.html（离线可开，无外部依赖）。

用法：python3 scripts/build_pool.py [--src requirements/pool.json] [--out docs/pool.html]
      python3 scripts/build_pool.py --tasks     开发任务页（requirements/dev_tasks.json → docs/dev-tasks.html）
数据格式见 docs/data-contract.md 的“需求池”一节。需求池与生成的页面都是私有文件，不入库。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIDES = {"FE", "BE", "QA", "PM"}
PRIS = {"P0", "P1", "P2", "—"}
STATUS = {"pool", "doing", "done", "out"}


def validate(pool):
    """返回问题列表（空＝通过）：id 唯一、字段合法、依赖存在且无环。"""
    errs = []
    items = pool.get("items")
    if not isinstance(items, list) or not items:
        return ["items 为空或不是列表"]
    ids = [i.get("id") for i in items]
    for d in {x for x in ids if ids.count(x) > 1}:
        errs.append("id 重复：%s" % d)
    idset = set(ids)
    for i in items:
        r = i.get("id", "?")
        if not i.get("title"):
            errs.append("%s 缺 title" % r)
        if i.get("side") not in SIDES:
            errs.append("%s side 非法：%s" % (r, i.get("side")))
        if i.get("pri") not in PRIS:
            errs.append("%s pri 非法：%s" % (r, i.get("pri")))
        if i.get("status", "pool") not in STATUS:
            errs.append("%s status 非法：%s" % (r, i.get("status")))
        if "est" in i and not (isinstance(i["est"], (int, float)) and 0 < i["est"] <= 10):
            errs.append("%s est 应为 0~10 的人天数：%s" % (r, i.get("est")))
        for d in i.get("deps", []):
            if d not in idset:
                errs.append("%s 的依赖 %s 不存在" % (r, d))
            if d == r:
                errs.append("%s 依赖了自己" % r)
    # 环检测（DFS 三色）
    graph = {i["id"]: [d for d in i.get("deps", []) if d in idset] for i in items if "id" in i}
    color = {}

    def dfs(n, path):
        color[n] = 1
        for m in graph.get(n, []):
            if color.get(m) == 1:
                errs.append("依赖成环：%s" % " → ".join(path + [n, m]))
            elif color.get(m) is None:
                dfs(m, path + [n])
        color[n] = 2

    for n in graph:
        if color.get(n) is None:
            dfs(n, [])
    return errs


def build(pool, template):
    payload = json.dumps(pool, ensure_ascii=False).replace("</", "<\\/")
    return template.replace("/*__POOL__*/null", payload)


def main():
    ap = argparse.ArgumentParser(description="生成需求池平铺页面")
    ap.add_argument("--src", default=os.path.join(ROOT, "requirements", "pool.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "pool.html"))
    ap.add_argument("--tasks", action="store_true", help="改为生成开发任务页：requirements/dev_tasks.json → docs/dev-tasks.html")
    a = ap.parse_args()
    if a.tasks:
        a.src = os.path.join(ROOT, "requirements", "dev_tasks.json")
        a.out = os.path.join(ROOT, "docs", "dev-tasks.html")
    with open(a.src, encoding="utf-8") as f:
        pool = json.load(f)
    errs = validate(pool)
    if errs:
        print("需求池校验未通过：\n  " + "\n  ".join(errs), file=sys.stderr)
        return 1
    with open(os.path.join(ROOT, "scripts", "pool_template.html"), encoding="utf-8") as f:
        tpl = f.read()
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(build(pool, tpl))
    n = len(pool["items"])
    print("已生成 %s（%d 条）" % (a.out, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
