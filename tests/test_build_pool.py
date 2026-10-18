import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import build_pool as bp  # noqa: E402


def item(i, deps=(), **kw):
    d = {"id": i, "title": "t" + i, "side": "BE", "pri": "P0", "deps": list(deps)}
    d.update(kw)
    return d


class ValidateTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(bp.validate({"items": [item("R01"), item("R02", ["R01"])]}), [])

    def test_empty_and_duplicate(self):
        self.assertTrue(bp.validate({"items": []}))
        self.assertTrue(any("重复" in e for e in bp.validate({"items": [item("R01"), item("R01")]})))

    def test_missing_dep_and_self_dep(self):
        errs = bp.validate({"items": [item("R01", ["R09"]), item("R02", ["R02"])]})
        self.assertTrue(any("R09 不存在" in e for e in errs))
        self.assertTrue(any("依赖了自己" in e for e in errs))

    def test_cycle(self):
        errs = bp.validate({"items": [item("R01", ["R02"]), item("R02", ["R03"]), item("R03", ["R01"])]})
        self.assertTrue(any("成环" in e for e in errs))

    def test_bad_enums(self):
        errs = bp.validate({"items": [item("R01", side="X"), item("R02", pri="P9"), item("R03", status="zzz")]})
        self.assertEqual(len(errs), 3)


    def test_est_range(self):
        self.assertEqual(bp.validate({"items": [item("T1", est=1.5)]}), [])
        self.assertTrue(bp.validate({"items": [item("T1", est=0)]}))
        self.assertTrue(bp.validate({"items": [item("T1", est="2")]}))


class BuildTests(unittest.TestCase):
    def test_embeds_and_escapes_script_end(self):
        html = bp.build({"items": [item("R01", what="</script><b>")]}, "var P=/*__POOL__*/null;")
        self.assertNotIn("</script>", html)
        self.assertIn("R01", html)


if __name__ == "__main__":
    unittest.main()
