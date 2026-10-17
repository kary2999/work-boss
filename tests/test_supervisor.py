import datetime as dt
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
            {"id": "a", "guid": "g1", "url": "https://t/g1", "summary": "[T001] 09:00–10:00 甲", "est_hours": 2.0,
             "completed": False, "reasons": ["需求不清"], "subtasks_open": [{"guid": "s1", "summary": "子甲"}]},
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
        self.assertEqual(plan["items"][0]["url"], "https://t/g1")
        self.assertEqual(plan["items"][0]["carry_reason"], "需求不清")
        self.assertEqual(plan["deferred"], ["T003"])
        self.assertEqual(plan["total_hours"], 5.0)

    def test_carried_items_always_included_even_over_cap(self):
        res = [{"id": str(i), "guid": "g%d" % i, "summary": "[T] 09:00–10:00 項%d" % i, "est_hours": 2.0,
                "completed": False, "reasons": [], "subtasks_open": []} for i in range(5)]
        plan = sv.build_next_plan(res, [{"id": "n", "ref": "N", "title": "新", "est_hours": 1}], "2026-10-16")
        self.assertEqual(len(plan["items"]), 5)
        self.assertEqual(plan["deferred"], ["N"])


class NewPureTests(unittest.TestCase):
    def test_pack_slots_start_after(self):
        out = sv.pack_slots([{"est_hours": 1}], start_after=hm("10:30"))
        self.assertEqual(out[0]["slot"], "10:30-11:30")
        out = sv.pack_slots([{"est_hours": 1}], start_after=hm("12:00"))
        self.assertEqual(out[0]["slot"], "14:00-15:00")
        out = sv.pack_slots([{"est_hours": 1}], start_after=hm("18:00"))
        self.assertIsNone(out[0]["slot"])

    def test_parse_needs_text(self):
        got = sv.parse_needs_text("@_user_1 1. 畫底部導航 2h\n- 整理圖標\n\n3、校對間距 1.5小時；補規範")
        self.assertEqual(got, [("畫底部導航", 2.0), ("整理圖標", 1.0), ("校對間距", 1.5), ("補規範", 1.0)])
        self.assertEqual(sv.parse_needs_text("   \n  "), [])

    def test_parse_needs_text_caps_estimate_and_count(self):
        self.assertEqual(sv.parse_needs_text("太長的 99h")[0][1], 8.0)
        self.assertEqual(len(sv.parse_needs_text("\n".join("事%d" % i for i in range(30)))), 10)

    def test_set_front_preserves_comment_and_body(self):
        text = "---\nid: T1\nstatus: todo        # todo | doing\nowner:\n---\n\n## 目標\n內容\n"
        out = sv.set_front(text, "status", "doing")
        self.assertIn("status: doing        # todo | doing", out)
        self.assertTrue(out.endswith("\n\n## 目標\n內容\n"))
        out2 = sv.set_front(out, "progress", "3/7")
        self.assertIn("status: doing        # todo | doing\nprogress: 3/7\nowner:", out2)
        self.assertEqual(sv.set_front(out2, "progress", "3/7"), out2)  # 冪等
        self.assertEqual(sv.set_front("沒有 front matter", "status", "x"), "沒有 front matter")

    def test_card_status_only_moves_forward(self):
        self.assertEqual(sv.card_status("todo", 0, 3), "todo")
        self.assertEqual(sv.card_status("todo", 1, 3), "doing")
        self.assertEqual(sv.card_status("doing", 3, 3), "review")
        self.assertEqual(sv.card_status("review", 0, 3), "review")  # 不降級
        self.assertEqual(sv.card_status("done", 1, 3), "done")
        self.assertEqual(sv.card_status("blocked", 3, 3), "blocked")

    def test_ask_allowed_limits_count_and_interval(self):
        now = dt.datetime(2026, 10, 16, 21, 0)
        self.assertTrue(sv.ask_allowed(None, now))
        recent = {"count": 1, "last": (now - dt.timedelta(minutes=30)).isoformat()}
        self.assertFalse(sv.ask_allowed(recent, now))  # 間隔不夠
        old = {"count": 1, "last": (now - dt.timedelta(minutes=120)).isoformat()}
        self.assertTrue(sv.ask_allowed(old, now))
        full = {"count": sv.ASK_MAX, "last": (now - dt.timedelta(hours=5)).isoformat()}
        self.assertFalse(sv.ask_allowed(full, now))
        self.assertTrue(sv.ask_allowed(full, now, final=True))  # 收尾可再要一次
        self.assertFalse(sv.ask_allowed({"count": sv.ASK_MAX + 1}, now, final=True))

    def test_build_next_plan_includes_requested_needs_first_among_fresh(self):
        needs = [{"id": "REQ-1", "title": "使用者要的", "est_hours": 2.0}]
        backlog = [{"id": "T9", "ref": "T9", "title": "卡", "est_hours": 3, "subtasks": []}]
        plan = sv.build_next_plan([], backlog, "2026-10-17", extra=needs)
        self.assertEqual([i["title"] for i in plan["items"]], ["使用者要的", "卡"])
        self.assertTrue(plan["draft"])
        self.assertEqual(plan["generated_by"], "supervisor")


