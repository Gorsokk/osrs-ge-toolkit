"""One-time import from the old stand-alone scripts (run_scan.bat / run_alerts.bat).

Early versions of this project were loose Python scripts in a folder such as
"osrs tools". If that folder is found, the app takes over their history once:
profits made, purchase costs of items you still hold, news already seen,
alert cooldowns and alert settings. The old folder is left untouched.
"""
import json
import time
from pathlib import Path

from paths import DATA_DIR

FLAG = DATA_DIR / "_legacy_import.json"
STATE_FILE = DATA_DIR / "_alerts_state.json"
CONFIG_FILE = DATA_DIR / "alerts_config.json"
FOLDER_NAMES = ("osrs tools", "osrs_tools", "osrs-tools", "OSRS tools", "OSRS Tools")


def _load(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def find_legacy_folder():
    home = Path.home()
    bases = [home, home / "Desktop", home / "Documents", home / "Downloads",
             home / "OneDrive" / "Desktop", home / "OneDrive" / "Documents"]
    for base in bases:
        for name in FOLDER_NAMES:
            d = base / name
            if (d / "osrs_alerts.py").is_file() and ((d / "_alerts_state.json").is_file()
                                                     or (d / "alerts_config.json").is_file()):
                return d
    return None


def merge_state(new, old):
    """Merge the old scripts' alert state into the app's (never loses app data)."""
    new = dict(new or {})
    old = old or {}
    # profits made (dedupe by timestamp + item)
    seen = {(r.get("ts"), r.get("item")) for r in new.get("realized", [])}
    new["realized"] = list(new.get("realized", [])) + [
        r for r in old.get("realized", []) if (r.get("ts"), r.get("item")) not in seen]
    new["realized"].sort(key=lambda r: r.get("ts", 0))
    # purchase costs of items still held: keep the app's entry when both exist
    positions = dict(old.get("positions", {}))
    positions.update(new.get("positions", {}))
    new["positions"] = positions
    # news already seen
    new["news_seen"] = list(dict.fromkeys(list(new.get("news_seen", [])) + list(old.get("news_seen", []))))[-200:]
    # cooldowns: the most recent one wins (so nothing is announced twice)
    cd = dict(old.get("cooldowns", {}))
    for k, v in new.get("cooldowns", {}).items():
        cd[k] = max(v, cd.get(k, 0))
    new["cooldowns"] = cd
    for k in ("offers", "unrealistic_since"):
        new.setdefault(k, {})
    return new


def run():
    """Import once. Returns the folder imported from, or None."""
    if FLAG.exists():
        return None
    folder = find_legacy_folder()
    if not folder:
        return None
    imported = []
    old_state = _load(folder / "_alerts_state.json")
    if old_state:
        _save(STATE_FILE, merge_state(_load(STATE_FILE, {}), old_state))
        imported.append("history")
    old_cfg = _load(folder / "alerts_config.json")
    if old_cfg and not CONFIG_FILE.exists():
        _save(CONFIG_FILE, old_cfg)
        imported.append("alert settings")
    _save(FLAG, {"from": str(folder), "at": time.time(), "imported": imported})
    return folder
