"""Remote bridge: the Claude connector over HTTPS, so Claude voice mode, the mobile app and
claude.ai (which only reach *remote* connectors) can use the toolkit's tools.

How it works:
- The dashboard server answers MCP requests (Streamable HTTP, JSON replies) at
  http://localhost:<port>/mcp/<secret>  (see server.py, mcp_server.handle_http).
- A free ngrok tunnel with a fixed address (e.g. https://gorsok.ngrok-free.app) forwards the
  internet to that local port. This module starts and watches ngrok when enabled in Settings.
- The user adds  https://<domain>/mcp/<secret>  as a custom connector in Claude.

The secret in the URL is the only key: anyone who has the full URL can read the character
data and post on the stream overlay/chat. Keep it private; "New address" in Settings changes it.
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import settings


def connector_url(s=None):
    s = s or settings.load()
    domain = (s.get("bridge_domain") or "").strip().replace("https://", "").replace("http://", "").strip("/")
    if not domain:
        return None
    return f"https://{domain}/mcp/{s['bridge_secret']}"


def find_ngrok():
    exe = shutil.which("ngrok")
    if exe:
        return exe
    local = os.environ.get("LOCALAPPDATA")
    for p in ([Path(local) / "Microsoft" / "WinGet" / "Links" / "ngrok.exe",
               Path(local) / "ngrok" / "ngrok.exe"] if local else []) + [Path.home() / "ngrok.exe"]:
        if p.exists():
            return str(p)
    return None


class Bridge:
    def __init__(self):
        self.proc = None
        self.status = "off"
        self.last_error = ""
        self.port = None
        self._thread = None
        self._running_key = None

    def start(self, port):
        self.port = port
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bridge")
        self._thread.start()

    def _stop_proc(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(5)
            except Exception:
                pass
        self.proc = None
        self._running_key = None

    def _loop(self):
        while True:
            try:
                s = settings.load()
                domain = (s.get("bridge_domain") or "").strip().replace("https://", "").strip("/")
                want = s.get("bridge_enabled") and s.get("bridge_run_ngrok") and domain and self.port
                key = (domain, self.port) if want else None
                if not s.get("bridge_enabled"):
                    self._stop_proc()
                    self.status = "off"
                elif not s.get("bridge_run_ngrok"):
                    self._stop_proc()
                    self.status = "on (your own tunnel)"
                elif not domain:
                    self.status = "waiting: enter your ngrok address"
                elif key != self._running_key or not self.proc or self.proc.poll() is not None:
                    self._stop_proc()
                    exe = find_ngrok()
                    if not exe:
                        self.status = "ngrok not found: install it (winget install ngrok.ngrok)"
                    else:
                        flags = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW
                        self.proc = subprocess.Popen([exe, "http", f"--url={domain}", str(self.port), "--log=stdout"],
                                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                     stdin=subprocess.DEVNULL, creationflags=flags,
                                                     text=True, encoding="utf-8", errors="replace")
                        self._running_key = key
                        self.status = "starting ngrok…"
                        threading.Thread(target=self._read_log, args=(self.proc,), daemon=True).start()
            except Exception:
                self.last_error = traceback.format_exc(limit=2)
                self.status = "error"
            time.sleep(5)

    def _read_log(self, proc):
        for line in proc.stdout:
            low = line.lower()
            if "started tunnel" in low or "url=https://" in low:
                self.status = "online"
            elif "lvl=eror" in low or "lvl=crit" in low or "authtoken" in low:
                self.last_error = line.strip()[-300:]
                if "authtoken" in low:
                    self.status = "ngrok needs your authtoken: run  ngrok config add-authtoken <token>"
                elif self.status != "online":
                    self.status = "ngrok error"
        if self.status == "online":
            self.status = "ngrok stopped, restarting…"

    def info(self):
        s = settings.load()
        return {"enabled": bool(s.get("bridge_enabled")), "status": self.status, "error": self.last_error,
                "url": connector_url(s), "ngrok_found": bool(find_ngrok()),
                "local_url": f"http://localhost:{self.port}/mcp/{s['bridge_secret']}" if self.port else None}


BRIDGE = Bridge()