def hm(s):
    return sv.hm_to_min(s)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        sv.SUP = os.path.join(self.tmp, ".supervisor")
        sv.WORKLOG = os.path.join(self.tmp, "worklog")
        sv.PLANS = os.path.join(self.tmp, "plans")
        sv.TASKS = os.path.join(self.tmp, "tasks")
        sv.DASHBOARD = False
        os.environ["WORK_BOSS_MIN_HOURS"] = "0"  # 舊測試不關心需求下限
        os.environ["WORK_BOSS_NO_NOTIFY"] = "1"
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

    # ---------- 新行為：進度同步、次日計畫維護、需求不足 ----------
    def _card(self, name, status="todo", est=None):
        extra = "est_hours: %s\n" % est if est is not None else ""
        with open(os.path.join(sv.TASKS, name + ".md"), "w", encoding="utf-8") as f:
            f.write("---\nid: %s\ntitle: 卡%s\nstatus: %s        # todo | doing\n%sowner:\n---\n\n## 驗收標準\n- [ ] 一\n- [ ] 二\n"
                    % (name, name, status, extra))

    def _next_plan(self, day):
        return json.load(open(os.path.join(sv.PLANS, day + ".json"), encoding="utf-8"))

    def test_sync_writes_progress_and_updates_task_cards(self):
        day = "2026-10-15"
        self._state(day)  # 主任務 g1，ref T001
        self._card("T001")
        c = FakeClient(completed={"g1": False, "s1": True})
        sv.cmd_sync(day, c, dry=False)
        prog = json.load(open(os.path.join(sv.SUP, "progress.json"), encoding="utf-8"))
        self.assertEqual(prog["days"][day]["main_done"], 0)
        self.assertEqual(prog["days"][day]["sub_done"], 1)
        text = open(os.path.join(sv.TASKS, "T001.md"), encoding="utf-8").read()
        self.assertIn("progress: 0/1", text)
        self.assertIn("status: todo", text)  # 還沒有主任務完成
        c2 = FakeClient(completed={"g1": True, "s1": True})
        sv.cmd_sync(day, c2, dry=False)
        text = open(os.path.join(sv.TASKS, "T001.md"), encoding="utf-8").read()
        self.assertIn("progress: 1/1", text)
        self.assertIn("status: review        # todo | doing", text)  # 全部完成 → review，且保留註解
        self.assertIn("## 驗收標準\n- [ ] 一", text)  # 正文不動
        sv.cmd_sync(day, FakeClient(completed={"g1": False}), dry=False)  # 之後狀態倒退也不降級卡片
        self.assertIn("status: review", open(os.path.join(sv.TASKS, "T001.md"), encoding="utf-8").read())

    def test_sync_backfills_history_so_cards_aggregate_across_days(self):
        self._card("T001")
        old = [{"id": "a", "guid": "g1", "summary": "[T001] 09:00–10:00 甲", "slot": "09:00–10:00", "est_hours": 1,
                "url": "", "subtasks": []},
               {"id": "b", "guid": "g2", "summary": "[T001] 10:00–11:00 乙", "slot": "10:00–11:00", "est_hours": 1,
                "url": "", "subtasks": []}]
        with open(os.path.join(sv.SUP, "tasks-20261015.json"), "w", encoding="utf-8") as f:
            json.dump(old, f)
        with open(os.path.join(sv.SUP, "tasks-20261016.json"), "w", encoding="utf-8") as f:  # g2 順延到次日
            json.dump([dict(old[1], slot="09:00–10:00")], f)
        c = FakeClient(completed={"g1": True, "g2": False})
        sv.cmd_sync("2026-10-16", c, dry=False)
        prog = json.load(open(os.path.join(sv.SUP, "progress.json"), encoding="utf-8"))
        self.assertEqual(sorted(prog["days"]), ["2026-10-15", "2026-10-16"])  # 歷史日已回填
        text = open(os.path.join(sv.TASKS, "T001.md"), encoding="utf-8").read()
        self.assertIn("progress: 1/2", text)  # 同一個 guid 跨天只算一次
        self.assertIn("status: doing", text)

    def test_evening_maintains_tomorrow_plan_and_asks_when_insufficient(self):
        os.environ["WORK_BOSS_MIN_HOURS"] = "4"
        day = "2026-10-15"
        self._state(day)
        c = FakeClient()
        sv.cmd_evening(day, c, dry=False)
        plan = self._next_plan("2026-10-16")  # 20:30 就已維護次日計畫（草案），不用等 00:00
        self.assertTrue(plan["draft"])
        self.assertEqual(plan["items"][0]["guid"], "g1")  # 順延項
        titles = [x[0] for x in c.cards]
        self.assertEqual(titles, ["今日進度詢問", "明日需求不足"])
        asks = json.load(open(os.path.join(sv.SUP, "asks.json"), encoding="utf-8"))
        self.assertEqual(asks["2026-10-16"]["count"], 1)
        self.assertEqual(len(asks["2026-10-16"]["mids"]), 1)

    def test_no_second_ask_within_interval(self):
        os.environ["WORK_BOSS_MIN_HOURS"] = "4"
        day = "2026-10-15"
        self._state(day)
        sv.cmd_evening(day, FakeClient(), dry=False)
        c = FakeClient()
        sv.cmd_chase(day, c, dry=False)  # 緊接著的催促：不應再要一次需求
        self.assertNotIn("明日需求不足", [x[0] for x in c.cards])

    def test_no_ask_when_enough_hours(self):
        os.environ["WORK_BOSS_MIN_HOURS"] = "0.5"
        day = "2026-10-15"
        self._state(day)  # 順延項 1h ≥ 0.5h
        c = FakeClient()
        sv.cmd_evening(day, c, dry=False)
        self.assertEqual([x[0] for x in c.cards], ["今日進度詢問"])

    def test_user_reply_to_ask_becomes_plan_items(self):
        os.environ["WORK_BOSS_MIN_HOURS"] = "4"
        day = "2026-10-15"
        self._state(day)
        sv.cmd_evening(day, FakeClient(), dry=False)
        ask_mid = json.load(open(os.path.join(sv.SUP, "asks.json"), encoding="utf-8"))["2026-10-16"]["mids"][0]
        reply = [{"message_id": "om_reply_abcdef", "sender_type": "user", "text": "畫導航欄 2h\n整理圖標 1h"}]
        c = FakeClient(replies={ask_mid: reply})
        sv.cmd_sync(day, c, dry=False)
        needs = json.load(open(os.path.join(sv.SUP, "needs.json"), encoding="utf-8"))
        self.assertEqual([(n["title"], n["est_hours"]) for n in needs["items"]], [("畫導航欄", 2.0), ("整理圖標", 1.0)])
        titles = [i["title"] for i in self._next_plan("2026-10-16")["items"]]
        self.assertIn("畫導航欄", titles)
        self.assertIn("整理圖標", titles)
        c2 = FakeClient(replies={ask_mid: reply})
        sv.cmd_sync(day, c2, dry=False)  # 再同步不重複讀入
        needs2 = json.load(open(os.path.join(sv.SUP, "needs.json"), encoding="utf-8"))
        self.assertEqual(len(needs2["items"]), 2)

    def test_close_finalizes_draft_and_asks_once_more(self):
        os.environ["WORK_BOSS_MIN_HOURS"] = "4"
        day = "2026-10-15"
        self._state(day)
        sv.cmd_evening(day, FakeClient(), dry=False)
        c = FakeClient()
        sv.cmd_close(day, c, dry=False)
        self.assertFalse(self._next_plan("2026-10-16")["draft"])  # 00:00 定稿
        asks = json.load(open(os.path.join(sv.SUP, "asks.json"), encoding="utf-8"))
        self.assertEqual(asks["2026-10-16"]["count"], 2)  # 收尾的最後一次不受間隔限制
        self.assertIn("昨日收尾", [x[0] for x in c.cards])

    def test_morning_builds_plan_from_backlog_when_missing(self):
        day = "2026-10-17"
        self._card("T002", est=3)
        c = FakeClient()
        sv.cmd_morning(day, c, dry=False)
        plan = self._next_plan(day)
        self.assertEqual(plan["items"][0]["ref"], "T002")
        self.assertTrue(any(x[1].startswith("[T002]") for x in c.created))
        self.assertEqual(c.cards[-1][0], "今日工作項")

    def test_morning_with_nothing_to_do_says_so_and_asks(self):
        os.environ["WORK_BOSS_MIN_HOURS"] = "4"
        c = FakeClient()
        sv.cmd_morning("2026-10-17", c, dry=False)
        self.assertEqual(c.created, [])
        titles = [x[0] for x in c.cards]
        self.assertIn("今日無工作計畫", titles)
        self.assertIn("今日需求不足", titles)

    def test_morning_appends_overnight_requirements_to_human_plan(self):
        day = "2026-10-17"
        self._plan(day, [{"id": "h1", "title": "人寫的", "slot": "09:00-10:00", "est_hours": 1}])
        with open(os.path.join(sv.SUP, "asks.json"), "w", encoding="utf-8") as f:
            json.dump({day: {"mids": ["om_ask"], "count": 1}}, f)
        reply = [{"message_id": "om_r_123456", "sender_type": "user", "text": "夜裡想到的 1h"}]
        c = FakeClient(replies={"om_ask": reply})
        sv.cmd_morning(day, c, dry=False)
        plan = self._next_plan(day)
        self.assertEqual([i["id"] for i in plan["items"]][0], "h1")  # 既有項不動
        self.assertEqual(plan["items"][1]["title"], "夜裡想到的")
        self.assertEqual(plan["items"][1]["slot"], "10:00-11:00")  # 排在現有時段之後


if __name__ == "__main__":
    unittest.main()
