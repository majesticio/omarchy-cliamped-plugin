#!/usr/bin/env python3
"""Persist Radio Browser search favorites that CLIAMP's IPC cannot identify."""

import json
import os
from pathlib import Path
import sys


def favorites_path() -> Path:
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_root / "cliamp" / "cliamped_search_favorites.json"


def load_favorites(path: Path | None = None) -> list[dict]:
    target = path or favorites_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(value, list):
        return []
    favorites = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        stream_url = str(item.get("path") or "").strip()
        title = str(item.get("title") or "").strip()
        if not stream_url or not title or stream_url in seen:
            continue
        seen.add(stream_url)
        favorites.append({"title": title, "path": stream_url,
            "artist": str(item.get("artist") or ""), "stream": True, "realtime": True})
    return favorites


def save_favorites(favorites: list[dict], path: Path | None = None) -> None:
    target = path or favorites_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(favorites, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, target)


def toggle_favorite(track: dict, path: Path | None = None) -> list[dict]:
    stream_url = str(track.get("path") or "").strip()
    title = str(track.get("title") or "").strip()
    if not stream_url or not title:
        raise ValueError("station title and stream URL are required")
    favorites = load_favorites(path)
    existing = next((i for i, item in enumerate(favorites) if item["path"] == stream_url), -1)
    if existing >= 0:
        favorites.pop(existing)
    else:
        favorites.append({"title": title, "path": stream_url,
            "artist": str(track.get("artist") or ""), "stream": True, "realtime": True})
    save_favorites(favorites, path)
    return favorites


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "list"
    try:
        if action == "list":
            favorites = load_favorites()
        elif action == "toggle" and len(sys.argv) > 2:
            favorites = toggle_favorite(json.loads(sys.argv[2]))
        else:
            raise ValueError("usage: cliamped_search_favorites.py list|toggle [track-json]")
        print(json.dumps({"ok": True, "favorites": favorites}, separators=(",", ":")))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, separators=(",", ":")))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
