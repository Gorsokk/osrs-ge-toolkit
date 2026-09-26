"""
market_scan.py — GE flip/alch scanner for a RuneLite character-exporter account.

What changed vs the old version:
  - Auto-detects F2P vs Members from character.json's world_types
    (no more hardcoded mode), and GE slot count follows (3 F2P / 8 members).
  - Uses the OSRS Wiki real-time prices API's /5m and /1h endpoints
    (avgLowPrice/avgHighPrice + real trade volume) instead of /latest,
    which just reflects the single last trade and can be an outlier
    (this is what bit us on the Earth rune order).
  - Applies a safety margin on the buy price so orders actually fill.
  - Liquidity filter: skips anything under MIN_VOLUME_1H trade volume —
    kills the "Ancient bracers 115% ROI" style trap where a tiny buy_limit
    and a handful of trades/hour fake a huge margin.
  - Quantities are capped to your REAL budget_per_slot, not just to what
    the market could theoretically absorb — every number in the output
    is something you can actually afford right now.
  - Volatility flag: compares the 5m price to the 1h baseline and marks
    "volatile": true when they've drifted apart by more than
    VOLATILITY_WARN_PCT — a warning that the scan may already be stale
    vs. what you'll see in-game (this is what happened with Broad bolts
    and Unpowered orb).
  - gp_per_hour: flips are ranked by profit normalized to actual fill
    time, not raw profit-per-4h-cycle, so a flip that fills in 20 minutes
    isn't buried under one that ties up capital for the full 4 hours.
  - Reads your real capital from BOTH bank.json and inventory.json and
    splits it across your GE slot count.
  - Two rankings: fastest-filling flips, and best gp/hour flips — plus
    an alch list gated to your actual Magic level.

Usage:
    python market_scan.py                          # single scan
    python market_scan.py --loop 275               # re-scan every 275s until Ctrl+C
    python market_scan.py --loop 275 --serve 8765   # also serve a live dashboard at
                                                     # http://localhost:8765/dashboard.html

Reads from (auto-detected, no editing needed — see find_export_dir() below):
    <your user folder>\\.runelite\\character-exporter\\<your character>\\character.json
    <your user folder>\\.runelite\\character-exporter\\<your character>\\bank.json
    <your user folder>\\.runelite\\character-exporter\\<your character>\\inventory.json

Writes:
    <your user folder>\\.runelite\\character-exporter\\<your character>\\market.json

First run: if you have more than one exported character, the script will
ask you to pick one and remember it. To skip the question, pass it directly:
    python market_scan.py --char TonPseudo
"""

import argparse
import functools
import http.server
import json
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------- config ---
# Nothing to edit here — the export folder and character name are detected
# automatically for whoever runs this script (see find_export_dir()).
from paths import RUNELITE_ROOT, DATA_DIR  # dossier de donnees utilisateur (%APPDATA%\\OSRS GE Toolkit)
CHAR_CHOICE_FILE = DATA_DIR / "_last_character.txt"

# Filled in by setup_paths() once we know which character we're scanning —
# left as None here so the script fails loudly if used before that runs.
CHAR_NAME = None
EXPORT_DIR = None
CHARACTER_JSON = None
BANK_JSON = None
INVENTORY_JSON = None
OUTPUT_JSON = None
GE_OFFERS_JSON = None
MAPPING_CACHE = None

GE_SLOTS_F2P = 3
GE_SLOTS_MEMBERS = 8
BUY_SAFETY_MARGIN = 0.03      # offer 3% above avg low price so it actually fills
VOLUME_SHARE = 0.25           # assume we can realistically claim 25% of hourly volume
CYCLE_HOURS = 4               # GE buy-limit reset window
TAX_RATE = 0.02
TAX_CAP = 5_000_000
MIN_TAXABLE_PRICE = 50
MAPPING_MAX_AGE_SEC = 7 * 24 * 3600   # refresh item mapping weekly

MIN_VOLUME_1H = 500            # below this, market is too thin to trust (the "Ancient bracers" trap)
VOLATILITY_WARN_PCT = 15       # flag items where 5m price has drifted >15% from the 1h baseline
MIN_NET_MARGIN_GP = 3          # flips a 1-2 gp de marge (Feather, Water rune...) ne se remplissent pas face a la concurrence
MIN_ROI_PCT = 1.0              # et sous 1 % de ROI, un seul mouvement de prix efface le profit
MIN_FLIP_PRICE = 10            # sous 10 gp, les moyennes arrondies mentent (Fish offcuts 6 -> 12 = "100 %")
MIN_SIDE_VOLUME_1H = 100       # il faut des echanges des DEUX cotes (acheteurs ET vendeurs) sur l'heure
ALCHS_PER_HOUR = 1200          # rythme max de High Alch (1 cast / 5 ticks)

WIKI_API = "https://prices.runescape.wiki/api/v1/osrs"
from paths import USER_AGENT

NATURE_RUNE_ID = 561


