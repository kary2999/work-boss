import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import supervisor as sv  # noqa: E402


class FakeClient(object):
    """記錄所有寫入呼叫；讀取結果由測試預先設定。"""

    def __init__(self, completed=None, comments=None, replies=None):
        self.completed = completed or {}
        self._comments = comments or {}
        self._replies = replies or {}
        self.created, self.updated, self.cards = [], [], []
        self.n = 0

    def _id(self, p):
        self.n += 1
        return "%s%d" % (p, self.n)

    def task_create(self, summary, desc, start, due):
        g = self._id("g")
        self.created.append((g, summary, start, due))
        return {"guid": g, "url": "u/" + g}

    def subtask_create(self, parent, summary, due, assign=False):
        g = self._id("s")
        self.created.append((g, summary, None, due))
        return {"guid": g, "url": ""}

    def task_update(self, guid, summary=None, desc=None, start=None, due=None):
        self.updated.append((guid, summary, start, due))
        return {"guid": guid}

    def task_get(self, guid):
        return {"guid": guid, "completed": self.completed.get(guid, False)}

    def comments(self, guid):
        return self._comments.get(guid, [])

    def replies(self, mid):
        return self._replies.get(mid, [])

    def card(self, title, template, md, subtitle=None, footer=None):
        self.cards.append((title, template, md))
        return self._id("om")


