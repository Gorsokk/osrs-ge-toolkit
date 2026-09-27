"""
OSRS Toolkit - the desktop app (tray icon, no console window).

Runs in the background, next to the clock:
  - the GE scanner (flips + High Alch), every few minutes
  - the dashboard at http://localhost:8765 (opens in your browser)
  - Windows alerts (GE offers, flips, crashes/spikes, bond, news, cheap materials)
  - "Play": opens RuneLite (optionally when the toolkit starts)
Right-click the tray icon for the menu (dashboard, settings, Connect to Claude, quit).

Game data comes from two RuneLite Plugin Hub plugins:
  - "Character Export" (by DZWNK): stats, bank, inventory, quests...
  - "Position Exporter" (this project): position + Grand Exchange offers

Flags: --background (start silently, used by "Start with Windows"), --play (also open RuneLite,
even if the toolkit is already running), --no-tray (dev/testing).
"""
import os
import sys
import threading
import time
import traceback
import webbrowser

import data
import settings
from i18n import t
from paths import APP_NAME, VERSION, DATA_DIR, LOG_FILE, RESOURCE_DIR, GITHUB_REPO

MUTEX_NAME = "OSRSGEToolkit"     # also used by the installer to close the app before updating
FIRST_RUN_FLAG = DATA_DIR / "_first_run_done"


# ------------------------------------------------------------------ logging ---
def setup_logging():
    """Windowed app: send prints and errors to %APPDATA%\\OSRS GE Toolkit\\toolkit.log."""
    if sys.stdout is not None and sys.stdout.isatty():
        return
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2_000_000:
            LOG_FILE.replace(LOG_FILE.with_suffix(".old.log"))
        f = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
    except OSError:
        pass


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


