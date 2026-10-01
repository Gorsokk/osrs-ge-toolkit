"""Stream module: Twitch chat commands, overlay state and Claude co-host for OSRS streamers.

Runs inside the desktop app (launcher.py). Everything is local:

- Twitch chat over IRC (TLS, irc.chat.twitch.tv:6697). Without a token the bot only
  READS chat (anonymous login): answers then show on the overlay only. With a bot
  token (Settings > Stream) it also replies in chat.
- Commands: !ge <item>, !bond, !flip, !toolkit, !kofi, !ask <question>, !commands.
- !ask questions wait in a queue that Claude reads through the Claude connector
  (stream_* tools in mcp_server.py). Claude's answers appear on the overlay (and in chat).
- The overlay page (web/stream/overlay.html, added to Meld Studio / OBS as a browser
  source) polls /api/stream/state and can switch Meld Studio scenes when asked.

Nothing here touches the game: it only reads the RuneLite exports and OSRS Wiki prices.
"""
import collections
import itertools
import random
import re
import socket
import ssl
import threading
import time
import traceback

import data
import money
import settings
from data import PRICES, read_export, resolve_character
from paths import GITHUB_REPO

IRC_HOST, IRC_PORT = "irc.chat.twitch.tv", 6697
BOND_ID = 13190
SCENES = ("starting", "game", "brb", "ending")   # suggested Meld scene names (any Meld scene name works)

HELP = {
    "en": "Commands: !ge <item> · !bond · !flip · !toolkit · !kofi · !ask <question for Claude>",
    "fr": "Commandes : !ge <objet> · !bond · !flip · !toolkit · !kofi · !ask <question pour Claude>",
}
TXT = {
    "ge_usage": {"en": "Usage: !ge <item name>, e.g. !ge dragon bones",
                 "fr": "Utilisation : !ge <objet>, ex. !ge dragon bones"},
    "ge_none": {"en": "No item matches \"{q}\".{hint}", "fr": "Aucun objet ne correspond à « {q} ».{hint}"},
    "ge_hint": {"en": " Did you mean: {s}?", "fr": " Tu voulais dire : {s} ?"},
    "ge": {"en": "{name}: buy {hi} · sell {lo} · margin after tax {m} gp{lim}",
           "fr": "{name} : achat {hi} · vente {lo} · marge après taxe {m} gp{lim}"},
    "ge_nodata": {"en": "{name}: no recent trades.", "fr": "{name} : aucune transaction récente."},
    "bond": {"en": "Bond: {price} gp · {who} has {cash} gp ({pct}%) {bar}",
             "fr": "Bond : {price} gp · {who} a {cash} gp ({pct} %) {bar}"},
    "bond_price": {"en": "Bond: {price} gp", "fr": "Bond : {price} gp"},
    "flip": {"en": "Top flip now: {name}, buy {buy} → sell {sell}, {margin} gp margin after tax (limit {limit})",
             "fr": "Meilleur flip : {name}, achat {buy} → vente {sell}, marge {margin} gp après taxe (limite {limit})"},
    "flip_none": {"en": "No flip scan yet.", "fr": "Pas encore de scan de flips."},
    "toolkit": {"en": "Free OSRS Toolkit (GE scanner, alerts, dashboard, stream kit): {url}",
                "fr": "OSRS Toolkit gratuit (scanner GE, alertes, dashboard, kit de stream) : {url}"},
    "kofi": {"en": "Support the tools ☕ {url}", "fr": "Soutenir les outils ☕ {url}"},
    "kofi_none": {"en": "No tip page set.", "fr": "Pas de page de dons configurée."},
    "ask_usage": {"en": "Usage: !ask <your question for Claude>", "fr": "Utilisation : !ask <ta question pour Claude>"},
    "ask_ok": {"en": "@{user} question #{id} sent to Claude 🤖 ({n} in queue)",
               "fr": "@{user} question #{id} envoyée à Claude 🤖 ({n} en attente)"},
    "ask_full": {"en": "@{user} the question queue is full, try again in a bit.",
                 "fr": "@{user} la file de questions est pleine, réessaie tantôt."},
    "ask_wait": {"en": "@{user} you already have a question waiting.",
                 "fr": "@{user} tu as déjà une question en attente."},
}


