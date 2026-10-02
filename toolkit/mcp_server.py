"""
OSRS Toolkit - Claude connector (MCP server over stdio).

Gives Claude read-only tools over the player's RuneLite exports (Character Export +
OSRS Toolkit Exporter plugins) and live OSRS Wiki prices. Claude can read and advise;
it never controls the game.

Launched by the Claude desktop app (see claude_connect.py). Protocol: JSON-RPC 2.0,
one message per line on stdin/stdout. Logs go to stderr only.
"""
import contextlib
import io
import json
import sys
import time
import traceback

import threading

# stdio mode: stdout is the protocol channel (main() sends every stray print to stderr).
# HTTP mode (the remote "bridge" served by the desktop app, see handle_http): replies are collected per thread.
_PROTO_OUT = sys.stdout
_HTTP = threading.local()
if __name__ == "__main__":
    sys.stdout = sys.stderr

import data  # noqa: E402
import money  # noqa: E402
import quests  # noqa: E402
from data import PRICES, read_export, resolve_character, human_age, net_sell, tax  # noqa: E402
import paths  # noqa: E402
from paths import APP_NAME, VERSION, DATA_DIR, RUNELITE_ROOT  # noqa: E402

SUPPORTED_PROTOCOLS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = """OSRS Toolkit gives you read-only access to the user's Old School RuneScape character \
(exported locally by the RuneLite plugin "OSRS Toolkit Exporter") and to live \
Grand Exchange prices from the OSRS Wiki.

- Start with get_status if you are unsure what data exists or how fresh it is; mention stale data \
(e.g. the bank only updates when the user opens it in game).
- Prices: GE tax is 2% on sales of 50 gp or more (capped at 5M, bonds exempt). Always quote margins after tax.
- Coordinates are OSRS world tiles (x, y, plane). Use your OSRS knowledge (and the map link) to \
identify the location; y > 9000 usually means an underground/dungeon area.
- You can advise, plan and explain. Never help automate gameplay (bots, macros, input automation): \
it breaks Jagex's rules and gets accounts banned.
- Quests: get_quest_info and get_available_quests read the game's own quest table (requirements, start \
point, rewards) and compare it with the player's levels and quests. Prefer them to memory; they only check \
levels, quests and quest points (not items): say so when it matters.
- Answer in the user's language.
- Stream co-host (stream_* tools): when the user is live on Twitch, viewers ask questions with !ask. \
Read them with stream_get_chat, answer with stream_say (short: 1-2 sentences, under 400 characters, \
friendly, in the viewer's language, no links you are not sure of). What you send is shown on stream \
and posted in Twitch chat, so never include private info (bank details, the user's real name, files). \
Skip trolling or rule-breaking questions with stream_skip_question. Only switch scenes \
(stream_show_scene) when the user asks you to.
- Voice mode: the user is usually playing while talking to you. Keep spoken answers short, act \
on requests right away (e.g. "answer the chat" = stream_get_chat then stream_say for each question)."""

CHAR_ARG = {"character": {"type": "string",
                          "description": "Character name. Optional: defaults to the most recently played character."}}


def tool(name, description, props=None, required=None, read_only=True):
    schema = {"type": "object", "properties": dict(props or {}), "additionalProperties": False}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema,
            "annotations": {"readOnlyHint": read_only, "openWorldHint": not read_only}}


