"""
osrs_alerts.py - Pop-ups Windows pour les evenements OSRS a ne pas manquer.

Surveille en boucle et affiche une notification Windows quand:
  1. MES OFFRES GE  - une offre se remplit (partiel ou complet), ou ton prix est
                      devenu irrealiste (vente trop haute / achat trop bas vs le marche),
                      ou un seuil que tu as fixe dans alerts_config.json est franchi.
  2. VRAIS FLIPS    - marge nette APRES taxe 2 %, calculee sur le volume reel
                      (moyennes 1h ET 24h, les deux cotes du marche), pas sur des pics.
  3. CRASH / PIC    - un objet tres echange bouge de plus de X % vs sa moyenne 24h,
                      + prix du bond sous ton seuil.
  4. NEWS OSRS      - nouveau post sur les news officielles (mise a jour, event, etc.).
  5. STOCKAGE       - un materiau de leveling (os, logs, runes, herbes, bars...) est
                      dans les 20 % les moins chers des 90 derniers jours.

Chaque alerte est aussi ecrite dans:
  <.runelite>\\character-exporter\\<perso>\\alerts.jsonl   (Claude peut la relire)

Usage:
    python osrs_alerts.py            # boucle infinie (Ctrl+C pour arreter)
    python osrs_alerts.py --test     # envoie une notification test et quitte
    python osrs_alerts.py --once     # un seul passage (debug)

Pop-ups: utilise le paquet 'winotify' (notifications Windows 10/11 natives).
run_alerts.bat l'installe automatiquement. Sans lui, le script utilise une
simple fenetre de message Windows a la place.
"""

import argparse
import json
import re
import sys
import threading
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

# ------------------------------------------------------------------ chemins ---
from i18n import t
from paths import RUNELITE_ROOT, DATA_DIR, USER_AGENT  # %APPDATA%\\OSRS GE Toolkit
CHAR_CHOICE_FILE = DATA_DIR / "_last_character.txt"
CONFIG_FILE = DATA_DIR / "alerts_config.json"
STATE_FILE = DATA_DIR / "_alerts_state.json"
HISTORY_CACHE = DATA_DIR / "_alerts_history_cache.json"

WIKI_API = "https://prices.runescape.wiki/api/v1/osrs"
NEWS_RSS = "https://secure.runescape.com/m=news/latest_news.rss?oldschool=true"
BOND_ID = 13190

# ------------------------------------------------------ config par defaut ---
# Cree alerts_config.json au premier lancement. Modifie ce fichier-la, pas le script.
DEFAULT_CONFIG = {
    "_help": "Edit these from the dashboard Settings page. Set enabled to false to turn a category off.",
    "poll_seconds": 60,

    "ge_offers": {
        "enabled": True,
        "unrealistic_pct": 3.0,
        "unrealistic_after_min": 30,
        "partial_milestones_pct": [25, 50, 75],
        "watch": {}
    },

    "flips": {
        "enabled": True,
        "min_profit_per_cycle": 20000,
        "min_roi_pct": 1.5,
        "min_volume_1h_each_side": 300,
        "max_capital_per_slot": 500000,
        "volume_share": 0.10,
        "cooldown_hours": 4,
        "max_alerts_per_scan": 3,
        "min_price": 10,
        "min_margin_gp": 3,
        "members": True
    },

    "moves": {
        "enabled": True,
        "min_move_pct": 12.0,
        "min_daily_traded_gp": 200000000,
        "min_price": 50,
        "cooldown_hours": 6,
        "max_alerts_per_scan": 3,
        "bond_enabled": True,
        "bond_alert_pct_below_7d": 3.0,
        "bond_alert_below": 0
    },

    "news": {
        "enabled": True,
        "check_every_min": 15,
        "priority_keywords": ["game update", "leagues", "deadman", "wilderness", "pvp", "bond",
                               "limited", "event", "double", "bonus", "grid", "poll", "sailing"]
    },

    "stockpile": {
        "enabled": True,
        "percentile": 20,
        "min_discount_pct": 8.0,
        "refresh_history_hours": 6,
        "cooldown_hours": 12,
        "min_price": 20,
        "items": [
            "Big bones", "Dragon bones", "Babydragon bones", "Wyrm bones",
            "Logs", "Oak logs", "Willow logs", "Maple logs", "Yew logs",
            "Plank", "Oak plank", "Teak plank",
            "Iron ore", "Coal", "Iron bar", "Steel bar", "Mithril bar", "Gold bar",
            "Uncut sapphire", "Uncut emerald", "Uncut ruby", "Leather", "Green dragon leather",
            "Molten glass", "Bow string", "Feather", "Headless arrow",
            "Nature rune", "Law rune", "Death rune", "Chaos rune", "Blood rune",
            "Cosmic rune", "Fire rune", "Air rune", "Water rune", "Earth rune",
            "Pure essence",
            "Grimy guam leaf", "Grimy marrentill", "Grimy tarromin", "Grimy harralander",
            "Grimy ranarr weed", "Eye of newt", "Limpwurt root", "Red spiders' eggs",
            "Vial of water",
            "Raw lobster", "Raw swordfish", "Raw monkfish"
        ]
    }
}