# ------------------------------------------------------------- fetch/io ---
def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_mapping():
    """Item metadata (name, buy_limit, members flag, highalch). Cached weekly."""
    if MAPPING_CACHE.exists():
        age = time.time() - MAPPING_CACHE.stat().st_mtime
        if age < MAPPING_MAX_AGE_SEC:
            return load_json(MAPPING_CACHE)
    mapping = fetch_json(f"{WIKI_API}/mapping")
    with open(MAPPING_CACHE, "w", encoding="utf-8") as f:
        json.dump(mapping, f)
    return mapping


def net_sell(price):
    """GE sell price after tax."""
    if price < MIN_TAXABLE_PRICE:
        return price
    tax = min(int(price * TAX_RATE), TAX_CAP)
    return price - tax


def find_export_dir(char_arg=None):
    """Figure out which exported character to use, with zero editing required.

    - If --char NAME was passed, use that.
    - Else if there's only one exported character, use it automatically.
    - Else if we remember a previous choice (and it's still there), reuse it.
    - Else ask once and remember the answer for next time.
    """
    if not RUNELITE_ROOT.exists():
        sys.exit(
            f"Error: {RUNELITE_ROOT} does not exist.\n"
            "Make sure RuneLite is installed with the 'character-exporter' plugin "
            "enabled, and that you've opened your bank in-game at least once so it "
            "can create an export."
        )

    available = sorted(p.name for p in RUNELITE_ROOT.iterdir() if p.is_dir())
    if not available:
        sys.exit(
            f"Error: no exported characters found in {RUNELITE_ROOT}.\n"
            "Open your bank in-game once with the character-exporter plugin "
            "enabled to create one."
        )

    if char_arg:
        if char_arg not in available:
            sys.exit(f"Error: no export found for '{char_arg}'. Available: {', '.join(available)}")
        chosen = char_arg
    elif len(available) == 1:
        chosen = available[0]
    elif CHAR_CHOICE_FILE.exists() and CHAR_CHOICE_FILE.read_text(encoding="utf-8").strip() in available:
        chosen = CHAR_CHOICE_FILE.read_text(encoding="utf-8").strip()
    else:
        print("Multiple exported characters found:")
        for i, name in enumerate(available, 1):
            print(f"  {i}. {name}")
        while True:
            pick = input(f"Which one is yours? (1-{len(available)}): ").strip()
            if pick.isdigit() and 1 <= int(pick) <= len(available):
                chosen = available[int(pick) - 1]
                break
            print("Invalid choice, try again.")

    CHAR_CHOICE_FILE.write_text(chosen, encoding="utf-8")
    return RUNELITE_ROOT / chosen


def setup_paths(char_arg=None):
    """Resolve EXPORT_DIR and friends for whoever is running the script."""
    global CHAR_NAME, EXPORT_DIR, CHARACTER_JSON, BANK_JSON, INVENTORY_JSON, OUTPUT_JSON, MAPPING_CACHE, GE_OFFERS_JSON
    EXPORT_DIR = find_export_dir(char_arg)
    CHAR_NAME = EXPORT_DIR.name
    CHARACTER_JSON = EXPORT_DIR / "character.json"
    BANK_JSON = EXPORT_DIR / "bank.json"
    INVENTORY_JSON = EXPORT_DIR / "inventory.json"
    OUTPUT_JSON = EXPORT_DIR / "market.json"
    GE_OFFERS_JSON = EXPORT_DIR / "ge_offers.json"  # written by Position Exporter
    MAPPING_CACHE = EXPORT_DIR / "_mapping_cache.json"


# ------------------------------------------------------------ GE offers ---
READY_STATES = {"BOUGHT", "SOLD", "CANCELLED_BUY", "CANCELLED_SELL"}
ACTIVE_STATES = {"BUYING", "SELLING"}


def read_ge_offers(total_slots):
    """Read ge_offers.json (Position Exporter plugin) if it exists.

    Coins sitting in a GE offer have already left your bank, so bank coins are
    already 'free cash'. What changes is how many slots that cash is split
    across: only slots that are empty or ready to collect can take a new offer.
    """
    if GE_OFFERS_JSON is None or not GE_OFFERS_JSON.exists():
        return None
    try:
        ge = load_json(GE_OFFERS_JSON)
    except (OSError, ValueError):
        return None
    slots = ge.get("slots", [])
    empty = sum(1 for s in slots if s.get("state") == "EMPTY")
    ready = sum(1 for s in slots if s.get("state") in READY_STATES)
    active = sum(1 for s in slots if s.get("state") in ACTIVE_STATES)
    # a members account whose export shows fewer slots still has total_slots
    free = min(total_slots, empty + ready)
    return {
        "exported_at": ge.get("exported_at"),
        "slots": slots,
        "active": active,
        "empty": empty,
        "ready_to_collect": ready,
        "free_slots": free,
        "gp_locked_in_buys": ge.get("gp_locked_in_buys", 0),
    }


def file_age_min(path):
    try:
        return round((time.time() - path.stat().st_mtime) / 60, 1)
    except OSError:
        return None


