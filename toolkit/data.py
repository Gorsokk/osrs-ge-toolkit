"""Shared, read-only access to the game exports and to live OSRS Wiki prices.

Used by the Claude connector (mcp_server.py), the dashboard server and the scanner.
Nothing here writes to the game folder except the Wiki item-mapping cache in DATA_DIR.
"""
import difflib
import json
import threading
import time
import urllib.request
from datetime import datetime, timezone

from paths import RUNELITE_ROOT, DATA_DIR, USER_AGENT

WIKI_API = "https://prices.runescape.wiki/api/v1/osrs"
NATURE_RUNE_ID = 561
COINS_ID = 995
MAPPING_CACHE = DATA_DIR / "_mapping_cache.json"
MAPPING_MAX_AGE = 7 * 24 * 3600

# file name -> (what it is, which plugin writes it)
EXPORT_FILES = {
    "character.json": ("stats, world, membership", "Character Export"),
    "bank.json": ("bank (updates when you open your bank)", "Character Export"),
    "inventory.json": ("inventory", "Character Export"),
    "equipment.json": ("worn equipment", "Character Export"),
    "quests.json": ("quests", "Character Export"),
    "diaries.json": ("achievement diaries", "Character Export"),
    "combat_achievements.json": ("combat achievements", "Character Export"),
    "position.json": ("live position", "Position Exporter"),
    "ge_offers.json": ("Grand Exchange offers", "Position Exporter"),
    "market.json": ("flip/alch scan", "OSRS GE Toolkit"),
}


# ------------------------------------------------------------------ basics ---
def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_ts(value):
    """ISO timestamp (with Z, offset, or 7-digit fractions) -> aware datetime, or None."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    # Python < 3.11 only accepts up to 6 fractional digits
    if "." in s:
        head, rest = s.split(".", 1)
        # only the digits right after the dot are fractions; keep the offset (e.g. -07:00)
        i = 0
        while i < len(rest) and rest[i].isdigit():
            i += 1
        digits, tz = rest[:i], rest[i:]
        s = f"{head}.{digits[:6]}{tz}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_minutes(value):
    dt = parse_ts(value)
    if not dt:
        return None
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 60, 1)


def human_age(minutes):
    if minutes is None:
        return "unknown"
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return f"{int(minutes)} min ago"
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f} h ago"
    return f"{minutes / 1440:.1f} days ago"


def tax(price):
    """GE tax: 2% (rounded down), 0 under 50 gp, capped at 5M."""
    if price < 50:
        return 0
    return min(int(price * 0.02), 5_000_000)


def net_sell(price):
    return price - tax(price)


# -------------------------------------------------------------- characters ---
def list_characters():
    """Exported characters, most recently active first."""
    if not RUNELITE_ROOT.exists():
        return []
    chars = []
    for p in RUNELITE_ROOT.iterdir():
        if not p.is_dir():
            continue
        mtimes = [f.stat().st_mtime for f in p.glob("*.json")]
        if mtimes:
            chars.append((max(mtimes), p.name))
    return [name for _, name in sorted(chars, reverse=True)]


def resolve_character(name=None):
    """Pick the requested character (case-insensitive), else the chosen one in settings,
    else the most recently active. Returns (name, folder) or (None, None)."""
    chars = list_characters()
    if not chars:
        return None, None
    if name:
        for c in chars:
            if c.lower() == str(name).strip().lower():
                return c, RUNELITE_ROOT / c
        return None, None
    try:
        import settings  # optional: the user's choice in the dashboard
        chosen = settings.load().get("character")
        if chosen in chars:
            return chosen, RUNELITE_ROOT / chosen
    except Exception:
        pass
    return chars[0], RUNELITE_ROOT / chars[0]


def read_export(folder, filename):
    """(data, age_in_minutes). Age comes from the file's own exported_at when present."""
    path = folder / filename
    data = load_json(path)
    if data is None:
        return None, None
    age = age_minutes(data.get("exported_at") or data.get("generated_at")) if isinstance(data, dict) else None
    if age is None:
        try:
            age = round((time.time() - path.stat().st_mtime) / 60, 1)
        except OSError:
            pass
    return data, age