# ------------------------------------------------------------------ utils ---
def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def fetch(url, as_json=True):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = r.read()
    return json.loads(data.decode("utf-8")) if as_json else data


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def deep_merge(default, user):
    """Garde les valeurs de l'utilisateur, ajoute les nouvelles cles par defaut."""
    if not isinstance(default, dict) or not isinstance(user, dict):
        return user
    out = dict(default)
    for k, v in user.items():
        if k in ("watch", "items"):   # listes perso: on garde exactement celles de l'utilisateur
            out[k] = v
        else:
            out[k] = deep_merge(default.get(k), v) if k in default else v
    return out


def tax(price):
    if price < 50:
        return 0
    return min(int(price * 0.02), 5_000_000)


def net_sell(price):
    return price - tax(price)


def breakeven(cost):
    """Plus petit prix de vente qui ne perd pas d'argent apres la taxe."""
    p = int(cost)
    while net_sell(p) < cost:
        p += 1
    return p


def gp(n):
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if abs(n) >= 10_000:
        return f"{n/1000:.1f}k"
    return f"{n:,}".replace(",", " ")


def percentile(values, pct):
    v = sorted(values)
    if not v:
        return None
    k = (len(v) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


# ---------------------------------------------------------- notifications ---
try:
    from winotify import Notification, audio as win_audio  # type: ignore
    HAVE_WINOTIFY = True
except Exception:
    HAVE_WINOTIFY = False


def _messagebox(title, body):
    try:
        import ctypes
        # MB_OK | MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
        ctypes.windll.user32.MessageBoxW(0, body, title, 0x40 | 0x40000 | 0x10000)
    except Exception:
        pass


class Notifier:
    def __init__(self, export_dir):
        self.log_path = export_dir / "alerts.jsonl" if export_dir else DATA_DIR / "alerts.jsonl"

    def send(self, category, title, body, important=False, url=None):
        full_title = ("!! " if important else "") + title
        log(f"[{category}] {full_title} | {body}")
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "category": category,
                                    "title": title, "body": body, "important": important,
                                    "url": url}, ensure_ascii=False) + "\n")
        except Exception:
            pass
        if sys.platform != "win32":
            return
        if HAVE_WINOTIFY:
            try:
                n = Notification(app_id="OSRS GE Toolkit", title=full_title, msg=body,
                                 duration="long" if important else "short")
                if url:
                    n.add_actions(label=t("open"), launch=url)
                n.set_audio(win_audio.LoopingAlarm if important else win_audio.Default, loop=False)
                n.show()
                return
            except Exception:
                pass
        if important:
            try:
                import winsound
                winsound.MessageBeep(0x30)
            except Exception:
                pass
        threading.Thread(target=_messagebox, args=(full_title, body), daemon=True).start()