TOOLS = [
    tool("get_status",
         "What data is available: exported characters, which plugins are working, how fresh each file is, "
         "and what to fix if something is missing. Call this first when unsure.", CHAR_ARG),
    tool("get_character",
         "Stats (levels, XP, combat level, total level), membership, world, quests (in progress / finished), "
         "achievement diaries and combat achievements progress.",
         {**CHAR_ARG, "include_all_quests": {"type": "boolean",
                                             "description": "Also list every not-started quest (long). Default false."}}),
    tool("get_combat_achievements",
         "The player's combat achievements: total tasks done and points (read from the game by OSRS Toolkit "
         "Exporter). Per-tier counts and task names only exist in the older Character Export file, which can be "
         "wrong (see its caution). Use it for 'how many combat achievements', 'which tasks am I missing in easy'.",
         {**CHAR_ARG,
          "tier": {"type": "string", "enum": ["easy", "medium", "hard", "elite", "master", "grandmaster"],
                   "description": "List the tasks of this tier. Without it: only the count per tier."},
          "status": {"type": "string", "enum": ["todo", "done", "all"],
                     "description": "Which tasks to list (with a tier). Default 'todo'."},
          "search": {"type": "string", "description": "Only tasks whose name contains this text."},
          "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Tasks to list. Default 25."}}),
    tool("get_quest_info",
         "One quest from the game's own quest table: members or not, difficulty, length, quest points, required "
         "levels / quests / quest points, recommended levels, XP rewards, start point (tile, map link, start NPC) "
         "and the game's own reason to do it. With the player's data: their state on it, what they still lack, "
         "and the unfinished quests to do first, in order. Use it for 'can I start X', 'what do I need for X', "
         "'where does X start', 'in which order'. Needs the 'OSRS Toolkit Exporter' plugin with "
         "'Export game quest data' on.",
         {**CHAR_ARG, "quest": {"type": "string", "description": "Quest name (case-insensitive; part of the name "
                                "works when only one quest matches)."}},
         ["quest"]),
    tool("get_available_quests",
         "Quests the player has not finished and can start now, judged on the requirements the game's quest table "
         "lists (levels, quests, quest points), plus the ones missing a single requirement. Optionally only quests "
         "that give XP in one skill (sorted by that XP), or only free-to-play quests. Use it for 'which quests can I "
         "do', 'which quest gives Attack XP'.",
         {**CHAR_ARG,
          "xp_skill": {"type": "string", "description": "Only quests rewarding XP in this skill (e.g. 'Attack')."},
          "free_only": {"type": "boolean", "description": "Only free-to-play quests. Default false."},
          "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Quests per list. Default 20."}}),
    tool("get_position",
         "The player's live location in game (world tile x/y/plane, region, world) with a map link. "
         "Useful for 'where am I', quest steps, navigation.", CHAR_ARG),
    tool("get_inventory",
         "Current inventory and worn equipment, with live GE value per item.", CHAR_ARG),
    tool("get_bank",
         "Bank contents valued at live GE prices: total value, coins, platinum tokens, cash_total "
         "(coins + 1,000 gp per platinum token: what the player can spend at the Grand Exchange), most valuable items. "
         "The bank export only refreshes when the player opens their bank in game.",
         {**CHAR_ARG,
          "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                    "description": "How many items to list, most valuable first. Default 40."},
          "search": {"type": "string", "description": "Only items whose name contains this text."}}),
    tool("get_ge_offers",
         "The player's 8 Grand Exchange slots: item, buy/sell, price, filled quantity, average price, "
         "gp locked, compared with the live market, plus the player's cost basis when known.", CHAR_ARG),
    tool("get_market_opportunities",
         "Current flip and High Alchemy opportunities sized to the player's cash (after GE tax, filtered "
         "for liquidity). Uses the toolkit's scan (refreshed every ~5 min) or runs a fresh one.",
         {**CHAR_ARG,
          "kind": {"type": "string", "enum": ["all", "flips", "alch"], "description": "Default 'all'."},
          "limit": {"type": "integer", "minimum": 1, "maximum": 15, "description": "Items per list. Default 8."}}),
    tool("get_item_price",
         "Live Grand Exchange price for any item (by name or id): instant buy/sell, 5-minute / 1-hour / "
         "24-hour averages and volumes, buy limit, margin after tax, High Alchemy value and profit, "
         "optional 7-day or 30-day history.",
         {"item": {"type": "string", "description": "Exact in-game item name (or numeric item id). Unclear names return an error listing the closest items: pick one or ask the user, never assume. In voice mode, if a name sounds misheard, check get_inventory / get_bank for the real name first. The result's matched_by says how the name was matched (exact, plural, prefix, words, fuzzy)."},
          "history_days": {"type": "integer", "enum": [0, 7, 30], "description": "Default 0 (no history)."}},
         ["item"]),
    tool("stream_get_chat",
         "Stream co-host: pending !ask questions from Twitch viewers (id, user, text), recently answered "
         "ones, and the last chat messages. Also says whether the chat bot is connected and can reply.",
         {"limit": {"type": "integer", "minimum": 1, "maximum": 60, "description": "Chat lines to return. Default 30."}}),
    tool("stream_say",
         "Stream co-host: show a message from Claude on the stream overlay and post it in Twitch chat. "
         "Pass question_id to answer a viewer's !ask question (it is then marked answered and the viewer is tagged). "
         "Keep it short (max ~400 characters). Visible to everyone watching.",
         {"text": {"type": "string", "description": "What to say (1-2 sentences)."},
          "question_id": {"type": "integer", "description": "Id of the !ask question being answered (optional)."},
          "to_chat": {"type": "boolean", "description": "Also post in Twitch chat (needs a bot token). Default true."}},
         ["text"], read_only=False),
    tool("stream_skip_question",
         "Stream co-host: drop a pending !ask question without answering (spam, off-topic, rule-breaking).",
         {"question_id": {"type": "integer"}}, ["question_id"], read_only=False),
    tool("stream_show_scene",
         "Stream co-host: switch the live scene in Meld Studio (through the overlay browser source). "
         "Use the exact name of one of the scenes listed by stream_status (e.g. Starting, Game, BRB, Ending). "
         "Only when the user asks.",
         {"scene": {"type": "string", "description": "Meld Studio scene name (case-insensitive)."}},
         ["scene"], read_only=False),
    tool("stream_status",
         "Stream co-host: Twitch chat connection, Meld Studio link (scenes, current scene, live or not), "
         "what the overlay shows (bond progress, top flip, recent alerts, last messages).", {}),
    tool("get_sell_advice",
         "How to price items the player wants to SELL: a ready-made ladder (fast / balanced / patient) from "
         "live prices, with GE tax, net gp per item and profit against the player's cost when known. Always "
         "use this for 'what price should I sell X at'. patient is always >= balanced >= fast.",
         {**CHAR_ARG,
          "item": {"type": "string", "description": "Exact item name or numeric id."},
          "quantity": {"type": "integer", "description": "How many to sell. Default: what the player holds "
                       "(inventory + bank + unsold GE sell offers)."},
          "cost_per_item": {"type": "number", "description": "What the player paid each, if they said so. "
                            "Default: the cost the toolkit tracked from their GE buys."}}),
    tool("get_income",
         "Income per PLAYED hour (logged-in time only): wealth change (bank + inventory + equipment + open "
         "GE offers, at live prices) divided by hours actually played, plus realized GE flip profit. "
         "The toolkit records a snapshot every minute while the game is logged in. Income counts once it "
         "lands in the bank or inventory; read the notes before quoting a number.",
         {**CHAR_ARG,
          "period": {"type": "string", "enum": ["session", "today", "24h", "7d", "30d"],
                     "description": "session = since the last log-in. Default 'today'."}}),
    tool("get_recent_alerts",
         "Alerts the toolkit raised recently (GE offers filled or stuck, flips, price crashes/spikes, "
         "bond price, official news, cheap skilling materials).",
         {**CHAR_ARG,
          "hours": {"type": "number", "minimum": 0.1, "maximum": 168, "description": "Look-back window. Default 24."},
          "category": {"type": "string", "description": "Optional filter: GE, FLIP, MARKET, BOND, NEWS, STOCK, TARGET."}}),
]

PROMPTS = [
    {"name": "ge_checkup", "title": "GE check-up",
     "description": "Review my Grand Exchange offers and what to do with my free slots.",
     "arguments": []},
    {"name": "where_am_i", "title": "Where am I / quest help",
     "description": "Where I am in game and, if I'm on a quest, the exact next step.",
     "arguments": []},
    {"name": "money_plan", "title": "Money plan",
     "description": "A realistic plan to reach a gp goal (e.g. a bond).",
     "arguments": [{"name": "goal", "description": "e.g. 'a bond', '10M', 'a dragon scimitar'", "required": False}]},
    {"name": "what_next", "title": "What should I do next?",
     "description": "Suggestions for this play session based on my account.",
     "arguments": [{"name": "focus", "description": "e.g. 'PvP', 'quests', 'money', 'skilling'", "required": False}]},
]

