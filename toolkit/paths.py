"""Locations shared by the app, the scanner, the alerts and the Claude connector.

- RUNELITE_ROOT: where the RuneLite plugins "Character Export" (Plugin Hub) and
  "Position Exporter" write their JSON files (one sub-folder per character).
- DATA_DIR: the toolkit's own settings, alert config, state and log.
  Windows: %APPDATA%\\OSRS GE Toolkit (kept across updates and reinstalls).
- RESOURCE_DIR: bundled read-only files (web/dashboard.html), also when frozen by PyInstaller.
"""
import os
import sys
from pathlib import Path

APP_NAME = "OSRS GE Toolkit"
VERSION = "1.1.0"
GITHUB_REPO = "Gorsokk/osrs-ge-toolkit"

RUNELITE_ROOT = Path.home() / ".runelite" / "character-exporter"

if sys.platform == "win32" and os.environ.get("APPDATA"):
    DATA_DIR = Path(os.environ["APPDATA"]) / APP_NAME
else:
    DATA_DIR = Path.home() / ".config" / "osrs-ge-toolkit"
DATA_DIR.mkdir(parents=True, exist_ok=True)

if getattr(sys, "frozen", False):
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    RESOURCE_DIR = Path(__file__).resolve().parent

LOG_FILE = DATA_DIR / "toolkit.log"

# The OSRS Wiki asks for a descriptive User-Agent with a way to contact the author.
USER_AGENT = f"osrs-ge-toolkit/{VERSION} (+https://github.com/{GITHUB_REPO})"
