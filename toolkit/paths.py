"""Locations shared by the app, the scanner, the alerts and the Claude connector.

- RUNELITE_ROOT: where the RuneLite plugin "Character Export" (Plugin Hub) writes its JSON files
  (one sub-folder per character).
- EXPORTER_ROOT: where "OSRS Toolkit Exporter" writes position.json and ge_offers.json, in
  RuneLite's data folder for that plugin (one sub-folder per character). Older versions wrote
  them into RUNELITE_ROOT: export_path() picks whichever file is newer.
- DATA_DIR: the toolkit's own settings, alert config, state and log.
  Windows: %APPDATA%\\OSRS GE Toolkit (kept across updates and reinstalls).
- RESOURCE_DIR: bundled read-only files (web/dashboard.html), also when frozen by PyInstaller.
"""
import os
import sys
from pathlib import Path

APP_NAME = "OSRS Toolkit"        # formerly "OSRS GE Toolkit" (now the GE module of OSRS Toolkit)
DATA_FOLDER = "OSRS GE Toolkit"  # kept so settings and history survive the rename
VERSION = "1.4.3"
GITHUB_REPO = "Gorsokk/osrs-toolkit"

RUNELITE_ROOT = Path.home() / ".runelite" / "character-exporter"
EXPORTER_ROOT = Path.home() / ".runelite" / "plugin-data" / "position-exporter"
EXPORTER_FILES = ("position.json", "ge_offers.json")


def export_path(folder, filename):
    """Where to read one of a character's export files. folder = RUNELITE_ROOT / <character>."""
    folder = Path(folder)
    old = folder / filename
    if filename not in EXPORTER_FILES:
        return old
    new = EXPORTER_ROOT / folder.name / filename
    found = [p for p in (new, old) if p.is_file()]
    if not found:
        return new
    return max(found, key=lambda p: p.stat().st_mtime)

if sys.platform == "win32" and os.environ.get("APPDATA"):
    DATA_DIR = Path(os.environ["APPDATA"]) / DATA_FOLDER
else:
    DATA_DIR = Path.home() / ".config" / "osrs-ge-toolkit"
DATA_DIR.mkdir(parents=True, exist_ok=True)

if getattr(sys, "frozen", False):
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    RESOURCE_DIR = Path(__file__).resolve().parent

LOG_FILE = DATA_DIR / "toolkit.log"

# The OSRS Wiki asks for a descriptive User-Agent with a way to contact the author.
USER_AGENT = f"osrs-toolkit/{VERSION} (+https://github.com/{GITHUB_REPO})"