PROMPT_TEXT = {
    "ge_checkup": "Use the OSRS Toolkit tools (get_ge_offers, get_market_opportunities, get_bank) to review my "
                  "Grand Exchange. For each active offer, say if the price is right and give an exact new price "
                  "if not. Then tell me what to put in my free slots with my current cash (exact item, quantity, "
                  "buy and sell prices, profit after tax), and any risks.",
    "where_am_i": "Use get_position, get_inventory and get_character (quests in progress) to tell me where I am in "
                  "game. If I'm in the middle of a quest, give me the exact next step from here, using my inventory.",
    "money_plan": "Use get_bank, get_ge_offers, get_market_opportunities and get_character to build a realistic "
                  "plan to reach this goal: {goal}. Show how much I have (cash + items I could sell), the gap, "
                  "and the fastest methods for MY stats, with gp/hour estimates.",
    "what_next": "Use get_character, get_position, get_inventory and get_recent_alerts to suggest what I should do "
                 "in this play session. Focus: {focus}. Be concrete (where to go, what to bring, why).",
}


# ------------------------------------------------------------------ helpers ---
class ToolError(Exception):
    pass


def _char(args):
    name, folder = resolve_character(args.get("character"))
    if not folder:
        if args.get("character"):
            raise ToolError(f"No exported character named '{args['character']}'. "
                            f"Available: {', '.join(data.list_characters()) or 'none'}.")
        raise ToolError("No game data found yet. The user needs RuneLite with the 'OSRS Toolkit Exporter' plugin "
                        "(Plugin Hub), then to log in and open their bank once. Call get_status for details.")
    return name, folder


def _need(folder, filename, hint):
    d, age = read_export(folder, filename)
    if d is None:
        raise ToolError(f"{filename} not found. {hint}")
    return d, age


def _gp(n):
    return None if n is None else money.gp(n)


