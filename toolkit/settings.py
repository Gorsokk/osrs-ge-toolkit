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
    return out


def save(changes):
    current = load()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS})
    with _lock:
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        tmp.replace(SETTINGS_FILE)
    return current
