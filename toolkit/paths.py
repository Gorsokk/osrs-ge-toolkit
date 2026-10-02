"""Locations shared by the app, the scanner, the alerts and the Claude connector.

- EXPORTER_ROOT: where our RuneLite plugin "OSRS Toolkit Exporter" writes a character's files (position,
  GE offers, levels, quests, diaries, combat achievement totals, inventory, equipment, bank), in RuneLite's data
  folder for that plugin (one sub-folder per character). It is the primary source.
- RUNELITE_ROOT: the toolkit's own per-character folder (market.json, alerts.jsonl, caches). It is also where the
  third-party plugin "Character Export" writes the same kinds of files: they are only read as a fallback (older
  set-ups), see export_path().
- GAME_QUESTS_FILE: the game's own quest table (requirements, start, rewards), also written by the Exporter
  (optional, off by default in the plugin), shared by all characters.
- DATA_DIR: the toolkit's own settings, alert config, state and log.
  Windows: %APPDATA%\\OSRS GE Toolkit (kept across updates and reinstalls).
- RESOURCE_DIR: bundled read-only files (web/dashboard.html), also when frozen by PyInstaller.
"""
import os
import sys
from pathlib import Path

APP_NAME = "OSRS Toolkit"        # formerly "OSRS GE Toolkit" (now the GE module of OSRS Toolkit)
DATA_FOLDER = "OSRS GE Toolkit"  # kept so settings and history survive the rename
VERSION = "1.4.4"
GITHUB_REPO = "Gorsokk/osrs-toolkit"

RUNELITE_ROOT = Path.home() / ".runelite" / "character-exporter"
EXPORTER_ROOT = Path.home() / ".runelite" / "plugin-data" / "position-exporter"
EXPORTER_FILES = ("position.json", "ge_offers.json", "character.json", "quests.json", "diaries.json",
                  "combat_achievements.json", "inventory.json", "equipment.json", "bank.json")
# A Character Export file is only preferred when ours is missing or clearly older (e.g. our export switched off).
FALLBACK_NEWER_BY_SEC = 3600
# Game data (the same for every character), written by "OSRS Toolkit Exporter" with "Export game quest data" on.
GAME_QUESTS_FILE = EXPORTER_ROOT / "game_quests.json"


def export_path(folder, filename):
    """Where to read one of a character's export files. folder = RUNELITE_ROOT / <character>.
    OSRS Toolkit Exporter's file first; the same file in `folder` (Character Export, or an older Exporter) only when
    ours is missing or more than an hour older."""
    folder = Path(folder)
    old = folder / filename
    if filename not in EXPORTER_FILES:
        return old
    new = EXPORTER_ROOT / folder.name / filename
    if not old.is_file():
        return new
    if not new.is_file():
        return old
    try:
        if old.stat().st_mtime - new.stat().st_mtime > FALLBACK_NEWER_BY_SEC:
            return old
    except OSError:
        pass
    return new

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