# ------------------------------------------------------------- main ---
def main():
    character = load_json(CHARACTER_JSON)
    bank = load_json(BANK_JSON)
    inventory = load_json(INVENTORY_JSON) if INVENTORY_JSON.exists() else {"items": []}

    is_member = "MEMBERS" in character.get("world_types", [])
    mode = "members" if is_member else "f2p"
    GE_SLOTS = GE_SLOTS_MEMBERS if is_member else GE_SLOTS_F2P

    magic_level = character["stats"]["Magic"]["real_level"]

    capital_gp = 0
    for item in bank.get("items", []) + inventory.get("items", []):
        if item.get("id") == 995:  # Coins
            capital_gp += item.get("quantity", 0)
    ge_state = read_ge_offers(GE_SLOTS)
    free_slots = ge_state["free_slots"] if ge_state else GE_SLOTS
    # split free cash only across slots that can actually take a new offer
    budget_per_slot = capital_gp // free_slots if free_slots else 0
    bank_age_min = file_age_min(BANK_JSON)

    print(f"Mode: {mode} | Magic: {magic_level} | Cash libre: {capital_gp:,} gp "
          f"| Slots libres: {free_slots}/{GE_SLOTS} | Budget/slot libre: {budget_per_slot:,} gp")
    if ge_state:
        print(f"GE: {ge_state['active']} actives, {ge_state['ready_to_collect']} a collecter, "
              f"{ge_state['gp_locked_in_buys']:,} gp bloques dans les achats")
    if bank_age_min is not None and bank_age_min > 30:
        print(f"ATTENTION: bank.json date de {bank_age_min:.0f} min -- ouvre ta banque en jeu pour rafraichir le cash.")

    mapping = get_mapping()
    by_id = {m["id"]: m for m in mapping}

    prices_5m = fetch_json(f"{WIKI_API}/5m")["data"]
    prices_1h = fetch_json(f"{WIKI_API}/1h")["data"]

    nature_rune_price = None
    for src in (prices_5m, prices_1h):
        p_nat = src.get(str(NATURE_RUNE_ID))
        if p_nat and p_nat.get("avgHighPrice"):
            nature_rune_price = p_nat["avgHighPrice"]
            break

    flips = []
    alchs = []

    for item_id_str, p5 in prices_5m.items():
        item_id = int(item_id_str)
        meta = by_id.get(item_id)
        if not meta:
            continue
        if meta.get("members") and not is_member:
            continue  # skip members items on an F2P account

        buy_limit = meta.get("limit")
        if not buy_limit:
            continue

        avg_low = p5.get("avgLowPrice")
        avg_high = p5.get("avgHighPrice")
        if not avg_low or not avg_high or avg_low <= 0:
            continue

        p1 = prices_1h.get(item_id_str, {})
        # crude hourly volume estimate from the 1h window's trade counts
        vol_1h = (p1.get("highPriceVolume") or 0) + (p1.get("lowPriceVolume") or 0)
        if vol_1h <= 0:
            # fall back to extrapolating the 5m window
            vol_5m = (p5.get("highPriceVolume") or 0) + (p5.get("lowPriceVolume") or 0)
            vol_1h = vol_5m * 12

        # --- liquidity filter: skip thin markets that fake a great ROI ---
        # (this is the "Ancient bracers" / "Bob's red shirt" trap: buy_limit
        # of 4-8 units and a handful of trades/hour can show a huge % margin
        # that's really just one weird trade, not a repeatable flip)
        if vol_1h < MIN_VOLUME_1H:
            continue

        buy_at = int(avg_low * (1 + BUY_SAFETY_MARGIN))
        sell_at = avg_high
        avg_low_1h = p1.get("avgLowPrice")
        avg_high_1h = p1.get("avgHighPrice")

        # --- alch candidate: evalue AVANT les filtres de flip ---
        # (avant, un item n'apparaissait en alch que s'il etait aussi un bon flip,
        # ce qui cachait Dragon javelin tips, Magic longbow, battlestaves...)
        highalch = meta.get("highalch")
        if magic_level >= 55 and highalch and nature_rune_price:
            # prix d'achat realiste pour remplir vite, sans payer plus que l'achat instantane
            alch_buy = min(buy_at, avg_high)
            cost_per_cast = alch_buy + nature_rune_price
            profit_per_cast = highalch - cost_per_cast
            if profit_per_cast > 0:
                qty_alch = min(
                    buy_limit,
                    ALCHS_PER_HOUR * CYCLE_HOURS,
                    int(vol_1h * VOLUME_SHARE * CYCLE_HOURS),
                    capital_gp // cost_per_cast,
                )
                if qty_alch > 0:
                    per_hour = min(ALCHS_PER_HOUR, qty_alch)
                    alchs.append({
                        "id": item_id,
                        "name": meta.get("name"),
                        "members": meta.get("members", False),
                        "buy_at": alch_buy,
                        "max_buy_price": highalch - nature_rune_price,
                        "nature_rune": nature_rune_price,
                        "alch_value": highalch,
                        "profit_per_cast": profit_per_cast,
                        "buy_limit": buy_limit,
                        "volume_1h": vol_1h,
                        "qty_per_cycle": qty_alch,
                        "profit_per_cycle": qty_alch * profit_per_cast,
                        "gp_per_hour": per_hour * profit_per_cast,
                        "capital_1h": per_hour * cost_per_cast,
                        "volatile": bool(avg_low_1h and abs(avg_low - avg_low_1h) / avg_low_1h * 100 > VOLATILITY_WARN_PCT),
                    })

        # --- filtres de flip ---
        if buy_at < MIN_FLIP_PRICE:
            continue
        if (p1.get("lowPriceVolume") or 0) < MIN_SIDE_VOLUME_1H or \
           (p1.get("highPriceVolume") or 0) < MIN_SIDE_VOLUME_1H:
            continue
        # prix prudents: jamais acheter sous la moyenne 1h des vendeurs,
        # jamais compter sur une vente au-dessus de la moyenne 1h des acheteurs (pas de pics)
        if avg_low_1h:
            buy_at = max(buy_at, int(avg_low_1h))
        if avg_high_1h:
            sell_at = min(sell_at, avg_high_1h)
        net_margin = net_sell(sell_at) - buy_at
        if net_margin < MIN_NET_MARGIN_GP:
            continue
        if net_margin / buy_at * 100 < MIN_ROI_PCT:
            continue
        # marge inversee sur l'heure (moyenne d'achat >= moyenne de vente apres taxe):
        # l'ecart 5m est du bruit, pas une vraie marge (le piege "Avantoe potion (unf) 27 %")
        if avg_low_1h and avg_high_1h and net_sell(avg_high_1h) <= avg_low_1h:
            continue

        # --- volatility check: has the 5m price drifted a lot from the 1h baseline? ---
        # if so, the scan may already be stale relative to what you'll actually see
        # in-game (this is exactly what happened with Broad bolts and Unpowered orb)
        volatile = False
        if avg_low_1h and avg_high_1h and avg_low_1h > 0:
            drift_pct = abs(avg_low - avg_low_1h) / avg_low_1h * 100
            if drift_pct > VOLATILITY_WARN_PCT:
                volatile = True

        # --- liquidity-realistic quantity, then capped to what you can actually afford ---
        qty_liquidity = min(buy_limit, int(vol_1h * VOLUME_SHARE))
        if qty_liquidity <= 0:
            continue
        qty_affordable = min(qty_liquidity, budget_per_slot // buy_at) if buy_at > 0 else 0
        if qty_affordable <= 0:
            continue

        capital_needed = qty_affordable * buy_at
        profit_per_cycle = qty_affordable * net_margin
        roi_pct = round((net_margin / buy_at) * 100, 2)
        fill_ratio = round(qty_affordable / buy_limit, 3)  # closer to 1 = fills fast

        # estimate real fill time from liquidity (not affordability) so gp/hour
        # reflects how fast the market actually absorbs your order
        fill_hours = min(CYCLE_HOURS, qty_affordable / max(vol_1h * VOLUME_SHARE, 1))
        gp_per_hour = round(profit_per_cycle / max(fill_hours, 0.1))

        # --- flip candidate ---
        flips.append({
            "id": item_id,
            "name": meta.get("name"),
            "members": meta.get("members", False),
            "buy_at": buy_at,
            "sell_at": sell_at,
            "net_margin": net_margin,
            "roi_pct": roi_pct,
            "buy_limit": buy_limit,
            "volume_1h": vol_1h,
            "qty_per_cycle": qty_affordable,
            "fill_ratio": fill_ratio,
            "profit_per_cycle": profit_per_cycle,
            "gp_per_hour": gp_per_hour,
            "capital_needed": capital_needed,
            "volatile": volatile,
        })

    # two separate rankings for flips: fastest-filling, and best gp/hour
    # (gp/hour instead of raw profit_per_cycle so a flip that fills in
    # 20 minutes is compared fairly against one that ties up capital for 4h)
    fastest = sorted(flips, key=lambda x: (x["fill_ratio"], x["gp_per_hour"]), reverse=True)[:15]
    highest_yield = sorted(flips, key=lambda x: x["gp_per_hour"], reverse=True)[:15]
    best_alch = sorted(alchs, key=lambda x: (x["gp_per_hour"], x["profit_per_cycle"]), reverse=True)[:15]

    ge_market = {}
    if ge_state:
        for slot in ge_state["slots"]:
            iid = slot.get("item_id")
            if not iid:
                continue
            p5 = prices_5m.get(str(iid), {})
            p1 = prices_1h.get(str(iid), {})
            ge_market[str(iid)] = {
                "low_5m": p5.get("avgLowPrice"), "high_5m": p5.get("avgHighPrice"),
                "low_1h": p1.get("avgLowPrice"), "high_1h": p1.get("avgHighPrice"),
            }

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "character": CHAR_NAME,
        "mode": mode,
        "capital_gp": capital_gp,
        "slots": GE_SLOTS,
        "budget_per_slot": budget_per_slot,
        "free_slots": free_slots,
        "bank_age_min": bank_age_min,
        "ge": ge_state and {
            "exported_at": ge_state["exported_at"],
            "active": ge_state["active"],
            "empty": ge_state["empty"],
            "ready_to_collect": ge_state["ready_to_collect"],
            "gp_locked_in_buys": ge_state["gp_locked_in_buys"],
            "total_engaged_gp": capital_gp + ge_state["gp_locked_in_buys"],
        },
        # live-ish market prices for the items in your GE slots, so the dashboard
        # can tell you if an offer is below/above the market (= slow to fill)
        "ge_market": ge_market,
        "magic_level": magic_level,
        "tax_rule": "2% floor, cap 5M, 0 under 50 gp; bond exempt",
        "assumptions": {
            "volume_share": VOLUME_SHARE,
            "cycle_hours": CYCLE_HOURS,
            "buy_safety_margin_pct": BUY_SAFETY_MARGIN * 100,
            "min_volume_1h_filter": MIN_VOLUME_1H,
            "volatility_warn_pct": VOLATILITY_WARN_PCT,
            "min_net_margin_gp": MIN_NET_MARGIN_GP,
            "min_roi_pct": MIN_ROI_PCT,
            "min_flip_price": MIN_FLIP_PRICE,
            "min_side_volume_1h": MIN_SIDE_VOLUME_1H,
            "alchs_per_hour": ALCHS_PER_HOUR,
            "note": "Marges basees sur les moyennes 5m/1h du Wiki (avgLow/avgHigh), pas sur "
                    "le prix instantane. Quantites deja plafonnees a ton budget_per_slot reel "
                    "(pas juste a la liquidite). 'volatile': true = le prix 5m a deja bouge de "
                    f"plus de {VOLATILITY_WARN_PCT}% vs la moyenne 1h, verifie le prix live "
                    "avant d'acheter. gp_per_hour normalise le rendement selon le temps de "
                    "remplissage reel, pas juste le profit sur 4h.",
        },
        "flips_fastest_fill": fastest,
        "flips_best_gp_per_hour": highest_yield,
        "alch": best_alch,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"\nWrote {OUTPUT_JSON}")
    print(f"Top 3 fastest-fill flips: {[f['name'] for f in fastest[:3]]}")
    print(f"Top 3 gp/hour flips: {[f['name'] for f in highest_yield[:3]]}")
    if best_alch:
        print(f"Top 3 alch: {[a['name'] for a in best_alch[:3]]}")


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>GE Scan — Live</title>
<style>
  :root {
    --bg: #14171c; --panel: #1c2028; --border: #2a2f3a;
    --text: #e8eaed; --muted: #8a919e; --green: #4caf7d;
    --red: #e0616b; --amber: #d9a441; --accent: #6ea8fe;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg); color: var(--text);
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    margin: 0; padding: 20px;
  }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
  .stale { color: var(--red); font-weight: 600; }
  .cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 22px; }
  .card {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 12px 16px; min-width: 140px;
  }
  .card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
  .card .value { font-size: 20px; font-weight: 600; margin-top: 2px; }
  h2 { font-size: 14px; color: var(--accent); margin: 24px 0 8px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--border); }
  th:first-child, td:first-child { text-align: left; }
  th { color: var(--muted); font-weight: 500; font-size: 11px; text-transform: uppercase; }
  tr:hover { background: rgba(255,255,255,0.03); }
  .vol { color: var(--amber); font-weight: 600; }
  .ok { color: var(--green); }
  .wildy-banner {
    border-radius: 10px; padding: 10px 16px; margin-bottom: 18px;
    font-weight: 600; display: none;
  }
  .wildy-safe { background: rgba(76,175,125,0.12); border: 1px solid var(--green); color: var(--green); }
  .wildy-low { background: rgba(217,164,65,0.12); border: 1px solid var(--amber); color: var(--amber); }
  .wildy-high { background: rgba(224,97,107,0.15); border: 1px solid var(--red); color: var(--red); }
  .tip {
    background: rgba(110,168,254,0.10); border: 1px solid var(--accent);
    border-radius: 10px; padding: 10px 16px; margin: 10px 0 4px;
    font-size: 13px; color: var(--text);
  }
  .bar { background: var(--border); border-radius: 4px; height: 6px; width: 90px; display: inline-block; vertical-align: middle; margin-right: 6px; }
  .bar > span { display: block; height: 100%; border-radius: 4px; background: var(--accent); }
  .bar.done > span { background: var(--green); }
  .tag { font-size: 11px; padding: 2px 7px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); white-space: nowrap; }
  .tag.buy { color: var(--accent); border-color: var(--accent); }
  .tag.sell { color: var(--amber); border-color: var(--amber); }
  .tag.ready { color: var(--green); border-color: var(--green); }
  #geOffers th:nth-child(2), #geOffers td:nth-child(2), #geOffers td:last-child, #geOffers th:last-child { text-align: left; }
  .warn { color: var(--amber); }
  .bad { color: var(--red); }
  .muted { color: var(--muted); }
  footer { margin-top: 24px; color: var(--muted); font-size: 11px; }
