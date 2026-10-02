"""Income per played hour: wealth snapshots taken while the game is logged in.

Every minute while a character is playing, one line is appended to
%APPDATA%\\OSRS GE Toolkit\\income\\<character>.jsonl:

    {"ts": ..., "cash": ..., "items": ..., "bank_ts": ...}

- cash  = coins and platinum tokens (1 token = 1,000 coins) in bank + inventory, plus the coins still locked in
          unfilled GE buy offers
- items = everything else at live prices: bank, inventory, worn equipment, unsold GE items

Counting rule: income counts when it lands in the bank or inventory. The GE export does not
say whether a filled offer was already collected, so only the UNFILLED part of each offer
is counted (counting the filled part would count collected coins twice).

Played time = time between samples that are at most GAP_SEC apart, so logged-out time is
never counted. gp/h = wealth change over the period / played hours.
"""
import json
import threading
import time
from datetime import datetime

import data
from data import PRICES, COINS_ID, PLATINUM_TOKEN_ID, PLATINUM_TOKEN_VALUE, read_export
from paths import DATA_DIR, export_path

HISTORY_DIR = DATA_DIR / "income"
SAMPLE_SEC = 60
GAP_SEC = 5 * 60          # samples further apart than this = logged out in between
ONLINE_SEC = 3 * 60       # the game is "playing" when an export changed this recently
KEEP_DAYS = 60
LIVE_FILES = ("character.json", "inventory.json", "position.json", "ge_offers.json")
_lock = threading.Lock()


def _history_file(name):
    safe = "".join(c for c in name if c.isalnum() or c in " -_").strip() or "unknown"
    return HISTORY_DIR / f"{safe}.jsonl"


def seconds_since_played(folder):
    """Seconds since any live export changed (None if none exist)."""
    newest = None
    for fn in LIVE_FILES:
        try:
            m = export_path(folder, fn).stat().st_mtime
        except OSError:
            continue
        newest = m if newest is None else max(newest, m)
    return None if newest is None else max(0.0, time.time() - newest)


def is_playing(folder):
    s = seconds_since_played(folder) if folder else None
    return s is not None and s < ONLINE_SEC


# ------------------------------------------------------------------ snapshot ---
def snapshot(folder):
    """{'cash', 'items', 'bank_ts'} for one character folder, or None without a bank export."""
    bank, _ = read_export(folder, "bank.json")
    if not bank:
        return None
    cash = items = 0

    def add(entries):
        nonlocal cash, items
        for it in entries or []:
            iid, qty = it.get("id"), it.get("quantity", 1) or 0
            if iid is None or qty <= 0:
                continue
            if iid == COINS_ID:
                cash += qty
            elif iid == PLATINUM_TOKEN_ID:
                cash += qty * PLATINUM_TOKEN_VALUE   # platinum is cash: the GE pays in both, so a swap is not income
            else:
                items += (PRICES.unit_value(iid) or 0) * qty

    add(bank.get("items"))
    inv, _ = read_export(folder, "inventory.json")
    add((inv or {}).get("items"))
    eq, _ = read_export(folder, "equipment.json")
    add((eq or {}).get("items"))
    ge, _ = read_export(folder, "ge_offers.json")
    for s in (ge or {}).get("slots", []):
        if s.get("state") == "EMPTY" or s.get("complete") or s.get("cancelled"):
            continue            # finished offers: their result is counted once collected
        left = max(0, (s.get("quantity_total") or 0) - (s.get("quantity_filled") or 0))
        if s.get("type") == "BUY":
            cash += left * (s.get("price") or 0)
        elif s.get("type") == "SELL" and s.get("item_id") is not None:
            items += left * (PRICES.unit_value(s["item_id"]) or 0)
    try:
        bank_ts = int(export_path(folder, "bank.json").stat().st_mtime)
    except OSError:
        bank_ts = None
    return {"cash": int(cash), "items": int(items), "bank_ts": bank_ts}