# ------------------------------------------------------------ background ---
class App:
    def __init__(self, background=False, tray=True):
        self.background = background
        self.use_tray = tray
        self.port = None
        self.icon = None
        self.character = None
        self.update = {}
        self.stop = threading.Event()

    @property
    def url(self):
        return f"http://localhost:{self.port}/"

    def notify(self, title, body):
        log(f"[notify] {title} | {body}")
        if self.icon is not None:
            try:
                self.icon.notify(body, title)
            except Exception:
                pass

    # --- scanner: re-scan every N seconds (settings), follow character changes
    def scanner_loop(self):
        import market_scan as ms
        last_char = None
        while not self.stop.is_set():
            s = settings.load()
            name, folder = data.resolve_character(None)
            if folder and (folder / "bank.json").exists() and (folder / "character.json").exists():
                try:
                    ms.setup_paths(name)
                    ms.main()
                except Exception:
                    log("scan failed:\n" + traceback.format_exc())
            last_char = name
            deadline = time.time() + max(60, int(s.get("scan_interval_sec", 275)))
            while time.time() < deadline and not self.stop.is_set():
                time.sleep(5)
                if data.resolve_character(None)[0] != last_char:
                    break   # character switched in the dashboard: rescan now

    def alerts_loop(self):
        import osrs_alerts
        while not self.stop.is_set():
            if not data.resolve_character(None)[1]:
                time.sleep(10)
                continue
            try:
                osrs_alerts.run_forever()
                # returns at once if another alert engine (e.g. an old run_alerts.bat window) is running
                time.sleep(30)
            except Exception:
                log("alerts crashed, restarting in 30 s:\n" + traceback.format_exc())
                time.sleep(30)

    def watcher_loop(self):
        """Setup hint while there is no data, tray tooltip, update notice."""
        told_setup = told_update = False
        while not self.stop.is_set():
            name, folder = data.resolve_character(None)
            self.character = name
            if not folder and not told_setup:
                self.notify(t("setup_title"), t("setup_body"))
                told_setup = True
            if self.icon is not None:
                try:
                    status = t("status_running", name=name) if name else t("status_waiting")
                    self.icon.title = t("tray_tip", status=status)[:127]
                except Exception:
                    pass
            if settings.load().get("check_updates"):
                try:
                    import updates
                    self.update = updates.check()
                    if self.update.get("update_available") and not told_update:
                        self.notify(APP_NAME, t("tray_update", version=self.update.get("latest")))
                        told_update = True
                        if self.icon is not None:
                            self.icon.update_menu()
                except Exception:
                    pass
            time.sleep(30)

    # --- tray actions
    def open_dashboard(self, *_):
        webbrowser.open(self.url)

    def open_settings(self, *_):
        webbrowser.open(self.url + "#settings")

    def play(self, *_):
        import runelite
        ok, msg = runelite.launch(settings.load().get("runelite_path"))
        log(f"RuneLite: {'started ' if ok else ''}{msg}")
        if not ok:
            self.notify(APP_NAME, t("runelite_fail", error=msg))

    def open_folder(self, *_):
        try:
            os.startfile(str(DATA_DIR))   # noqa: windows only
        except Exception:
            webbrowser.open(DATA_DIR.as_uri())

    def open_update(self, *_):
        webbrowser.open(self.update.get("url") or f"https://github.com/{GITHUB_REPO}/releases/latest")

    def connect_claude(self, *_):
        import claude_connect
        try:
            if claude_connect.status().get("connected"):
                self.open_settings()
                return
            res = claude_connect.connect()
            if res and all(r[1] for r in res):
                self.notify(t("claude_done_title"), t("claude_done_body"))
            else:
                self.notify(APP_NAME, t("claude_fail", error="; ".join(r[2] for r in res if not r[1])))
        except Exception as e:
            self.notify(APP_NAME, t("claude_fail", error=str(e)))
        if self.icon is not None:
            self.icon.update_menu()

    def quit(self, *_):
        self.stop.set()
        if self.icon is not None:
            self.icon.stop()
        os._exit(0)

    def claude_label(self, _item=None):
        import claude_connect
        try:
            return t("tray_claude_connected") if claude_connect.status().get("connected") else t("tray_claude_connect")
        except Exception:
            return t("tray_claude_connect")

    def build_tray(self):
        import pystray
        from PIL import Image
        image = Image.open(RESOURCE_DIR / "web" / "icon.ico")
        M = pystray.MenuItem
        menu = pystray.Menu(
            M(lambda _: t("tray_open"), self.open_dashboard, default=True),
            M(lambda _: t("tray_play"), self.play),
            M(lambda _: t("tray_settings"), self.open_settings),
            M(self.claude_label, self.connect_claude),
            M(lambda _: t("tray_update", version=self.update.get("latest") or ""), self.open_update,
              visible=lambda _: bool(self.update.get("update_available"))),
            pystray.Menu.SEPARATOR,
            M(lambda _: t("tray_folder"), self.open_folder),
            M(lambda _: t("tray_quit"), self.quit),
        )
        self.icon = pystray.Icon("osrs-ge-toolkit", image, APP_NAME, menu)

    def run(self):
        import server
        log(f"=== {APP_NAME} {VERSION} starting ===")
        legacy_folder = None
        try:
            import legacy
            legacy_folder = legacy.run()     # one-time import from the old stand-alone scripts
            if legacy_folder:
                log(f"imported history from old scripts in {legacy_folder}")
        except Exception:
            log("legacy import failed:\n" + traceback.format_exc())
        if getattr(self, "force_play", False) or (not self.background and settings.load().get("launch_runelite")):
            threading.Thread(target=self.play, daemon=True, name="runelite").start()
        httpd, self.port = server.start()
        if not self.port:
            log("no free port for the dashboard (8765-8775)")
        for target in (self.scanner_loop, self.alerts_loop, self.watcher_loop):
            threading.Thread(target=target, daemon=True, name=target.__name__).start()
        try:
            from stream import STREAM
            STREAM.start()      # Twitch chat bot; idles until enabled in Settings > Stream
        except Exception:
            log("stream module failed to start:\n" + traceback.format_exc())

        first_run = not FIRST_RUN_FLAG.exists()
        if self.port and not self.background and (first_run or settings.load().get("open_dashboard_on_start")):
            threading.Timer(2.0, self.open_dashboard).start()

        if not self.use_tray:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                return
        self.build_tray()
        if legacy_folder:
            threading.Timer(6.0, lambda: self.notify(t("legacy_title"), t("legacy_body", folder=legacy_folder.name))).start()
        if first_run:
            def hello():
                time.sleep(3)
                self.notify(t("started_title"), t("started_body"))
                try:
                    FIRST_RUN_FLAG.write_text(VERSION)
                except OSError:
                    pass
            threading.Thread(target=hello, daemon=True).start()
        self.icon.run()


def already_running():
    """True if another instance holds the mutex (Windows) or answers on the dashboard port."""
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        already = ctypes.windll.kernel32.GetLastError() == 183   # ERROR_ALREADY_EXISTS
        globals()["_mutex_handle"] = handle   # keep it alive for the process lifetime
        if already:
            return True
    import server
    return server.running_instance_port() is not None


def ask_running_instance_to_play(port):
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/runelite", data=b"{}", method="POST",
                                     headers={"X-Toolkit": "1", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception:
        return False


def main():
    setup_logging()
    background = "--background" in sys.argv
    tray = "--no-tray" not in sys.argv
    play = "--play" in sys.argv        # "Play" shortcut: toolkit + RuneLite in one click
    if already_running():
        import server
        port = server.running_instance_port()
        if port and play:
            if not ask_running_instance_to_play(port):
                import runelite
                runelite.launch(settings.load().get("runelite_path"))
        elif port and not background:
            webbrowser.open(f"http://localhost:{port}/")
        return
    app = App(background=background, tray=tray)
    app.force_play = play
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log("fatal:\n" + traceback.format_exc())
        raise