</style>
</head>
<body>
  <h1>GE Scan — Live</h1>
  <div class="sub" id="meta">Chargement...</div>
  <div class="wildy-banner" id="wildyBanner"></div>
  <div class="cards" id="cards"></div>

  <h2>Mes offres GE <span class="muted" id="geAge" style="font-weight:400"></span></h2>
  <table id="geOffers"><thead></thead><tbody></tbody></table>

  <h2>Flips les plus rapides à remplir</h2>
  <table id="fastest"><thead></thead><tbody></tbody></table>

  <h2>Meilleur gp/heure</h2>
  <table id="yield"><thead></thead><tbody></tbody></table>

  <h2>Alch</h2>
  <table id="alch"><thead></thead><tbody></tbody></table>

  <h2>Progression — Diaries</h2>
  <table id="diaries"><thead></thead><tbody></tbody></table>

  <h2>Progression — Combat Achievements</h2>
  <table id="ca"><thead></thead><tbody></tbody></table>
  <div class="tip" id="wildyTip" style="display:none;"></div>

  <footer id="refresh">—</footer>

<script>
const FLIP_COLS = [
  ["name","Item"], ["buy_at","Achat"], ["sell_at","Vente"],
  ["roi_pct","ROI %"], ["qty_per_cycle","Qté"], ["gp_per_hour","gp/h"],
  ["capital_needed","Capital"], ["fill_ratio","Remplissage"]
];
const ALCH_COLS = [
  ["name","Item"], ["buy_at","Achat"], ["max_buy_price","Achat max"], ["alch_value","Valeur alch"],
  ["profit_per_cast","Profit/cast"], ["gp_per_hour","gp/h (alch)"], ["capital_1h","Capital 1h"],
  ["qty_per_cycle","Qté / 4h"], ["profit_per_cycle","Profit / 4h"]
];

