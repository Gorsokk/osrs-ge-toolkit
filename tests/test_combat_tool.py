"""The get_combat_achievements tool: task names and counts from the Character Export file.
No game, no network, no real export: a small fixture with the same shape as the real file.

    python -m unittest discover -s tests
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "toolkit"))

import mcp_server  # noqa: E402

CA = {
    "summary": {"total_tasks_completed": 3, "named_data_available": True},
    "tiers": {
        "easy": {"complete": False, "tasks_completed": 1, "tasks_total": 4, "tasks": [
            {"id": 0, "name": "Noxious Foe", "complete": False},
            {"id": 1, "name": "One by one", "complete": True},
            {"id": 2, "name": "Barrows Novice", "complete": False},
            {"id": 3, "name": "Defence Doesn't Matter", "complete": False}]},
        "medium": {"complete": False, "tasks_completed": 2, "tasks_total": 3, "tasks": [
            {"id": 10, "name": "Medium A", "complete": True},
            {"id": 11, "name": "Medium B", "complete": True},
            {"id": 12, "name": "Medium C", "complete": False}]},
        "hard": {"complete": False, "tasks_completed": 0, "tasks_total": 0, "tasks": []},
    },
}


class CombatAchievementsTool(unittest.TestCase):
    def setUp(self):
        self._char, self._read = mcp_server._char, mcp_server.read_export
        self.data = CA
        mcp_server._char = lambda args: ("Test", "folder")
        mcp_server.read_export = lambda folder, name: (self.data, 5)

    def tearDown(self):
        mcp_server._char, mcp_server.read_export = self._char, self._read

    def call(self, **args):
        return mcp_server.t_get_combat_achievements(args)

    def test_without_a_tier_only_the_counts(self):
        out = self.call()
        self.assertEqual(out["tiers"], {"easy": "1/4", "medium": "2/3", "hard": "0/0"})
        self.assertEqual(out["total_tasks_completed"], 3)
        self.assertNotIn("tasks", out)

    def test_the_tasks_still_to_do_are_the_default(self):
        out = self.call(tier="easy")
        self.assertEqual(out["status"], "todo")
        self.assertEqual([t["name"] for t in out["tasks"]], ["Noxious Foe", "Barrows Novice", "Defence Doesn't Matter"])
        self.assertEqual(out["matching"], 3)
        self.assertTrue(all(not t["complete"] for t in out["tasks"]))

    def test_done_all_search_and_limit(self):
        self.assertEqual([t["name"] for t in self.call(tier="easy", status="done")["tasks"]], ["One by one"])
        self.assertEqual(self.call(tier="easy", status="all")["matching"], 4)
        self.assertEqual([t["name"] for t in self.call(tier="easy", search="barrows")["tasks"]], ["Barrows Novice"])
        limited = self.call(tier="easy", limit=2)
        self.assertEqual(len(limited["tasks"]), 2)
        self.assertEqual(limited["matching"], 3)
        self.assertTrue(limited["truncated"])
        self.assertNotIn("truncated", self.call(tier="easy", limit=3))

    def test_a_bad_limit_falls_back_and_is_bounded(self):
        self.assertEqual(len(self.call(tier="easy", limit="many")["tasks"]), 3)
        self.assertEqual(len(self.call(tier="easy", limit=0)["tasks"]), 3)         # 0 is not a valid limit: the default applies
        self.assertEqual(len(self.call(tier="easy", limit=-5)["tasks"]), 1)        # a negative one is raised to 1
        self.assertEqual(len(self.call(tier="medium", status="all", limit=10 ** 6)["tasks"]), 3)

    def test_tier_is_not_case_sensitive_and_a_wrong_one_is_an_error(self):
        self.assertEqual(self.call(tier="EASY")["tier"], "easy")
        with self.assertRaises(mcp_server.ToolError):
            self.call(tier="legendary")
        with self.assertRaises(mcp_server.ToolError):
            self.call(tier="easy", status="maybe")

    def test_names_not_in_the_export_yet_are_said(self):
        self.data = {"summary": {"total_tasks_completed": 0, "named_data_available": False},
                     "tiers": {"easy": {"tasks_completed": 0, "tasks_total": 41}}}
        out = self.call(tier="easy")
        self.assertEqual(out["tasks"], [])
        self.assertIn("open the Combat Achievements tab", out["note"])

    def test_every_answer_carries_the_caution_about_the_plugin(self):
        # the plugin's list lagged behind the game (checked in game): never present the detail as certain
        for args in ({}, {"tier": "easy"}, {"tier": "medium", "status": "all"}):
            out = self.call(**args)
            self.assertIn("Character Export", out["caution"])
            self.assertIn("can be wrong", out["caution"])
            self.assertIn("reference", out["caution"])

    def test_a_missing_file_is_a_tool_error_with_a_hint(self):
        mcp_server.read_export = lambda folder, name: (None, None)
        with self.assertRaises(mcp_server.ToolError) as cm:
            self.call()
        self.assertIn("OSRS Toolkit Exporter", str(cm.exception))


class OurExport(unittest.TestCase):
    """The file OSRS Toolkit Exporter writes: total and points from the game, no per-tier detail, no caution."""
    def setUp(self):
        self._char, self._read = mcp_server._char, mcp_server.read_export
        self.data = {"format_version": 1, "source": "OSRS Toolkit Exporter, read from the game client (game variables)",
                     "summary": {"total_tasks_completed": 6, "points": 8, "named_data_available": False},
                     "tier_status_raw": {"easy": 0}}
        mcp_server._char = lambda args: ("Test", "folder")
        mcp_server.read_export = lambda folder, name: (self.data, 5)

    def tearDown(self):
        mcp_server._char, mcp_server.read_export = self._char, self._read

    def test_total_and_points_without_detail(self):
        out = mcp_server.t_get_combat_achievements({})
        self.assertEqual((out["total_tasks_completed"], out["points"]), (6, 8))
        self.assertNotIn("caution", out)
        self.assertNotIn("tiers", out)
        self.assertIn("not exported yet", out["note"])

    def test_a_tier_question_gets_no_invented_names(self):
        out = mcp_server.t_get_combat_achievements({"tier": "easy"})
        self.assertEqual(out["tasks"], [])
        self.assertIn("in-game", out["note"])


class Declaration(unittest.TestCase):
    def test_it_is_declared_read_only_and_wired(self):
        decl = next(t for t in mcp_server.TOOLS if t["name"] == "get_combat_achievements")
        self.assertTrue(decl["annotations"]["readOnlyHint"])
        self.assertIs(mcp_server.HANDLERS["get_combat_achievements"], mcp_server.t_get_combat_achievements)
        self.assertEqual(decl["inputSchema"]["properties"]["tier"]["enum"],
                         ["easy", "medium", "hard", "elite", "master", "grandmaster"])
        json.dumps(decl)                                       # the declaration can be sent as it is

    def test_tool_names_stay_unique(self):
        names = [t["name"] for t in mcp_server.TOOLS]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
