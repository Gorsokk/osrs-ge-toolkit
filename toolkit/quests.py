"""Quest facts from the game's own quest table, crossed with the player's levels and quest states.

Source: game_quests.json, written by the RuneLite plugin "OSRS Toolkit Exporter" (option "Export game quest
data"): the game client's database table Quest, read on the player's own computer. Nothing here comes from a
website. The player's side comes from the player's export files (OSRS Toolkit Exporter, or Character Export as a
fallback): character.json (levels) and quests.json (states, and the game's own quest points in our file).

Rules kept here:
- Say "unknown" rather than guess: an NPC name the game cache calls "null" stays unknown, a required quest that
  is a placeholder row stays unknown, the raw combat value is not interpreted.
- Only the requirements that the table lists are checked. Items, boostable levels and the combat level are not in
  the export: every answer says so (see LIMITS).
- No network, no writes: pure functions over two dicts, so it is testable without the game.
"""
import json
import re
from pathlib import Path

# The game's stat ids (the order of the skills in the game). 23 is not named by the client the file was written
# with (the export says "skill#23"); its quests are the Sailing quests (Prying Times, Current Affairs, The Red Reef),
# and Character Export lists "Sailing" as the 24th skill.
SKILLS = ["Attack", "Defence", "Strength", "Hitpoints", "Ranged", "Prayer", "Magic", "Cooking", "Woodcutting",
          "Fletching", "Fishing", "Firemaking", "Crafting", "Smithing", "Mining", "Herblore", "Agility", "Thieving",
          "Slayer", "Farming", "Runecraft", "Hunter", "Construction", "Sailing"]

# The game stores difficulty and length as codes. The labels follow the order of the codes and were checked
# against well-known quests (Cook's Assistant 0/0, Dragon Slayer I 2/2, Desert Treasure I 3/3,
# Dragon Slayer II 4/4, Recipe for Disaster 5/4): they are our reading, the codes are the game's.
DIFFICULTY = ["Novice", "Intermediate", "Experienced", "Master", "Grandmaster", "Special"]
LENGTH = ["Very short", "Short", "Medium", "Long", "Very long"]

LIMITS = ("Only the requirements listed in the game's quest table are checked: levels (compared with real, "
          "unboosted levels; the export does not say which can be boosted), required quests and quest points. "
          "Items, the combat level and other conditions are not in the export: check them before starting.")

FINISHED, IN_PROGRESS, NOT_STARTED = "FINISHED", "IN_PROGRESS", "NOT_STARTED"


class QuestDataError(Exception):
    """The quest file is missing, unreadable or of an unknown format."""


def norm(name):
    """Name used to join the game table with the player's quest states: case, apostrophes and spaces ignored."""
    s = str(name or "").lower().replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", s).strip()


def _has_letter(s):
    return any(c.isalpha() for c in str(s or ""))


def _known_name(s):
    """A real name, or None: the game cache calls some NPC variants "null", and placeholder rows are named "."."""
    s = None if s is None else str(s).strip()
    if not s or s.lower() == "null" or not _has_letter(s):
        return None
    return s


def skill_name(skill_id, given=None):
    if given and not str(given).startswith("skill#"):
        return given
    try:
        return SKILLS[int(skill_id)]
    except (TypeError, ValueError, IndexError):
        return given or f"skill#{skill_id}"


def label(table, code):
    try:
        return table[int(code)] if int(code) >= 0 else None
    except (TypeError, ValueError, IndexError):
        return None


def map_link(x, y, plane=0):
    return f"https://explv.github.io/?centreX={x}&centreY={y}&centreZ={plane or 0}&zoom=10"


