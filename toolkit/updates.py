"""Checks GitHub for a newer release (at most every 6 hours, cached on disk)."""
import json
import time

from paths import VERSION, GITHUB_REPO, DATA_DIR, USER_AGENT
import urllib.request

CACHE = DATA_DIR / "_update_check.json"
MAX_AGE = 6 * 3600


def _parse(v):
    parts = []
    for p in str(v).lstrip("vV").split("-")[0].split("."):
        parts.append(int(p) if p.isdigit() else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def check(force=False):
    """{'current', 'latest', 'update_available', 'url', 'notes'} (never raises)."""
    cached = None
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    if not force and cached and time.time() - cached.get("checked_at", 0) < MAX_AGE:
        info = cached
    else:
        info = {"checked_at": time.time(), "latest": None, "url": None, "notes": ""}
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                         headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                rel = json.loads(r.read().decode("utf-8"))
            info.update(latest=rel.get("tag_name"), url=rel.get("html_url"), notes=(rel.get("body") or "")[:600])
            asset = next((a for a in rel.get("assets", []) if a.get("name", "").lower().endswith(".exe")), None)
            if asset:
                info["download"] = asset.get("browser_download_url")
        except Exception as e:
            info["error"] = str(e)
            if cached:   # keep the last good answer when offline
                info.update({k: cached.get(k) for k in ("latest", "url", "notes", "download")})
        try:
            CACHE.write_text(json.dumps(info), encoding="utf-8")
        except OSError:
            pass
    latest = info.get("latest")
    return {"current": VERSION, "latest": latest,
            "update_available": bool(latest and _parse(latest) > _parse(VERSION)),
            "url": info.get("url"), "download": info.get("download"), "notes": info.get("notes", "")}
