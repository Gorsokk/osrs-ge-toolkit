"""One-click connection between OSRS Toolkit and the Claude desktop app.

Adds (or removes) an "osrs-ge-toolkit" entry in Claude Desktop's local connector
config (claude_desktop_config.json). Everything else in that file is kept as-is,
and a backup is written before any change.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVER_NAME = "osrs-ge-toolkit"
MCP_EXE_NAME = "osrs-ge-mcp.exe"


def config_paths():
    """Every Claude Desktop config location that exists (or the standard one if none do)."""
    found = []
    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    standard = None
    if sys.platform == "win32" and appdata:
        standard = Path(appdata) / "Claude" / "claude_desktop_config.json"
        if standard.parent.exists():
            found.append(standard)
        # Microsoft Store install keeps its data in a virtualized folder
        if localappdata:
            for pkg in (Path(localappdata) / "Packages").glob("Claude_*"):
                p = pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
                if p.parent.exists():
                    found.append(p)
    elif sys.platform == "darwin":
        standard = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        if standard.parent.exists():
            found.append(standard)
    else:
        standard = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
        if standard.parent.exists():
            found.append(standard)
    if not found and standard:
        found.append(standard)   # Claude not installed yet: prepare the config for later
    return found


def mcp_command():
    """Command Claude should launch: the bundled connector exe, or python + script in dev."""
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        for candidate in (app_dir / MCP_EXE_NAME, app_dir / "mcp" / MCP_EXE_NAME,
                          app_dir.parent / MCP_EXE_NAME):
            if candidate.exists():
                return {"command": str(candidate), "args": []}
        return {"command": str(app_dir / "mcp" / MCP_EXE_NAME), "args": []}
    script = Path(__file__).resolve().parent / "mcp_server.py"
    return {"command": sys.executable, "args": [str(script)]}


def _read(path):
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)   # raises on invalid JSON: we never overwrite a file we can't parse


def _write(path, cfg):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak-osrs-ge-toolkit"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def status():
    """{'connected': bool, 'configs': [...], 'claude_running': bool}"""
    configs = []
    for p in config_paths():
        entry = None
        error = None
        try:
            entry = (_read(p).get("mcpServers") or {}).get(SERVER_NAME)
        except Exception as e:
            error = str(e)
        configs.append({"path": str(p), "exists": p.exists(), "connected": bool(entry), "error": error})
    return {"connected": any(c["connected"] for c in configs), "configs": configs,
            "claude_running": claude_running()}


def connect():
    """Add/refresh the connector entry. Returns a list of (path, ok, message)."""
    results = []
    entry = mcp_command()
    for p in config_paths():
        try:
            cfg = _read(p)
            servers = cfg.setdefault("mcpServers", {})
            if servers.get(SERVER_NAME) == entry:
                results.append((str(p), True, "already connected"))
                continue
            servers[SERVER_NAME] = entry
            _write(p, cfg)
            results.append((str(p), True, "connected"))
        except Exception as e:
            results.append((str(p), False, f"could not update: {e}"))
    return results


def disconnect():
    results = []
    for p in config_paths():
        try:
            cfg = _read(p)
            if SERVER_NAME in (cfg.get("mcpServers") or {}):
                del cfg["mcpServers"][SERVER_NAME]
                _write(p, cfg)
                results.append((str(p), True, "disconnected"))
        except Exception as e:
            results.append((str(p), False, f"could not update: {e}"))
    return results


def claude_running():
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe", "/NH"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return "claude.exe" in out.lower()
    except Exception:
        return False


if __name__ == "__main__":
    # Used by the installer:  osrs-ge-mcp.exe --connect-claude  /  --disconnect-claude
    action = sys.argv[1] if len(sys.argv) > 1 else "--status"
    if action == "--connect-claude":
        print(connect())
    elif action == "--disconnect-claude":
        print(disconnect())
    else:
        print(json.dumps(status(), indent=2))