def _combat_level(stats):
    lv = {k: v.get("real_level", 1) for k, v in stats.items()}
    base = 0.25 * (lv.get("Defence", 1) + lv.get("Hitpoints", 10) + lv.get("Prayer", 1) // 2)
    melee = 0.325 * (lv.get("Attack", 1) + lv.get("Strength", 1))
    ranged = 0.325 * (lv.get("Ranged", 1) * 3 // 2)
    magic = 0.325 * (lv.get("Magic", 1) * 3 // 2)
    return int(base + max(melee, ranged, magic))


def _valued(items):
    out, total = [], 0
    for it in items or []:
        unit = PRICES.unit_value(it["id"]) if it.get("id") is not None else None
        value = unit * it.get("quantity", 1) if unit else None
        total += value or 0
        out.append({"name": it.get("name"), "id": it.get("id"), "quantity": it.get("quantity", 1),
                    "unit_price": unit, "value": value})
    return out, total


# -------------------------------------------------------------------- tools ---
def t_get_status(args):
    chars = data.list_characters()
    name, folder = resolve_character(args.get("character"))
    out = {"toolkit": f"{APP_NAME} {VERSION}", "exports_folder": str(RUNELITE_ROOT),
           "characters": chars, "selected_character": name, "files": {}, "problems": []}
    if not folder:
        out["problems"].append("No exports found. Install RuneLite plugin 'OSRS Toolkit Exporter' from the Plugin Hub, "
                               "log in, and open the bank once.")
        return out
    for fn, (what, plugin) in data.EXPORT_FILES.items():
        d, age = read_export(folder, fn)
        out["files"][fn] = {"contains": what, "written_by": plugin,
                            "present": d is not None, "updated": human_age(age) if d is not None else None}
    out["game_data"] = {"game_quests.json": {"contains": "the game's quest table (requirements, start, rewards)",
                                             "written_by": "OSRS Toolkit Exporter (option 'Export game quest data')",
                                             "present": paths.GAME_QUESTS_FILE.is_file()}}
    f = out["files"]
    if not f["position.json"]["present"] or not f["ge_offers.json"]["present"]:
        out["problems"].append("No live position / GE offers: install the 'OSRS Toolkit Exporter' plugin from the "
                               "RuneLite Plugin Hub.")
    if not f["market.json"]["present"]:
        out["problems"].append("No flip/alch scan yet: start OSRS Toolkit (desktop icon). "
                               "get_market_opportunities can also run a scan on demand.")
    _, bank_age = read_export(folder, "bank.json")
    if bank_age and bank_age > 60:
        out["problems"].append(f"Bank data is {human_age(bank_age)}: ask the user to open their bank in game.")
    return out


def t_get_character(args):
    name, folder = _char(args)
    ch, age = _need(folder, "character.json", "Needs the 'OSRS Toolkit Exporter' plugin and a login.")
    stats = ch.get("stats", {})
    skills = {k: {"level": v.get("real_level"), "boosted": v.get("boosted_level"), "xp": v.get("experience")}
              for k, v in stats.items()}
    out = {"character": name, "updated": human_age(age), "world": ch.get("world"),
           "members": "MEMBERS" in (ch.get("world_types") or []),
           "combat_level": _combat_level(stats),
           "total_level": sum(v.get("real_level", 0) for v in stats.values()),
           "total_xp": sum(v.get("experience", 0) for v in stats.values()),
           "skills": skills}
    q, _ = read_export(folder, "quests.json")
    if q:
        quests = q.get("quests", [])
        out["quests"] = {"summary": q.get("summary"),
                         "in_progress": [x["name"] for x in quests if x.get("state") == "IN_PROGRESS"],
                         "finished": [x["name"] for x in quests if x.get("state") == "FINISHED"]}
        if args.get("include_all_quests"):
            out["quests"]["not_started"] = [x["name"] for x in quests if x.get("state") == "NOT_STARTED"]
    d, _ = read_export(folder, "diaries.json")
    if d:
        out["diaries"] = {region: {tier: ("complete" if v.get("complete") else
                                          f"{v['tasks_done']} tasks done" if "tasks_done" in v else "not complete")
                                   for tier, v in tiers.items()}
                          for region, tiers in (d.get("diaries") or {}).items()}
    ca, _ = read_export(folder, "combat_achievements.json")
    if ca:
        summary = ca.get("summary") or {}
        out["combat_achievements"] = {"total_tasks_completed": summary.get("total_tasks_completed")}
        if summary.get("points") is not None:
            out["combat_achievements"]["points"] = summary["points"]
        tiers = ca.get("tiers") or {}
        if tiers:   # per-tier detail: only in Character Export's file, which can be wrong (see get_combat_achievements)
            out["combat_achievements"]["tiers"] = {
                tier: f"{v.get('tasks_completed', 0)}/{v.get('tasks_total', '?')}" + (" (complete)" if v.get("complete") else "")
                for tier, v in tiers.items()}
            out["combat_achievements"]["caution"] = _CA_CAUTION
    return out


# Checked on 2 Oct 2026 against the game's own tab (Character Export 0.6.0): its task list was 18 tasks behind the game,
# every task really done was flagged not done, and only the overall total matched. So say it with every answer.
_CA_CAUTION = ("Per-tier counts and task names come from the Character Export plugin, whose task list can lag behind the "
               "game (new tasks shift it): they can be wrong. total_tasks_completed matched the game when last checked. "
               "The in-game Combat Achievements tab is the reference.")


def t_get_combat_achievements(args):
    name, folder = _char(args)
    ca, age = _need(folder, "combat_achievements.json", "Needs the 'OSRS Toolkit Exporter' RuneLite plugin and a login.")
    tiers = ca.get("tiers") or {}
    summary = ca.get("summary") or {}
    out = {"character": name, "updated": human_age(age),
           "total_tasks_completed": summary.get("total_tasks_completed"),
           "named_data_available": bool(summary.get("named_data_available"))}
    if summary.get("points") is not None:
        out["points"] = summary["points"]
    if not tiers:
        # OSRS Toolkit Exporter: the total and the points come straight from the game; no per-tier detail yet.
        out["note"] = ("Per-tier counts and task names are not exported yet: only the total and the points, read "
                       "from the game. The in-game Combat Achievements tab has the detail.")
        if args.get("tier"):
            out["tier"] = str(args.get("tier")).lower()
            out["tasks"] = []
        return out
    out["caution"] = _CA_CAUTION
    out["tiers"] = {tier: f"{v.get('tasks_completed', 0)}/{v.get('tasks_total', '?')}"
                          + (" (complete)" if v.get("complete") else "") for tier, v in tiers.items()}
    tier = str(args.get("tier") or "").lower()
    if not tier:
        return out
    if tier not in tiers:
        raise ToolError(f"Unknown tier '{tier}'. Tiers: {', '.join(tiers) or 'none'}.")
    if not out["named_data_available"]:
        out["note"] = "The task names are not in the export yet: open the Combat Achievements tab once in game."
    status = str(args.get("status") or "todo").lower()
    if status not in ("todo", "done", "all"):
        raise ToolError("status must be 'todo', 'done' or 'all'.")
    search = str(args.get("search") or "").lower()
    try:
        limit = max(1, min(200, int(args.get("limit") or 25)))
    except (TypeError, ValueError):
        limit = 25
    tasks = [x for x in tiers[tier].get("tasks") or []
             if (status == "all" or (status == "done") == bool(x.get("complete")))
             and search in str(x.get("name") or "").lower()]
    out.update({"tier": tier, "status": status, "matching": len(tasks),
                "tasks": [{"name": x.get("name"), "complete": bool(x.get("complete"))} for x in tasks[:limit]]})
    if len(tasks) > limit:
        out["truncated"] = True
    return out


# ------------------------------------------------------------------- quests ---
_QUEST_HINT = ("The game's quest data is missing: in RuneLite, open the settings of the 'OSRS Toolkit Exporter' "
               "plugin, turn on 'Export game quest data', then log in (the file is written within a few seconds).")


def _quest_table():
    try:
        return quests.load(paths.GAME_QUESTS_FILE)
    except quests.QuestDataError as e:
        raise ToolError(_QUEST_HINT if str(e) == "missing" else str(e)) from None


def _quest_player(args):
    """(name, Player, notes). Works without any character data: the facts are then given without comparison."""
    name, folder = resolve_character(args.get("character"))
    if args.get("character") and not folder:
        raise ToolError(f"No exported character named '{args['character']}'. "
                        f"Available: {', '.join(data.list_characters()) or 'none'}.")
    notes = []
    ch = qs = None
    if folder:
        ch, _ = read_export(folder, "character.json")
        qs, _ = read_export(folder, "quests.json")
    if ch is None:
        notes.append("No level data (character.json): levels not compared.")
    if qs is None:
        notes.append("No quest states (quests.json): quests and quest points not compared.")
    return name, quests.Player(ch, qs), notes


def _source(table):
    return {"file": "game_quests.json (OSRS Toolkit Exporter, read from the game client)",
            "exported_at": table.exported_at, "client_revision": table.client_revision}


def t_get_quest_info(args):
    table = _quest_table()
    try:
        quest, matched_by = table.find(args.get("quest"))
    except LookupError as e:
        raise ToolError(str(e)) from None
    name, player, notes = _quest_player(args)
    out = {"quest": quests.describe(quest), "matched_by": matched_by, "character": name}
    qp = player.quest_points(table)
    state = player.state(quest["name"])
    you = {"state": state, "quest_points": qp}
    if state == quests.FINISHED:
        you["note"] = "Already finished."
    else:
        m = quests.missing(quest, player, table, qp)
        you["requirements_met"] = quests.is_met(m) if not m["unknown"] else None
        you["missing"] = {k: v for k, v in m.items() if v}
        todo, levels = quests.chain(quest, player, table)
        if todo:
            you["quests_to_do_first"] = [q["name"] for q in todo]
            you["levels_needed_for_the_whole_chain"] = levels
    out["you"] = you
    out["limits"] = quests.LIMITS
    if notes:
        out["notes"] = notes
    out["source"] = _source(table)
    return out


def _skill_arg(value):
    if not value:
        return None
    v = str(value).strip().lower()
    for s in quests.SKILLS:
        if s.lower() == v or (len(v) >= 3 and s.lower().startswith(v)):
            return s
    raise ToolError(f"Unknown skill '{value}'. Skills: {', '.join(quests.SKILLS)}.")


def t_get_available_quests(args):
    table = _quest_table()
    name, player, notes = _quest_player(args)
    if player.levels is None or player.states is None:
        raise ToolError("Needs the player's levels and quest states from the 'OSRS Toolkit Exporter' RuneLite plugin "
                        "(log in once). " + " ".join(notes))
    skill = _skill_arg(args.get("xp_skill"))
    try:
        limit = max(1, min(100, int(args.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    qp = player.quest_points(table)
    ready, almost = [], []
    for q in table.quests:
        if player.state(q["name"]) == quests.FINISHED:
            continue
        if args.get("free_only") and q["members"]:
            continue
        xp = sum(r["xp"] or 0 for r in q["xp_rewards"] if r["skill"] == skill) if skill else None
        if skill and not xp:
            continue
        m = quests.missing(q, player, table, qp)
        if m["unknown"]:
            continue
        lacks = ([f"{r['skill']} {r['need']} (you have {r['have']})" for r in m["levels"]]
                 + [f"quest {r['name']}" for r in m["quests"]]
                 + ([f"{m['quest_points']['need']} quest points (you have {m['quest_points']['have']})"]
                    if m["quest_points"] else []))
        item = {"name": q["name"], "members": q["members"],
                "difficulty": quests.label(quests.DIFFICULTY, q["difficulty_code"]),
                "length": quests.label(quests.LENGTH, q["length_code"]),
                "quest_points": q["quest_points"], "state": player.state(q["name"])}
        if skill:
            item["xp"] = xp
        if not lacks:
            ready.append((q, item))
        elif len(lacks) == 1:
            item["missing"] = lacks[0]
            almost.append((q, item))

    def order(pair):
        q, item = pair
        started = 0 if item["state"] == quests.IN_PROGRESS else 1
        if skill:
            return (started, -item["xp"], q["name"])
        return (started, q["difficulty_code"] if q["difficulty_code"] is not None else 9,
                q["length_code"] if q["length_code"] is not None else 9, q["name"])

    ready.sort(key=order)
    almost.sort(key=order)
    out = {"character": name, "quest_points": qp, "xp_skill": skill, "free_only": bool(args.get("free_only")),
           "can_start_count": len(ready), "can_start": [i for _, i in ready[:limit]],
           "one_requirement_missing_count": len(almost),
           "one_requirement_missing": [i for _, i in almost[:limit]],
           "order": ("in progress first, then by " + ("XP in that skill" if skill else "difficulty, then length")),
           "limits": quests.LIMITS, "source": _source(table)}
    if len(ready) > limit or len(almost) > limit:
        out["truncated"] = True
    return out


def t_get_position(args):
    name, folder = _char(args)
    p, age = _need(folder, "position.json", "Needs the 'OSRS Toolkit Exporter' RuneLite plugin.")
    x, y, plane = p.get("x"), p.get("y"), p.get("plane", 0)
    return {"character": name, "updated": human_age(age), "world": p.get("world"),
            "x": x, "y": y, "plane": plane, "region_id": p.get("region_id"),
            "underground": bool(y and y > 9000),
            "map": f"https://explv.github.io/?centreX={x}&centreY={y}&centreZ={plane}&zoom=10",
            "note": "Identify the place from the coordinates/region id with your OSRS knowledge."}


def t_get_inventory(args):
    name, folder = _char(args)
    inv, inv_age = _need(folder, "inventory.json", "Needs the 'OSRS Toolkit Exporter' plugin.")
    items, total = _valued(inv.get("items"))
    out = {"character": name, "inventory_updated": human_age(inv_age),
           "inventory": items, "inventory_value": total, "inventory_value_text": _gp(total),
           "free_slots": 28 - len(inv.get("items") or [])}
    eq, eq_age = read_export(folder, "equipment.json")
    if eq:
        eitems, etotal = _valued(eq.get("items"))
        out.update({"equipment_updated": human_age(eq_age), "equipment": eitems,
                    "equipment_value": etotal, "equipment_value_text": _gp(etotal)})
    return out


def t_get_bank(args):
    name, folder = _char(args)
    bank, age = _need(folder, "bank.json", "The bank export appears after opening the bank in game.")
    items, total = _valued(bank.get("items"))
    coins = sum(i["quantity"] for i in items if i["id"] == data.COINS_ID)
    tokens = sum(i["quantity"] for i in items if i["id"] == data.PLATINUM_TOKEN_ID)
    cash_total = coins + tokens * data.PLATINUM_TOKEN_VALUE   # what you can really spend: coins + 1,000 x tokens
    search = (args.get("search") or "").lower()
    shown = [i for i in items if search in (i["name"] or "").lower()] if search else items
    shown.sort(key=lambda i: i["value"] or 0, reverse=True)
    limit = int(args.get("limit") or 40)
    no_price = [i["name"] for i in items if not i["unit_price"]]
    return {"character": name, "updated": human_age(age),
            "stale_warning": "Bank is over 1h old; ask the user to open their bank for fresh data." if age and age > 60 else None,
            "total_value": total, "total_value_text": _gp(total), "coins": coins, "coins_text": _gp(coins),
            "platinum_tokens": tokens, "cash_total": cash_total, "cash_total_text": _gp(cash_total),
            "item_count": len(items), "items": shown[:limit],
            "untradeable_or_unpriced": no_price[:30]}


def t_get_ge_offers(args):
    name, folder = _char(args)
    ge, age = _need(folder, "ge_offers.json", "Needs the 'OSRS Toolkit Exporter' RuneLite plugin, and a login.")
    latest, h1 = PRICES.latest(), PRICES.h1()
    positions = (data.load_json(DATA_DIR / "_alerts_state.json", {}) or {}).get("positions", {})
    slots = []
    for s in ge.get("slots", []):
        if s.get("state") == "EMPTY":
            slots.append({"slot": s["slot"], "state": "EMPTY"})
            continue
        iid = str(s.get("item_id"))
        lt, hh = latest.get(iid) or {}, h1.get(iid) or {}
        mkt = {"instant_buy": lt.get("high"), "instant_sell": lt.get("low"),
               "avg_buy_1h": hh.get("avgHighPrice"), "avg_sell_1h": hh.get("avgLowPrice"),
               "volume_1h": (hh.get("highPriceVolume") or 0) + (hh.get("lowPriceVolume") or 0)}
        price, note = s.get("price"), None
        if s.get("complete"):
            note = "Finished: collect it."
        elif s.get("type") == "BUY" and mkt["instant_sell"] and price < mkt["instant_sell"]:
            note = f"Below what sellers accept ({mkt['instant_sell']}): will be slow or may not fill."
        elif s.get("type") == "SELL" and mkt["instant_buy"] and price > mkt["instant_buy"]:
            note = f"Above what buyers pay ({mkt['instant_buy']}): will be slow or may not fill."
        entry = {**{k: s.get(k) for k in ("slot", "state", "type", "item_name", "item_id", "price",
                                          "quantity_filled", "quantity_total", "avg_price", "progress_pct")},
                 "market": mkt, "note": note}
        pos = positions.get(s.get("item_name"))
        if pos and pos.get("qty"):
            cost = pos["cost_total"] / pos["qty"]
            entry["your_avg_cost"] = round(cost, 1)
            if s.get("type") == "SELL":
                entry["profit_per_item_after_tax"] = round(net_sell(price) - cost, 1)
        slots.append(entry)
    return {"character": name, "updated": human_age(age), "world": ge.get("world"),
            "active_offers": ge.get("active_offers"), "empty_slots": ge.get("empty_slots"),
            "gp_locked_in_buys": ge.get("gp_locked_in_buys"), "slots": slots}


def t_get_market_opportunities(args):
    name, folder = _char(args)
    kind, limit = args.get("kind") or "all", int(args.get("limit") or 8)
    m, age = read_export(folder, "market.json")
    ran_scan = False
    if m is None or age is None or age > 15:
        try:
            import market_scan as ms
            with contextlib.redirect_stdout(io.StringIO()):
                ms.setup_paths(name)
                ms.main()
            m, age = read_export(folder, "market.json")
            ran_scan = True
        except Exception as e:
            if m is None:
                raise ToolError(f"Could not run a market scan: {e}")
    keep = ("name", "buy_at", "sell_at", "net_margin", "roi_pct", "buy_limit", "volume_1h", "qty_per_cycle",
            "profit_per_cycle", "gp_per_hour", "capital_needed", "volatile",
            "max_buy_price", "alch_value", "profit_per_cast", "capital_1h", "nature_rune")
    trim = lambda rows: [{k: r[k] for k in keep if k in r} for r in (rows or [])[:limit]]
    out = {"character": name, "scan_age": human_age(age), "fresh_scan": ran_scan,
           "cash": m.get("capital_gp"), "cash_text": _gp(m.get("capital_gp")),
           "free_ge_slots": m.get("free_slots"), "budget_per_slot": m.get("budget_per_slot"),
           "magic_level": m.get("magic_level"),
           "rules": "Margins are after 2% GE tax. buy_at/sell_at come from 5-minute/1-hour averages; "
                    "tell the user to check the live price in game before buying. For alch: never pay more "
                    "than max_buy_price; ~1200 casts/hour."}
    if kind in ("all", "flips"):
        out["flips_best_gp_per_hour"] = trim(m.get("flips_best_gp_per_hour"))
        out["flips_fastest_fill"] = trim(m.get("flips_fastest_fill"))
    if kind in ("all", "alch"):
        out["high_alch"] = trim(m.get("alch"))
    return out


def t_get_item_price(args):
    item, suggestions, how = PRICES.match_item(args.get("item", ""))
    if not item:
        raise ToolError(f"No item matches '{args.get('item')}'. Did you mean: {', '.join(suggestions) or 'nothing close'}?")
    iid = str(item["id"])
    lt = PRICES.latest().get(iid) or {}
    f5, hh, dd = PRICES.m5().get(iid) or {}, PRICES.h1().get(iid) or {}, PRICES.h24().get(iid) or {}
    hi, lo = lt.get("high"), lt.get("low")
    nat = PRICES.nature_rune()
    out = {"item": item["name"], "id": item["id"], "query": args.get("item"), "matched_by": how,
           **({"other_matches": suggestions} if suggestions else {}), "members": item.get("members"), "examine": item.get("examine"),
           "buy_limit_4h": item.get("limit"), "high_alch": item.get("highalch"),
           "instant_buy": hi, "instant_sell": lo,
           "last_trades_age": {"buy": human_age((time.time() - lt["highTime"]) / 60) if lt.get("highTime") else None,
                               "sell": human_age((time.time() - lt["lowTime"]) / 60) if lt.get("lowTime") else None},
           "avg_5m": {"buy": f5.get("avgHighPrice"), "sell": f5.get("avgLowPrice"),
                      "volume": (f5.get("highPriceVolume") or 0) + (f5.get("lowPriceVolume") or 0)},
           "avg_1h": {"buy": hh.get("avgHighPrice"), "sell": hh.get("avgLowPrice"),
                      "volume": (hh.get("highPriceVolume") or 0) + (hh.get("lowPriceVolume") or 0)},
           "avg_24h": {"buy": dd.get("avgHighPrice"), "sell": dd.get("avgLowPrice"),
                       "volume": (dd.get("highPriceVolume") or 0) + (dd.get("lowPriceVolume") or 0)},
           "ge_tax_on_sale": tax(hi) if hi else None,
           "flip_margin_after_tax": (net_sell(hi) - lo) if hi and lo else None,
           "wiki": f"https://oldschool.runescape.wiki/w/Special:Lookup?type=item&id={item['id']}"}
    if item.get("highalch") and hi and nat:
        out["alch_profit_per_cast"] = item["highalch"] - hi - nat
        out["alch_max_buy_price"] = item["highalch"] - nat
    days = int(args.get("history_days") or 0)
    if days:
        ts = PRICES.timeseries(item["id"], "6h" if days <= 7 else "24h")
        pts = ts[-(days * 4 if days <= 7 else days):]
        mids = [(p["avgHighPrice"] + p["avgLowPrice"]) / 2 for p in pts if p.get("avgHighPrice") and p.get("avgLowPrice")]
        if mids:
            out[f"history_{days}d"] = {"low": int(min(mids)), "high": int(max(mids)), "average": int(sum(mids) / len(mids)),
                                       "first": int(mids[0]), "last": int(mids[-1]),
                                       "change_pct": round((mids[-1] - mids[0]) / mids[0] * 100, 1)}
    if suggestions:
        out["other_matches"] = suggestions
    return out


def _held(folder, iid):
    """(quantity held in inventory + bank + unsold GE sell offers, open sell offers for this item)."""
    held, offers = 0, []
    for fn in ("inventory.json", "bank.json"):
        d, _ = read_export(folder, fn)
        held += sum(i.get("quantity", 0) for i in (d or {}).get("items", []) if i.get("id") == iid)
    ge, _ = read_export(folder, "ge_offers.json")
    for sl in (ge or {}).get("slots", []):
        if sl.get("item_id") == iid and sl.get("type") == "SELL" and sl.get("state") != "EMPTY" and not sl.get("complete"):
            left = max(0, (sl.get("quantity_total") or 0) - (sl.get("quantity_filled") or 0))
            held += left
            offers.append({"slot": sl.get("slot"), "price": sl.get("price"), "unsold": left})
    return held, offers


def t_get_sell_advice(args):
    name, folder = _char(args)
    item, suggestions, _how = PRICES.match_item(args.get("item", ""))
    if not item:
        raise ToolError(f"No item matches '{args.get('item')}'. Did you mean: {', '.join(suggestions) or 'nothing close'}?")
    iid = item["id"]
    lt = PRICES.latest().get(str(iid)) or {}
    hh = PRICES.h1().get(str(iid)) or {}
    hi, lo = lt.get("high"), lt.get("low")
    if not hi or not lo:
        raise ToolError(f"No recent trades for {item['name']}: cannot price it right now.")
    if lo > hi:
        hi, lo = lo, hi
    volume = (hh.get("highPriceVolume") or 0) + (hh.get("lowPriceVolume") or 0)
    held, offers = _held(folder, iid) if folder else (0, [])
    qty = int(args.get("quantity") or held or 1)
    cost = args.get("cost_per_item")
    cost_source = "player" if cost else None
    if not cost:
        pos = ((data.load_json(DATA_DIR / "_alerts_state.json", {}) or {}).get("positions", {})).get(item["name"])
        if pos and pos.get("qty"):
            cost, cost_source = pos["cost_total"] / pos["qty"], "tracked GE buys"
    fast = lo                                   # sells now, to the best current buyer
    patient = max(lo, hi - 1)                   # undercut the cheapest seller by 1 gp
    balanced = max(fast, min(patient, round((fast + patient) / 2)))

    def option(price, speed):
        net = net_sell(price)
        o = {"price": price, "tax_per_item": tax(price), "net_per_item": net, "total_net": net * qty, "speed": speed}
        if cost:
            o["profit_per_item"] = round(net - cost, 1)
            o["total_profit"] = int((net - cost) * qty)
        return o

    out = {"character": name, "item": item["name"], "quantity": qty,
           "quantity_source": "player" if args.get("quantity") else ("held" if held else "default 1"),
           "your_cost_per_item": round(cost, 1) if cost else None, "cost_source": cost_source,
           "market": {"instant_buy": hi, "instant_sell": lo, "volume_1h": volume,
                      "last_trade": human_age((time.time() - max(lt.get("highTime") or 0, lt.get("lowTime") or 0)) / 60)},
           "fast": option(fast, "fills right away (sells to the current best buyer)"),
           "balanced": option(balanced, "usually fills within the hour on a liquid item"),
           "patient": option(patient, "best price; can take hours and stalls if the market drops"),
           "open_sell_offers": offers, "warnings": []}
    if volume and qty > 0.1 * volume:
        out["warnings"].append(f"{qty} is {round(qty / volume * 100)}% of the hourly volume: expect slower fills, "
                               "or split the sale.")
    if cost and net_sell(patient) < cost:
        out["warnings"].append("Every option sells below your cost: selling now locks in a loss.")
    elif cost and net_sell(fast) < cost:
        out["warnings"].append("The fast price is below your cost; balanced or patient keeps a profit.")
    for o in offers:
        if o["price"] > hi:
            out["warnings"].append(f"Your offer in slot {o['slot']} at {o['price']} is above every current "
                                   f"seller ({hi}): it will not fill until the market rises.")
    # the tool picks, so the model only has to read it out
    out["suggested"] = "patient" if cost and net_sell(balanced) < cost <= net_sell(patient) else "balanced"
    return out


def t_get_income(args):
    import income
    name, _folder = _char(args)
    return income.summary(name, args.get("period") or "today")


def t_get_recent_alerts(args):
    name, folder = _char(args)
    hours = float(args.get("hours") or 24)
    cat = (args.get("category") or "").upper()
    aliases = {"MARKET": {"MARKET", "MARCHE"}, "TARGET": {"TARGET", "SEUIL"}}
    cutoff = time.time() - hours * 3600
    rows = []
    for path in (folder / "alerts.jsonl", DATA_DIR / "alerts.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-2000:]
        except OSError:
            continue
        for line in lines:
            try:
                a = json.loads(line)
                ts = time.mktime(time.strptime(a["ts"], "%Y-%m-%dT%H:%M:%S"))
            except Exception:
                continue
            if ts >= cutoff and (not cat or a.get("category", "").upper() in aliases.get(cat, {cat})):
                rows.append(a)
    rows.sort(key=lambda a: a["ts"], reverse=True)
    return {"character": name, "hours": hours, "count": len(rows), "alerts": rows[:60]}


# ----------------------------------------------------------------- stream ---
def _app(method, path, body=None):
    """Call the running desktop app's local server (the stream module lives there)."""
    import urllib.request
    ports = []
    try:
        ports.append(int((DATA_DIR / "port.txt").read_text().strip()))
    except Exception:
        pass
    ports += [p for p in range(8765, 8776) if p not in ports]
    last = None
    for port in ports[:4]:
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                     data=json.dumps(body or {}).encode("utf-8") if method == "POST" else None,
                                     headers={"Content-Type": "application/json", "X-Toolkit": "1",
                                              "Host": f"localhost:{port}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
    raise ToolError(f"The {APP_NAME} app is not running (start it from the desktop icon): {last}")


def t_stream_get_chat(args):
    out = _app("GET", "/api/stream/chat")
    out["recent_chat"] = out.get("recent_chat", [])[-int(args.get("limit") or 30):]
    return out


def t_stream_say(args):
    return _app("POST", "/api/stream/say", {"text": args.get("text", ""), "question_id": args.get("question_id"),
                                            "to_chat": args.get("to_chat", True)})


def t_stream_skip_question(args):
    return _app("POST", "/api/stream/skip", {"question_id": args.get("question_id")})


def t_stream_show_scene(args):
    out = _app("POST", "/api/stream/scene", {"scene": args.get("scene", "")})
    meld = out.get("meld") or {}
    if not meld.get("online"):
        out["warning"] = ("The overlay browser source is not open in Meld Studio (or Meld's API is off), "
                          "so the scene will switch as soon as it is.")
    elif meld.get("scenes") and args.get("scene", "").lower() not in [s.lower() for s in meld["scenes"]]:
        out["warning"] = f"No Meld scene named '{args.get('scene')}'. Scenes: {', '.join(meld['scenes'])}"
    return out


def t_stream_status(args):
    return _app("GET", "/api/stream/state")


HANDLERS = {
    "stream_get_chat": t_stream_get_chat, "stream_say": t_stream_say,
    "stream_skip_question": t_stream_skip_question, "stream_show_scene": t_stream_show_scene,
    "stream_status": t_stream_status,
    "get_status": t_get_status, "get_character": t_get_character, "get_position": t_get_position,
    "get_combat_achievements": t_get_combat_achievements,
    "get_quest_info": t_get_quest_info, "get_available_quests": t_get_available_quests,
    "get_inventory": t_get_inventory, "get_bank": t_get_bank, "get_ge_offers": t_get_ge_offers,
    "get_market_opportunities": t_get_market_opportunities, "get_item_price": t_get_item_price,
    "get_recent_alerts": t_get_recent_alerts, "get_income": t_get_income,
    "get_sell_advice": t_get_sell_advice,
}


# ----------------------------------------------------------------- protocol ---
def send(msg):
    box = getattr(_HTTP, "replies", None)
    if box is not None:
        box.append(msg)
        return
    _PROTO_OUT.write(json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
    _PROTO_OUT.flush()


def handle_http(payload):
    """Streamable-HTTP transport (JSON responses): one JSON-RPC message or a batch in, the replies out.
    Returns None when there is nothing to answer (notifications only)."""
    _HTTP.replies = []
    try:
        msgs = payload if isinstance(payload, list) else [payload]
        for m in msgs:
            if not isinstance(m, dict):
                continue
            try:
                handle(m)
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                if m.get("id") is not None:
                    error(m.get("id"), -32603, str(e))
        out = _HTTP.replies
    finally:
        _HTTP.replies = None
    if not out:
        return None
    return out if isinstance(payload, list) else out[0]


def result(rid, res):
    send({"jsonrpc": "2.0", "id": rid, "result": res})


def error(rid, code, message):
    send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def handle(msg):
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if rid is None:           # notification (initialized, cancelled...): nothing to answer
        return
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[1]
        result(rid, {"protocolVersion": version,
                     "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
                     "serverInfo": {"name": "osrs-ge-toolkit", "title": APP_NAME, "version": VERSION},
                     "instructions": INSTRUCTIONS})
    elif method == "ping":
        result(rid, {})
    elif method == "tools/list":
        result(rid, {"tools": TOOLS})
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            error(rid, -32602, f"Unknown tool: {name}")
            return
        try:
            out = fn(args)
            text = json.dumps(out, ensure_ascii=False, indent=1, default=str)
            result(rid, {"content": [{"type": "text", "text": text}], "isError": False})
        except ToolError as e:
            result(rid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            result(rid, {"content": [{"type": "text", "text": f"Tool failed: {e}"}], "isError": True})
    elif method == "prompts/list":
        result(rid, {"prompts": PROMPTS})
    elif method == "prompts/get":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in PROMPT_TEXT:
            error(rid, -32602, f"Unknown prompt: {name}")
            return
        text = PROMPT_TEXT[name].format(goal=args.get("goal") or "a bond",
                                        focus=args.get("focus") or "whatever fits my account best")
        desc = next(p["description"] for p in PROMPTS if p["name"] == name)
        result(rid, {"description": desc, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]})
    elif method in ("resources/list", "resources/templates/list"):
        result(rid, {"resources": []} if method == "resources/list" else {"resourceTemplates": []})
    else:
        error(rid, -32601, f"Method not found: {method}")


def serve():
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            error(None, -32700, "Parse error")
            continue
        for m in (msg if isinstance(msg, list) else [msg]):
            try:
                handle(m)
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                if m.get("id") is not None:
                    error(m.get("id"), -32603, str(e))


def main():
    global _PROTO_OUT
    if len(sys.argv) > 1 and sys.argv[1] in ("--connect-claude", "--disconnect-claude", "--status"):
        import claude_connect
        sys.stdout = _PROTO_OUT
        action = sys.argv[1]
        res = (claude_connect.connect() if action == "--connect-claude" else
               claude_connect.disconnect() if action == "--disconnect-claude" else claude_connect.status())
        print(json.dumps(res, indent=2, default=str))
        return
    # binary-safe UTF-8 stdout for the protocol (Windows consoles default to cp1252)
    _PROTO_OUT = io.TextIOWrapper(_PROTO_OUT.buffer, encoding="utf-8", newline="\n", line_buffering=True)
    serve()


if __name__ == "__main__":
    main()
