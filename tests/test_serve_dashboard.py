import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import serve_dashboard as sd  # noqa: E402


class AllowListTests(unittest.TestCase):
    def test_allows_dashboard_and_reference_images(self):
        self.assertTrue(sd.allowed("/docs/dashboard.html"))
        self.assertTrue(sd.allowed("/%E5%8F%82%E8%80%83%E5%9B%BE%E7%BB%84/20261017/%E5%90%88%E7%BA%A6.jpg"))
        self.assertTrue(sd.allowed("/docs/dashboard.html?x=1"))

    def test_blocks_private_paths(self):
        for p in ("/config/lark.json", "/.supervisor/progress.json", "/tasks/S01-trade-skeleton.md",
                  "/worklog/a.md", "/plans/2026-10-18.json", "/PROJECT.md", "/scripts/supervisor.py", "/"):
            self.assertFalse(sd.allowed(p), p)

    def test_blocks_path_traversal_even_when_encoded(self):
        for p in ("/docs/../config/lark.json", "/docs/%2e%2e/config/lark.json",
                  "/%E5%8F%82%E8%80%83%E5%9B%BE%E7%BB%84/../config/lark.json"):
            self.assertFalse(sd.allowed(p), p)


if __name__ == "__main__":
    unittest.main()
