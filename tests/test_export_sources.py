"""Where the toolkit reads a character's files: OSRS Toolkit Exporter first, Character Export only as a fallback.
Temporary folders only; no game, no network.

    python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "toolkit"))

import data  # noqa: E402
import mcp_server  # noqa: E402
import paths  # noqa: E402


class Sources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ours, self.theirs = root / "plugin-data" / "position-exporter", root / "character-exporter"
        self.saved = (paths.EXPORTER_ROOT, paths.RUNELITE_ROOT, data.EXPORTER_ROOT, data.RUNELITE_ROOT)
        paths.EXPORTER_ROOT = data.EXPORTER_ROOT = self.ours
        paths.RUNELITE_ROOT = data.RUNELITE_ROOT = self.theirs

    def tearDown(self):
        paths.EXPORTER_ROOT, paths.RUNELITE_ROOT, data.EXPORTER_ROOT, data.RUNELITE_ROOT = self.saved
        self.tmp.cleanup()

    def write(self, root, char, name, content, age_sec=0):
        f = root / char / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(content), encoding="utf-8")
        t = time.time() - age_sec
        os.utime(f, (t, t))
        return f

    def test_ours_first_theirs_only_when_ours_is_missing_or_much_older(self):
        folder = self.theirs / "Gorsok"
        ours = self.write(self.ours, "Gorsok", "bank.json", {"items": []}, age_sec=600)
        theirs = self.write(self.theirs, "Gorsok", "bank.json", {"items": []}, age_sec=0)
        self.assertEqual(paths.export_path(folder, "bank.json"), ours)       # theirs newer by 10 min: still ours
        os.utime(ours, (time.time() - 7200, time.time() - 7200))
        self.assertEqual(paths.export_path(folder, "bank.json"), theirs)     # ours 2 h older (switched off?)
        ours.unlink()
        self.assertEqual(paths.export_path(folder, "bank.json"), theirs)
        theirs.unlink()
        self.assertEqual(paths.export_path(folder, "bank.json"), self.ours / "Gorsok" / "bank.json")

    def test_the_toolkits_own_files_stay_in_its_folder(self):
        folder = self.theirs / "Gorsok"
        self.assertEqual(paths.export_path(folder, "market.json"), folder / "market.json")

    def test_a_character_known_only_from_our_exporter_is_listed_and_gets_a_folder(self):
        self.write(self.ours, "Gorsok", "character.json", {"stats": {}})
        self.write(self.ours, "Gorsok", "quests.json", {"quests": []})
        (self.ours / "game_quests.json").write_text("{}", encoding="utf-8")   # game data, not a character
        self.assertEqual(data.list_characters(), ["Gorsok"])
        name, folder = data.resolve_character("gorsok")
        self.assertEqual(name, "Gorsok")
        self.assertTrue(folder.is_dir())                                       # the toolkit can write market.json
        self.assertEqual(folder, self.theirs / "Gorsok")
        d, _ = data.read_export(folder, "character.json")
        self.assertEqual(d, {"stats": {}})

    def test_both_sources_list_each_character_once_most_recent_first(self):
        self.write(self.ours, "Alt", "position.json", {}, age_sec=50)
        self.write(self.theirs, "Alt", "bank.json", {}, age_sec=500)
        self.write(self.theirs, "Main", "bank.json", {}, age_sec=10)
        self.assertEqual(data.list_characters(), ["Main", "Alt"])


class CharacterSummary(unittest.TestCase):
    """get_character with our files: diaries without task counts, combat achievements without tiers."""
    def setUp(self):
        self._char, self._read = mcp_server._char, mcp_server.read_export
        self.files = {
            "character.json": {"world": 496, "world_types": ["MEMBERS"],
                               "stats": {"Attack": {"real_level": 54, "boosted_level": 54, "experience": 1}}},
            "diaries.json": {"diaries": {"Ardougne": {"easy": {"complete": True, "value": 1},
                                                      "medium": {"complete": False, "value": 0}}}},
            "combat_achievements.json": {"summary": {"total_tasks_completed": 6, "points": 8}},
        }
        mcp_server._char = lambda args: ("Test", "folder")
        mcp_server.read_export = lambda folder, fn: (self.files.get(fn), 1) if fn in self.files else (None, None)

    def tearDown(self):
        mcp_server._char, mcp_server.read_export = self._char, self._read

    def test_summary(self):
        out = mcp_server.t_get_character({})
        self.assertEqual(out["diaries"], {"Ardougne": {"easy": "complete", "medium": "not complete"}})
        self.assertEqual(out["combat_achievements"], {"total_tasks_completed": 6, "points": 8})

    def test_character_export_detail_still_comes_with_its_caution(self):
        self.files["combat_achievements.json"] = {"summary": {"total_tasks_completed": 6},
                                                  "tiers": {"easy": {"tasks_completed": 3, "tasks_total": 41}}}
        self.files["diaries.json"] = {"diaries": {"Desert": {"easy": {"complete": False, "tasks_done": 2}}}}
        out = mcp_server.t_get_character({})
        self.assertEqual(out["combat_achievements"]["tiers"], {"easy": "3/41"})
        self.assertIn("can be wrong", out["combat_achievements"]["caution"])
        self.assertEqual(out["diaries"], {"Desert": {"easy": "2 tasks done"}})


if __name__ == "__main__":
    unittest.main()
