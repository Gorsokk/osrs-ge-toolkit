"""Local web server for the dashboard (http://localhost:8765).

- /                      the dashboard (web/dashboard.html)
- /data/<file>.json      the selected character's exports (read-only, whitelisted)
- /api/state             app status: version, character, problems, Claude, updates
- /api/settings          GET / POST: general settings + alert settings
- /api/claude            POST {"action": "connect" | "disconnect"}
- /api/runelite          POST: open RuneLite (automatic or the program picked in Settings)
- /api/ping              used to detect an already-running instance

Listens on 127.0.0.1 only. Write requests must carry the X-Toolkit header and a
localhost Host header, so other websites can't change your settings.
"""
import copy
import http.server
import json
import threading
from urllib.parse import urlparse

import autostart
import claude_connect
import data
import runelite
import settings
import updates
from paths import APP_NAME, VERSION, RESOURCE_DIR, DATA_DIR

PORTS = range(8765, 8776)
PORT_FILE = DATA_DIR / "port.txt"
DATA_FILES = set(data.EXPORT_FILES) | {"alerts.jsonl"}


# ------------------------------------------------------------ alert config ---
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

    def do_GET(self):
        if not self._local_host():
            return self._send(403, {"error": "forbidden"})
        path = urlparse(self.path).path
        if path in ("/", "/dashboard.html", "/index.html"):
            html = (RESOURCE_DIR / "web" / "dashboard.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        if path == "/favicon.ico":
            ico = RESOURCE_DIR / "web" / "icon.ico"
            return self._send(200, ico.read_bytes(), "image/x-icon") if ico.exists() else self._send(404, b"", "text/plain")
        if path == "/api/ping":
            return self._send(200, {"app": "osrs-ge-toolkit", "version": VERSION})
        if path == "/api/state":
            return self._send(200, app_state())
        if path == "/api/settings":
            return self._send(200, {"settings": settings.load(), "alerts": load_alerts_config(),
                                    "autostart": autostart.is_enabled()})
        if path.startswith("/data/"):
            fn = path[len("/data/"):]
            if fn not in DATA_FILES:
                return self._send(404, {"error": "unknown file"})
            _, folder = data.resolve_character(None)
            f = folder / fn if folder else None
            if not f or not f.exists():
                return self._send(404, {"error": "not found"})
            return self._send(200, f.read_bytes(),
                              "application/json; charset=utf-8" if fn.endswith(".json") else "text/plain; charset=utf-8")
        return self._send(404, {"error": "not found"})

    def do_POST(self):
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
            if path == "/api/check-update":
                return self._send(200, updates.check(force=True))
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