function fmt(n) {
  if (typeof n !== "number") return n;
  if (Number.isInteger(n)) return n.toLocaleString("fr-CA");
  return n.toFixed(3);
}

function renderTable(tableId, rows, cols) {
  const table = document.getElementById(tableId);
  const thead = table.querySelector("thead");
  const tbody = table.querySelector("tbody");
  thead.innerHTML = "<tr>" + cols.map(c => `<th>${c[1]}</th>`).join("") + "</tr>";
  tbody.innerHTML = rows.map(r => {
    return "<tr" + (r.volatile ? ' class="vol"' : "") + ">" +
      cols.map(c => {
        let v = r[c[0]];
        if (c[0] === "name" && r.volatile) v = v + " ⚠";
        if (c[0] === "roi_pct") v = fmt(v) + "%";
        if (c[0] === "fill_ratio") v = fmt(v);
        else if (typeof v === "number") v = fmt(v);
        return `<td>${v}</td>`;
      }).join("") + "</tr>";
  }).join("");
}

// Approximation: standard OSRS wilderness levels follow y = 3520 + (level-1)*8,
// roughly valid for the mainland wilderness x-range (~2944 to 3392) and its
// underground mirrors. This does not cover every special zone (e.g. some
// deep-wildy bosses, wilderness resource areas at different x/y offsets) --
// treat it as a strong heuristic, not a guarantee, and always glance at the
// in-game wilderness level indicator too.
function wildernessLevel(pos) {
  if (!pos || typeof pos.y !== "number") return null;
  if (pos.y < 3520 || pos.y > 3968) return null;
  const inMainlandX = pos.x >= 2944 && pos.x <= 3392;
  if (!inMainlandX) return null;
  return Math.floor((pos.y - 3520) / 8) + 1;
}