class QuestTable:
    """The game's quest table, cleaned: placeholder rows dropped, unknown names kept unknown."""

    def __init__(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("quests"), list):
            raise QuestDataError("game_quests.json has no quest list.")
        if raw.get("format_version") not in (1,):
            raise QuestDataError(f"game_quests.json has an unknown format_version ({raw.get('format_version')}): "
                                 "update OSRS Toolkit.")
        self.exported_at = raw.get("exported_at")
        self.client_revision = raw.get("client_revision")
        rows = {}
        for q in raw["quests"]:
            if isinstance(q, dict) and q.get("row_id") is not None:
                rows[q["row_id"]] = q
        self.quests = []
        self.by_row, self.by_name = {}, {}
        for q in raw["quests"]:
            if not isinstance(q, dict) or not _known_name(q.get("name")):
                continue                                       # placeholder rows ("."), from older exports too
            c = self._clean(q, rows)
            self.quests.append(c)
            self.by_row[c["row_id"]] = c
            self.by_name.setdefault(norm(c["name"]), c)

    @staticmethod
    def _levels(items):
        return [{"skill": skill_name(x.get("skill_id"), x.get("skill")), "level": x.get("level")}
                for x in items or [] if isinstance(x, dict) and x.get("level") is not None]

    def _clean(self, q, rows):
        req = []
        for i, row in enumerate(q.get("required_quest_rows") or []):
            target = rows.get(row)
            name = _known_name(target.get("name")) if target else None
            if name is None and not target:                    # no row: fall back on the name the export resolved
                given = (q.get("required_quests") or [None] * (i + 1))
                name = _known_name(given[i]) if i < len(given) else None
            req.append({"row_id": row, "name": name})
        start = q.get("start") or {}
        npcs = [_known_name(n) for n in start.get("npc_names") or []]
        return {
            "row_id": q.get("row_id"), "name": _known_name(q.get("name")), "members": bool(q.get("members")),
            "difficulty_code": q.get("difficulty_code"), "length_code": q.get("length_code"),
            "quest_points": q.get("quest_points") or 0,
            "series": q.get("series"), "series_number": q.get("series_number"),
            "levels": self._levels(q.get("levels")),
            "recommended_levels": self._levels(q.get("recommended_levels")),
            "required_quests": req,
            "required_quest_points": q.get("required_quest_points") or 0,
            "xp_rewards": [{"skill": skill_name(x.get("skill_id"), x.get("skill")), "xp": x.get("xp")}
                           for x in q.get("xp_rewards") or [] if isinstance(x, dict)],
            "start": {"x": start.get("x"), "y": start.get("y"), "plane": start.get("plane", 0),
                      "npc_ids": list(start.get("npc_ids") or []), "npc_names": npcs},
            "reason": q.get("recommendation_reason") or None,
        }

    # ----------------------------------------------------------------- lookup ---
    def find(self, query):
        """(quest, matched_by). Raises LookupError listing the closest names when unclear."""
        q = norm(query)
        if not q:
            raise LookupError("Give a quest name.")
        if q in self.by_name:
            return self.by_name[q], "exact"
        for prefix in ("the ",):                                # "restless ghost" -> "The Restless Ghost"
            if prefix + q in self.by_name:
                return self.by_name[prefix + q], "exact"
        if q.endswith(" quest") and q[:-6] in self.by_name:
            return self.by_name[q[:-6]], "exact"
        roman = re.sub(r"\b([1-5])$", lambda m: ["i", "ii", "iii", "iv", "v"][int(m.group(1)) - 1], q)
        if roman != q:                                          # "dragon slayer 2" -> "Dragon Slayer II"
            return self.find(roman)
        hits = [x for n, x in self.by_name.items() if q in n]
        if not hits:                                            # every word of the query, in any order
            words = q.split()
            hits = [x for n, x in self.by_name.items() if all(w in n for w in words)]
        if len(hits) == 1:
            return hits[0], "partial"
        if hits:
            names = sorted(x["name"] for x in hits)
            raise LookupError(f"Several quests match '{query}': {', '.join(names[:12])}"
                              + (" ..." if len(names) > 12 else "") + ". Pick one or ask the user.")
        raise LookupError(f"No quest named '{query}' in the game's quest table ({len(self.quests)} quests).")


class Player:
    """The player's levels and quest states (character.json, quests.json). Either part may be missing (None)."""

    def __init__(self, character=None, quests=None):
        self.levels = None
        if isinstance(character, dict) and isinstance(character.get("stats"), dict):
            self.levels = {k: (v or {}).get("real_level") for k, v in character["stats"].items()}
        self.states = None
        self.game_quest_points = None
        if isinstance(quests, dict) and isinstance(quests.get("quest_points"), int):
            self.game_quest_points = quests["quest_points"]     # the game's own count (OSRS Toolkit Exporter)
        if isinstance(quests, dict) and isinstance(quests.get("quests"), list):
            self.states = {norm(x.get("name")): x.get("state") for x in quests["quests"] if isinstance(x, dict)}

    def state(self, quest_name):
        if self.states is None:
            return None
        return self.states.get(norm(quest_name))

    def level(self, skill):
        if self.levels is None:
            return None
        return self.levels.get(skill)

    def quest_points(self, table):
        """Quest points: the game's own count when the export has it, else computed from the finished quests and the
        game's quest points per quest."""
        if self.game_quest_points is not None:
            return self.game_quest_points
        if self.states is None:
            return None
        return sum(q["quest_points"] for q in table.quests if self.states.get(norm(q["name"])) == FINISHED)


