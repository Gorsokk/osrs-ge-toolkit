"""Open RuneLite from the toolkit ("Play" in the tray / dashboard).

- Automatic: finds RuneLite (standard installer) or, failing that, the Jagex Launcher.
- Custom: any program or .bat the player picks in Settings (e.g. a RuneLite dev client).
"""
import os
import shlex
import subprocess
import sys
from pathlib import Path


def _candidates():
    env = os.environ.get
    local, pf, pf86 = env("LOCALAPPDATA"), env("ProgramFiles"), env("ProgramFiles(x86)")
    out = []
    if local:
        out.append(Path(local) / "RuneLite" / "RuneLite.exe")
    if pf:
        out.append(Path(pf) / "RuneLite" / "RuneLite.exe")
    if pf86:
        out.append(Path(pf86) / "RuneLite" / "RuneLite.exe")
        out.append(Path(pf86) / "Jagex Launcher" / "JagexLauncher.exe")
    if pf:
        out.append(Path(pf) / "Jagex Launcher" / "JagexLauncher.exe")
    if local:
        out.append(Path(local) / "Jagex Launcher" / "JagexLauncher.exe")
    return out


def find_auto():
    for p in _candidates():
        if p.is_file():
            return str(p)
    return None


def resolve(custom=""):
    """(path_or_command, source) where source is 'custom', 'auto' or None."""
    custom = (custom or "").strip().strip('"')
    if custom:
        return custom, "custom"
    auto = find_auto()
    return (auto, "auto") if auto else (None, None)


def launch(custom=""):
    """Start RuneLite. Returns (ok, message)."""
    target, source = resolve(custom)
    if not target:
        return False, "RuneLite not found. Install it from runelite.net, or pick its program in Settings."
    path = Path(target)
    try:
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_CONSOLE if path.suffix.lower() in (".bat", ".cmd") else 0
            if path.is_file():
                if path.suffix.lower() in (".bat", ".cmd"):
                    subprocess.Popen(["cmd", "/c", str(path)], cwd=str(path.parent), creationflags=flags)
                else:
                    subprocess.Popen([str(path)], cwd=str(path.parent))
            else:   # a full command line typed by the player
                subprocess.Popen(target, shell=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            args = [str(path)] if path.is_file() else shlex.split(target)
            subprocess.Popen(args, cwd=str(path.parent) if path.is_file() else None)
    except Exception as e:
        return False, f"Could not start RuneLite ({target}): {e}"
    return True, target
