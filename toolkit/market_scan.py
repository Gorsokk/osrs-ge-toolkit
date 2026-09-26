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
    """Character to scan: --char / explicit name, else the one chosen in the dashboard,
    else the most recently played. Never prompts (the app runs without a console)."""
    import data
    name, folder = data.resolve_character(char_arg)
    if not folder:
        raise RuntimeError(
            f"No game data in {RUNELITE_ROOT}. Install the RuneLite plugin 'Character Export' "
            "(Plugin Hub), log in and open your bank once.")
    return folder


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

    print(f"Mode: {mode} | Magic: {magic_level} | Free cash: {capital_gp:,} gp "
          f"| Free slots: {free_slots}/{GE_SLOTS} | Budget/free slot: {budget_per_slot:,} gp")
    if ge_state:
        print(f"GE: {ge_state['active']} actives, {ge_state['ready_to_collect']} to collect, "
              f"{ge_state['gp_locked_in_buys']:,} gp locked in buys")
    if bank_age_min is not None and bank_age_min > 30:
        print(f"WARNING: bank.json is {bank_age_min:.0f} min old -- open your bank in game to refresh cash.")

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
    parser = argparse.ArgumentParser(description="GE flip/alch scanner (normally started by the toolkit app).")
    parser.add_argument("--loop", type=int, default=0, metavar="SECONDS", help="re-scan every SECONDS")
    parser.add_argument("--char", default=None, help="character name (default: most recently played)")
    args = parser.parse_args()
    setup_paths(args.char)
    print(f"Using character: {CHAR_NAME}  ({EXPORT_DIR})\n")
    if args.loop > 0:
        run_loop(args.loop)
    else:
        main()