def missing(quest, player, table, qp=None):
    """What the player lacks for the requirements the table lists. None parts = cannot tell (no player data)."""
    out = {"levels": [], "quests": [], "quest_points": None, "unknown": []}
    for r in quest["levels"]:
        have = player.level(r["skill"])
        if have is None:
            out["unknown"].append(f"{r['skill']} level (no level data)")
        elif have < r["level"]:
            out["levels"].append({"skill": r["skill"], "need": r["level"], "have": have})
    for r in quest["required_quests"]:
        if r["name"] is None:
            out["unknown"].append(f"a required quest the game does not name (row {r['row_id']})")
            continue
        st = player.state(r["name"])
        if st is None:
            out["unknown"].append(f"state of {r['name']}")
        elif st != FINISHED:
            out["quests"].append({"name": r["name"], "state": st})
    need = quest["required_quest_points"]
    if need:
        have = qp if qp is not None else player.quest_points(table)
        if have is None:
            out["unknown"].append("quest points (no quest data)")
        elif have < need:
            out["quest_points"] = {"need": need, "have": have}
    return out


def is_met(m):
    return not m["levels"] and not m["quests"] and not m["quest_points"] and not m["unknown"]


def chain(quest, player, table):
    """Unfinished quests to do before `quest`, prerequisites first (depth-first, each once), and the highest
    level each skill needs along that chain (including `quest`) that the player does not have yet."""
    order, seen = [], set()

    def visit(q, depth):
        if depth > 60:                                          # a loop in the data: stop rather than recurse forever
            return
        for r in q["required_quests"]:
            sub = table.by_row.get(r["row_id"])
            if not sub or sub["row_id"] in seen:
                continue
            if player.state(sub["name"]) == FINISHED:
                continue
            seen.add(sub["row_id"])
            visit(sub, depth + 1)
            order.append(sub)

    visit(quest, 0)
    need = {}
    for q in order + [quest]:
        for r in q["levels"]:
            if r["level"] > need.get(r["skill"], 0):
                need[r["skill"]] = r["level"]
    levels = []
    for skill, lv in need.items():
        have = player.level(skill)
        if have is None or have < lv:
            levels.append({"skill": skill, "need": lv, "have": have})
    levels.sort(key=lambda r: SKILLS.index(r["skill"]) if r["skill"] in SKILLS else 99)
    return order, levels


def describe(quest):
    """The quest's facts, as the tools return them."""
    s = quest["start"]
    start = None
    if s.get("x") is not None and s.get("y") is not None:
        names = [n for n in s["npc_names"] if n]
        start = {"x": s["x"], "y": s["y"], "plane": s.get("plane", 0), "map": map_link(s["x"], s["y"], s.get("plane")),
                 "npc": names or None}
        if s["npc_ids"] and len(names) < len(s["npc_ids"]):
            start["npc_note"] = "The game does not name this start NPC: use the coordinates and the map link."
    return {
        "name": quest["name"], "members": quest["members"],
        "difficulty": label(DIFFICULTY, quest["difficulty_code"]), "length": label(LENGTH, quest["length_code"]),
        "quest_points": quest["quest_points"],
        "requirements": {"levels": quest["levels"],
                         "quests": [r["name"] or f"unknown quest (row {r['row_id']})" for r in quest["required_quests"]],
                         "quest_points": quest["required_quest_points"]},
        "recommended_levels": quest["recommended_levels"],
        "xp_rewards": quest["xp_rewards"],
        "start": start,
        "game_reason": quest["reason"],
    }


def load(path):
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as f:
            return QuestTable(json.load(f))
    except FileNotFoundError:
        raise QuestDataError("missing") from None
    except (OSError, ValueError) as e:
        raise QuestDataError(f"game_quests.json could not be read ({e}).") from None
