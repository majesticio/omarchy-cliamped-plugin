#!/usr/bin/python3 -I
"""CLIAMPed's own music library: a SQLite index of folders of audio files.

The panel asks this one-shot helper to scan, search and browse folders such as
a mounted NAS share, so the library works with any CLIAMP: it needs nothing from
CLIAMP but its public IPC to play what the panel picks.

Each call is one short process. A scan is a series of calls, each limited to a
time budget, that resume where the last one stopped, so no process runs for
long, a stalled share can only hold up one step, and a restart of the shell
loses nothing. The index is a SQLite file in WAL mode, which lets the panel
browse while a scan writes.

The folders are chosen by the user in a native dialog. File names and tags come
from the share and are untrusted: names with control characters are skipped,
tags are cleaned and bounded by ``cliamped_tags``, and every track that leaves
this module passes ``normalize_track``.
"""

from __future__ import annotations

import ctypes
import contextlib
import hashlib
import json
import os
import re
import secrets
import signal
import sqlite3
import stat
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
from functools import lru_cache
from typing import Any

# Python isolated mode intentionally omits the script directory. The QML side
# invokes this helper with -I, so expose only this resolved bundle directory for
# the duration of the sibling imports, after the standard-library imports above.
_BUNDLE_DIRECTORY = os.path.dirname(os.path.realpath(__file__))
_inserted_bundle_directory = _BUNDLE_DIRECTORY not in sys.path
if _inserted_bundle_directory:
    sys.path.insert(0, _BUNDLE_DIRECTORY)
try:
    from cliamped_security import (
        MAX_IDENTIFIER_BYTES,
        MAX_OUTPUT_BYTES,
        MAX_PATH_BYTES,
        ValidationError,
        _boolean,
        _integer,
        _opaque_string,
        _require_keys,
        display_text,
        encode_json,
        error_text,
        normalize_track,
    )
    from cliamped_tags import read_tags
finally:
    if _inserted_bundle_directory:
        sys.path.remove(_BUNDLE_DIRECTORY)

SCHEMA_VERSION = 2
AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".aacp", ".m4b", ".alac", ".wma", ".opus", ".webm"}
)
SKIP_NAMES = frozenset({"@eaDir", "#recycle", "$RECYCLE.BIN", "System Volume Information"})

MAX_ROOTS = 64
MAX_DEPTH = 40
MAX_TRACKS_TOTAL = 1_000_000
PAGE_MAX = 200
SEARCH_LIMIT_MAX = 100
SEARCH_CANDIDATES = 20_000
FUZZY_ROWS = 300_000
M3U_MAX_TRACKS = 1000
SCAN_BUDGET_DEFAULT = 8
SCAN_BUDGET_MAX = 20
TAG_WORKERS = 4
TAG_BATCH = 64
# A file whose tags could not be read in time is tried this many times, then indexed by its name.
TAG_TRIES = 3
TAG_WAIT_MIN_SECONDS = 1.0
TAG_WAIT_SLACK_SECONDS = 5.0
REGROUP_INTERVAL_SECONDS = 15.0
QUEUE_FILE_MAX_AGE_SECONDS = 2 * 3600

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"
VARIOUS_ARTISTS = "Various Artists"

ALBUM_SORTS = (
    ("alphabeticalByName", "By Name"),
    ("alphabeticalByArtist", "By Artist"),
    ("newest", "Recently Added"),
    ("byYear", "By Year"),
)

_LIBC = ctypes.CDLL(None, use_errno=True)


class LibraryError(ValueError):
    """A request the library cannot honor, with a message fit for the user."""


# Text -----------------------------------------------------------------------