def _t(key, lang, **kw):
    s = TXT[key].get(lang) or TXT[key]["en"]
    return s.format(**kw)


def gp(n):
    return "?" if n is None else money.gp(n)


def _clean(text, limit=450):
    """One line, no IRC control chars, bounded length."""
    text = re.sub(r"[\r\n\x00-\x1f]+", " ", str(text or "")).strip()
    return text[:limit]


# ------------------------------------------------------------- game data ---
def bond_status():
    """{'price', 'cash', 'pct', 'character'}; cash = coins + platinum tokens (bank + inventory) + gp locked in GE buys."""
    out = {"price": None, "cash": None, "pct": None, "character": None}
    try:
        p = PRICES.latest().get(str(BOND_ID)) or {}
        out["price"] = p.get("high") or p.get("low")
    except Exception:
        pass
    name, folder = resolve_character(None)
    out["character"] = name
    if folder:
        m, _ = read_export(folder, "market.json")
        if m:
            ge = m.get("ge") or {}
            out["cash"] = int(m.get("capital_gp") or 0) + int(ge.get("gp_locked_in_buys") or 0)
    if out["price"] and out["cash"] is not None:
        out["pct"] = min(100, round(100 * out["cash"] / out["price"], 1))
    return out


def top_flip():
    name, folder = resolve_character(None)
    if not folder:
        return None
    m, age = read_export(folder, "market.json")
    rows = (m or {}).get("flips_best_gp_per_hour") or (m or {}).get("flips_fastest_fill") or []
    if not rows:
        return None
    r = rows[0]
    return {"name": r.get("name"), "buy": r.get("buy_at"), "sell": r.get("sell_at"),
            "margin": r.get("net_margin"), "limit": r.get("buy_limit"), "roi": r.get("roi_pct"),
            "scan_age_min": age}


