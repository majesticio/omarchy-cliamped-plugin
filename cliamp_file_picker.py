#!/usr/bin/env python3
"""Choose local audio files or folders and hand them directly to CLIAMP."""

import json
from pathlib import Path
import re
import subprocess
import sys

from cliamp_ipc import send_request


AUDIO_FILTER = "Audio files | *.mp3 *.flac *.ogg *.opus *.wav *.m4a *.aac *.wma"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".wav", ".m4a", ".aac", ".wma"}


def natural_key(value: str) -> list[tuple[int, object]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
    ]


def is_visible_audio(path: Path, root: Path | None = None) -> bool:
    try:
        relative = path.relative_to(root) if root else path
    except ValueError:
        relative = path
    return (
        path.is_file()
        and path.suffix.lower() in AUDIO_EXTENSIONS
        and not any(part.startswith(".") for part in relative.parts)
        and not path.name.startswith("._")
    )


def expand_selections(selections: list[str]) -> list[str]:
    tracks: list[Path] = []
    for value in selections:
        candidate = Path(value).expanduser()
        if candidate.is_dir():
            folder_tracks = [
                path for path in candidate.rglob("*")
                if is_visible_audio(path, candidate)
            ]
            tracks.extend(sorted(folder_tracks, key=lambda path: natural_key(str(path.relative_to(candidate)))))
        elif is_visible_audio(candidate):
            tracks.append(candidate)

    unique: list[str] = []
    seen: set[str] = set()
    for path in tracks:
        resolved = str(path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def choose_paths(folder_mode: bool, explicit: list[str]) -> tuple[list[str], bool]:
    if explicit:
        return expand_selections(explicit), False

    command = [
        "zenity",
        "--file-selection",
        "--multiple",
        "--separator=\n",
        "--title=Add an audio folder to CLIAMP" if folder_mode else "--title=Choose audio for CLIAMP",
    ]
    if folder_mode:
        command.append("--directory")
    else:
        command.extend([f"--file-filter={AUDIO_FILTER}", "--file-filter=All files | *"])
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return [], True
    selections = [path.strip() for path in completed.stdout.splitlines() if path.strip()]
    return expand_selections(selections), False


def load_paths(paths: list[str]) -> dict:
    response: dict = {}
    for index, path in enumerate(paths):
        response = send_request({
            "cmd": "track.play" if index == 0 else "track.queue",
            "track": {"title": Path(path).stem, "path": path},
        })
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "CLIAMP rejected the file"))
    return send_request({"cmd": "queue.list"})


def main() -> None:
    arguments = sys.argv[1:]
    folder_mode = bool(arguments and arguments[0] == "--folder")
    if arguments and arguments[0] in ("--folder", "--files"):
        arguments = arguments[1:]

    paths, cancelled = choose_paths(folder_mode, arguments)
    if not paths:
        if cancelled:
            print(json.dumps({"ok": True, "cancelled": True, "count": 0}))
        else:
            print(json.dumps({
                "ok": False,
                "error": "That selection contains no supported audio files.",
            }))
            raise SystemExit(1)
        return

    try:
        response = load_paths(paths)
    except (OSError, TimeoutError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": f"Could not play selected files: {error}"}))
        raise SystemExit(1)

    response["selected_count"] = len(paths)
    response["source_kind"] = "folder" if folder_mode or any(Path(value).is_dir() for value in arguments) else "files"
    print(json.dumps(response, separators=(",", ":")))


if __name__ == "__main__":
    main()