_LETTER_MAP = str.maketrans({"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "ł": "l", "đ": "d", "ı": "i"})


def fold(text: str) -> str:
    """Lowercase ``text`` without accents, so "Beyoncé" and "beyonce" match."""

    if text.isascii():
        return text.lower()
    decomposed = unicodedata.normalize("NFKD", text.lower().translate(_LETTER_MAP))
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def sort_key(text: str) -> str:
    key = fold(text).strip()
    return key[4:] if key.startswith("the ") else key


def group_id(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


def _like(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# Storage --------------------------------------------------------------------


def data_directory() -> str:
    home = os.environ.get("HOME")
    if not home or not os.path.isabs(home):
        raise LibraryError("HOME is not set")
    return os.path.join(home, ".local", "share", "cliamped")


def _private_directory(path: str) -> str:
    """Create ``path`` as a private directory of the user, or check that it is one."""

    os.makedirs(path, mode=0o700, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise LibraryError(f"{path} must be a private directory of the current user")
    return path


def database_path() -> str:
    return os.path.join(_private_directory(data_directory()), "library.sqlite3")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS roots(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, added INTEGER NOT NULL, available INTEGER NOT NULL DEFAULT 1,
  found INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS tracks(
  path TEXT PRIMARY KEY, dir TEXT NOT NULL, root_id INTEGER NOT NULL, mtime INTEGER NOT NULL, size INTEGER NOT NULL,
  title TEXT NOT NULL, artist TEXT NOT NULL, album TEXT NOT NULL, album_artist TEXT NOT NULL, genre TEXT NOT NULL,
  year INTEGER NOT NULL, track INTEGER NOT NULL, disc INTEGER NOT NULL,
  title_f TEXT NOT NULL, artist_f TEXT NOT NULL, album_f TEXT NOT NULL,
  album_id TEXT NOT NULL DEFAULT '', link_artist_id TEXT NOT NULL DEFAULT '', seen INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS tracks_dir ON tracks(dir);
CREATE INDEX IF NOT EXISTS tracks_root ON tracks(root_id);
CREATE INDEX IF NOT EXISTS tracks_album ON tracks(album_id, disc, track, path);
CREATE TABLE IF NOT EXISTS artists(id TEXT PRIMARY KEY, name TEXT NOT NULL, sort TEXT NOT NULL, albums INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS artists_sort ON artists(sort);
CREATE TABLE IF NOT EXISTS albums(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, sort TEXT NOT NULL, artist TEXT NOT NULL, artist_id TEXT NOT NULL,
  artist_sort TEXT NOT NULL, year INTEGER NOT NULL, tracks INTEGER NOT NULL, added INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS albums_artist ON albums(artist_id, year, sort);
CREATE INDEX IF NOT EXISTS albums_sort ON albums(sort, artist_sort);
CREATE INDEX IF NOT EXISTS albums_added ON albums(added);
CREATE TABLE IF NOT EXISTS walk(dir TEXT PRIMARY KEY, root_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS todo(path TEXT PRIMARY KEY, dir TEXT NOT NULL, root_id INTEGER NOT NULL, mtime INTEGER NOT NULL, size INTEGER NOT NULL, tries INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_DERIVED_TABLES = ("tracks", "artists", "albums", "walk", "todo")


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open the library database, creating it on first use."""

    path = path or database_path()
    previous = os.umask(0o077)
    try:
        db = sqlite3.connect(path, timeout=30, isolation_level=None)
    finally:
        os.umask(previous)
    db.execute("PRAGMA busy_timeout = 30000")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    db.execute("PRAGMA temp_store = MEMORY")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    ready = version == SCHEMA_VERSION and db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN ('roots', 'tracks', 'meta', 'todo', 'walk', 'albums', 'artists')"
    ).fetchone()[0] == 7
    if not ready:
        # Only the first call, or a change of schema, takes the write lock; a query never does.
        with transaction(db, "IMMEDIATE"):
            if version != SCHEMA_VERSION:
                # Only the folders are the user's. The index is rebuilt by the next scan.
                for table in (*_DERIVED_TABLES, "meta"):
                    db.execute(f"DROP TABLE IF EXISTS {table}")
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    db.execute(statement)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return db


def _meta(db: sqlite3.Connection, key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _set_meta(db: sqlite3.Connection, key: str, value: object) -> None:
    db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


@contextlib.contextmanager
def transaction(db: sqlite3.Connection, mode: str = ""):
    """BEGIN ... COMMIT, with a ROLLBACK when the block raises, so the connection stays usable."""

    db.execute(f"BEGIN {mode}".strip())
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


# Folders ----------------------------------------------------------------------


def _expand_root(path: str) -> str:
    if path == "~" or path.startswith("~/"):
        path = os.path.join(os.environ.get("HOME", ""), path[2:])
    return os.path.normpath(os.path.abspath(path))


def _within(directory: str, path: str) -> bool:
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)


def list_roots(db: sqlite3.Connection) -> list[dict[str, Any]]:
    counts = dict(db.execute("SELECT root_id, COUNT(*) FROM tracks GROUP BY root_id"))
    return [
        {"path": path, "tracks": counts.get(root_id, 0), "available": bool(available)}
        for root_id, path, available in db.execute("SELECT id, path, available FROM roots ORDER BY id")
    ]


def add_root(db: sqlite3.Connection, path: str) -> None:
    if not (path == "~" or path.startswith("~/") or os.path.isabs(path)):
        # The helper's working directory is not the user's.
        raise LibraryError("the folder must be an absolute path")
    root = _expand_root(path)
    if not os.path.isdir(root):
        raise LibraryError(f"{root} is not a folder")
    real = os.path.realpath(root)
    with transaction(db, "IMMEDIATE"):
        existing = [row[0] for row in db.execute("SELECT path FROM roots")]
        if len(existing) >= MAX_ROOTS:
            raise LibraryError(f"at most {MAX_ROOTS} folders can be added")
        for have in existing:
            real_have = os.path.realpath(have)
            if root == have or real == real_have:
                raise LibraryError(f"{root} is already in the library")
            if _within(have, root) or _within(real_have, real):
                raise LibraryError(f"{root} is inside {have}, which is already in the library")
            if _within(root, have) or _within(real, real_have):
                raise LibraryError(f"{root} holds {have}, which is already in the library; remove that folder first")
        cursor = db.execute("INSERT INTO roots(path, added, available) VALUES(?, ?, 1)", (root, int(time.time())))
        if _meta(db, "phase", "idle") != "idle":
            # A scan is under way: walk the new folder too, even if it is reading tags now.
            db.execute("INSERT OR IGNORE INTO walk(dir, root_id) VALUES(?, ?)", (root, cursor.lastrowid))
            _set_meta(db, "phase", "walk")


def remove_root(db: sqlite3.Connection, path: str) -> None:
    root = _expand_root(path)
    with transaction(db, "IMMEDIATE"):
        row = db.execute("SELECT id FROM roots WHERE path = ?", (root,)).fetchone()
        if row is None:
            raise LibraryError(f"{root} is not in the library")
        for table in ("tracks", "walk", "todo"):
            db.execute(f"DELETE FROM {table} WHERE root_id = ?", (row[0],))
        db.execute("DELETE FROM roots WHERE id = ?", (row[0],))
    regroup(db)


# Scan -------------------------------------------------------------------------


def _file_name_title(path: str) -> tuple[str, str]:
    name = os.path.splitext(os.path.basename(path))[0]
    artist, sep, title = name.partition(" - ")
    if sep and artist.strip() and title.strip():
        return display_text(artist, maximum=256), display_text(title, maximum=256)
    return "", display_text(name, maximum=256)


def _readable_name(name: str) -> bool:
    if name.startswith(".") or name in SKIP_NAMES:
        return False
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    # The categories that every path must avoid to pass the plugin's validation
    # later: controls (including C1), invisible formatting marks and surrogates.
    return not any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in name)


def _is_audio(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS


def scan_progress(db: sqlite3.Connection) -> dict[str, Any]:
    phase = _meta(db, "phase", "idle")
    return {
        "scanning": phase != "idle",
        "phase": phase,
        "dirs": db.execute("SELECT COUNT(*) FROM walk").fetchone()[0],
        "pending": db.execute("SELECT COUNT(*) FROM todo").fetchone()[0],
        "tracks": db.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
    }


def start_scan(db: sqlite3.Connection) -> bool:
    """Begin a scan unless one is under way. Returns True when one begins."""

    with transaction(db, "IMMEDIATE"):
        if _meta(db, "phase", "idle") != "idle":
            return False
        run = int(_meta(db, "run", "0")) + 1
        _set_meta(db, "run", run)
        db.execute("DELETE FROM walk")
        db.execute("DELETE FROM todo")
        db.execute("UPDATE roots SET found = 0")
        for root_id, path in list(db.execute("SELECT id, path FROM roots")):
            try:
                with os.scandir(path):
                    pass
                available = 1
            except OSError:
                available = 0
            db.execute("UPDATE roots SET available = ? WHERE id = ?", (available, root_id))
            if available:
                db.execute("INSERT OR IGNORE INTO walk(dir, root_id) VALUES(?, ?)", (path, root_id))
        _set_meta(db, "phase", "walk")
        _set_meta(db, "started", int(time.time()))
        _set_meta(db, "regrouped", 0)
        _set_meta(db, "capped", 0)
    return True


def _walk_directory(db: sqlite3.Connection, run: int, directory: str, root_id: int, root_path: str) -> int:
    """Read one folder: queue its subfolders, and note which audio files need their tags read.

    Returns the number of files whose tags must be read.
    """

    depth = directory[len(root_path):].count(os.sep)
    known = {
        path: (mtime, size)
        for path, mtime, size in db.execute("SELECT path, mtime, size FROM tracks WHERE dir = ?", (directory,))
    }
    unchanged: list[tuple[int, str]] = []
    changed: list[tuple[str, str, int, int, int]] = []
    subdirectories: list[tuple[str, int]] = []
    found = 0
    try:
        entries = list(os.scandir(directory))
    except OSError:
        # The folder could not be read: what the index knows below it stays. The
        # prefix is compared exactly: LIKE would ignore the case of ASCII letters.
        prefix = directory.rstrip(os.sep) + os.sep
        db.execute(
            "UPDATE tracks SET seen = ? WHERE dir = ? OR substr(dir, 1, ?) = ?",
            (run, directory, len(prefix), prefix),
        )
        db.execute("DELETE FROM walk WHERE dir = ?", (directory,))
        return 0
    for entry in entries:
        name = entry.name
        if not _readable_name(name):
            continue
        path = entry.path
        try:
            if entry.is_dir(follow_symlinks=False):
                if depth < MAX_DEPTH:
                    subdirectories.append((entry.path, root_id))
                continue
            if not entry.is_file(follow_symlinks=False) or not _is_audio(name):
                continue
            info = entry.stat(follow_symlinks=False)
        except OSError:
            # A share that fails for a moment must not make a known track look deleted.
            if path in known:
                unchanged.append((run, path))
            continue
        if len(path.encode("utf-8")) > MAX_PATH_BYTES:
            continue
        found += 1
        mtime, size = int(info.st_mtime), info.st_size
        if known.get(path) == (mtime, size):
            unchanged.append((run, path))
        else:
            changed.append((path, directory, root_id, mtime, size))
    with transaction(db):
        db.executemany("UPDATE tracks SET seen = ? WHERE path = ?", unchanged)
        db.executemany("INSERT OR REPLACE INTO todo(path, dir, root_id, mtime, size) VALUES(?, ?, ?, ?, ?)", changed)
        db.executemany("INSERT OR IGNORE INTO walk(dir, root_id) VALUES(?, ?)", subdirectories)
        db.execute("UPDATE roots SET found = found + ? WHERE id = ?", (found, root_id))
        db.execute("DELETE FROM walk WHERE dir = ?", (directory,))
    return len(changed)


def _read_one(item: tuple[str, str, int, int, int]) -> tuple[tuple[str, str, int, int, int], dict[str, object]]:
    return item, read_tags(item[0])


def _tag_row(item: tuple[str, str, int, int, int], tags: dict[str, object], run: int) -> tuple[Any, ...]:
    path, directory, root_id, mtime, size = item
    title = str(tags.get("title", ""))
    artist = str(tags.get("artist", ""))
    if not title:
        name_artist, title = _file_name_title(path)
        artist = artist or name_artist
    album = str(tags.get("album", ""))
    return (
        path, directory, root_id, mtime, size, title, artist, album, str(tags.get("album_artist", "")),
        str(tags.get("genre", "")), int(tags.get("year", 0)), int(tags.get("track", 0)), int(tags.get("disc", 0)),
        fold(title), fold(artist), fold(album), run,
    )


_UPSERT = """
INSERT INTO tracks(path, dir, root_id, mtime, size, title, artist, album, album_artist, genre, year, track, disc,
                   title_f, artist_f, album_f, seen)
VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(path) DO UPDATE SET dir=excluded.dir, root_id=excluded.root_id, mtime=excluded.mtime, size=excluded.size,
  title=excluded.title, artist=excluded.artist, album=excluded.album, album_artist=excluded.album_artist,
  genre=excluded.genre, year=excluded.year, track=excluded.track, disc=excluded.disc,
  title_f=excluded.title_f, artist_f=excluded.artist_f, album_f=excluded.album_f, seen=excluded.seen
"""


def _read_tags_batch(db: sqlite3.Connection, run: int, deadline: float) -> int:
    rows = db.execute(
        "SELECT path, dir, root_id, mtime, size, tries FROM todo ORDER BY tries, path LIMIT ?", (TAG_BATCH,)
    ).fetchall()
    if not rows:
        return 0
    # A file that has held a reader too many times is indexed by its name: it must not
    # keep the scan from ever ending.
    give_up = [row for row in rows if row[5] >= TAG_TRIES]
    reading = [row for row in rows if row[5] < TAG_TRIES]
    results: list[tuple[tuple[str, str, int, int, int], dict[str, object]]] = [
        (tuple(row[:5]), {}) for row in give_up
    ]
    stuck: list[str] = []
    if reading:
        pool = ThreadPoolExecutor(max_workers=TAG_WORKERS)
        try:
            futures = {pool.submit(_read_one, tuple(row[:5])): row[0] for row in reading}
            # A file on a stalled share can hold a worker: it is left in the queue and counted.
            done, pending = wait(futures, timeout=max(TAG_WAIT_MIN_SECONDS, deadline - time.monotonic() + TAG_WAIT_SLACK_SECONDS))
            results += [future.result() for future in done if future.exception() is None]
            stuck = [futures[future] for future in pending]
            # A reader that failed with an error (not a hang) is indexed by its name too.
            results += [(tuple(next(r[:5] for r in reading if r[0] == futures[future])), {})
                        for future in done if future.exception() is not None]
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    live = {row[0] for row in db.execute("SELECT id FROM roots")}
    with transaction(db):
        # A folder removed while its tags were read must not bring its tracks back.
        db.executemany(_UPSERT, [_tag_row(item, tags, run) for item, tags in results if item[2] in live])
        db.executemany("DELETE FROM todo WHERE path = ?", [(item[0],) for item, _ in results])
        db.executemany("UPDATE todo SET tries = tries + 1 WHERE path = ?", [(path,) for path in stuck])
    return len(results) + len(stuck)


def regroup(db: sqlite3.Connection) -> None:
    """Group the tracks into artists and albums.

    Tracks group by album artist, then artist. A folder whose files share an album
    name but name several artists, with no album artist tag, is one compilation
    under Various Artists. Names that differ only in case or accents are one
    artist, and the spelling most files use is shown.
    """

    rows = db.execute(
        "SELECT path, dir, album, album_f, artist, artist_f, album_artist, year, mtime, album_id, link_artist_id FROM tracks"
    ).fetchall()
    # Names repeat across the tracks of an album, so each is folded once.
    cached_fold = lru_cache(maxsize=None)(fold)
    folder_artists: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _path, directory, _album, album_f, _artist, artist_f, album_artist, _year, _mtime, _a, _l in rows:
        if not album_artist and album_f:
            folder_artists[(directory, album_f)].add(artist_f)

    artist_names: dict[str, Counter[str]] = defaultdict(Counter)
    albums: dict[str, dict[str, Any]] = {}
    placed: list[tuple[str, str, str, str, str, str]] = []  # path, album id, artist id, own artist id, old ids
    for path, directory, album, album_f, artist, artist_f, album_artist, year, mtime, old_album, old_link in rows:
        shown = album_artist or artist
        if not album_artist and len(folder_artists.get((directory, album_f), ())) > 1:
            shown = VARIOUS_ARTISTS
        shown = shown.strip() or UNKNOWN_ARTIST
        album_name = album.strip() or UNKNOWN_ALBUM
        artist_key = cached_fold(shown)
        artist_id = group_id(artist_key)
        album_id = group_id(artist_key, cached_fold(album_name))
        artist_names[artist_id][shown] += 1
        entry = albums.get(album_id)
        if entry is None:
            entry = albums[album_id] = {"names": Counter(), "artist_id": artist_id, "year": 0, "tracks": 0, "added": 0}
        entry["names"][album_name] += 1
        entry["tracks"] += 1
        if year and (not entry["year"] or year < entry["year"]):
            entry["year"] = year
        entry["added"] = max(entry["added"], mtime)
        own = group_id(artist_f) if artist.strip() else ""
        placed.append((path, album_id, artist_id, own, old_album, old_link))

    def most_used(counter: Counter[str]) -> str:
        # A tie goes to the spelling that sorts first, so the choice does not depend on file order.
        return min(counter, key=lambda name: (-counter[name], name))

    artist_rows: dict[str, tuple[str, str, int]] = {}
    artist_display = {identifier: most_used(counter) for identifier, counter in artist_names.items()}
    album_rows = []
    albums_by_artist: Counter[str] = Counter()
    for album_id, entry in albums.items():
        name = most_used(entry["names"])
        artist_id = entry["artist_id"]
        albums_by_artist[artist_id] += 1
        album_rows.append((
            album_id, name, sort_key(name), artist_display[artist_id], artist_id,
            sort_key(artist_display[artist_id]), entry["year"], entry["tracks"], entry["added"],
        ))
    for artist_id, name in artist_display.items():
        artist_rows[artist_id] = (artist_id, name, sort_key(name), albums_by_artist[artist_id])
    # A track links to its own artist when that artist has albums of their own.
    updates = []
    for path, album_id, artist_id, own, old_album, old_link in placed:
        link = own if own in artist_rows else artist_id
        if (album_id, link) != (old_album, old_link):
            updates.append((album_id, link, path))
    with transaction(db, "IMMEDIATE"):
        db.executemany("UPDATE tracks SET album_id = ?, link_artist_id = ? WHERE path = ?", updates)
        db.execute("DELETE FROM artists")
        db.execute("DELETE FROM albums")
        db.executemany("INSERT INTO artists(id, name, sort, albums) VALUES(?, ?, ?, ?)", artist_rows.values())
        db.executemany(
            "INSERT INTO albums(id, name, sort, artist, artist_id, artist_sort, year, tracks, added) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            album_rows,
        )


def _finish_scan(db: sqlite3.Connection, run: int) -> None:
    """End a scan: drop what is gone, keep what could not be checked, and group."""

    with transaction(db, "IMMEDIATE"):
        capped = _meta(db, "capped", "0") == "1"
        for root_id, available, found in list(db.execute("SELECT id, available, found FROM roots")):
            has_tracks = db.execute("SELECT 1 FROM tracks WHERE root_id = ? LIMIT 1", (root_id,)).fetchone() is not None
            # An unmounted share looks like an empty folder: keep the saved index for it.
            unreadable = not available or (found == 0 and has_tracks)
            if capped or unreadable:
                # A scan that stopped short of some folders keeps their tracks too.
                db.execute("UPDATE tracks SET seen = ? WHERE root_id = ?", (run, root_id))
            db.execute("UPDATE roots SET available = ? WHERE id = ?", (0 if unreadable else 1, root_id))
        db.execute("DELETE FROM tracks WHERE seen != ?", (run,))
    # Group before the scan is marked ended: if this is cut short, the next step runs it again.
    regroup(db)
    with transaction(db, "IMMEDIATE"):
        _set_meta(db, "phase", "idle")
        _set_meta(db, "finished", int(time.time()))


def scan_step(db: sqlite3.Connection, budget_seconds: float) -> dict[str, Any]:
    """Run scan work for at most ``budget_seconds``. The scan resumes at the next call."""

    deadline = time.monotonic() + budget_seconds
    with transaction(db, "IMMEDIATE"):
        until = float(_meta(db, "step_until", "0"))
        busy = until > time.time()
        if not busy:
            _set_meta(db, "step_until", time.time() + budget_seconds + 30)
        phase = _meta(db, "phase", "idle")
        run = int(_meta(db, "run", "0"))
    if busy:
        return {**scan_progress(db), "busy": True}
    try:
        if phase == "walk":
            roots = {row[0]: row[1] for row in db.execute("SELECT id, path FROM roots")}
            queued = db.execute("SELECT COUNT(*) FROM todo").fetchone()[0]
            # Every step does at least one folder, so a scan always moves on.
            first = True
            while first or time.monotonic() < deadline:
                first = False
                row = db.execute("SELECT dir, root_id FROM walk LIMIT 1").fetchone()
                if row is None:
                    _set_meta(db, "phase", "tags")
                    phase = "tags"
                    break
                if row[1] not in roots:
                    # A folder may have been added since this step began.
                    roots = {r[0]: r[1] for r in db.execute("SELECT id, path FROM roots")}
                    if row[1] not in roots:
                        db.execute("DELETE FROM walk WHERE dir = ?", (row[0],))
                        continue
                if queued > MAX_TRACKS_TOTAL:
                    # More files than the library takes: stop reading folders, and keep
                    # the tracks of the folders that were not reached.
                    _set_meta(db, "capped", 1)
                    db.execute("DELETE FROM walk")
                    continue
                queued += _walk_directory(db, run, row[0], row[1], roots[row[1]])
        if phase == "tags":
            first = True
            while first or time.monotonic() < deadline:
                first = False
                done = _read_tags_batch(db, run, deadline)
                if done == 0:
                    if db.execute("SELECT 1 FROM todo LIMIT 1").fetchone() is None:
                        _finish_scan(db, run)
                    break
                # Show the first results while a long scan goes on, but spend at most
                # about a sixth of the time on it: grouping a large library takes seconds.
                waited = time.time() - float(_meta(db, "regrouped", "0"))
                if waited > max(REGROUP_INTERVAL_SECONDS, 6 * float(_meta(db, "regroup_seconds", "0"))):
                    started = time.monotonic()
                    regroup(db)
                    _set_meta(db, "regroup_seconds", time.monotonic() - started)
                    _set_meta(db, "regrouped", time.time())
    finally:
        _set_meta(db, "step_until", 0)
    return {**scan_progress(db), "busy": False}


# Queries ----------------------------------------------------------------------


def _tracks(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """The tracks of ``rows``. A row that fails validation is left out; it must not spoil a page."""

    tracks = []
    for row in rows:
        try:
            tracks.append(_track(row))
        except ValidationError:
            continue
    return tracks


def _track(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    path, title, artist, album, genre, year, track, album_id, artist_id = row
    item: dict[str, Any] = {
        "path": path,
        "title": title,
        "artist": artist,
        "album": album,
        "genre": genre,
        "year": year,
        "track_number": track,
        "provider_meta": {
            "library.id": group_id(path),
            "library.album": album_id,
            "library.artist": artist_id,
        },
    }
    return normalize_track({key: value for key, value in item.items() if value not in ("", 0) or key == "path"})


_TRACK_COLUMNS = "path, title, artist, album, genre, year, track, album_id, link_artist_id"


def list_artists(db: sqlite3.Connection, offset: int, limit: int) -> dict[str, Any]:
    rows = db.execute("SELECT id, name, albums FROM artists ORDER BY sort, id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    total = db.execute("SELECT COUNT(*) FROM artists").fetchone()[0]
    return {
        "artists": [{"id": i, "name": n, "album_count": c} for i, n, c in rows],
        "total": total,
    }


def _album(row: tuple[Any, ...]) -> dict[str, Any]:
    identifier, name, artist, artist_id, year, tracks = row
    item: dict[str, Any] = {"id": identifier, "name": name, "artist": artist, "artist_id": artist_id, "track_count": tracks}
    if year:
        item["year"] = year
    return item


_ALBUM_COLUMNS = "id, name, artist, artist_id, year, tracks"


def artist_albums(db: sqlite3.Connection, artist_id: str, offset: int, limit: int) -> dict[str, Any]:
    exists = db.execute("SELECT 1 FROM artists WHERE id = ?", (artist_id,)).fetchone()
    if exists is None:
        raise LibraryError("that artist is no longer in the library")
    rows = db.execute(
        f"SELECT {_ALBUM_COLUMNS} FROM albums WHERE artist_id = ? ORDER BY year = 0, year, sort, id LIMIT ? OFFSET ?",
        (artist_id, limit, offset),
    ).fetchall()
    total = db.execute("SELECT COUNT(*) FROM albums WHERE artist_id = ?", (artist_id,)).fetchone()[0]
    return {"albums": [_album(row) for row in rows], "total": total}


_ALBUM_ORDER = {
    "alphabeticalByName": "sort, artist_sort, id",
    "alphabeticalByArtist": "artist_sort, year = 0, year, sort, id",
    "newest": "added DESC, sort, id",
    "byYear": "year = 0, year, sort, artist_sort, id",
}


def list_albums(db: sqlite3.Connection, sort: str, offset: int, limit: int) -> dict[str, Any]:
    order = _ALBUM_ORDER.get(sort, _ALBUM_ORDER["alphabeticalByName"])
    rows = db.execute(f"SELECT {_ALBUM_COLUMNS} FROM albums ORDER BY {order} LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    total = db.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
    return {
        "albums": [_album(row) for row in rows],
        "total": total,
        "sorts": [{"id": identifier, "label": label} for identifier, label in ALBUM_SORTS],
    }


def album_tracks(db: sqlite3.Connection, album_id: str, offset: int, limit: int) -> dict[str, Any]:
    rows = db.execute(
        f"SELECT {_TRACK_COLUMNS} FROM tracks WHERE album_id = ? ORDER BY disc, track, path LIMIT ? OFFSET ?",
        (album_id, limit, offset),
    ).fetchall()
    total = db.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (album_id,)).fetchone()[0]
    if total == 0:
        raise LibraryError("that album is no longer in the library")
    return {"tracks": _tracks(rows), "total": total}


_FIELD_BONUS = (10, 6, 0)  # title, artist, album


def word_score(word: str, field: str) -> int:
    if not field:
        return 0
    if field == word:
        return 100
    position = field.find(word)
    if position < 0:
        return 0
    if position == 0:
        return 60
    while position >= 0:
        if not field[position - 1].isalnum():
            return 40
        position = field.find(word, position + 1)
    return 20


def search_tracks(db: sqlite3.Connection, query: str, limit: int) -> dict[str, Any]:
    """Tracks whose title, artist or album hold every word of ``query``, best first."""

    words = fold(query).split()[:8]
    if not words:
        return {"tracks": [], "total": 0}
    clause = " AND ".join(
        "(title_f LIKE ? ESCAPE '\\' OR artist_f LIKE ? ESCAPE '\\' OR album_f LIKE ? ESCAPE '\\')" for _ in words
    )
    parameters: list[object] = [_like(word) for word in words for _ in range(3)]
    parameters.append(SEARCH_CANDIDATES)
    rows = db.execute(
        f"SELECT {_TRACK_COLUMNS}, title_f, artist_f, album_f, disc FROM tracks WHERE {clause} LIMIT ?", parameters
    ).fetchall()
    scored: list[tuple[int, str, str, int, int, str, tuple[Any, ...]]] = []
    for row in rows:
        title_f, artist_f, album_f, disc = row[9], row[10], row[11], row[12]
        total = 0
        for word in words:
            best = 0
            for field, bonus in zip((title_f, artist_f, album_f), _FIELD_BONUS):
                score = word_score(word, field)
                if score and score + bonus > best:
                    best = score + bonus
            total += best
        scored.append((-total, artist_f, album_f, disc, row[6], title_f, row[:9]))
    if not scored:
        scored = _fuzzy(db, "".join(words))
    scored.sort(key=lambda item: item[:6])
    return {"tracks": _tracks([item[6] for item in scored[:limit]]), "total": min(len(scored), 1_000_000)}


def _fuzzy(db: sqlite3.Connection, letters: str) -> list[tuple[int, str, str, int, int, str, tuple[Any, ...]]]:
    """Tracks whose title, artist or album hold the letters in order, tightest match first."""

    if len(letters) < 2:
        return []
    pattern = re.compile(".*?".join(re.escape(letter) for letter in letters[:24]))
    found = []
    for row in db.execute(
        f"SELECT {_TRACK_COLUMNS}, title_f, artist_f, album_f, disc FROM tracks LIMIT ?", (FUZZY_ROWS,)
    ):
        best = None
        for field in (row[9], row[10], row[11]):
            match = pattern.search(field)
            if match and (best is None or match.end() - match.start() < best):
                best = match.end() - match.start()
        if best is not None:
            found.append((best, row[10], row[11], row[12], row[6], row[9], row[:9]))
    return found


# Queue files ------------------------------------------------------------------


def _queue_directory() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isabs(runtime) and os.path.isdir(runtime):
        base = os.path.join(runtime, "cliamped")
    else:
        home = os.environ.get("HOME", "")
        if not os.path.isabs(home):
            raise LibraryError("HOME is not set")
        base = os.path.join(home, ".cache", "cliamped")
    return _private_directory(base)


def write_queue_file(db: sqlite3.Connection, album_id: str) -> dict[str, Any]:
    """Write an M3U8 of an album for CLIAMP's ``url.load``, which loads it in one call.

    The file holds bare paths. CLIAMP reads the tags of a file itself when its
    playlist line has no #EXTINF description, so it shows the artist and the
    album; a description would be taken whole as the title.
    """

    rows = db.execute(
        "SELECT path FROM tracks WHERE album_id = ? ORDER BY disc, track, path LIMIT ?",
        (album_id, M3U_MAX_TRACKS),
    ).fetchall()
    if not rows:
        raise LibraryError("that album is no longer in the library")
    directory = _queue_directory()
    cutoff = time.time() - QUEUE_FILE_MAX_AGE_SECONDS
    for name in os.listdir(directory):
        if name.startswith("queue-") and name.endswith(".m3u8"):
            try:
                if os.lstat(os.path.join(directory, name)).st_mtime < cutoff:
                    os.unlink(os.path.join(directory, name))
            except OSError:
                pass
    lines = ["#EXTM3U"] + [path for (path,) in rows]
    target = os.path.join(directory, f"queue-{secrets.token_hex(8)}.m3u8")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return {"path": target, "count": len(rows)}


# Requests ---------------------------------------------------------------------

_PAGED = {"artists", "artist_albums", "albums", "album_tracks"}


def normalize_library_request(request: Any) -> dict[str, Any]:
    """Validate a request of the panel. Anything not listed is refused."""

    if not isinstance(request, dict):
        raise ValidationError("request must be an object")
    operation = _opaque_string(request.get("op"), field="op", maximum=32)

    def copy(allowed: set[str]) -> dict[str, Any]:
        _require_keys(request, {"op", *allowed}, context="request")
        return {"op": operation}

    if operation in {"roots"}:
        return copy(set())
    if operation in {"add_root", "remove_root"}:
        result = copy({"path"})
        if "path" not in request:
            raise ValidationError(f"{operation} requires path")
        result["path"] = _opaque_string(request["path"], field="path", maximum=MAX_PATH_BYTES)
        return result
    if operation == "scan":
        result = copy({"fresh", "budget"})
        if "fresh" in request:
            result["fresh"] = _boolean(request["fresh"], field="fresh")
        if "budget" in request:
            result["budget"] = _integer(request["budget"], field="budget", minimum=1, maximum=SCAN_BUDGET_MAX)
        return result
    if operation in _PAGED:
        allowed = {"offset", "limit"}
        item = {"artist_albums": "artist", "album_tracks": "album"}.get(operation)
        if item:
            allowed.add(item)
        if operation == "albums":
            allowed.add("sort")
        result = copy(allowed)
        if item:
            if item not in request:
                raise ValidationError(f"{operation} requires {item}")
            result[item] = _opaque_string(request[item], field=item, maximum=MAX_IDENTIFIER_BYTES)
        if "sort" in request:
            result["sort"] = _opaque_string(request["sort"], field="sort", maximum=64)
        result["offset"] = _integer(request.get("offset", 0), field="offset", maximum=1_000_000_000)
        result["limit"] = _integer(request.get("limit", 60), field="limit", minimum=1, maximum=PAGE_MAX)
        return result
    if operation == "search":
        result = copy({"query", "limit"})
        if "query" not in request:
            raise ValidationError("search requires query")
        result["query"] = _opaque_string(request["query"], field="query", maximum=512)
        result["limit"] = _integer(request.get("limit", 50), field="limit", minimum=1, maximum=SEARCH_LIMIT_MAX)
        return result
    if operation == "queue_file":
        result = copy({"album"})
        if "album" not in request:
            raise ValidationError("queue_file requires album")
        result["album"] = _opaque_string(request["album"], field="album", maximum=MAX_IDENTIFIER_BYTES)
        return result
    raise ValidationError("unsupported library operation")


def execute(request: dict[str, Any], db: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Run one validated request and return its result."""

    request = normalize_library_request(request)
    db = db or connect()
    operation = request["op"]
    result: dict[str, Any] = {"ok": True}
    if operation == "roots":
        result["roots"] = list_roots(db)
        result.update(scan_progress(db))
    elif operation == "add_root":
        add_root(db, request["path"])
        start_scan(db)
        result["roots"] = list_roots(db)
        result.update(scan_progress(db))
    elif operation == "remove_root":
        remove_root(db, request["path"])
        result["roots"] = list_roots(db)
        result.update(scan_progress(db))
    elif operation == "scan":
        if request.get("fresh"):
            start_scan(db)
        if _meta(db, "phase", "idle") != "idle":
            result.update(scan_step(db, request.get("budget", SCAN_BUDGET_DEFAULT)))
        else:
            result.update(scan_progress(db))
        result["roots"] = list_roots(db)
    elif operation == "artists":
        result.update(list_artists(db, request["offset"], request["limit"]))
    elif operation == "artist_albums":
        result.update(artist_albums(db, request["artist"], request["offset"], request["limit"]))
    elif operation == "albums":
        result.update(list_albums(db, request.get("sort", ""), request["offset"], request["limit"]))
    elif operation == "album_tracks":
        result.update(album_tracks(db, request["album"], request["offset"], request["limit"]))
    elif operation == "search":
        result.update(search_tracks(db, request["query"], request["limit"]))
    elif operation == "queue_file":
        result.update(write_queue_file(db, request["album"]))
    return result


# Process ----------------------------------------------------------------------

_LISTS = ("tracks", "albums", "artists", "roots")


def fit_output(result: dict[str, Any]) -> bytes:
    """Encode ``result`` within the output cap, dropping trailing list items if it is too large.

    A page of 200 tracks with maximum-length tags can exceed the cap. The panel
    pages by what it received, so a shorter page is correct, and ``truncated``
    says so.
    """

    for _ in range(12):
        try:
            return encode_json(result, maximum=MAX_OUTPUT_BYTES)
        except ValidationError:
            longest = max((name for name in _LISTS if isinstance(result.get(name), list)),
                          key=lambda name: len(result[name]), default=None)
            if longest is None or len(result[longest]) <= 1:
                raise
            result[longest] = result[longest][: max(1, len(result[longest]) // 2)]
            result["truncated"] = True
    return encode_json(result, maximum=MAX_OUTPUT_BYTES)


def _arm_helper_parent_death() -> None:
    """Ensure this one-shot helper cannot outlive its QML owner."""

    expected_parent = os.getppid()
    if expected_parent <= 1:
        raise LibraryError("the library helper has no live owner")
    if _LIBC.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise LibraryError("cannot arm library-helper owner cleanup")
    if os.getppid() != expected_parent:
        raise LibraryError("the library-helper owner exited during startup")


def main() -> None:
    try:
        _arm_helper_parent_death()
        if len(sys.argv) != 2 or len(sys.argv[1].encode("utf-8", "surrogatepass")) > 16 * 1024:
            raise LibraryError("usage: cliamped_library.py <request-json>")
        output = fit_output(execute(json.loads(sys.argv[1])))
        code = 0
    except (OSError, ValueError, sqlite3.Error, RecursionError, UnicodeError) as error:
        output = encode_json({"ok": False, "error": error_text(str(error))})
        code = 1
    sys.stdout.buffer.write(output + b"\n")
    sys.stdout.buffer.flush()
    # Do not wait for a tag read that is stuck on a stalled share.
    os._exit(code)


if __name__ == "__main__":
    main()