function renderWildyBanner(pos) {
  const el = document.getElementById("wildyBanner");
  const level = wildernessLevel(pos);
  if (level === null) {
    el.style.display = "none";
    return;
  }
  el.style.display = "block";
  el.className = "wildy-banner " + (level >= 20 ? "wildy-high" : level >= 1 ? "wildy-low" : "wildy-safe");
  el.textContent = `⚔ En Wilderness — niveau ${level} (x:${pos.x}, y:${pos.y})`;
}

const DIARY_TIERS = ["easy", "medium", "hard", "elite"];

function renderDiaries(diariesData) {
  const table = document.getElementById("diaries");
  const thead = table.querySelector("thead");
  const tbody = table.querySelector("tbody");
  thead.innerHTML = "<tr><th>Région</th><th>Easy</th><th>Medium</th><th>Hard</th><th>Elite</th></tr>";
  const diaries = diariesData.diaries || {};
  tbody.innerHTML = Object.keys(diaries).sort().map(region => {
    const cells = DIARY_TIERS.map(tier => {
      const t = diaries[region][tier];
      if (!t) return "<td>—</td>";
      const v = t.complete ? "✓" : String(t.tasks_done || 0);
      return `<td class="${t.complete ? 'ok' : ''}">${v}</td>`;
    }).join("");
    return `<tr><td>${region}</td>${cells}</tr>`;
  }).join("");
}

function renderCA(caData) {
  const table = document.getElementById("ca");
  const thead = table.querySelector("thead");
  const tbody = table.querySelector("tbody");
  thead.innerHTML = "<tr><th>Palier</th><th>Complétées</th><th>Total</th></tr>";
  const tiers = caData.tiers || {};
  tbody.innerHTML = Object.keys(tiers).map(tierName => {
    const t = tiers[tierName];
    return `<tr><td>${tierName}</td><td>${t.tasks_completed}</td><td>${t.tasks_total}</td></tr>`;
  }).join("");
}

const STATE_FR = {
  BUYING: "Achat en cours", SELLING: "Vente en cours", BOUGHT: "Acheté — à collecter",
  SOLD: "Vendu — à collecter", CANCELLED_BUY: "Achat annulé — à collecter",
  CANCELLED_SELL: "Vente annulée — à collecter", EMPTY: "Vide"
};

