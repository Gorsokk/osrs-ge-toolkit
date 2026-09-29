"""Local web server for the dashboard (http://localhost:8765).

- /                      the dashboard (web/dashboard.html)
- /data/<file>.json      the selected character's exports (read-only, whitelisted)
- /api/state             app status: version, character, problems, Claude, updates
- /api/income?period=    gp per played hour (session / today / 24h / 7d / 30d)
- /api/settings          GET / POST: general settings + alert settings
- /api/claude            POST {"action": "connect" | "disconnect"}
- /api/runelite          POST: open RuneLite (automatic or the program picked in Settings)
- /api/ping              used to detect an already-running instance
- /stream/<page>         stream pages for Meld Studio / OBS browser sources (overlay, starting, brb, ending)
- /api/stream/state      GET: what the overlay shows; POST /api/stream/*: Claude co-host, scenes, test commands

Listens on 127.0.0.1 only. Write requests must carry the X-Toolkit header and a
localhost Host header, so other websites can't change your settings.
"""
import copy
import http.server
import json
import threading
import time
from urllib.parse import urlparse

import autostart
import claude_connect
import data
import runelite
import settings
import updates
from stream import STREAM
from paths import APP_NAME, VERSION, RESOURCE_DIR, DATA_DIR

PORTS = range(8765, 8776)
PORT_FILE = DATA_DIR / "port.txt"
DATA_FILES = set(data.EXPORT_FILES) | {"alerts.jsonl"}


# ------------------------------------------------------------ alert config ---
LAST_REMOTE_CALL = 0.0   # last request through the remote bridge (time.time())

def load_alerts_config():
    import osrs_alerts
    return osrs_alerts.load_config()


def save_alerts_config(changes):
    """Apply only known keys, with the same types as the defaults."""
    import osrs_alerts
    current = osrs_alerts.load_config()
    defaults = osrs_alerts.DEFAULT_CONFIG

    def merge(dst, src, ref):
        for k, v in src.items():
            if k.startswith("_") or k not in ref:
                continue
            r = ref[k]
            if k == "watch":
                clean = {}
                for name, w in (v or {}).items():
                    name = str(name).strip()
                    if not name:
                        continue
                    entry = {}
                    for f in ("cost", "sell_alert_at", "buy_alert_at"):
                        try:
                            if w.get(f) not in (None, "", 0, "0"):
                                entry[f] = int(float(w[f]))
                        except (TypeError, ValueError):
                            pass
                    if entry:
                        clean[name] = entry
                dst[k] = clean
            elif isinstance(r, dict):
                dst.setdefault(k, {})
                merge(dst[k], v or {}, r)
            elif isinstance(r, bool):
                dst[k] = bool(v)
            elif isinstance(r, int):
                dst[k] = int(float(v))
            elif isinstance(r, float):
                dst[k] = float(v)
            elif isinstance(r, list):
                items = v if isinstance(v, list) else str(v).replace(",", "\n").split("\n")
                if r and isinstance(r[0], (int, float)):
                    dst[k] = sorted({int(float(x)) for x in items if str(x).strip()})
                else:
                    dst[k] = [str(x).strip() for x in items if str(x).strip()]
            else:
                dst[k] = v

    new = copy.deepcopy(current)
    merge(new, changes, defaults)
    osrs_alerts.save_json(osrs_alerts.CONFIG_FILE, new)
    return new


# ------------------------------------------------------------------- state ---
def app_state():
    s = settings.load()
    name, folder = data.resolve_character(None)
    problems = []
    files = {}
    if not folder:
        problems.append("no_data")
    else:
        for fn in data.EXPORT_FILES:
            _, age = data.read_export(folder, fn)
            files[fn] = age
        if files.get("ge_offers.json") is None:
            problems.append("no_position_exporter")
        if files.get("bank.json") and files["bank.json"] > 60:
            problems.append("bank_old")
    try:
        claude = claude_connect.status()
    except Exception as e:
        claude = {"connected": False, "error": str(e)}
    upd = updates.check() if s.get("check_updates") else {"update_available": False, "current": VERSION}
    rl_target, rl_source = runelite.resolve(s.get("runelite_path"))
    return {"app": APP_NAME, "version": VERSION, "language": s["language"], "character": name,
            "characters": data.list_characters(), "file_ages_min": files, "problems": problems,
            "claude": claude, "update": upd, "autostart": autostart.is_enabled(),
            "runelite": {"target": rl_target, "source": rl_source}}


