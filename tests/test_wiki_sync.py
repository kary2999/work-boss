import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import wiki_sync as ws  # noqa: E402


def sample():
    return {
        "today": "2026-10-17", "min_hours": 4.0,
        "tasks": [
            {"id": "S01", "title": "交易页骨架", "status": "todo", "progress": "", "est_hours": "1.5", "phase": "P-1", "page": "A07"},
            {"id": "S11", "title": "含 | 竖线的标题", "status": "doing", "progress": "1/2", "est_hours": "2.5", "phase": "P-2", "page": "A17"},
            {"id": "T001", "title": "旧卡", "status": "doing", "progress": "6/7", "est_hours": "", "phase": "", "page": ""},
        ],
        "logs": [
            {"file": "a.md", "date": "2026-10-17", "time": "20:30", "agent": "supervisor", "task": "progress", "absent": False,
             "body": "# 标题\n\n## 小节\n内容\n"},
            {"file": "b.md", "date": "2026-10-16", "time": "24:00", "agent": "supervisor", "task": "absent", "absent": True,
             "body": "# 旷工\n" + "很长" * 5000},
        ],
        "plans": [{"date": "2026-10-18", "draft": True, "total_hours": 2.0, "deferred": [], "generated_by": "supervisor",
                   "items": [{"ref": "S01", "title": "交易页骨架", "slot": "09:00-10:30", "est_hours": 1.5, "carried": True,
                              "reason": "没看懂\n需求", "req": False, "subs": 3}]}],
        "progress": {"2026-10-17": {"main_done": 0, "main_total": 1, "sub_done": 0, "sub_total": 0, "reminders": 0, "absent": False,
                                    "items": [{"completed": False, "slot": "09:00–09:45", "ref": "T001", "title": "M01", "subs_done": 0,
                                               "subs_total": 0, "reasons": ["没看懂需求"]}]}},
        "asks": {},
    }


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 17, 20, 0)
        self.text = "\n\n".join(ws.render_chunks(sample(), now=self.now))

    def test_banner_and_overview(self):
        self.assertIn("请勿手改", self.text)
        self.assertIn("2026-10-17 20:00", self.text)
        self.assertIn("## 概览", self.text)
        self.assertIn("明日计划（2026-10-18）：2h，**需求不足**", self.text)  # 2h < 4h
        self.assertIn("旷工记录：1 次", self.text)

    def test_table_cells_are_escaped_and_single_line(self):
        self.assertIn("含 \\| 竖线的标题", self.text)  # 竖线必须转义，否则会撑坏表格
        self.assertIn("顺延：没看懂 需求", self.text)    # 换行变空格

    def test_tasks_grouped_by_phase_with_status_labels(self):
        self.assertIn("### P-1 下单核心（1 张）", self.text)
        self.assertIn("### P-2 持仓与记录（1 张）", self.text)
        self.assertIn("### 其他（1 张）", self.text)
        self.assertIn("| S01 | A07 | 交易页骨架 | 待办 |", self.text)
        self.assertIn("| 进行中 | 1/2 | 2.5h |", self.text)

    def test_logs_newest_first_with_demoted_headings_and_truncation(self):
        self.assertLess(self.text.index("2026-10-17 20:30"), self.text.index("2026-10-16 24:00"))
        self.assertIn("#### 小节", self.text)  # ## 降 3 级
        self.assertNotIn("\n# 标题", self.text)
        self.assertIn("以下省略", self.text)    # 超长日志被截断

    def test_max_logs_is_respected(self):
        text = "\n".join(ws.render_chunks(sample(), now=self.now, max_logs=1))
        self.assertIn("2026-10-17 20:30", text)
        self.assertNotIn("2026-10-16 24:00", text)

    def test_chunks_are_merged_but_bounded(self):
        chunks = ws.render_chunks(sample(), now=self.now)
        self.assertLess(len(chunks), 10)
        self.assertTrue(all(len(c) < 12000 for c in chunks))


class MergeTests(unittest.TestCase):
    def test_merge_respects_limit_and_order(self):
        out = ws.merge_chunks(["a" * 10, "b" * 10, "c" * 30], limit=25)
        self.assertEqual(out, ["a" * 10 + "\n\n" + "b" * 10, "c" * 30])
        self.assertEqual(ws.merge_chunks([]), [])

    def test_demote_caps_at_9(self):
        self.assertEqual(ws.demote("# a"), "#### a")
        self.assertEqual(ws.demote("###### x", by=5), "######### x")


class CleanBlocksTests(unittest.TestCase):
    def test_strips_parent_id_and_merge_info(self):
        src = [{"block_id": "a", "parent_id": "", "block_type": 2},
               {"block_id": "t", "parent_id": "", "block_type": 31,
                "table": {"property": {"row_size": 1, "merge_info": [{}]}}}]
        out = ws.clean_blocks(src)
        self.assertNotIn("parent_id", out[0])
        self.assertNotIn("merge_info", out[1]["table"]["property"])
        self.assertEqual(out[1]["table"]["property"]["row_size"], 1)
        self.assertIn("merge_info", src[1]["table"]["property"])  # 不改原数据


class PagesTests(unittest.TestCase):
    def setUp(self):
        d = sample()
        d["needs"] = []
        for x in d["tasks"]:
            x.update({"due": "", "owner": "", "done": 0, "total": 0})
        d["tasks"][0].update({"images": ["参考图组/a.jpg", "参考图组/b.jpg"], "body": "## 目标\n画图\n\n![x](a.jpg)\n- [ ] 验收", "blocked_by": "", "done": 0, "total": 1})
        d["tasks"][1].update({"images": [], "body": "正文", "blocked_by": "[S01]", "done": 0, "total": 0})
        d["tasks"][2].update({"images": [], "body": "旧", "blocked_by": "", "done": 0, "total": 0})
        self.pages = ws.build_pages(d, now=dt.datetime(2026, 10, 17, 20, 0))

    def test_tree_order_parents_first(self):
        seen = set()
        for p in self.pages:
            self.assertTrue(p["parent"] is None or p["parent"] in seen, p["key"])
            seen.add(p["key"])
        self.assertEqual(self.pages[0]["title"], "工作计划")

    def test_card_pages_carry_images_and_drop_image_lines(self):
        c = next(p for p in self.pages if p["key"] == "card:S01")
        self.assertEqual(c["images"], ["参考图组/a.jpg", "参考图组/b.jpg"])
        self.assertNotIn("![", "\n".join(c["chunks"]))
        self.assertIn("画图", "\n".join(c["chunks"]))

    def test_log_pages(self):
        logs = [p for p in self.pages if p["parent"] == "logs"]
        self.assertEqual(len(logs), 2)

    def test_hash_ignores_timestamp_but_not_content(self):
        a = dict(self.pages[1])
        b = dict(a, chunks=[c.replace("20:00", "21:30") for c in a["chunks"]])
        c = dict(a, chunks=a["chunks"] + ["新增"])
        self.assertEqual(ws.page_hash(a), ws.page_hash(b))
        self.assertNotEqual(ws.page_hash(a), ws.page_hash(c))


if __name__ == "__main__":
    unittest.main()