// Compare your offer price with the market (5m avg, else 1h).
function marketHint(slot, market) {
  if (!market || slot.state === "EMPTY" || slot.complete || slot.cancelled) return "";
  const low = market.low_5m || market.low_1h, high = market.high_5m || market.high_1h;
  if (!low || !high) return '<span class="muted">pas de prix</span>';
  const range = `<span class="muted">marché ${fmt(low)}–${fmt(high)}</span>`;
  if (slot.type === "BUY") {
    if (slot.price < low) return `<span class="bad">sous le marché, risque de ne pas remplir</span> · ${range}`;
    if (slot.price <= low) return `<span class="warn">au prix bas, lent</span> · ${range}`;
    if (slot.price > high) return `<span class="warn">tu paies plus que le haut</span> · ${range}`;
    return `<span class="ok">dans la fourchette</span> · ${range}`;
  } else {
    if (slot.price > high) return `<span class="bad">au-dessus du marché, risque de ne pas vendre</span> · ${range}`;
    if (slot.price >= high) return `<span class="warn">au prix haut, lent</span> · ${range}`;
    if (slot.price < low) return `<span class="warn">tu vends sous le bas</span> · ${range}`;
    return `<span class="ok">dans la fourchette</span> · ${range}`;
  }
}

function renderGE(ge, market) {
  const table = document.getElementById("geOffers");
  table.querySelector("thead").innerHTML =
    "<tr><th>Slot</th><th>Objet</th><th>Prix offre</th><th>Rempli</th><th>Prix moyen réel</th><th>État</th><th>Marché</th></tr>";
  const rows = (ge.slots || []).map(s => {
    if (s.state === "EMPTY") {
      return `<tr><td>${s.slot}</td><td class="muted">— vide —</td><td></td><td></td><td></td><td class="muted">Libre</td><td></td></tr>`;
    }
    const ready = s.complete || s.cancelled;
    const tag = ready ? '<span class="tag ready">' : (s.type === "BUY" ? '<span class="tag buy">' : '<span class="tag sell">');
    const pct = Math.max(0, Math.min(100, s.progress_pct || 0));
    const bar = `<span class="bar ${pct >= 100 ? 'done' : ''}"><span style="width:${pct}%"></span></span>`;
    const label = s.type === "BUY" ? "Achat" : "Vente";
    return `<tr>
      <td>${s.slot}</td>
      <td>${tag}${label}</span> ${s.item_name}</td>
      <td>${fmt(s.price)}</td>
      <td>${bar}${fmt(s.quantity_filled)} / ${fmt(s.quantity_total)}</td>
      <td>${s.avg_price != null ? fmt(s.avg_price) : '<span class="muted">—</span>'}</td>
      <td>${STATE_FR[s.state] || s.state}</td>
      <td>${marketHint(s, market[String(s.item_id)])}</td>
    </tr>`;
  });
  table.querySelector("tbody").innerHTML = rows.join("");
  const age = (Date.now() - new Date(ge.exported_at).getTime()) / 60000;
  document.getElementById("geAge").textContent =
    `· mis à jour il y a ${age < 1 ? "moins d'1" : Math.round(age)} min`;
}