# ------------------------------------------------------------------ stream ---
STREAM_PAGES = {"overlay", "starting", "brb", "ending"}
STREAM_ASSETS = {"stream.js": "text/javascript; charset=utf-8", "stream.css": "text/css; charset=utf-8"}


def stream_post(action, body):
    """(status, json) for POST /api/stream/<action>."""
    if action == "say":
        text = str(body.get("text") or "").strip()
        if not text:
            return 400, {"ok": False, "error": "text is required"}
        item = STREAM.claude_say(text, body.get("question_id"), to_chat=body.get("to_chat", True) is not False)
        return 200, {"ok": True, "shown": item}
    if action == "skip":
        return 200, {"ok": STREAM.skip_question(body.get("question_id"))}
    if action == "scene":
        scene = str(body.get("scene") or "").strip()
        if not scene:
            return 400, {"ok": False, "error": "scene is required"}
        return 200, {"ok": True, "request": STREAM.request_scene(scene), "meld": STREAM.state(False)["meld"]}
    if action == "meld":      # the overlay page reports what it sees in Meld Studio
        STREAM.meld_report(body)
        return 200, {"ok": True}
    if action == "command":   # test a chat command from the dashboard without Twitch
        user = str(body.get("user") or "tester")[:25]
        text = str(body.get("text") or "")
        last = max((f["id"] for f in STREAM.feed), default=0)
        STREAM.cooldowns.clear()
        STREAM.command(user, text, STREAM.cfg())
        return 200, {"ok": True, "answers": [f for f in STREAM.feed if f["id"] > last]}
    return 404, {"ok": False, "error": "unknown action"}


