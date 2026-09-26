"""
OSRS GE Toolkit - Claude connector (MCP server over stdio).

Gives Claude read-only tools over the player's RuneLite exports (Character Export +
Position Exporter plugins) and live OSRS Wiki prices. Claude can read and advise;
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

# stdout is the protocol channel: keep a private handle and send every stray print to stderr
_PROTO_OUT = sys.stdout
sys.stdout = sys.stderr

import data  # noqa: E402
from data import PRICES, read_export, resolve_character, human_age, net_sell, tax  # noqa: E402
from paths import APP_NAME, VERSION, DATA_DIR, RUNELITE_ROOT  # noqa: E402

SUPPORTED_PROTOCOLS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = """OSRS GE Toolkit gives you read-only access to the user's Old School RuneScape character \
(exported locally by the RuneLite plugins "Character Export" and "Position Exporter") and to live \
Grand Exchange prices from the OSRS Wiki.

- Start with get_status if you are unsure what data exists or how fresh it is; mention stale data \
(e.g. the bank only updates when the user opens it in game).
- Prices: GE tax is 2% on sales of 50 gp or more (capped at 5M, bonds exempt). Always quote margins after tax.
- Coordinates are OSRS world tiles (x, y, plane). Use your OSRS knowledge (and the map link) to \
identify the location; y > 9000 usually means an underground/dungeon area.
- You can advise, plan and explain. Never help automate gameplay (bots, macros, input automation): \
it breaks Jagex's rules and gets accounts banned.
- Answer in the user's language."""

CHAR_ARG = {"character": {"type": "string",
                          "description": "Character name. Optional: defaults to the most recently played character."}}


def tool(name, description, props=None, required=None):
    schema = {"type": "object", "properties": dict(props or {}), "additionalProperties": False}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema,
            "annotations": {"readOnlyHint": True, "openWorldHint": False}}