async function refresh() {
  const metaEl = document.getElementById("meta");
  const refreshEl = document.getElementById("refresh");
  try {
    const res = await fetch("market.json?_=" + Date.now());
    const data = await res.json();

    const genAge = (Date.now() - new Date(data.generated_at).getTime()) / 1000;
    const staleTag = genAge > 600 ? ' <span class="stale">(scan vieux de ' + Math.round(genAge/60) + ' min — vérifie si le script tourne)</span>' : "";
    metaEl.innerHTML = `Perso: <b>${data.character}</b> · Mode: <b>${data.mode}</b> · Généré: ${data.generated_at}${staleTag}`;

    let posCardHtml = "";
    try {
      const posRes = await fetch("position.json?_=" + Date.now());
      const pos = await posRes.json();
      renderWildyBanner(pos);
      posCardHtml = `<div class="card"><div class="label">Position</div><div class="value">${pos.x}, ${pos.y}</div></div>`;
    } catch (e) {
      document.getElementById("wildyBanner").style.display = "none";
    }

    let ge = null;
    try {
      const geRes = await fetch("ge_offers.json?_=" + Date.now());
      if (geRes.ok) {
        ge = await geRes.json();
        renderGE(ge, data.ge_market || {});
      }
    } catch (e) { /* ge_offers.json not there yet (plugin not running) */ }

    // Cash live : lu directement dans bank.json + inventory.json (pas besoin d'attendre le scan)
    let cash = data.capital_gp;
    let bankAgeMin = data.bank_age_min;
    let cashSrc = "scan";
    try {
      const [bankRes, invRes] = await Promise.all([
        fetch("bank.json?_=" + Date.now()),
        fetch("inventory.json?_=" + Date.now()),
      ]);
      const coinsIn = c => ((c && c.items) || [])
        .filter(it => it.id === 995).reduce((a, it) => a + it.quantity, 0);
      const bank = bankRes.ok ? await bankRes.json() : null;
      const inv = invRes.ok ? await invRes.json() : null;
      if (bank) {
        cash = coinsIn(bank) + coinsIn(inv);
        bankAgeMin = (Date.now() - new Date(bank.exported_at).getTime()) / 60000;
        cashSrc = "live";
      }
    } catch (e) { /* on garde la valeur du scan */ }
    const freeSlots = ge ? (ge.slots || []).filter(s => s.state === "EMPTY").length : (data.free_slots ?? data.slots);
    const budgetPerSlot = freeSlots > 0 ? Math.floor(cash / freeSlots) : 0;
    const bankAgeTxt = bankAgeMin < 1 ? "à l'instant" : "il y a " + Math.round(bankAgeMin) + " min";
    const bankWarn = bankAgeMin > 30
      ? ' <span class="warn">⚠ banque ' + bankAgeTxt + ' — ouvre ta banque en jeu</span>'
      : ' <span class="muted">(banque ' + bankAgeTxt + ')</span>';

    document.getElementById("cards").innerHTML = `
      <div class="card"><div class="label">Cash libre${cashSrc === "live" ? " · banque + inventaire" : ""}${bankWarn}</div><div class="value">${fmt(cash)} gp</div></div>
      ${ge ? `<div class="card"><div class="label">Bloqué dans le GE</div><div class="value">${fmt(ge.gp_locked_in_buys)} gp</div></div>` : ""}
      <div class="card"><div class="label">Slots libres</div><div class="value">${freeSlots} / ${data.slots}</div></div>
      <div class="card"><div class="label">Budget / slot libre</div><div class="value">${fmt(budgetPerSlot)} gp</div></div>
      <div class="card"><div class="label">Magic</div><div class="value">${data.magic_level}</div></div>
      ${posCardHtml}
    `;

    renderTable("fastest", data.flips_fastest_fill || [], FLIP_COLS);
    renderTable("yield", data.flips_best_gp_per_hour || [], FLIP_COLS);
    renderTable("alch", data.alch || [], ALCH_COLS);

    try {
      const diariesRes = await fetch("diaries.json?_=" + Date.now());
      renderDiaries(await diariesRes.json());
    } catch (e) { /* diaries.json not there yet */ }

    try {
      const caRes = await fetch("combat_achievements.json?_=" + Date.now());
      renderCA(await caRes.json());
    } catch (e) { /* combat_achievements.json not there yet */ }

    const tipEl = document.getElementById("wildyTip");
    tipEl.style.display = "block";
    tipEl.innerHTML = "💡 <b>Astuce:</b> ouvre ta banque en jeu de temps en temps pour que le cash affiché reste à jour. Les prix viennent de l'API du OSRS Wiki — vérifie toujours le prix en jeu avant d'acheter.";

    refreshEl.textContent = "Dernière lecture: " + new Date().toLocaleTimeString("fr-CA");
  } catch (e) {
    refreshEl.textContent = "Erreur de lecture de market.json: " + e;
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def write_dashboard(export_dir):
    dashboard_path = export_dir / "dashboard.html"
    with open(dashboard_path, "w", encoding="utf-8") as f:
        f.write(DASHBOARD_HTML)
    return dashboard_path


def start_dashboard_server(export_dir, port):
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):   # pas de spam dans la console (le dashboard relit toutes les 5 s)
            pass

    handler = functools.partial(QuietHandler, directory=str(export_dir))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def run_loop(interval_sec):
    print(f"Watch mode: re-scanning every {interval_sec}s. Ctrl+C to stop.\n")
    while True:
        started = time.time()
        try:
            main()
        except Exception:
            # keep looping even if one run fails (e.g. wiki API hiccup) —
            # print the error so you can see what happened, then retry next cycle
            traceback.print_exc()
        elapsed = time.time() - started
        sleep_for = max(0, interval_sec - elapsed)
        print(f"\n--- next scan in {int(sleep_for)}s ---\n")
        time.sleep(sleep_for)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--loop",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Re-run automatically every SECONDS (e.g. --loop 60 for every minute). "
             "Omit this flag to just run once.",
    )
    parser.add_argument(
        "--serve",
        type=int,
        default=0,
        metavar="PORT",
        help="Also serve a live-updating dashboard at http://localhost:PORT/dashboard.html "
             "(reads market.json every 5s in your browser). Combine with --loop so the "
             "data actually keeps refreshing.",
    )
    parser.add_argument(
        "--char",
        type=str,
        default=None,
        metavar="NAME",
        help="Which exported character to scan (only needed if you have more than one "
             "and don't want to be asked / want to switch).",
    )
    args = parser.parse_args()

    setup_paths(args.char)
    print(f"Using character: {CHAR_NAME}  ({EXPORT_DIR})\n")

    if args.serve > 0:
        dash_path = write_dashboard(EXPORT_DIR)
        start_dashboard_server(EXPORT_DIR, args.serve)
        print(f"Dashboard live: http://localhost:{args.serve}/dashboard.html")
        print(f"(serving {EXPORT_DIR})\n")

    if args.loop > 0:
        run_loop(args.loop)
    else:
        main()
