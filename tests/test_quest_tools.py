"""The quest tools: the game's quest table (game_quests.json, OSRS Toolkit Exporter) crossed with the player's
levels and quest states (Character Export). No game, no network, no real export: small fixtures with the same
shape as the real files, including the defects older exports have (placeholder rows named ".", start NPCs named
"null", the skill id 23 written "skill#23").

    python -m unittest discover -s tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "toolkit"))

import mcp_server  # noqa: E402
import paths  # noqa: E402
import quests  # noqa: E402


def lv(skill_id, level, name=None):
    return {"skill_id": skill_id, "skill": name or quests.SKILLS[skill_id], "level": level}


def row(row_id, name, *, members=True, diff=0, length=0, qp=1, levels=(), req=(), req_qp=0, xp=(), npcs=(), names=()):
    return {"row_id": row_id, "quest_id": row_id + 100, "name": name, "members": members, "difficulty_code": diff,
            "length_code": length, "quest_points": qp, "series": -1, "series_number": 0,
            "release_date_parts": [1, 1, 2001], "levels": list(levels), "recommended_levels": [],
            "required_quest_rows": list(req), "required_quests": [], "required_quest_points": req_qp,
            "requirement_combat_raw": 3, "prerequisite_direct": 0, "prerequisite_indirect": 0,
            "start": {"x": 3200 + row_id, "y": 3200, "plane": 0, "npc_ids": list(npcs), "npc_names": list(names)},
            "xp_rewards": [{"skill_id": s, "skill": n, "xp": x} for s, n, x in xp],
            "unstarted_state": 0, "end_state": 10, "recommendation_reason": f"Reason {name}."}


GAME = {"exported_at": "2026-10-02T15:48:23Z", "format_version": 1, "client_revision": 241, "quest_count": 9,
        "quests": [
            row(1, "Cook's Assistant", members=False, xp=[(7, "Cooking", 300)], npcs=[4626], names=["Cook"]),
            row(2, "The Restless Ghost", members=False, length=1, xp=[(5, "Prayer", 1125)]),
            row(3, "Priest in Peril", length=1, xp=[(5, "Prayer", 1406)]),
            row(4, "Animal Magnetism", diff=1, length=2, levels=[lv(8, 35), lv(4, 30)], req=[2, 3],
                xp=[(8, "Woodcutting", 25000)], npcs=[4408], names=["null"]),
            row(5, "Dragon Slayer I", diff=2, length=2, qp=2, req_qp=5, xp=[(0, "Attack", 1000), (2, "Strength", 18650)]),
            row(6, "Big Quest", diff=4, length=4, qp=5, levels=[lv(6, 75)], req=[4, 5], xp=[(0, "Attack", 50000)]),
            row(7, "Sailing Quest", levels=[lv(23, 12, "skill#23")], xp=[(23, "skill#23", 8000)]),
            row(8, ".", qp=0),                                  # placeholder row, as in client revision 241
            row(9, "Needs Placeholder", req=[8]),
            row(10, "Dragon Slayer II", diff=4, length=4, qp=5, req=[5]),
        ]}

CHARACTER = {"stats": {s: {"real_level": 1} for s in quests.SKILLS}}
CHARACTER["stats"].update({"Woodcutting": {"real_level": 40}, "Ranged": {"real_level": 10},
                           "Magic": {"real_level": 67}, "Attack": {"real_level": 54}})
QUESTS = {"summary": {}, "quests": [
    {"id": 1, "name": "Cook's Assistant", "state": "FINISHED"},
    {"id": 2, "name": "The Restless Ghost", "state": "FINISHED"},
    {"id": 3, "name": "Priest in Peril", "state": "IN_PROGRESS"},
    {"id": 4, "name": "Animal Magnetism", "state": "NOT_STARTED"},
    {"id": 5, "name": "Dragon Slayer I", "state": "NOT_STARTED"},
    {"id": 6, "name": "Big Quest", "state": "NOT_STARTED"},
    {"id": 7, "name": "Sailing Quest", "state": "NOT_STARTED"},
    {"id": 9, "name": "Needs Placeholder", "state": "NOT_STARTED"},
    {"id": 10, "name": "Dragon Slayer II", "state": "NOT_STARTED"},
]}


class Table(unittest.TestCase):
    def setUp(self):
        self.t = quests.QuestTable(json.loads(json.dumps(GAME)))

    def test_placeholder_rows_are_dropped_and_unknown_names_stay_unknown(self):
        names = [q["name"] for q in self.t.quests]
        self.assertNotIn(".", names)
        self.assertEqual(len(names), 9)
        self.assertEqual(self.t.by_name["needs placeholder"]["required_quests"], [{"row_id": 8, "name": None}])
        am = self.t.by_name["animal magnetism"]
        self.assertEqual(am["start"]["npc_names"], [None])         # the cache's "null" is not a name
        d = quests.describe(am)
        self.assertIsNone(d["start"]["npc"])
        self.assertIn("coordinates", d["start"]["npc_note"])
        self.assertEqual(quests.describe(self.t.by_name["cook's assistant"])["start"]["npc"], ["Cook"])

    def test_skill_23_is_sailing(self):
        sq = self.t.by_name["sailing quest"]
        self.assertEqual(sq["levels"], [{"skill": "Sailing", "level": 12}])
        self.assertEqual(sq["xp_rewards"], [{"skill": "Sailing", "xp": 8000}])

    def test_labels_follow_the_codes_and_unknown_codes_are_none(self):
        self.assertEqual(quests.label(quests.DIFFICULTY, 4), "Grandmaster")
        self.assertEqual(quests.label(quests.LENGTH, 0), "Very short")
        self.assertIsNone(quests.label(quests.DIFFICULTY, 9))
        self.assertIsNone(quests.label(quests.DIFFICULTY, -1))
        self.assertIsNone(quests.label(quests.LENGTH, None))

    def test_find(self):
        self.assertEqual(self.t.find("COOK'S ASSISTANT")[0]["name"], "Cook's Assistant")
        self.assertEqual(self.t.find("cook’s assistant")[0]["name"], "Cook's Assistant")   # typographic apostrophe
        self.assertEqual(self.t.find("restless ghost"), (self.t.by_name["the restless ghost"], "exact"))
        self.assertEqual(self.t.find("dragon slayer 2")[0]["name"], "Dragon Slayer II")
        self.assertEqual(self.t.find("magnetism")[1], "partial")
        with self.assertRaises(LookupError) as cm:
            self.t.find("dragon slayer")                            # two quests: never pick one silently
        self.assertIn("Dragon Slayer I, Dragon Slayer II", str(cm.exception))
        with self.assertRaises(LookupError):
            self.t.find("nothing like this")
        with self.assertRaises(LookupError):
            self.t.find("  ")

    def test_bad_files(self):
        with self.assertRaises(quests.QuestDataError):
            quests.QuestTable({"format_version": 1})
        with self.assertRaises(quests.QuestDataError):
            quests.QuestTable({"format_version": 2, "quests": []})
        with self.assertRaises(quests.QuestDataError) as cm:
            quests.load(Path(tempfile.gettempdir()) / "no-such-dir-xyz" / "game_quests.json")
        self.assertEqual(str(cm.exception), "missing")


class Comparison(unittest.TestCase):
    def setUp(self):
        self.t = quests.QuestTable(json.loads(json.dumps(GAME)))
        self.p = quests.Player(CHARACTER, QUESTS)

    def test_quest_points_are_counted_from_finished_quests(self):
        self.assertEqual(self.p.quest_points(self.t), 2)            # Cook's Assistant 1 + The Restless Ghost 1

    def test_the_games_own_quest_points_win_when_the_export_has_them(self):
        p = quests.Player(CHARACTER, dict(QUESTS, quest_points=7))
        self.assertEqual(p.quest_points(self.t), 7)

    def test_missing_levels_quests_and_points(self):
        m = quests.missing(self.t.by_name["animal magnetism"], self.p, self.t)
        self.assertEqual(m["levels"], [{"skill": "Ranged", "need": 30, "have": 10}])    # Woodcutting 40 >= 35
        self.assertEqual(m["quests"], [{"name": "Priest in Peril", "state": "IN_PROGRESS"}])
        self.assertFalse(quests.is_met(m))
        m = quests.missing(self.t.by_name["dragon slayer i"], self.p, self.t)
        self.assertEqual(m["quest_points"], {"need": 5, "have": 2})
        self.assertTrue(quests.is_met(quests.missing(self.t.by_name["cook's assistant"], self.p, self.t)))

    def test_without_player_data_nothing_is_claimed(self):
        p = quests.Player(None, None)
        m = quests.missing(self.t.by_name["animal magnetism"], p, self.t)
        self.assertEqual(m["levels"], [])
        self.assertEqual(m["quests"], [])
        self.assertEqual(len(m["unknown"]), 4)                      # 2 levels + 2 quests: unknown, not "met"
        self.assertFalse(quests.is_met(m))
        self.assertIn("unknown", quests.missing(self.t.by_name["needs placeholder"], self.p, self.t))
        self.assertTrue(quests.missing(self.t.by_name["needs placeholder"], self.p, self.t)["unknown"])

    def test_chain_puts_prerequisites_first_skips_finished_ones_and_gathers_levels(self):
        order, levels = quests.chain(self.t.by_name["big quest"], self.p, self.t)
        self.assertEqual([q["name"] for q in order], ["Priest in Peril", "Animal Magnetism", "Dragon Slayer I"])
        self.assertEqual(levels, [{"skill": "Ranged", "need": 30, "have": 10},
                                  {"skill": "Magic", "need": 75, "have": 67}])
        self.assertEqual(quests.chain(self.t.by_name["cook's assistant"], self.p, self.t), ([], []))

    def test_a_loop_in_the_data_does_not_hang(self):
        g = json.loads(json.dumps(GAME))
        g["quests"].append(row(20, "Loop A", req=[21]))
        g["quests"].append(row(21, "Loop B", req=[20]))
        t = quests.QuestTable(g)
        order, _ = quests.chain(t.by_name["loop a"], quests.Player(CHARACTER, QUESTS), t)
        self.assertLessEqual(len(order), 2)                         # it returns, each quest at most once
        self.assertEqual(len({q["name"] for q in order}), len(order))


class Tools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name) / "game_quests.json"
        self.game.write_text(json.dumps(GAME), encoding="utf-8")
        self.saved = (paths.GAME_QUESTS_FILE, mcp_server.resolve_character, mcp_server.read_export)
        paths.GAME_QUESTS_FILE = self.game
        self.files = {"character.json": CHARACTER, "quests.json": QUESTS}
        mcp_server.resolve_character = lambda name=None: ("Test", "folder") if name in (None, "Test") else (None, None)
        mcp_server.read_export = lambda folder, fn: (self.files.get(fn), 5) if fn in self.files else (None, None)

    def tearDown(self):
        paths.GAME_QUESTS_FILE, mcp_server.resolve_character, mcp_server.read_export = self.saved
        self.tmp.cleanup()

    def test_quest_info_with_the_player(self):
        out = mcp_server.t_get_quest_info({"quest": "Animal Magnetism"})
        q = out["quest"]
        self.assertEqual((q["difficulty"], q["length"], q["members"]), ("Intermediate", "Medium", True))
        self.assertEqual(q["requirements"]["quests"], ["The Restless Ghost", "Priest in Peril"])
        self.assertEqual(q["start"]["map"], "https://explv.github.io/?centreX=3204&centreY=3200&centreZ=0&zoom=10")
        self.assertEqual(q["game_reason"], "Reason Animal Magnetism.")
        you = out["you"]
        self.assertEqual(you["state"], "NOT_STARTED")
        self.assertFalse(you["requirements_met"])
        self.assertEqual(you["quests_to_do_first"], ["Priest in Peril"])
        self.assertIn("Items", out["limits"])
        self.assertEqual(out["source"]["client_revision"], 241)

    def test_finished_quest_and_ready_quest(self):
        self.assertEqual(mcp_server.t_get_quest_info({"quest": "cook's assistant"})["you"]["note"], "Already finished.")
        self.files["quests.json"] = {"quests": [dict(x, state="FINISHED") if x["name"] == "Priest in Peril" else x
                                                for x in QUESTS["quests"]]}
        self.files["character.json"] = {"stats": dict(CHARACTER["stats"], Ranged={"real_level": 30})}
        you = mcp_server.t_get_quest_info({"quest": "animal magnetism"})["you"]
        self.assertTrue(you["requirements_met"])
        self.assertEqual(you["missing"], {})
        self.assertNotIn("quests_to_do_first", you)

    def test_quest_info_without_any_character_data_still_gives_the_facts(self):
        self.files = {}
        out = mcp_server.t_get_quest_info({"quest": "Animal Magnetism"})
        self.assertEqual(out["quest"]["requirements"]["levels"][0], {"skill": "Woodcutting", "level": 35})
        self.assertIsNone(out["you"]["requirements_met"])          # cannot tell, never "yes"
        self.assertEqual(len(out["notes"]), 2)

    def test_errors(self):
        with self.assertRaises(mcp_server.ToolError) as cm:
            mcp_server.t_get_quest_info({"quest": "dragon slayer"})
        self.assertIn("Several quests", str(cm.exception))
        with self.assertRaises(mcp_server.ToolError):
            mcp_server.t_get_quest_info({"quest": "Animal Magnetism", "character": "Nobody"})
        self.game.unlink()
        with self.assertRaises(mcp_server.ToolError) as cm:
            mcp_server.t_get_quest_info({"quest": "Animal Magnetism"})
        self.assertIn("Export game quest data", str(cm.exception))
        self.game.write_text("{not json", encoding="utf-8")
        with self.assertRaises(mcp_server.ToolError) as cm:
            mcp_server.t_get_available_quests({})
        self.assertIn("could not be read", str(cm.exception))

    def test_available_quests(self):
        out = mcp_server.t_get_available_quests({})
        ready = [q["name"] for q in out["can_start"]]
        self.assertEqual(ready[0], "Priest in Peril")               # in progress first
        self.assertIn("Sailing Quest", [q["name"] for q in out["one_requirement_missing"]])
        self.assertNotIn("Cook's Assistant", ready)                 # finished
        self.assertNotIn("Needs Placeholder", ready + [q["name"] for q in out["one_requirement_missing"]])
        almost = {q["name"]: q["missing"] for q in out["one_requirement_missing"]}
        self.assertEqual(almost["Dragon Slayer I"], "5 quest points (you have 2)")
        self.assertEqual(almost["Sailing Quest"], "Sailing 12 (you have 1)")
        self.assertNotIn("Big Quest", almost)                       # several things missing
        self.assertEqual(out["quest_points"], 2)

    def test_available_quests_by_xp_skill_and_free_only(self):
        out = mcp_server.t_get_available_quests({"xp_skill": "att"})
        self.assertEqual(out["xp_skill"], "Attack")
        self.assertEqual(out["can_start"], [])
        self.assertEqual([(q["name"], q["xp"]) for q in out["one_requirement_missing"]], [("Dragon Slayer I", 1000)])
        free = mcp_server.t_get_available_quests({"free_only": True})
        self.assertTrue(all(not q["members"] for q in free["can_start"] + free["one_requirement_missing"]))
        with self.assertRaises(mcp_server.ToolError):
            mcp_server.t_get_available_quests({"xp_skill": "Juggling"})

    def test_available_quests_needs_the_player(self):
        self.files = {"character.json": CHARACTER}
        with self.assertRaises(mcp_server.ToolError) as cm:
            mcp_server.t_get_available_quests({})
        self.assertIn("OSRS Toolkit Exporter", str(cm.exception))

    def test_limit(self):
        out = mcp_server.t_get_available_quests({"limit": 1})
        self.assertEqual(len(out["can_start"]), 1)
        self.assertTrue(out["truncated"])
        self.assertEqual(len(mcp_server.t_get_available_quests({"limit": "x"})["can_start"]), out["can_start_count"])


class Declaration(unittest.TestCase):
    def test_declared_read_only_and_wired(self):
        for name, fn in (("get_quest_info", mcp_server.t_get_quest_info),
                         ("get_available_quests", mcp_server.t_get_available_quests)):
            decl = next(t for t in mcp_server.TOOLS if t["name"] == name)
            self.assertTrue(decl["annotations"]["readOnlyHint"])
            self.assertIs(mcp_server.HANDLERS[name], fn)
            json.dumps(decl)
        self.assertEqual(next(t for t in mcp_server.TOOLS if t["name"] == "get_quest_info")["inputSchema"]["required"],
                         ["quest"])


if __name__ == "__main__":
    unittest.main()
