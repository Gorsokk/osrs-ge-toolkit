"""User settings for the toolkit (language, character, scan interval...).

Stored in %APPDATA%\\OSRS GE Toolkit\\settings.json. Alert thresholds live in
alerts_config.json next to it (see osrs_alerts.py); the dashboard edits both.
"""
import json
import locale
import threading

from paths import DATA_DIR

SETTINGS_FILE = DATA_DIR / "settings.json"
_lock = threading.Lock()


def _default_language():
    try:
        lang = (locale.getlocale()[0] or "").lower()
    except Exception:
        lang = ""
    if not lang:
        try:
            import ctypes  # Windows UI language, e.g. 0x0c0c = fr-CA
            lang = "fr" if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF) == 0x0C else "en"
        except Exception:
            lang = "en"
    return "fr" if lang.startswith("fr") else "en"


DEFAULTS = {
    "language": None,           # "en" / "fr"; None = follow Windows
    "character": None,          # None = most recently played character
    "scan_interval_sec": 275,
    "open_dashboard_on_start": True,
    "check_updates": True,
    "auto_install_updates": False,  # install new versions by itself (silently), only while you're not playing/streaming
    "launch_runelite": False,   # open RuneLite when the toolkit is started by hand (not at Windows start)
    "runelite_path": "",        # "" = find RuneLite / Jagex Launcher automatically; or a program / .bat
    # --- Stream module (stream.py)
    "stream_enabled": False,    # connect to Twitch chat and answer !commands
    "stream_channel": "",       # Twitch channel to join, e.g. "blodvis"
    "stream_bot_name": "",      # account that posts the replies ("" = read-only, answers on the overlay only)
    "stream_bot_token": "",     # chat token of that account (oauth:...)
    "stream_display_name": "",  # name used in !bond ("" = character name)
    "stream_toolkit_url": "",   # link for !toolkit ("" = this project's GitHub page)
    "stream_kofi_url": "",      # link for !kofi (Ko-fi, GitHub Sponsors...)
    # --- Remote bridge (bridge.py): the Claude connector over HTTPS, for Claude voice mode / mobile / web
    "bridge_enabled": False,
    "bridge_domain": "",        # public address of the tunnel, e.g. "gorsok.ngrok-free.app"
    "bridge_run_ngrok": True,   # start ngrok automatically with the toolkit
    "bridge_secret": "",        # random, part of the connector URL (generated on first use)
}


def load():
    with _lock:
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                user = json.load(f)
        except Exception:
            user = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in user.items() if k in DEFAULTS})
    if out["language"] not in ("en", "fr"):
        out["language"] = _default_language()
    if not out["bridge_secret"]:
        import secrets
        out["bridge_secret"] = secrets.token_urlsafe(24)
        _write(dict(user, bridge_secret=out["bridge_secret"]))
    return out


def _write(values):
    with _lock:
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(values, f, ensure_ascii=False, indent=2)
        tmp.replace(SETTINGS_FILE)


def save(changes):
    current = load()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS and k != "bridge_secret"})
    _write(current)
    return current
