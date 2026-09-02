#!/usr/bin/python3 -I
"""Safely persist the small set of Radio Browser search favorites.

The favorites file is local state, but both its contents and pathname can be
replaced while the long-lived panel is running. Keep filesystem access relative
to a validated private directory descriptor and bound data before parsing or
returning it to QML.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import sys
import time
import unicodedata
from urllib.parse import urlsplit


MAX_FILE_BYTES = 128 * 1024
MAX_AGGREGATE_BYTES = 64 * 1024
MAX_FAVORITES = 128
MAX_TITLE_BYTES = 256
MAX_ARTIST_BYTES = 256
MAX_URL_BYTES = 4096
MAX_STORAGE_PATH_BYTES = 4096
MAX_TRACK_ARGUMENT_BYTES = 16 * 1024
MAX_JSON_DEPTH = 8
MAX_ERROR_BYTES = 512
LOCK_TIMEOUT_SECONDS = 2.0
_LIBC = ctypes.CDLL(None, use_errno=True)
_DOMAIN_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class FavoritesSecurityError(ValueError):
    """The favorites store or payload failed a security boundary."""


def _arm_helper_parent_death() -> None:
    """Ensure this one-shot helper cannot outlive its QML owner."""

    expected_parent = os.getppid()
    if expected_parent <= 1:
        raise FavoritesSecurityError("the favorites helper has no live owner")
    if _LIBC.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise FavoritesSecurityError("cannot arm favorites-helper owner cleanup")
    if os.getppid() != expected_parent:
        raise FavoritesSecurityError("the favorites-helper owner exited during startup")


def _utf8_length(value: str) -> int:
    try:
        return len(value.encode("utf-8", "strict"))
    except UnicodeError as error:
        raise FavoritesSecurityError("text contains invalid Unicode") from error


def _validate_url_host(value: str) -> None:
    # Percent escapes and zone identifiers in authority names are rejected so
    # Python and CLIAMP's URL parser cannot disagree about the network peer.
    if not value or "%" in value:
        raise FavoritesSecurityError("path has an invalid host")
    try:
        ipaddress.ip_address(value)
        return
    except ValueError:
        pass
    try:
        host = value.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise FavoritesSecurityError("path has an invalid host") from error
    if host.endswith("."):
        host = host[:-1]
    labels = host.split(".")
    if (
        not host
        or len(host) > 253
        or any(not _DOMAIN_LABEL.fullmatch(label) for label in labels)
    ):
        raise FavoritesSecurityError("path has an invalid host")


def _bounded_error(error: BaseException) -> str:
    value = unicodedata.normalize("NFC", str(error))
    value = "".join(
        " " if character.isspace()
        else "" if unicodedata.category(character).startswith("C")
        else character
        for character in value
    )
    value = " ".join(value.split())
    encoded = value.encode("utf-8")[:MAX_ERROR_BYTES]
    return encoded.decode("utf-8", errors="ignore") or "favorites operation failed"


def _normal_text(value: object, *, field: str, maximum: int,
                 required: bool = False) -> str:
    if not isinstance(value, str):
        if required or value is not None:
            raise FavoritesSecurityError(f"{field} must be a string")
        return ""

    normalized = unicodedata.normalize("NFC", value)
    normalized = "".join(
        " " if character.isspace()
        else "" if unicodedata.category(character).startswith("C")
        else character
        for character in normalized
    )
    normalized = " ".join(normalized.split())
    if required and not normalized:
        raise FavoritesSecurityError(f"{field} is required")
    if _utf8_length(normalized) > maximum:
        raise FavoritesSecurityError(f"{field} is too long")
    return normalized


def _stream_url(value: object) -> str:
    if not isinstance(value, str):
        raise FavoritesSecurityError("path must be a string")
    if not value:
        raise FavoritesSecurityError("path is required")
    if _utf8_length(value) > MAX_URL_BYTES:
        raise FavoritesSecurityError("path is too long")
    if any(character.isspace() or unicodedata.category(character).startswith("C")
           for character in value):
        raise FavoritesSecurityError("path contains whitespace or control characters")

    try:
        parsed = urlsplit(value)
        # Accessing port performs urllib's numeric/range validation.
        parsed.port
    except ValueError as error:
        raise FavoritesSecurityError("path is not a valid URL") from error
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise FavoritesSecurityError("path must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise FavoritesSecurityError("path must not contain credentials")
    _validate_url_host(parsed.hostname)
    # URL paths and query strings are opaque provider identifiers. Validation
    # must never normalize them into a different network resource.
    return value


def _normalize_favorite(item: object) -> dict:
    if not isinstance(item, dict):
        raise FavoritesSecurityError("favorite must be an object")
    title = _normal_text(item.get("title"), field="title",
                         maximum=MAX_TITLE_BYTES, required=True)
    stream_url = _stream_url(item.get("path"))
    artist = _normal_text(item.get("artist"), field="artist",
                          maximum=MAX_ARTIST_BYTES)
    return {
        "title": title,
        "path": stream_url,
        "artist": artist,
        "stream": True,
        "realtime": True,
    }


def _normalize_favorites(value: object, *, reject_invalid: bool = False) -> list[dict]:
    if not isinstance(value, list):
        if reject_invalid:
            raise FavoritesSecurityError("favorites must be an array")
        return []
    if len(value) > MAX_FAVORITES:
        raise FavoritesSecurityError("too many favorites")

    favorites: list[dict] = []
    seen: set[str] = set()
    for item in value:
        try:
            favorite = _normalize_favorite(item)
        except FavoritesSecurityError:
            if reject_invalid:
                raise
            continue
        if favorite["path"] in seen:
            continue
        seen.add(favorite["path"])
        favorites.append(favorite)

    encoded = json.dumps(favorites, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_AGGREGATE_BYTES:
        raise FavoritesSecurityError("favorites exceed the aggregate size limit")
    return favorites


def _check_json_depth(value: str) -> None:
    depth = 0
    quoted = False
    escaped = False
    for character in value:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise FavoritesSecurityError("JSON is nested too deeply")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                return


def _decode_json(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8", errors="strict")
        _check_json_depth(text)
        return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return []


def favorites_path() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    config_root = Path(configured) if configured else Path.home() / ".config"
    if not config_root.is_absolute():
        raise FavoritesSecurityError("XDG_CONFIG_HOME must be absolute")
    return config_root / "cliamp" / "cliamped_search_favorites.json"


def _validate_storage_path(path: Path) -> tuple[Path, str]:
    path_text = os.fspath(path)
    if "\0" in path_text or _utf8_length(path_text) > MAX_STORAGE_PATH_BYTES:
        raise FavoritesSecurityError("favorites path is invalid or too long")
    name = path.name
    if not name or name in {".", ".."} or os.sep in name:
        raise FavoritesSecurityError("favorites filename is invalid")
    return path.parent, name


def _open_private_directory(path: Path) -> int:
    # mkdir only establishes the directory. Every subsequent operation uses the
    # descriptor opened and validated below, so pathname replacement cannot
    # redirect a read or write after validation.
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise FavoritesSecurityError("cannot create favorites directory") from error

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(path, flags)
    except OSError as error:
        raise FavoritesSecurityError("favorites directory is not a real directory") from error
    try:
        status = os.fstat(directory_fd)
        if not stat.S_ISDIR(status.st_mode):
            raise FavoritesSecurityError("favorites directory is not a directory")
        if status.st_uid != os.geteuid():
            raise FavoritesSecurityError("favorites directory has the wrong owner")
        if stat.S_IMODE(status.st_mode) & 0o077:
            raise FavoritesSecurityError("favorites directory must be private (mode 0700)")
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def _open_lock(directory_fd: int, target_name: str) -> int:
    lock_name = f".{target_name}.lock"
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(lock_name, flags, 0o600, dir_fd=directory_fd)
    except OSError as error:
        raise FavoritesSecurityError("cannot open favorites lock") from error
    try:
        status = os.fstat(lock_fd)
        if (not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or status.st_nlink != 1):
            raise FavoritesSecurityError("favorites lock is not a trusted regular file")
        os.fchmod(lock_fd, 0o600)
        return lock_fd
    except BaseException:
        os.close(lock_fd)
        raise


@contextmanager
def _locked_store(path: Path, *, exclusive: bool):
    parent, name = _validate_storage_path(path)
    directory_fd = _open_private_directory(parent)
    try:
        lock_fd = _open_lock(directory_fd, name)
        try:
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(lock_fd, operation | fcntl.LOCK_NB)
                    break
                except BlockingIOError as error:
                    if time.monotonic() >= deadline:
                        raise FavoritesSecurityError("favorites store is busy") from error
                    time.sleep(0.01)
            yield directory_fd, name
        finally:
            os.close(lock_fd)
    finally:
        os.close(directory_fd)


def _read_favorites(directory_fd: int, name: str) -> list[dict]:
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return []
    except OSError as error:
        raise FavoritesSecurityError("cannot inspect favorites file") from error
    if (not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1):
        raise FavoritesSecurityError("favorites file is not a trusted regular file")
    if before.st_size < 0 or before.st_size > MAX_FILE_BYTES:
        raise FavoritesSecurityError("favorites file exceeds the size limit")

    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        file_fd = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise FavoritesSecurityError("cannot safely open favorites file") from error
    try:
        opened = os.fstat(file_fd)
        if (not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise FavoritesSecurityError("favorites file changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(16 * 1024, MAX_FILE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise FavoritesSecurityError("favorites file exceeds the size limit")
        return _normalize_favorites(_decode_json(b"".join(chunks)))
    finally:
        os.close(file_fd)


def _write_all(file_fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(file_fd, payload[offset:])
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        offset += written


def _write_favorites(directory_fd: int, name: str, favorites: object) -> list[dict]:
    normalized = _normalize_favorites(favorites, reject_invalid=True)
    payload = (json.dumps(normalized, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(payload) > MAX_FILE_BYTES:
        raise FavoritesSecurityError("favorites file exceeds the size limit")

    temporary_name = ""
    temporary_fd = -1
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        for _ in range(16):
            temporary_name = f".{name}.{secrets.token_hex(16)}.tmp"
            try:
                temporary_fd = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
                break
            except FileExistsError:
                continue
        if temporary_fd < 0:
            raise FavoritesSecurityError("cannot allocate a temporary favorites file")

        os.fchmod(temporary_fd, 0o600)
        _write_all(temporary_fd, payload)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1
        os.replace(temporary_name, name,
                   src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary_name = ""
        os.fsync(directory_fd)
        return normalized
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def load_favorites(path: Path | None = None) -> list[dict]:
    target = Path(path) if path is not None else favorites_path()
    with _locked_store(target, exclusive=False) as (directory_fd, name):
        return _read_favorites(directory_fd, name)


def save_favorites(favorites: list[dict], path: Path | None = None) -> None:
    target = Path(path) if path is not None else favorites_path()
    with _locked_store(target, exclusive=True) as (directory_fd, name):
        _write_favorites(directory_fd, name, favorites)


def toggle_favorite(track: dict, path: Path | None = None) -> list[dict]:
    favorite = _normalize_favorite(track)
    target = Path(path) if path is not None else favorites_path()
    with _locked_store(target, exclusive=True) as (directory_fd, name):
        favorites = _read_favorites(directory_fd, name)
        existing = next(
            (index for index, item in enumerate(favorites)
             if item["path"] == favorite["path"]),
            -1,
        )
        if existing >= 0:
            favorites.pop(existing)
        else:
            if len(favorites) >= MAX_FAVORITES:
                raise FavoritesSecurityError("favorite limit reached")
            favorites.append(favorite)
        return _write_favorites(directory_fd, name, favorites)


def _parse_track_argument(value: str) -> dict:
    if _utf8_length(value) > MAX_TRACK_ARGUMENT_BYTES:
        raise FavoritesSecurityError("track argument exceeds the size limit")
    _check_json_depth(value)
    try:
        track = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as error:
        raise FavoritesSecurityError("track argument is not valid JSON") from error
    if not isinstance(track, dict):
        raise FavoritesSecurityError("track argument must be an object")
    return track


def main() -> None:
    try:
        _arm_helper_parent_death()
        if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] == "list"):
            favorites = load_favorites()
        elif len(sys.argv) == 3 and sys.argv[1] == "toggle":
            favorites = toggle_favorite(_parse_track_argument(sys.argv[2]))
        else:
            raise FavoritesSecurityError(
                "usage: cliamped_search_favorites.py list|toggle [track-json]"
            )
        print(json.dumps({"ok": True, "favorites": favorites},
                         ensure_ascii=False, separators=(",", ":")))
    except (OSError, ValueError, json.JSONDecodeError, RecursionError) as error:
        print(json.dumps({"ok": False, "error": _bounded_error(error)},
                         ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