# ---------------------------------------------------------------- prices ---
class Prices:
    """Tiny cached client for the OSRS Wiki real-time prices API."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cache = {}  # endpoint -> (fetched_at, data)
        self._mapping = None
        self._by_name = None

    def _get(self, endpoint, max_age):
        with self._lock:
            hit = self._cache.get(endpoint)
            if hit and time.time() - hit[0] < max_age:
                return hit[1]
        data = fetch_json(f"{WIKI_API}/{endpoint}")
        data = data.get("data", data)
        with self._lock:
            self._cache[endpoint] = (time.time(), data)
        return data

    def latest(self):
        return self._get("latest", 60)

    def m5(self):
        return self._get("5m", 120)

    def h1(self):
        return self._get("1h", 600)

    def h24(self):
        return self._get("24h", 1800)

    def timeseries(self, item_id, step="6h"):
        return self._get(f"timeseries?timestep={step}&id={item_id}", 1800)

    def mapping(self):
        if self._mapping is None:
            cached = None
            try:
                if MAPPING_CACHE.exists() and time.time() - MAPPING_CACHE.stat().st_mtime < MAPPING_MAX_AGE:
                    cached = load_json(MAPPING_CACHE)
            except OSError:
                pass
            if not cached:
                cached = fetch_json(f"{WIKI_API}/mapping")
                try:
                    with open(MAPPING_CACHE, "w", encoding="utf-8") as f:
                        json.dump(cached, f)
                except OSError:
                    pass
            self._mapping = {m["id"]: m for m in cached}
            self._by_name = {m["name"].lower(): m for m in cached}
        return self._mapping

    # auto-pick a fuzzy match only when it is close AND clearly ahead of the runner-up;
    # otherwise return suggestions so the caller (Claude, !ge) can ask instead of guessing
    FUZZY_ACCEPT = 0.85
    FUZZY_LEAD = 0.05

    def find_item(self, query):
        """Item by id or name. Returns (item, suggestions).

        Order: id, exact name, exact name with the plural trimmed, unique prefix, then a
        fuzzy match only if it is unambiguous. A wrong item is worse than no item (voice
        mishearings like "Camulet" used to land on a random amulet), so when in doubt it
        returns None plus the closest names."""
        item, sugg, _how = self.match_item(query)
        return item, sugg

    def match_item(self, query):
        """Like find_item but also says how it matched: id, exact, plural, prefix, fuzzy or None."""
        mapping = self.mapping()
        q = " ".join(str(query).split())
        if q.isdigit() and int(q) in mapping:
            return mapping[int(q)], [], "id"
        ql = q.lower()
        if not ql:
            return None, [], None
        if ql in self._by_name:
            return self._by_name[ql], [], "exact"
        for singular in (ql[:-2] if ql.endswith("es") else None, ql[:-1] if ql.endswith("s") else None):
            if singular and singular in self._by_name:       # "nature runes" -> "Nature rune"
                return self._by_name[singular], [], "plural"
        starts = sorted((n for n in self._by_name if n.startswith(ql)), key=len)
        contains = sorted((n for n in self._by_name if ql in n and n not in starts), key=len)
        if len(starts) == 1:   # e.g. "Draynor manor teleport" -> "Draynor manor teleport (tablet)"
            return self._by_name[starts[0]], [], "prefix"
        if starts and len(starts[0]) - len(ql) <= 3:
            return (self._by_name[starts[0]],
                    [self._by_name[n]["name"] for n in (starts[1:] + contains)[:5]], "prefix")
        words = ql.split()
        if len(words) > 1:   # shorthand: every spoken word is in exactly one name ("camphor kit")
            by_words = [n for n in self._by_name if all(w in n.replace("(", " ").split() for w in words)]
            if len(by_words) == 1:
                return self._by_name[by_words[0]], [], "words"
        scored = sorted(((difflib.SequenceMatcher(None, ql, n).ratio(), n) for n in self._by_name), reverse=True)[:6]
        fuzzy = [n for r, n in scored if r >= 0.6]
        candidates = list(dict.fromkeys(starts + contains + fuzzy))
        if not starts and not contains and scored:
            best, runner = scored[0][0], (scored[1][0] if len(scored) > 1 else 0)
            if best >= self.FUZZY_ACCEPT and best - runner >= self.FUZZY_LEAD:
                return self._by_name[scored[0][1]], [], "fuzzy"
        if len(candidates) == 1 and (starts or contains):
            return self._by_name[candidates[0]], [], "prefix"
        return None, [self._by_name[n]["name"] for n in candidates[:8]], None

    def unit_value(self, item_id):
        """Conservative gp value of one item: the lower of the last instant buy/sell prices."""
        if item_id == COINS_ID:
            return 1
        p = self.latest().get(str(item_id)) or {}
        lo, hi = p.get("low"), p.get("high")
        if lo and hi:
            return min(lo, hi)
        return lo or hi

    def nature_rune(self):
        p = self.latest().get(str(NATURE_RUNE_ID)) or {}
        return p.get("high") or p.get("low")


PRICES = Prices()