# ------------------------------------------------------------ export dir ---
def find_export_dir():
    """Selected character (dashboard setting), else the most recently played one."""
    import data
    return data.resolve_character(None)[1]


# ---------------------------------------------------------------- moteur ---
class Alerts:
    def __init__(self, cfg, export_dir):
        self.cfg = cfg
        self.export_dir = export_dir
        self.notify = Notifier(export_dir).send
        self.state = load_json(STATE_FILE, {}) or {}
        self.state.setdefault("cooldowns", {})
        self.state.setdefault("offers", {})
        self.state.setdefault("news_seen", [])
        self.state.setdefault("unrealistic_since", {})
        self.state.setdefault("positions", {})
        self.state.setdefault("realized", [])
        self.state["realized"] = [r for r in self.state["realized"] if time.time() - r["ts"] < 7 * 86400]
        self._offers_mtime = None
        self._first_offer_scan = not self.state["offers"]
        self.mapping = {}
        self.by_name = {}
        self.latest, self.m5, self.h1, self.h24 = {}, {}, {}, {}
        self.last_5m = self.last_1h = self.last_24h = self.last_news = 0
        self.bond_avg_7d = None
        self.last_bond_hist = 0
        self.first_news_run = not self.state["news_seen"]

    # ----- helpers
    def cooldown_ok(self, key, hours):
        t = self.state["cooldowns"].get(key, 0)
        if time.time() - t >= hours * 3600:
            self.state["cooldowns"][key] = time.time()
            return True
        return False

    def name(self, item_id):
        m = self.mapping.get(int(item_id))
        return m["name"] if m else f"item {item_id}"

    def refresh_prices(self):
        now = time.time()
        if not self.mapping:
            mp = fetch(f"{WIKI_API}/mapping")
            self.mapping = {m["id"]: m for m in mp}
            self.by_name = {m["name"].lower(): m for m in mp}
        self.latest = fetch(f"{WIKI_API}/latest")["data"]
        if now - self.last_5m >= 290:
            self.m5 = fetch(f"{WIKI_API}/5m")["data"]
            self.last_5m = now
        if now - self.last_1h >= 900:
            self.h1 = fetch(f"{WIKI_API}/1h")["data"]
            self.last_1h = now
        if now - self.last_24h >= 3600:
            self.h24 = fetch(f"{WIKI_API}/24h")["data"]
            self.last_24h = now

    # ----- 1. offres GE
    def check_offers(self, with_watch=True):
        """Suit tes offres GE. Tout est automatique:
        - chaque ACHAT rempli ajoute la quantite et le cout reel a tes 'positions'
        - chaque VENTE remplie calcule ton profit net (taxe incluse) avec ce cout
        - des qu'une position existe, tu as une alerte quand le marche permet de revendre avec profit
        """
        c = self.cfg["ge_offers"]
        if not c["enabled"] or not self.export_dir:
            return
        path = self.export_dir / "ge_offers.json"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime and mtime != self._offers_mtime:
            self._offers_mtime = mtime
            ge = load_json(path)
            if ge:
                self._process_offers(ge)
        if with_watch:
            self._check_watch()
            self._check_positions()

    def _process_offers(self, ge):
        c = self.cfg["ge_offers"]
        seen = self.state["offers"]
        first = self._first_offer_scan
        self._first_offer_scan = False
        for s in ge.get("slots", []):
            slot = str(s.get("slot"))
            if s.get("state") == "EMPTY":
                seen.pop(slot, None)
                self.state["unrealistic_since"].pop(slot, None)
                continue
            key = f'{s.get("item_id")}:{s.get("type")}:{s.get("price")}:{s.get("quantity_total")}'
            prev = seen.get(slot)
            if prev is None or prev.get("key") != key:
                # nouvelle offre: si le script tournait deja, on part de zero (capte les remplissages instantanes)
                prev = None if first else {"key": key, "filled": 0, "spent": 0}
            filled = s.get("quantity_filled", 0) or 0
            spent = s.get("spent", 0) or 0
            total = s.get("quantity_total", 0)
            name = s.get("item_name", "?")
            if prev is not None and filled > prev.get("filled", 0):
                dq = filled - prev.get("filled", 0)
                dspent = spent - prev.get("spent", 0)
                if dspent <= 0:
                    dspent = dq * s.get("price", 0)
                avg_lot = dspent / dq
                done = s.get("complete") or filled >= total
                # la comptabilite (cout, profit) se fait a CHAQUE remplissage,
                # mais on ne notifie qu'au franchissement d'un palier (anti-spam)
                pct_prev = prev.get("filled", 0) / total * 100 if total else 0
                pct_now = filled / total * 100 if total else 0
                crossed = [m for m in c.get("partial_milestones_pct", [25, 50, 75]) if pct_prev < m <= pct_now]
                should_notify = done or bool(crossed)
                if s.get("type") == "BUY":
                    body = self._on_buy(name, s.get("item_id"), dq, dspent)
                    if should_notify:
                        self.notify("GE", t("buy_done", name=name) if done else t("buy_pct", name=name, pct=int(pct_now)),
                                    t("bought_body", filled=filled, total=total, avg=gp(avg_lot), extra=body),
                                    important=done)
                else:
                    body = self._on_sell(name, dq, avg_lot)
                    if should_notify:
                        self.notify("GE", t("sell_done", name=name) if done else t("sell_pct", name=name, pct=int(pct_now)),
                                    t("sold_body", filled=filled, total=total, avg=gp(avg_lot), extra=body),
                                    important=done)
            seen[slot] = {"key": key, "filled": filled, "spent": spent}
            self._check_unrealistic(slot, s)
        save_json(STATE_FILE, self.state)

    def _cost_of(self, name):
        pos = self.state["positions"].get(name)
        if pos and pos.get("qty", 0) > 0:
            return pos["cost_total"] / pos["qty"]
        w = self._watch(name)
        return w.get("cost") if w else None

    def _on_buy(self, name, item_id, dq, dspent):
        pos = self.state["positions"].setdefault(name, {"item_id": item_id, "qty": 0, "cost_total": 0})
        pos["qty"] += dq
        pos["cost_total"] += dspent
        avg = pos["cost_total"] / pos["qty"]
        be = breakeven(avg)
        h1 = self.h1.get(str(item_id)) or {}
        d = self.h24.get(str(item_id)) or {}
        target = min([p for p in (h1.get("avgHighPrice"), d.get("avgHighPrice")) if p] or [be])
        tip = (t("resell_tip", target=gp(target), profit=gp((net_sell(target) - avg) * pos['qty']), qty=pos['qty'])
               if target > be else t("wait_tip"))
        return t("cost_line", avg=gp(avg), be=gp(be), tip=tip)

    def _on_sell(self, name, dq, avg_sell):
        cost = self._cost_of(name)
        if cost is None:
            return t("unknown_cost")
        profit = (net_sell(avg_sell) - cost) * dq
        self.state["realized"].append({"ts": time.time(), "item": name, "profit": profit})
        pos = self.state["positions"].get(name)
        if pos and pos.get("qty", 0) > 0:
            used = min(dq, pos["qty"])
            pos["cost_total"] -= cost * used
            pos["qty"] -= used
            if pos["qty"] <= 0:
                self.state["positions"].pop(name, None)
        day = sum(r["profit"] for r in self.state["realized"] if time.time() - r["ts"] < 86400)
        return t("profit_line", profit=gp(profit), day=gp(day))

    def _check_positions(self):
        """Alerte quand une position achetee peut etre revendue avec profit."""
        for name, pos in list(self.state["positions"].items()):
            if pos.get("qty", 0) <= 0:
                continue
            if any(v.get("key", "").startswith(f"{pos['item_id']}:SELL:") for v in self.state["offers"].values()):
                continue      # deja en vente
            hi = (self.latest.get(str(pos["item_id"])) or {}).get("high")
            avg = pos["cost_total"] / pos["qty"]
            if hi and net_sell(hi) - avg >= max(1, avg * 0.01):
                if self.cooldown_ok(f"pos:{name}", 1):
                    self.notify("TARGET", t("sell_now", name=name),
                                t("sell_now_body", hi=gp(hi), qty=pos['qty'], avg=gp(avg),
                                  profit=gp((net_sell(hi) - avg) * pos['qty'])), important=True)

    def _watch(self, name):
        for k, v in self.cfg["ge_offers"].get("watch", {}).items():
            if k.lower() == (name or "").lower():
                return v
        return None

    def _check_unrealistic(self, slot, s):
        c = self.cfg["ge_offers"]
        if s.get("complete") or s.get("cancelled"):
            return
        lt = self.latest.get(str(s.get("item_id")))
        if not lt:
            return
        price = s.get("price", 0)
        bad = None
        if s.get("type") == "SELL" and lt.get("high"):
            ref = min(lt["high"], (self.h1.get(str(s["item_id"])) or {}).get("avgHighPrice") or lt["high"])
            if price > ref * (1 + c["unrealistic_pct"] / 100):
                bad = t("stuck_sell", price=gp(price), ref=gp(ref))
        elif s.get("type") == "BUY" and lt.get("low"):
            ref = max(lt["low"], (self.h1.get(str(s["item_id"])) or {}).get("avgLowPrice") or lt["low"])
            if price < ref * (1 - c["unrealistic_pct"] / 100):
                bad = t("stuck_buy", price=gp(price), ref=gp(ref))
        since = self.state["unrealistic_since"]
        if not bad:
            since.pop(slot, None)
            return
        since.setdefault(slot, time.time())
        if time.time() - since[slot] >= c["unrealistic_after_min"] * 60:
            if self.cooldown_ok(f"unreal:{slot}:{s.get('item_id')}:{price}", 2):
                self.notify("GE", t("stuck", name=s.get('item_name')),
                            bad + t("stuck_tail", min=c['unrealistic_after_min']))

    def _check_watch(self):
        for name, w in self.cfg["ge_offers"].get("watch", {}).items():
            m = self.by_name.get(name.lower())
            if not m:
                continue
            lt = self.latest.get(str(m["id"])) or {}
            hi, lo = lt.get("high"), lt.get("low")
            if w.get("sell_alert_at") and hi and hi >= w["sell_alert_at"]:
                if self.cooldown_ok(f"watch-sell:{name}", 1):
                    extra = t("watch_sell_extra", profit=gp(net_sell(hi) - w['cost'])) if w.get("cost") else ""
                    self.notify("TARGET", t("watch_hit", name=name, price=gp(hi)),
                                t("watch_sell_body", target=gp(w['sell_alert_at']), extra=extra), important=True)
            if w.get("buy_alert_at") and lo and lo <= w["buy_alert_at"]:
                if self.cooldown_ok(f"watch-buy:{name}", 1):
                    self.notify("TARGET", t("watch_hit", name=name, price=gp(lo)),
                                t("watch_buy_body", target=gp(w['buy_alert_at'])), important=True)

    # ----- 2. vrais flips
    def check_flips(self):
        c = self.cfg["flips"]
        if not c["enabled"] or not self.h1 or not self.h24:
            return
        found = []
        for sid, h in self.h1.items():
            m = self.mapping.get(int(sid))
            if not m or not m.get("limit") or int(sid) == BOND_ID:
                continue
            if m.get("members") and not c["members"]:
                continue
            d = self.h24.get(sid) or {}
            lo1, hi1 = h.get("avgLowPrice"), h.get("avgHighPrice")
            lo24, hi24 = d.get("avgLowPrice"), d.get("avgHighPrice")
            if not all((lo1, hi1, lo24, hi24)):
                continue
            if net_sell(hi1) <= lo1 or net_sell(hi24) <= lo24:
                continue                                  # marge inversee = bruit, pas un vrai flip
            if (h.get("lowPriceVolume") or 0) < c["min_volume_1h_each_side"] or \
               (h.get("highPriceVolume") or 0) < c["min_volume_1h_each_side"]:
                continue
            lt = self.latest.get(sid) or {}
            buy = max(lo1, lt.get("low") or lo1)          # jamais sous ce que les vendeurs acceptent vraiment
            sell = min(hi1, hi24, lt.get("high") or hi1)  # plafonne -> pas de pics
            if buy < c.get("min_price", 10):
                continue                                  # sous 10 gp, les moyennes arrondies mentent
            margin = net_sell(sell) - buy
            if margin < c.get("min_margin_gp", 2):
                continue
            roi = margin / buy * 100
            if roi < c["min_roi_pct"]:
                continue
            vol24 = (d.get("lowPriceVolume") or 0) + (d.get("highPriceVolume") or 0)
            qty = int(min(m["limit"], c["max_capital_per_slot"] // buy, vol24 / 6 * c["volume_share"]))
            profit = margin * qty
            if qty <= 0 or profit < c["min_profit_per_cycle"]:
                continue
            found.append((profit, m["name"], buy, sell, margin, qty, roi, int(sid)))
        found.sort(reverse=True)
        sent = 0
        for profit, name, buy, sell, margin, qty, roi, iid in found:
            if sent >= c["max_alerts_per_scan"]:
                break
            if self.cooldown_ok(f"flip:{iid}", c["cooldown_hours"]):
                self.notify("FLIP", t("flip_title", name=name, profit=gp(profit)),
                            t("flip_body", buy=gp(buy), sell=gp(sell), margin=margin, roi=f"{roi:.1f}",
                              qty=qty, capital=gp(buy * qty)),
                            important=profit >= 2 * c["min_profit_per_cycle"])
                sent += 1

    # ----- 3. crash / pic
    def check_moves(self):
        c = self.cfg["moves"]
        if not c["enabled"] or not self.m5 or not self.h24:
            return
        cands = []
        for sid, f in self.m5.items():
            d, h = self.h24.get(sid), self.h1.get(sid)
            if not d or not h:
                continue
            p5 = [f.get("avgLowPrice"), f.get("avgHighPrice")]
            p1 = [h.get("avgLowPrice"), h.get("avgHighPrice")]
            p24 = [d.get("avgLowPrice"), d.get("avgHighPrice")]
            if not all(p5 + p1 + p24):
                continue                                   # il faut des echanges des DEUX cotes
            vol5 = (f.get("lowPriceVolume") or 0) + (f.get("highPriceVolume") or 0)
            vol24 = (d.get("lowPriceVolume") or 0) + (d.get("highPriceVolume") or 0)
            mid5, mid1, mid24 = sum(p5) / 2, sum(p1) / 2, sum(p24) / 2
            if mid24 < c.get("min_price", 50) or vol24 * mid24 < c["min_daily_traded_gp"]:
                continue
            if vol5 < 2 * vol24 / 288:                     # 5 min au moins 2x plus actives que la normale
                continue
            move = (mid5 - mid24) / mid24 * 100
            move1 = (mid1 - mid24) / mid24 * 100
            if abs(move) < c["min_move_pct"] or move * move1 <= 0 or abs(move1) < c["min_move_pct"] / 2:
                continue                                   # confirme sur l'heure, pas juste 5 min de bruit
            cands.append((abs(move) * vol24 * mid24, sid, move, mid24, mid5, vol5))
        cands.sort(reverse=True)
        sent = 0
        for _, sid, move, mid24, mid5, vol5 in cands:
            if sent >= c.get("max_alerts_per_scan", 3):
                break
            if self.cooldown_ok(f"move:{sid}:{'up' if move > 0 else 'dn'}", c["cooldown_hours"]):
                name = self.name(sid)
                kind = t("spike") if move > 0 else t("crash")
                self.notify("MARKET", t("move_title", kind=kind, name=name, move=f"{move:+.1f}"),
                            t("move_body", avg24=gp(mid24), now=gp(mid5), vol=vol5),
                            important=abs(move) >= 2 * c["min_move_pct"])
                sent += 1

    def check_bond(self):
        """Bond alerts are independent from the crash/spike switch."""
        c = self.cfg["moves"]
        if not c.get("bond_enabled", True):
            return
        bl = (self.latest.get(str(BOND_ID)) or {}).get("low")
        if not bl:
            return
        # moyenne 7 jours rafraichie toutes les 6 h (timeseries 6h = 28 points pour 7 jours)
        if time.time() - self.last_bond_hist >= 6 * 3600 or self.bond_avg_7d is None:
            try:
                ts = fetch(f"{WIKI_API}/timeseries?timestep=6h&id={BOND_ID}")["data"][-28:]
                mids = [(p["avgLowPrice"] + p["avgHighPrice"]) / 2 for p in ts
                        if p.get("avgLowPrice") and p.get("avgHighPrice")]
                if mids:
                    self.bond_avg_7d = sum(mids) / len(mids)
                self.last_bond_hist = time.time()
            except Exception as e:
                log(f"Bond: historique indisponible ({e})")
        pct = c.get("bond_alert_pct_below_7d") or 0
        if pct and self.bond_avg_7d:
            seuil = self.bond_avg_7d * (1 - pct / 100)
            if bl <= seuil and self.cooldown_ok("bond-rel", 6):
                ecart = (self.bond_avg_7d - bl) / self.bond_avg_7d * 100
                self.notify("BOND", t("bond_rel", price=gp(bl), pct=f"{ecart:.1f}"),
                            t("bond_rel_body", avg=gp(self.bond_avg_7d), thr=f"{pct:g}", level=gp(seuil)),
                            important=True)
        fixe = c.get("bond_alert_below") or 0
        if fixe and bl <= fixe and self.cooldown_ok("bond-low", 2):
            self.notify("BOND", t("bond_fixed", price=gp(bl)), t("bond_fixed_body", target=gp(fixe)), important=True)

    # ----- 4. news
    def check_news(self):
        c = self.cfg["news"]
        if not c["enabled"] or time.time() - self.last_news < c["check_every_min"] * 60:
            return
        self.last_news = time.time()
        root = ET.fromstring(fetch(NEWS_RSS, as_json=False))
        seen = set(self.state["news_seen"])
        new = []
        for it in root.iter("item"):
            guid = (it.findtext("guid") or it.findtext("link") or "").strip()
            if guid and guid not in seen:
                new.append((guid, (it.findtext("title") or "").strip(), (it.findtext("category") or "").strip(),
                            re.sub(r"\s+", " ", (it.findtext("description") or "")).strip(),
                            (it.findtext("link") or "").strip()))
        if self.first_news_run:
            # premier lancement: on memorise l'existant sans spammer
            self.state["news_seen"] = [g for g, *_ in new][:200]
            self.first_news_run = False
            log(f"News: {len(new)} posts existants memorises (pas d'alerte).")
            return
        for guid, title, cat, desc, link in reversed(new):
            text = f"{title} {cat} {desc}".lower()
            important = any(k in text for k in c["priority_keywords"])
            self.notify("NEWS", t("news_title", title=title), f"[{cat}] {desc[:180]}", important=important, url=link)
            self.state["news_seen"].insert(0, guid)
        self.state["news_seen"] = self.state["news_seen"][:200]

    # ----- 5. stockage de materiaux de leveling
    def check_stockpile(self):
        c = self.cfg["stockpile"]
        if not c["enabled"] or not self.by_name:
            return
        cache = load_json(HISTORY_CACHE, {}) or {}
        stale = time.time() - cache.get("_ts", 0) > c["refresh_history_hours"] * 3600
        if stale:
            log("Stockage: mise a jour de l'historique 90 jours...")
            for name in c["items"]:
                m = self.by_name.get(name.lower())
                if not m:
                    continue
                try:
                    ts = fetch(f"{WIKI_API}/timeseries?timestep=6h&id={m['id']}")["data"]
                    lows = [p["avgLowPrice"] for p in ts if p.get("avgLowPrice")]
                    if len(lows) >= 40:
                        cache[str(m["id"])] = {"p": percentile(lows, c["percentile"]),
                                               "med": percentile(lows, 50),
                                               "min": min(lows)}
                except Exception as e:
                    log(f"  historique {name}: {e}")
                time.sleep(0.4)
            cache["_ts"] = time.time()
            save_json(HISTORY_CACHE, cache)
        for name in c["items"]:
            m = self.by_name.get(name.lower())
            if not m:
                continue
            hist = cache.get(str(m["id"]))
            h = self.h1.get(str(m["id"])) or {}
            now = h.get("avgLowPrice") or (self.latest.get(str(m["id"])) or {}).get("low")
            if not hist or not now or now < c.get("min_price", 20):
                continue
            disc = (hist["med"] - now) / hist["med"] * 100
            if now <= hist["p"] and disc >= c["min_discount_pct"]:
                if self.cooldown_ok(f"stock:{m['id']}", c["cooldown_hours"]):
                    lim = m.get("limit")
                    self.notify("STOCK", t("stock_title", name=name, price=gp(now)),
                                t("stock_body", disc=f"{disc:.0f}", med=gp(hist['med']), low=gp(hist['min']),
                                  limit=lim or '?'),
                                important=disc >= 2 * c["min_discount_pct"])

    # ----- boucle
    def run_once(self):
        self.refresh_prices()
        for fn in (self.check_offers, self.check_moves, self.check_bond, self.check_flips, self.check_news,
                   self.check_stockpile):
            try:
                fn()
            except Exception as e:
                log(f"Erreur dans {fn.__name__}: {e}")
        save_json(STATE_FILE, self.state)


def load_config():
    user = load_json(CONFIG_FILE)
    if user is None:
        save_json(CONFIG_FILE, DEFAULT_CONFIG)
        log(f"Config creee: {CONFIG_FILE} (modifie-la pour changer les seuils)")
        return DEFAULT_CONFIG
    merged = deep_merge(DEFAULT_CONFIG, user)
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="envoie une notification test")
    ap.add_argument("--once", action="store_true", help="un seul passage")
    args = ap.parse_args()

    export_dir = find_export_dir()
    if args.test:
        Notifier(export_dir).send("TEST", "OSRS Alertes fonctionnent", "Si tu vois ce pop-up, tout est pret.",
                                  important=True)
        time.sleep(3)
        return

    run_forever(export_dir, once=args.once)


def run_forever(export_dir=None, once=False):
    """Boucle principale des alertes (utilisee par main() et par le lanceur)."""
    if export_dir is None:
        export_dir = find_export_dir()
    cfg = load_config()
    eng = Alerts(cfg, export_dir)
    log(f"OSRS alertes demarrees. Perso: {export_dir.name if export_dir else '(aucun export trouve)'}. "
        f"Pop-ups: {'winotify' if HAVE_WINOTIFY else 'fenetre simple'}.")
    while True:
        # follow a character switch made in the dashboard
        current = find_export_dir()
        if current and current != eng.export_dir:
            log(f"Character changed: {current.name}")
            eng = Alerts(load_config(), current)
        try:
            eng.run_once()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"Erreur (on reessaie au prochain tour): {e}")
            traceback.print_exc()
        if once:
            break
        # recharge la config a chaud si tu l'as modifiee
        try:
            eng.cfg = load_config()
        except Exception:
            pass
        # entre deux scans, on relit tes offres GE toutes les 5 s (pour ne rater aucun remplissage)
        end = time.time() + max(30, int(eng.cfg.get("poll_seconds", 60)))
        while time.time() < end:
            time.sleep(5)
            try:
                eng.check_offers(with_watch=False)
            except Exception as e:
                log(f"Erreur offres GE: {e}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Arret.")