class PureTests(unittest.TestCase):
    def test_pack_slots_basic(self):
        out = sv.pack_slots([{"est_hours": 1}] * 4)
        self.assertEqual([o["slot"] for o in out], ["09:00-10:00", "10:00-11:00", "11:00-12:00", "14:00-15:00"])

    def test_pack_slots_rounds_up_to_15_minutes(self):
        out = sv.pack_slots([{"est_hours": 0.75}, {"est_hours": 0.3}])
        self.assertEqual(out[0]["slot"], "09:00-09:45")
        self.assertEqual(out[1]["slot"], "09:45-10:15")  # 0.3h = 18 分鐘 → 30 分鐘

    def test_pack_slots_long_item_not_split(self):
        out = sv.pack_slots([{"est_hours": 7}, {"est_hours": 1}])
        self.assertEqual(out[0]["slot"], "09:00-16:00")
        self.assertEqual(out[1]["slot"], "16:00-17:00")

    def test_pack_slots_overflow_is_none(self):
        out = sv.pack_slots([{"est_hours": 4}, {"est_hours": 4}, {"est_hours": 4}])
        self.assertIsNone(out[2]["slot"])

    def test_parse_summary(self):
        self.assertEqual(sv.parse_summary("[T001] 09:00–10:00 補測缺口"), ("T001", "補測缺口"))
        self.assertEqual(sv.parse_summary("[T001] 待排 做事"), ("T001", "做事"))
        self.assertEqual(sv.parse_summary("沒有前綴"), ("", "沒有前綴"))

    def test_decide_absent(self):
        none = [{"completed": False, "reasons": []}]
        self.assertTrue(sv.decide_absent(none, 0))
        self.assertFalse(sv.decide_absent(none, 1))
        self.assertFalse(sv.decide_absent([{"completed": False, "reasons": ["沒時間"]}], 0))
        self.assertFalse(sv.decide_absent([{"completed": True, "reasons": []}], 0))

    def test_build_next_plan_carries_first_then_backlog_within_cap(self):
        res = [
            {"id": "a", "guid": "g1", "summary": "[T001] 09:00–10:00 甲", "est_hours": 2.0, "completed": False,
             "reasons": ["需求不清"], "subtasks_open": [{"guid": "s1", "summary": "子甲"}]},
            {"id": "b", "guid": "g2", "summary": "[T001] 10:00–11:00 乙", "est_hours": 1.0, "completed": True,
             "reasons": [], "subtasks_open": []},
        ]
        backlog = [
            {"id": "T001", "ref": "T001", "title": "已順延的卡", "est_hours": 1, "subtasks": []},
            {"id": "T002", "ref": "T002", "title": "放得下", "est_hours": 3, "subtasks": ["x"]},
            {"id": "T003", "ref": "T003", "title": "放不下", "est_hours": 5, "subtasks": []},
        ]
        plan = sv.build_next_plan(res, backlog, "2026-10-16")
        titles = [i["title"] for i in plan["items"]]
        self.assertEqual(titles, ["甲", "放得下"])  # 完成項不順延；T001 已在順延中不重複；T003 超出上限
        self.assertTrue(plan["items"][0]["carried"])
        self.assertEqual(plan["items"][0]["guid"], "g1")
        self.assertEqual(plan["items"][0]["carry_reason"], "需求不清")
        self.assertEqual(plan["deferred"], ["T003"])
        self.assertEqual(plan["total_hours"], 5.0)

    def test_carried_items_always_included_even_over_cap(self):
        res = [{"id": str(i), "guid": "g%d" % i, "summary": "[T] 09:00–10:00 項%d" % i, "est_hours": 2.0,
                "completed": False, "reasons": [], "subtasks_open": []} for i in range(5)]
        plan = sv.build_next_plan(res, [{"id": "n", "ref": "N", "title": "新", "est_hours": 1}], "2026-10-16")
        self.assertEqual(len(plan["items"]), 5)
        self.assertEqual(plan["deferred"], ["N"])


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        sv.SUP = os.path.join(self.tmp, ".supervisor")
        sv.WORKLOG = os.path.join(self.tmp, "worklog")
        sv.PLANS = os.path.join(self.tmp, "plans")
        sv.TASKS = os.path.join(self.tmp, "tasks")
        for d in (sv.SUP, sv.WORKLOG, sv.PLANS, sv.TASKS):
            os.makedirs(d)

    def _plan(self, day, items):
        with open(os.path.join(sv.PLANS, day + ".json"), "w", encoding="utf-8") as f:
            json.dump({"date": day, "goal": "測試", "items": items}, f)

    def test_morning_creates_then_is_idempotent(self):
        self._plan("2026-10-16", [
            {"id": "n1", "ref": "T002", "title": "新任務", "slot": "09:00-10:00", "est_hours": 1, "subtasks": ["a", "b"]},
            {"id": "c1", "ref": "T001", "title": "順延任務", "slot": "10:00-11:00", "est_hours": 1, "carried": True,
             "guid": "old1", "subtasks": [{"guid": "oldsub", "title": "舊子任務"}, "新子任務"]},
        ])
        c = FakeClient()
        sv.cmd_morning("2026-10-16", c, dry=False)
        creates = [x for x in c.created if x[1].startswith("[T002]")]
        self.assertEqual(len(creates), 1)
        self.assertEqual(len(c.created), 1 + 2 + 1)  # 新任務+2 子任務 + 順延項補 1 個新子任務
        self.assertEqual([u[0] for u in c.updated], ["old1", "oldsub"])  # 順延項改期，不重建
        self.assertEqual(len(c.cards), 1)
        c2 = FakeClient()
        sv.cmd_morning("2026-10-16", c2, dry=False)  # 第二次不應重複建立
        self.assertEqual(c2.created, [])
        self.assertEqual(c2.updated, [])
        self.assertEqual(c2.cards, [])

    def test_morning_without_plan_sends_hint_only(self):
        c = FakeClient()
        sv.cmd_morning("2026-10-17", c, dry=False)
        self.assertEqual(c.created, [])
        self.assertEqual(len(c.cards), 1)

    def _state(self, day):
        with open(os.path.join(sv.SUP, "tasks-%s.json" % day.replace("-", "")), "w", encoding="utf-8") as f:
            json.dump([{"id": "a", "guid": "g1", "summary": "[T001] 09:00–10:00 甲", "slot": "09:00–10:00",
                        "est_hours": 1, "url": "u1", "subtasks": [{"guid": "s1", "summary": "子"}]}], f)

    def test_chase_urges_then_stops_after_user_comment(self):
        day = "2026-10-15"
        self._state(day)
        c = FakeClient()
        sv.cmd_chase(day, c, dry=False)
        self.assertEqual(c.cards[0][1], "red")
        q = json.load(open(os.path.join(sv.SUP, "20261015.json"), encoding="utf-8"))
        self.assertEqual(q["reminders"], 1)
        self.assertFalse(q["replied"])
        c2 = FakeClient(comments={"g1": [{"creator_type": "user", "content": "沒看懂需求"}]})
        sv.cmd_chase(day, c2, dry=False)
        self.assertEqual(c2.cards[0][1], "green")
        q = json.load(open(os.path.join(sv.SUP, "20261015.json"), encoding="utf-8"))
        self.assertTrue(q["replied"])

    def test_chase_ignores_app_comments_and_stops_when_done(self):
        day = "2026-10-15"
        self._state(day)
        c = FakeClient(comments={"g1": [{"creator_type": "app", "content": "機器人的評論"}]})
        sv.cmd_chase(day, c, dry=False)
        self.assertEqual(c.cards[0][1], "red")  # app 評論不算使用者說明
        c2 = FakeClient(completed={"g1": True, "s1": True})
        sv.cmd_chase(day, c2, dry=False)
        self.assertEqual(c2.cards[0][1], "green")

    def test_close_marks_absent_and_writes_next_plan(self):
        day = "2026-10-15"
        self._state(day)
        c = FakeClient()
        sv.cmd_close(day, c, dry=False)
        names = os.listdir(sv.WORKLOG)
        self.assertIn("20261015-2400-supervisor-absent.md", names)
        self.assertIn("20261015-2400-supervisor-rollover.md", names)
        plan = json.load(open(os.path.join(sv.PLANS, "2026-10-16.json"), encoding="utf-8"))
        self.assertEqual(plan["items"][0]["guid"], "g1")
        self.assertTrue(plan["items"][0]["carried"])
        sv.cmd_close(day, FakeClient(), dry=False)  # 重跑不重複

    def test_close_not_absent_when_user_commented(self):
        day = "2026-10-15"
        self._state(day)
        c = FakeClient(comments={"g1": [{"creator_type": "user", "content": "在做別的"}]})
        sv.cmd_close(day, c, dry=False)
        self.assertNotIn("20261015-2400-supervisor-absent.md", os.listdir(sv.WORKLOG))

    def test_close_does_not_overwrite_existing_plan(self):
        day = "2026-10-15"
        self._state(day)
        self._plan("2026-10-16", [{"id": "mine", "title": "別的 agent 寫的", "slot": "09:00-10:00"}])
        sv.cmd_close(day, FakeClient(), dry=False)
        plan = json.load(open(os.path.join(sv.PLANS, "2026-10-16.json"), encoding="utf-8"))
        self.assertEqual(plan["items"][0]["id"], "mine")


if __name__ == "__main__":
    unittest.main()