def recent_alerts(minutes=30, limit=5):
    import json
    name, folder = resolve_character(None)
    if not folder:
        return []
    try:
        lines = (folder / "alerts.jsonl").read_text(encoding="utf-8").splitlines()[-200:]
    except OSError:
        return []
    cutoff = time.time() - minutes * 60
    rows = []
    for line in lines:
        try:
            a = json.loads(line)
            ts = time.mktime(time.strptime(a["ts"], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
        if ts >= cutoff:
            rows.append({"ts": a["ts"], "category": a.get("category"), "title": a.get("title"),
                         "body": a.get("body"), "important": bool(a.get("important"))})
    return rows[-limit:][::-1]


# ------------------------------------------------------------------ stream ---
class Stream:
    def __init__(self):
        self.lock = threading.Lock()
        self.ids = itertools.count(1)
        self.chat = collections.deque(maxlen=60)       # recent chat lines
        self.questions = collections.OrderedDict()     # id -> question (pending / answered / skipped)
        self.feed = collections.deque(maxlen=20)       # what the overlay shows (Claude + bot answers)
        self.scene_request = None                      # {"id", "scene", "ts"} consumed by the overlay
        self.meld = {"connected": False, "scenes": [], "current": None, "seen": 0}
        self.irc = None
        self.irc_status = "off"
        self.cooldowns = {}
        self._stop = threading.Event()
        self._thread = None
        self._cfg_seen = None

    # --- settings
    @staticmethod
    def cfg():
        s = settings.load()
        return {"lang": s.get("language") or "en",
                "enabled": bool(s.get("stream_enabled")),
                "channel": (s.get("stream_channel") or "").strip().lstrip("#").lower(),
                "bot_name": (s.get("stream_bot_name") or "").strip().lower(),
                "token": (s.get("stream_bot_token") or "").strip(),
                "toolkit_url": s.get("stream_toolkit_url") or f"https://github.com/{GITHUB_REPO}",
                "kofi_url": s.get("stream_kofi_url") or "",
                "streamer": s.get("stream_display_name") or ""}

    # --- lifecycle (the loop reconnects by itself when settings change)
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="twitch_chat")
        self._thread.start()

    def _loop(self):
        backoff = 5
        while not self._stop.is_set():
            c = self.cfg()
            key = (c["enabled"], c["channel"], c["bot_name"], c["token"])
            if not c["enabled"] or not c["channel"]:
                self.irc_status = "off"
                self._cfg_seen = key
                time.sleep(3)
                continue
            try:
                self._run_irc(c, key)      # returns when the settings change
                self.irc_status = "reconnecting"
                self._close()
                time.sleep(1)
                backoff = 5
                continue
            except Exception as e:
                self.irc_status = f"error: {e}"
                print("[stream] chat error:\n" + traceback.format_exc())
            finally:
                self._close()
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)

    def _close(self):
        with self.lock:
            sock, self.irc = self.irc, None
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    def _send_raw(self, line):
        with self.lock:
            sock = self.irc
        if sock:
            sock.sendall((line + "\r\n").encode("utf-8"))

    def _run_irc(self, c, key):
        self._cfg_seen = key
        ctx = ssl.create_default_context()
        raw = socket.create_connection((IRC_HOST, IRC_PORT), timeout=15)
        sock = ctx.wrap_socket(raw, server_hostname=IRC_HOST)
        sock.settimeout(1.0)
        with self.lock:
            self.irc = sock
        token = c["token"]
        if token:
            if not token.startswith("oauth:"):
                token = "oauth:" + token
            self._send_raw(f"PASS {token}")
            self._send_raw(f"NICK {c['bot_name'] or c['channel']}")
        else:
            self._send_raw(f"NICK justinfan{random.randint(10000, 99999)}")   # anonymous, read-only
        self._send_raw("CAP REQ :twitch.tv/tags twitch.tv/commands")
        self._send_raw(f"JOIN #{c['channel']}")
        self.irc_status = "connecting"
        buf, last_rx = b"", time.time()
        while not self._stop.is_set():
            cur = self.cfg()
            if (cur["enabled"], cur["channel"], cur["bot_name"], cur["token"]) != key:
                return   # settings changed: reconnect
            try:
                chunk = sock.recv(8192)
                if not chunk:
                    raise ConnectionError("disconnected")
                buf += chunk
                last_rx = time.time()
            except (socket.timeout, TimeoutError):
                if time.time() - last_rx > 360:
                    raise ConnectionError("no data for 6 minutes")
                continue
            *lines, buf = buf.split(b"\r\n")
            for line in lines:
                self._on_line(line.decode("utf-8", "replace"), c)

    def _on_line(self, line, c):
        if line.startswith("PING"):
            self._send_raw("PONG" + line[4:])
            return
        tags = {}
        if line.startswith("@"):
            t, _, line = line.partition(" ")
            for kv in t[1:].split(";"):
                k, _, v = kv.partition("=")
                tags[k] = v.replace("\\s", " ")
        parts = line.split(" ", 3)
        if len(parts) >= 2 and parts[1] == "001":
            self.irc_status = "connected (can reply)" if c["token"] else "connected (read-only)"
        elif len(parts) >= 2 and parts[1] == "NOTICE" and "authentication failed" in line.lower():
            self.irc_status = "error: Twitch refused the bot token"
            raise ConnectionError("login failed")
        if len(parts) < 4 or parts[1] != "PRIVMSG":
            return
        login = parts[0][1:].split("!")[0]
        user = tags.get("display-name") or login
        text = parts[3][1:] if parts[3].startswith(":") else parts[3]
        text = text.split(" :", 1)[1] if text.startswith("#") and " :" in text else text
        with self.lock:
            self.chat.append({"user": user, "text": _clean(text, 300), "ts": time.time()})
        if login == (c["bot_name"] or "") and c["token"]:
            return   # don't answer ourselves
        if text.startswith("!"):
            try:
                self.command(user, text, c, badges=tags.get("badges", ""))
            except Exception:
                print("[stream] command failed:\n" + traceback.format_exc())

    # --- output
    def reply(self, text, c=None, source="bot", extra=None):
        c = c or self.cfg()
        text = _clean(text)
        if not text:
            return
        item = {"id": next(self.ids), "source": source, "text": text, "ts": time.time()}
        item.update(extra or {})
        with self.lock:
            self.feed.append(item)
        if c["token"] and c["channel"] and self.irc is not None:
            try:
                self._send_raw(f"PRIVMSG #{c['channel']} :{text}")
            except OSError:
                pass
        return item

    def _cool(self, key, seconds):
        now = time.time()
        if now - self.cooldowns.get(key, 0) < seconds:
            return False
        self.cooldowns[key] = now
        return True

    # --- commands
    def command(self, user, text, c, badges=""):
        lang = c["lang"]
        cmd, _, arg = text.strip().partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd in ("!commands", "!commandes", "!help"):
            if self._cool(cmd, 20):
                self.reply(HELP.get(lang, HELP["en"]), c)
        elif cmd == "!ge":
            if not arg:
                return self.reply(_t("ge_usage", lang), c) if self._cool("!ge-usage", 20) else None
            if not self._cool(f"!ge:{arg.lower()}", 15):
                return
            self.reply(self.ge_text(arg, lang), c)
        elif cmd == "!bond":
            if self._cool(cmd, 20):
                self.reply(self.bond_text(lang, c), c)
        elif cmd == "!flip":
            if self._cool(cmd, 30):
                f = top_flip()
                self.reply(_t("flip", lang, name=f["name"], buy=gp(f["buy"]), sell=gp(f["sell"]),
                              margin=gp(f["margin"]), limit=f["limit"] or "?") if f else _t("flip_none", lang), c)
        elif cmd in ("!toolkit", "!tools", "!outils"):
            if self._cool("!toolkit", 20):
                self.reply(_t("toolkit", lang, url=c["toolkit_url"]), c)
        elif cmd in ("!kofi", "!tip", "!support"):
            if self._cool("!kofi", 20):
                self.reply(_t("kofi", lang, url=c["kofi_url"]) if c["kofi_url"] else _t("kofi_none", lang), c)
        elif cmd in ("!ask", "!claude"):
            if not arg:
                return self.reply(_t("ask_usage", lang), c) if self._cool("!ask-usage", 20) else None
            self.ask(user, arg, c)

    def ge_text(self, query, lang="en"):
        item, sugg = PRICES.find_item(query)
        if not item:
            hint = _t("ge_hint", lang, s=", ".join(sugg[:3])) if sugg else ""
            return _t("ge_none", lang, q=_clean(query, 40), hint=hint)
        p = PRICES.latest().get(str(item["id"])) or {}
        hi, lo = p.get("high"), p.get("low")
        if not hi and not lo:
            return _t("ge_nodata", lang, name=item["name"])
        margin = (data.net_sell(hi) - lo) if hi and lo else None
        lim = (f" · {'limite' if lang == 'fr' else 'limit'} {item['limit']}") if item.get("limit") else ""
        text = _t("ge", lang, name=item["name"], hi=gp(hi or lo), lo=gp(lo or hi), m=gp(margin), lim=lim)
        if not margin or margin <= 0:   # no positive spread right now: don't advertise a margin
            text = re.sub(r" · (?:margin after tax|marge après taxe) \S+ gp", "", text)
        return text

    def bond_text(self, lang="en", c=None):
        b = bond_status()
        who = (c or {}).get("streamer") or b["character"] or "the streamer"
        if b["price"] is None:
            return "Bond: price unavailable right now." if lang != "fr" else "Bond : prix indisponible pour l'instant."
        if b["pct"] is None:
            return _t("bond_price", lang, price=gp(b["price"]))
        filled = int(b["pct"] // 10)
        bar = "▰" * filled + "▱" * (10 - filled)
        return _t("bond", lang, price=gp(b["price"]), who=who, cash=gp(b["cash"]), pct=b["pct"], bar=bar)

    def ask(self, user, question, c=None):
        c = c or self.cfg()
        lang = c["lang"]
        with self.lock:
            pending = [q for q in self.questions.values() if q["status"] == "pending"]
            if len(pending) >= 25:
                full = True
            else:
                full = False
                if any(q["user"].lower() == user.lower() for q in pending):
                    dup = True
                else:
                    dup = False
                    qid = next(self.ids)
                    self.questions[qid] = {"id": qid, "user": user, "text": _clean(question, 300),
                                           "ts": time.time(), "status": "pending", "answer": None}
                    while len(self.questions) > 100:
                        self.questions.popitem(last=False)
                    n = len(pending) + 1
        if full:
            return self.reply(_t("ask_full", lang, user=user), c) if self._cool("!ask-full", 30) else None
        if dup:
            return self.reply(_t("ask_wait", lang, user=user), c) if self._cool(f"!ask-dup:{user}", 30) else None
        self.reply(_t("ask_ok", lang, user=user, id=qid, n=n), c)
        return qid

    # --- Claude (called by the Claude connector through the local server)
    def claude_say(self, text, question_id=None, to_chat=True):
        q = None
        with self.lock:
            if question_id is not None:
                q = self.questions.get(int(question_id))
                if q:
                    q["status"], q["answer"] = "answered", _clean(text)
        extra = {"question": q and {"id": q["id"], "user": q["user"], "text": q["text"]}}
        c = self.cfg()
        if not to_chat:
            c = dict(c, token="")
        prefix = f"@{q['user']} " if q else ""
        return self.reply(("🤖 " + prefix + text) if to_chat else text, c, source="claude", extra=extra)

    def skip_question(self, question_id):
        with self.lock:
            q = self.questions.get(int(question_id))
            if q:
                q["status"] = "skipped"
            return bool(q)

    def request_scene(self, scene):
        scene = _clean(scene, 60)
        with self.lock:
            self.scene_request = {"id": next(self.ids), "scene": scene, "ts": time.time()}
            return self.scene_request

    def meld_report(self, info):
        with self.lock:
            self.meld = {"connected": bool(info.get("connected")),
                         "scenes": [_clean(s, 60) for s in (info.get("scenes") or [])][:50],
                         "current": _clean(info.get("current"), 60) or None,
                         "streaming": bool(info.get("streaming")),
                         "seen": time.time()}

    # --- snapshots
    def state(self, include_game=True):
        c = self.cfg()
        with self.lock:
            out = {"channel": c["channel"], "lang": c["lang"], "chat_status": self.irc_status,
                   "can_reply": bool(c["token"]),
                   "feed": list(self.feed)[-8:],
                   "questions": [q for q in self.questions.values() if q["status"] == "pending"][:10],
                   "scene_request": self.scene_request,
                   "meld": dict(self.meld, online=time.time() - self.meld.get("seen", 0) < 15),
                   "links": {"toolkit": c["toolkit_url"], "kofi": c["kofi_url"]},
                   "streamer": c["streamer"]}
        if include_game:
            out["bond"] = bond_status()
            out["flip"] = top_flip()
            out["alerts"] = recent_alerts()
        return out

    def chat_snapshot(self, limit=30):
        with self.lock:
            return {"channel": self.cfg()["channel"], "chat_status": self.irc_status,
                    "pending_questions": [q for q in self.questions.values() if q["status"] == "pending"],
                    "recently_answered": [q for q in self.questions.values() if q["status"] == "answered"][-5:],
                    "recent_chat": list(self.chat)[-limit:]}


STREAM = Stream()