TOOLS = [
    tool("get_status",
         "What data is available: exported characters, which plugins are working, how fresh each file is, "
         "and what to fix if something is missing. Call this first when unsure.", CHAR_ARG),
    tool("get_character",
         "Stats (levels, XP, combat level, total level), membership, world, quests (in progress / finished), "
         "achievement diaries and combat achievements progress.",
         {**CHAR_ARG, "include_all_quests": {"type": "boolean",
                                             "description": "Also list every not-started quest (long). Default false."}}),
    tool("get_position",
         "The player's live location in game (world tile x/y/plane, region, world) with a map link. "
         "Useful for 'where am I', quest steps, navigation.", CHAR_ARG),
    tool("get_inventory",
         "Current inventory and worn equipment, with live GE value per item.", CHAR_ARG),
    tool("get_bank",
         "Bank contents valued at live GE prices: total value, coins, most valuable items. "
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
         {"item": {"type": "string", "description": "Item name (fuzzy) or numeric item id."},
          "history_days": {"type": "integer", "enum": [0, 7, 30], "description": "Default 0 (no history)."}},
         ["item"]),
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
    "ge_checkup": "Use the OSRS GE Toolkit tools (get_ge_offers, get_market_opportunities, get_bank) to review my "
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
        raise ToolError("No game data found yet. The user needs RuneLite with the 'Character Export' plugin "
                        "(Plugin Hub), then to log in and open their bank once. Call get_status for details.")
    return name, folder


def _need(folder, filename, hint):
    d, age = read_export(folder, filename)
    if d is None:
        raise ToolError(f"{filename} not found. {hint}")
    return d, age


def _gp(n):
    if n is None:
        return None
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if abs(n) >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


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
        out["problems"].append("No exports found. Install RuneLite plugin 'Character Export' from the Plugin Hub, "
                               "log in, and open the bank once.")
        return out
    for fn, (what, plugin) in data.EXPORT_FILES.items():
        d, age = read_export(folder, fn)
        out["files"][fn] = {"contains": what, "written_by": plugin,
                            "present": d is not None, "updated": human_age(age) if d is not None else None}
    f = out["files"]
    if not f["position.json"]["present"] or not f["ge_offers.json"]["present"]:
        out["problems"].append("No live position / GE offers: install the 'Position Exporter' plugin from the "
                               "RuneLite Plugin Hub.")
    if not f["market.json"]["present"]:
        out["problems"].append("No flip/alch scan yet: start OSRS GE Toolkit (desktop icon). "
                               "get_market_opportunities can also run a scan on demand.")
    _, bank_age = read_export(folder, "bank.json")
    if bank_age and bank_age > 60:
        out["problems"].append(f"Bank data is {human_age(bank_age)}: ask the user to open their bank in game.")
    return out


def t_get_character(args):
    name, folder = _char(args)
    ch, age = _need(folder, "character.json", "Needs the 'Character Export' plugin and a login.")
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
        out["diaries"] = {region: {tier: (("complete" if v.get("complete") else f"{v.get('tasks_done', 0)} tasks done"))
                                   for tier, v in tiers.items()}
                          for region, tiers in (d.get("diaries") or {}).items()}
    ca, _ = read_export(folder, "combat_achievements.json")
    if ca:
        out["combat_achievements"] = {tier: f"{v.get('tasks_completed', 0)}/{v.get('tasks_total', '?')}"
                                             + (" (complete)" if v.get("complete") else "")
                                      for tier, v in (ca.get("tiers") or {}).items()}
    return out


def t_get_position(args):
    name, folder = _char(args)
    p, age = _need(folder, "position.json", "Needs the 'Position Exporter' plugin (RuneLite Plugin Hub).")
    x, y, plane = p.get("x"), p.get("y"), p.get("plane", 0)
    return {"character": name, "updated": human_age(age), "world": p.get("world"),
            "x": x, "y": y, "plane": plane, "region_id": p.get("region_id"),
            "underground": bool(y and y > 9000),
            "map": f"https://explv.github.io/?centreX={x}&centreY={y}&centreZ={plane}&zoom=10",
            "note": "Identify the place from the coordinates/region id with your OSRS knowledge."}


def t_get_inventory(args):
    name, folder = _char(args)
    inv, inv_age = _need(folder, "inventory.json", "Needs the 'Character Export' plugin.")
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
    search = (args.get("search") or "").lower()
    shown = [i for i in items if search in (i["name"] or "").lower()] if search else items
    shown.sort(key=lambda i: i["value"] or 0, reverse=True)
    limit = int(args.get("limit") or 40)
    no_price = [i["name"] for i in items if not i["unit_price"]]
    return {"character": name, "updated": human_age(age),
            "stale_warning": "Bank is over 1h old; ask the user to open their bank for fresh data." if age and age > 60 else None,
            "total_value": total, "total_value_text": _gp(total), "coins": coins, "coins_text": _gp(coins),
            "item_count": len(items), "items": shown[:limit],
            "untradeable_or_unpriced": no_price[:30]}


def t_get_ge_offers(args):
    name, folder = _char(args)
    ge, age = _need(folder, "ge_offers.json", "Needs the 'Position Exporter' plugin, and a login.")
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
    item, suggestions = PRICES.find_item(args.get("item", ""))
    if not item:
        raise ToolError(f"No item matches '{args.get('item')}'. Did you mean: {', '.join(suggestions) or 'nothing close'}?")
    iid = str(item["id"])
    lt = PRICES.latest().get(iid) or {}
    f5, hh, dd = PRICES.m5().get(iid) or {}, PRICES.h1().get(iid) or {}, PRICES.h24().get(iid) or {}
    hi, lo = lt.get("high"), lt.get("low")
    nat = PRICES.nature_rune()
    out = {"item": item["name"], "id": item["id"], "members": item.get("members"), "examine": item.get("examine"),
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


HANDLERS = {
    "get_status": t_get_status, "get_character": t_get_character, "get_position": t_get_position,
    "get_inventory": t_get_inventory, "get_bank": t_get_bank, "get_ge_offers": t_get_ge_offers,
    "get_market_opportunities": t_get_market_opportunities, "get_item_price": t_get_item_price,
    "get_recent_alerts": t_get_recent_alerts,
}


# ----------------------------------------------------------------- protocol ---
def send(msg):
    _PROTO_OUT.write(json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
    _PROTO_OUT.flush()


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