# ----------------------------------------------------------------- handler ---
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "OSRSGEToolkit"

    def log_message(self, *args):   # keep the log quiet (the dashboard polls every 5 s)
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _local_host(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("localhost", "127.0.0.1")

    # --- remote bridge: the Claude connector over HTTPS (bridge.py). Reached through the
    # tunnel, so the Host header is the public domain: the secret in the path is the key.
    def _mcp(self, path):
        import hmac
        s = settings.load()
        secret = path[len("/mcp/"):].strip("/")
        if not s.get("bridge_enabled") or not hmac.compare_digest(secret, s["bridge_secret"]):
            return self._send(404, {"error": "not found"})
        if self.command != "POST":
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                return self._send(413, {"error": "too large"})
            payload = json.loads(self.rfile.read(length) or b"null")
        except Exception:
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        import mcp_server
        global LAST_REMOTE_CALL
        LAST_REMOTE_CALL = time.time()     # auto-update waits while Claude (voice) is using the bridge
        out = mcp_server.handle_http(payload)
        if out is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        return self._send(200, out)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/mcp/"):
            return self._mcp(path)
        return self._send(405, {"error": "method not allowed"})

    def do_GET(self):
        if urlparse(self.path).path.startswith("/mcp/"):
            return self._mcp(urlparse(self.path).path)
        if not self._local_host():
            return self._send(403, {"error": "forbidden"})
        path = urlparse(self.path).path
        if path in ("/", "/dashboard.html", "/index.html"):
            html = (RESOURCE_DIR / "web" / "dashboard.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        if path == "/favicon.ico":
            ico = RESOURCE_DIR / "web" / "icon.ico"
            return self._send(200, ico.read_bytes(), "image/x-icon") if ico.exists() else self._send(404, b"", "text/plain")
        if path.startswith("/stream/"):
            page = path[len("/stream/"):].strip("/") or "overlay"
            if page in STREAM_ASSETS:
                return self._send(200, (RESOURCE_DIR / "web" / "stream" / page).read_bytes(), STREAM_ASSETS[page])
            if page not in STREAM_PAGES:
                return self._send(404, {"error": "unknown page"})
            html = (RESOURCE_DIR / "web" / "stream" / f"{page}.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        if path == "/api/bridge":
            from bridge import BRIDGE
            return self._send(200, BRIDGE.info())
        if path == "/api/stream/state":
            return self._send(200, STREAM.state())
        if path == "/api/stream/chat":
            return self._send(200, STREAM.chat_snapshot())
        if path == "/api/ping":
            return self._send(200, {"app": "osrs-ge-toolkit", "version": VERSION})
        if path == "/api/state":
            return self._send(200, app_state())
        if path == "/api/income":
            import income
            from urllib.parse import parse_qs
            period = (parse_qs(urlparse(self.path).query).get("period") or ["today"])[0]
            name, _ = data.resolve_character(None)
            return self._send(200, income.summary(name, period) if name else {"note": "no character yet"})
        if path == "/api/settings":
            return self._send(200, {"settings": settings.load(), "alerts": load_alerts_config(),
                                    "autostart": autostart.is_enabled()})
        if path.startswith("/data/"):
            fn = path[len("/data/"):]
            if fn not in DATA_FILES:
                return self._send(404, {"error": "unknown file"})
            _, folder = data.resolve_character(None)
            f = data.export_path(folder, fn) if folder else None
            if not f or not f.exists():
                return self._send(404, {"error": "not found"})
            return self._send(200, f.read_bytes(),
                              "application/json; charset=utf-8" if fn.endswith(".json") else "text/plain; charset=utf-8")
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path.startswith("/mcp/"):
            return self._mcp(urlparse(self.path).path)
        # custom header => browsers block cross-site requests (no CORS allowed here)
        if not self._local_host() or self.headers.get("X-Toolkit") != "1":
            return self._send(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        path = urlparse(self.path).path
        try:
            if path == "/api/settings":
                out = {}
                if "settings" in body:
                    out["settings"] = settings.save(body["settings"])
                if "alerts" in body:
                    out["alerts"] = save_alerts_config(body["alerts"])
                if "autostart" in body:
                    autostart.set_enabled(bool(body["autostart"]))
                    out["autostart"] = autostart.is_enabled()
                return self._send(200, {"ok": True, **out})
            if path == "/api/claude":
                action = body.get("action")
                res = claude_connect.connect() if action == "connect" else claude_connect.disconnect()
                ok = all(r[1] for r in res) and bool(res)
                return self._send(200, {"ok": ok, "results": res, "status": claude_connect.status()})
            if path == "/api/runelite":
                ok, msg = runelite.launch(settings.load().get("runelite_path"))
                return self._send(200, {"ok": ok, "message": msg})
            if path == "/api/bridge/new-secret":
                import secrets
                from bridge import BRIDGE
                cur = settings.load()
                settings._write(dict(cur, bridge_secret=secrets.token_urlsafe(24)))
                return self._send(200, BRIDGE.info())
            if path.startswith("/api/stream/"):
                return self._send(*stream_post(path[len("/api/stream/"):], body))
            if path == "/api/check-update":
                return self._send(200, updates.check(force=True))
            if path == "/api/update/install":
                ok, msg = updates.install()
                return self._send(200, {"ok": ok, "message": msg})
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)})
        return self._send(404, {"error": "not found"})


def start():
    """Start in a background thread. Returns (httpd, port) or (None, None)."""
    for port in PORTS:
        try:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            continue
        threading.Thread(target=httpd.serve_forever, daemon=True, name="dashboard").start()
        try:
            PORT_FILE.write_text(str(port), encoding="utf-8")
        except OSError:
            pass
        return httpd, port
    return None, None


def running_instance_port():
    """Port of an already-running toolkit, or None."""
    import urllib.request
    ports = []
    try:
        ports.append(int(PORT_FILE.read_text().strip()))
    except Exception:
        pass
    ports += [p for p in PORTS if p not in ports]
    for p in ports[:3]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/api/ping", timeout=1) as r:
                if json.loads(r.read()).get("app") == "osrs-ge-toolkit":
                    return p
        except Exception:
            continue
    return None
