"""Emplacements partages par le scanner, les alertes et le lanceur.

- RUNELITE_ROOT : ou le plugin Character Export (Plugin Hub) et Position Exporter
  ecrivent leurs fichiers JSON (un sous-dossier par personnage).
- DATA_DIR : config + etat des outils. Sur Windows: %APPDATA%\\OSRS GE Toolkit
  (toujours inscriptible, meme si l'appli est installee dans Program Files,
  et conserve lors d'une mise a jour ou reinstallation).
"""
import os
import sys
from pathlib import Path

APP_NAME = "OSRS GE Toolkit"
VERSION = "1.0.0"

RUNELITE_ROOT = Path.home() / ".runelite" / "character-exporter"

if sys.platform == "win32" and os.environ.get("APPDATA"):
    DATA_DIR = Path(os.environ["APPDATA"]) / APP_NAME
else:
    DATA_DIR = Path.home() / ".config" / "osrs-ge-toolkit"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Le OSRS Wiki demande un User-Agent descriptif avec un moyen de contact.
USER_AGENT = f"osrs-ge-toolkit/{VERSION} (+https://github.com/Gorsokk/osrs-ge-toolkit)"