def record(name, folder):
    """Append one sample if the character is playing. Returns the sample or None."""
    if not name or not folder or not is_playing(folder):
        return None
    snap = snapshot(folder)
    if snap is None:
        return None
    row = {"ts": int(time.time()), **snap}
    with _lock:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        with open(_history_file(name), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    return row


def load(name, since=0):
    rows = []
    try:
        with open(_history_file(name), "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue            # half-written line after a crash
                if r.get("ts", 0) >= since:
                    rows.append(r)
    except OSError:
        pass
    return rows


def prune(name):
    """Drop samples older than KEEP_DAYS (called at startup)."""
    path = _history_file(name)
    if not path.exists():
        return
    rows = load(name, since=time.time() - KEEP_DAYS * 86400)
    with _lock:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        tmp.replace(path)


def prune_all():
    if HISTORY_DIR.exists():
        for p in HISTORY_DIR.glob("*.jsonl"):
            prune(p.stem)


# ------------------------------------------------------------------- summary ---
PERIODS = ("session", "today", "24h", "7d", "30d")


def _since(period, rows):
    now = time.time()
    if period == "today":
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    if period == "24h":
        return now - 86400
    if period == "7d":
        return now - 7 * 86400
    if period == "30d":
        return now - 30 * 86400
    # session: back to the last logged-out gap
    start = rows[-1]["ts"] if rows else now
    for a, b in zip(reversed(rows[:-1]), reversed(rows[1:])):
        if b["ts"] - a["ts"] > GAP_SEC:
            break
        start = a["ts"]
    return start


def _flip_profit(since):
    st = data.load_json(DATA_DIR / "_alerts_state.json", {}) or {}
    return int(sum(r.get("profit", 0) for r in st.get("realized", []) if r.get("ts", 0) >= since))


def summary(name, period="today"):
    period = period if period in PERIODS else "today"
    window = {"session": 2, "today": 2, "24h": 2, "7d": 8, "30d": 31}[period]
    rows = load(name, since=time.time() - window * 86400)
    since = _since(period, rows)
    rows = [r for r in rows if r["ts"] >= since]
    out = {"character": name, "period": period, "samples": len(rows)}
    if len(rows) < 2:
        out["note"] = ("Not enough data yet: the toolkit records your wealth every minute while you play. "
                       "Play a few minutes with the toolkit running.")
        return out
    played, sessions = 0, 1
    for a, b in zip(rows, rows[1:]):
        gap = b["ts"] - a["ts"]
        if gap <= GAP_SEC:
            played += gap
        else:
            sessions += 1
    first, last = rows[0], rows[-1]
    cash_change = last["cash"] - first["cash"]
    items_change = last["items"] - first["items"]
    total = cash_change + items_change
    hours = played / 3600
    flips = _flip_profit(first["ts"])
    out.update({
        "played_hours": round(hours, 2),
        "sessions": sessions,
        "from": datetime.fromtimestamp(first["ts"]).strftime("%Y-%m-%d %H:%M"),
        "to": datetime.fromtimestamp(last["ts"]).strftime("%Y-%m-%d %H:%M"),
        "wealth_start": first["cash"] + first["items"],
        "wealth_now": last["cash"] + last["items"],
        "wealth_change": total,
        "cash_change": cash_change,
        "items_value_change": items_change,
        "gp_per_hour": int(total / hours) if hours >= 0.05 else None,
        "ge_flip_profit": flips,
        "ge_flip_gp_per_hour": int(flips / hours) if hours >= 0.05 else None,
        "notes": [],
    })
    if hours < 0.25:
        out["notes"].append("Under 15 minutes played: gp/h is still very noisy.")
    bank_age_min = (time.time() - last["bank_ts"]) / 60 if last.get("bank_ts") else None
    if bank_age_min and bank_age_min > 60:
        out["notes"].append(f"Bank not opened for {data.human_age(bank_age_min)}: items collected to the bank "
                            "since then are not counted yet. Open the bank for an exact number.")
    out["notes"].append("items_value_change includes price moves of items you hold, not only earnings; "
                        "cash_change is coins only.")
    return out
