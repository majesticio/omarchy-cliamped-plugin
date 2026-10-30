import inspect
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cliamped_library as library
from cliamped_security import ValidationError

from tests.test_tags import flac_file, id3_frame, id3_tag


def mp3(title, artist="", album="", *, album_artist="", track=0, year=0, disc=0):
    frames = [id3_frame(b"TIT2", title, encoding=3, version=4)]
    for frame_id, value in (
        (b"TPE1", artist), (b"TALB", album), (b"TPE2", album_artist),
        (b"TRCK", str(track) if track else ""), (b"TYER", str(year) if year else ""),
        (b"TPOS", str(disc) if disc else ""),
    ):
        if value:
            frames.append(id3_frame(frame_id, value, encoding=3, version=4))
    return id3_tag(frames, version=4) + b"\x00" * 128


class LibraryTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.home.mkdir()
        self.music = Path(self.temporary.name) / "music"
        self.music.mkdir()
        patcher = patch.dict(os.environ, {"HOME": str(self.home), "XDG_RUNTIME_DIR": str(Path(self.temporary.name) / "run")})
        patcher.start()
        self.addCleanup(patcher.stop)
        (Path(self.temporary.name) / "run").mkdir(mode=0o700)
        self.db = library.connect(str(self.home / "library.sqlite3"))
        self.addCleanup(self.db.close)

    def add(self, relative, data):
        path = self.music / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self, *, fresh=True, budget=5):
        """Run scan steps until the scan ends, and return the number of steps."""

        if fresh:
            library.start_scan(self.db)
        for step in range(1, 200):
            progress = library.scan_step(self.db, budget)
            if not progress["scanning"]:
                return step
        self.fail("the scan did not end")

    def fixture(self):
        self.add("Pink Floyd/Wall/01.mp3", mp3("In the Flesh", "Pink Floyd", "The Wall", album_artist="Pink Floyd", track=1, year=1979, disc=1))
        self.add("Pink Floyd/Wall/02.mp3", mp3("The Thin Ice", "Pink Floyd", "The Wall", album_artist="Pink Floyd", track=2, year=1979, disc=1))
        self.add("Pink Floyd/Wall/d2.mp3", mp3("Hey You", "Pink Floyd", "The Wall", album_artist="Pink Floyd", track=1, year=1979, disc=2))
        self.add("Pink Floyd/Dark/01.mp3", mp3("Speak to Me", "pink floyd", "The Dark Side of the Moon", album_artist="pink floyd", track=1, year=1973))
        self.add("Pink Floyd/Dark/02.mp3", mp3("Money", "Pink Floyd", "The Dark Side of the Moon", album_artist="Pink Floyd", track=6, year=1973))
        self.add("Beyoncé/Fierce/01.mp3", mp3("Halo", "Beyoncé", "I Am... Sasha Fierce", album_artist="Beyoncé", track=1, year=2008))
        self.add("ABBA/Gold/01.mp3", mp3("Dancing Queen", "ABBA", "Gold", album_artist="ABBA", track=1, year=1992))
        self.add("Hits/01.mp3", mp3("One", "Band A", "Hits 1", track=1))
        self.add("Hits/02.mp3", mp3("Two", "Band B", "Hits 1", track=2))
        self.add("BandA/Own/01.mp3", mp3("Own Song", "Band A", "Own Album", album_artist="Band A", track=1))
        self.add("Loose/Artist X - Named By File.mp3", b"no tags here")
        self.add("Loose/plain.flac", flac_file([b"TITLE=Flac Song", b"ARTIST=Flac Band", b"ALBUM=Flac Album"]))
        # None of these belong in the library.
        self.add("ABBA/Gold/._01.mp3", mp3("Fork", "ABBA", "Gold"))
        self.add("@eaDir/thumb.mp3", mp3("Thumb", "X", "Y"))
        self.add(".hidden/h.mp3", mp3("Hidden", "X", "Y"))
        self.add("notes.txt", b"not audio")
        self.add("cover.jpg", b"not audio")


class RootTests(LibraryTestCase):
    def test_database_is_private_and_versioned(self):
        path = library.database_path()
        self.assertEqual(os.path.dirname(path), str(self.home / ".local" / "share" / "cliamped"))
        self.assertEqual(os.stat(os.path.dirname(path)).st_mode & 0o777, 0o700)
        db = library.connect(path)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], library.SCHEMA_VERSION)
        self.assertEqual(os.stat(path).st_mode & 0o077, 0)

    def test_a_directory_of_another_user_or_open_to_others_is_refused(self):
        shared = self.home / ".local" / "share" / "cliamped"
        shared.mkdir(parents=True, mode=0o755)
        os.chmod(shared, 0o777)
        with self.assertRaises(library.LibraryError):
            library.database_path()

    def test_folders_survive_a_schema_change_but_the_index_does_not(self):
        self.fixture()
        library.add_root(self.db, str(self.music))
        self.scan()
        self.assertGreater(library.scan_progress(self.db)["tracks"], 0)
        self.db.execute("PRAGMA user_version = 0")
        again = library.connect(str(self.home / "library.sqlite3"))
        self.addCleanup(again.close)
        self.assertEqual([r["path"] for r in library.list_roots(again)], [str(self.music)])
        self.assertEqual(library.scan_progress(again)["tracks"], 0)

    def test_add_root_rules(self):
        other = Path(self.temporary.name) / "other"
        other.mkdir()
        library.add_root(self.db, str(self.music))
        link = Path(self.temporary.name) / "link"
        link.symlink_to(self.music)
        (self.music / "child").mkdir()
        file = other / "f.mp3"
        file.write_bytes(b"x")
        cases = {
            "not a folder": (str(file), "not a folder"),
            "missing": (str(other / "nope"), "not a folder"),
            "the same folder": (str(self.music) + "/", "already in the library"),
            "a link to it": (str(link), "already in the library"),
            "inside it": (str(self.music / "child"), "is inside"),
            "above it": (self.temporary.name, "holds"),
        }
        for name, (path, message) in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(library.LibraryError, message):
                library.add_root(self.db, path)
        library.add_root(self.db, str(other))
        self.assertEqual(len(library.list_roots(self.db)), 2)

    def test_at_most_64_folders(self):
        folders = []
        for index in range(library.MAX_ROOTS):
            folder = Path(self.temporary.name) / f"f{index}"
            folder.mkdir()
            library.add_root(self.db, str(folder))
            folders.append(folder)
        extra = Path(self.temporary.name) / "extra"
        extra.mkdir()
        with self.assertRaisesRegex(library.LibraryError, "at most"):
            library.add_root(self.db, str(extra))

    def test_remove_root_drops_its_tracks_only(self):
        self.fixture()
        other = Path(self.temporary.name) / "other"
        other.mkdir()
        (other / "k.mp3").write_bytes(mp3("Keep", "K", "K"))
        library.add_root(self.db, str(self.music))
        library.add_root(self.db, str(other))
        self.scan()
        library.remove_root(self.db, str(self.music))
        self.assertEqual([t["title"] for t in library.search_tracks(self.db, "keep", 5)["tracks"]], ["Keep"])
        self.assertEqual(library.search_tracks(self.db, "money", 5)["tracks"], [])
        self.assertEqual(library.list_artists(self.db, 0, 50)["total"], 1)
        self.assertTrue((self.music / "Pink Floyd" / "Dark" / "02.mp3").exists(), "removing a folder must not touch the files")
        with self.assertRaisesRegex(library.LibraryError, "not in the library"):
            library.remove_root(self.db, str(self.music))


class ScanTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.fixture()
        library.add_root(self.db, str(self.music))

    def test_first_scan_indexes_the_audio_and_skips_the_rest(self):
        self.scan()
        paths = {row[0] for row in self.db.execute("SELECT path FROM tracks")}
        names = {os.path.relpath(p, self.music) for p in paths}
        self.assertEqual(len(names), 12)
        for skipped in ("ABBA/Gold/._01.mp3", "@eaDir/thumb.mp3", ".hidden/h.mp3", "notes.txt", "cover.jpg"):
            self.assertNotIn(skipped, names)
        self.assertEqual(library.list_roots(self.db), [{"path": str(self.music), "tracks": 12, "available": True}])

    def test_a_file_without_tags_is_named_from_its_file_name(self):
        self.scan()
        row = self.db.execute("SELECT title, artist FROM tracks WHERE path LIKE '%Named By File.mp3'").fetchone()
        self.assertEqual(tuple(row), ("Named By File", "Artist X"))

    def test_flac_tags_are_read(self):
        self.scan()
        row = self.db.execute("SELECT title, artist, album FROM tracks WHERE path LIKE '%plain.flac'").fetchone()
        self.assertEqual(tuple(row), ("Flac Song", "Flac Band", "Flac Album"))

    def test_names_with_control_characters_are_not_indexed(self):
        self.add("Odd/bad\nname.mp3", mp3("Newline", "X", "Y"))
        self.add("Odd/fine.mp3", mp3("Fine", "X", "Y"))
        self.scan()
        titles = {row[0] for row in self.db.execute("SELECT title FROM tracks")}
        self.assertIn("Fine", titles)
        self.assertNotIn("Newline", titles)

    def test_symlinks_are_not_followed(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (outside / "o.mp3").write_bytes(mp3("Outside", "X", "Y"))
        (self.music / "link").symlink_to(outside)
        (self.music / "filelink.mp3").symlink_to(outside / "o.mp3")
        self.scan()
        self.assertEqual(library.search_tracks(self.db, "outside", 5)["tracks"], [])

    def test_rescan_reads_only_what_changed(self):
        self.scan()
        reads = []
        original = library.read_tags

        def counting(path):
            reads.append(os.path.relpath(path, self.music))
            return original(path)

        with patch.object(library, "read_tags", counting):
            self.scan()
            self.assertEqual(reads, [], "an unchanged library must not read any file")
            changed = self.music / "Pink Floyd" / "Dark" / "02.mp3"
            changed.write_bytes(mp3("Money (remaster)", "Pink Floyd", "The Dark Side of the Moon", album_artist="Pink Floyd", track=6, year=1973))
            os.utime(changed, (time.time() + 10, time.time() + 10))
            self.add("New/n.mp3", mp3("Newcomer", "New", "Album"))
            (self.music / "ABBA" / "Gold" / "01.mp3").unlink()
            self.scan()
        self.assertEqual(sorted(reads), ["New/n.mp3", "Pink Floyd/Dark/02.mp3"])
        titles = {row[0] for row in self.db.execute("SELECT title FROM tracks")}
        self.assertIn("Money (remaster)", titles)
        self.assertIn("Newcomer", titles)
        self.assertNotIn("Dancing Queen", titles)

    def test_the_scan_resumes_across_processes_and_tiny_budgets(self):
        steps = self.scan(budget=0)
        self.assertGreater(steps, 10, "a zero budget still does one unit of work a step, so the scan splits")
        self.assertEqual(library.scan_progress(self.db)["tracks"], 12)
        # A second connection stands for another process that picks up an unfinished scan.
        library.start_scan(self.db)
        library.scan_step(self.db, 0.001)
        other = library.connect(str(self.home / "library.sqlite3"))
        self.addCleanup(other.close)
        self.assertTrue(library.scan_progress(other)["scanning"])
        for _ in range(200):
            if not library.scan_step(other, 5)["scanning"]:
                break
        self.assertFalse(library.scan_progress(other)["scanning"])

    def test_a_second_step_at_the_same_time_is_told_to_wait(self):
        library.start_scan(self.db)
        library._set_meta(self.db, "step_until", time.time() + 60)
        self.assertTrue(library.scan_step(self.db, 1)["busy"])
        library._set_meta(self.db, "step_until", 0)
        self.assertFalse(library.scan_step(self.db, 1)["busy"])

    def test_start_scan_continues_a_scan_under_way(self):
        self.assertTrue(library.start_scan(self.db))
        self.assertFalse(library.start_scan(self.db))

    def test_an_unmounted_share_keeps_its_saved_index(self):
        self.scan()
        before = library.scan_progress(self.db)["tracks"]
        hidden = Path(self.temporary.name) / "music-away"
        self.music.rename(hidden)
        self.scan()
        self.assertEqual(library.scan_progress(self.db)["tracks"], before)
        self.assertFalse(library.list_roots(self.db)[0]["available"])
        # The empty mount point of an unmounted share counts as unavailable too.
        self.music.mkdir()
        self.scan()
        self.assertEqual(library.scan_progress(self.db)["tracks"], before)
        self.assertFalse(library.list_roots(self.db)[0]["available"])
        # Back again: the folder is readable and the index is current.
        self.music.rmdir()
        hidden.rename(self.music)
        self.scan()
        self.assertTrue(library.list_roots(self.db)[0]["available"])
        self.assertEqual(library.scan_progress(self.db)["tracks"], before)

    def test_an_unreadable_subfolder_keeps_what_the_index_knew(self):
        self.scan()
        real_scandir = os.scandir
        target = str(self.music / "Pink Floyd")

        def failing(path):
            if str(path) == target:
                raise PermissionError("denied")
            return real_scandir(path)

        with patch("os.scandir", failing):
            self.scan()
        titles = {row[0] for row in self.db.execute("SELECT title FROM tracks")}
        self.assertIn("Money", titles)
        self.assertIn("Dancing Queen", titles)

    def test_a_folder_added_during_a_scan_is_scanned_in_it(self):
        other = Path(self.temporary.name) / "other"
        other.mkdir()
        (other / "late.mp3").write_bytes(mp3("Late", "L", "L"))
        library.start_scan(self.db)
        library.scan_step(self.db, 0.001)
        library.add_root(self.db, str(other))
        self.scan(fresh=False)
        self.assertEqual([t["title"] for t in library.search_tracks(self.db, "late", 5)["tracks"]], ["Late"])

    def test_a_removed_folder_does_not_come_back_from_a_scan_under_way(self):
        library.start_scan(self.db)
        library.scan_step(self.db, 0.001)
        library.remove_root(self.db, str(self.music))
        self.scan(fresh=False)
        self.assertEqual(library.scan_progress(self.db)["tracks"], 0)

    def test_depth_is_limited(self):
        deep = self.music
        for index in range(library.MAX_DEPTH + 5):
            deep = deep / f"d{index}"
        deep.mkdir(parents=True)
        (deep / "deep.mp3").write_bytes(mp3("Too Deep", "X", "Y"))
        self.scan()
        self.assertEqual(library.search_tracks(self.db, "too deep", 5)["tracks"], [])


class ScanRobustnessTests(LibraryTestCase):
    """Failures that a share can cause: hung or hostile files, errors that come and go, races."""

    def setUp(self):
        super().setUp()
        self.fixture()
        library.add_root(self.db, str(self.music))

    def other_connection(self):
        db = library.connect(str(self.home / "library.sqlite3"))
        self.addCleanup(db.close)
        return db

    def test_a_file_that_hangs_a_reader_is_indexed_by_name_and_the_scan_ends(self):
        import threading

        release = threading.Event()
        self.addCleanup(release.set)
        stuck = str(self.music / "Loose" / "plain.flac")
        original = library.read_tags

        def hanging(path):
            if path == stuck:
                release.wait(30)
            return original(path)

        with patch.object(library, "read_tags", hanging), \
                patch.object(library, "TAG_WAIT_MIN_SECONDS", 0.05), patch.object(library, "TAG_WAIT_SLACK_SECONDS", 0.0):
            steps = self.scan(budget=0)
        self.assertLess(steps, 500, "the scan must end")
        row = self.db.execute("SELECT title, artist FROM tracks WHERE path = ?", (stuck,)).fetchone()
        self.assertEqual(tuple(row), ("plain", ""), "indexed by its name, without tags")
        self.assertEqual(library.scan_progress(self.db)["tracks"], 12)

    def test_a_reader_that_raises_does_not_stop_the_scan(self):
        bad = str(self.music / "Loose" / "plain.flac")
        original = library.read_tags

        def raising(path):
            if path == bad:
                raise RuntimeError("boom")
            return original(path)

        with patch.object(library, "read_tags", raising):
            self.scan()
        self.assertEqual(library.scan_progress(self.db)["tracks"], 12)
        self.assertEqual(library.scan_progress(self.db)["pending"], 0)

    def test_names_with_invisible_or_c1_characters_are_not_indexed(self):
        for name in ("zero\u200bwidth.mp3", "c1\x85.mp3", "bidi\u202e.mp3", "mark\u200e.mp3"):
            self.add("Hostile/" + name, mp3("Hostile " + name, "X", "Hostile Album", album_artist="X"))
        self.add("Hostile/fine.mp3", mp3("Fine", "X", "Hostile Album", album_artist="X"))
        self.scan()
        album = next(a for a in library.list_albums(self.db, "", 0, 200)["albums"] if a["name"] == "Hostile Album")
        self.assertEqual(album["track_count"], 1)
        tracks = library.album_tracks(self.db, album["id"], 0, 50)["tracks"]
        self.assertEqual([t["title"] for t in tracks], ["Fine"])

    def test_a_row_that_fails_validation_is_left_out_of_a_page_not_fatal_to_it(self):
        self.scan()
        self.db.execute("UPDATE tracks SET path = path || char(133) WHERE title = 'Money'")
        found = library.search_tracks(self.db, "floyd", 50)["tracks"]
        titles = [t["title"] for t in found]
        self.assertNotIn("Money", titles)
        self.assertIn("Speak to Me", titles)

    def test_a_folder_added_while_tags_are_read_is_scanned(self):
        other = Path(self.temporary.name) / "other"
        other.mkdir()
        (other / "late.mp3").write_bytes(mp3("Late", "L", "L"))
        library.start_scan(self.db)
        for _ in range(100):  # into the tag phase
            library.scan_step(self.db, 0)
            if library._meta(self.db, "phase") == "tags":
                break
        self.assertEqual(library._meta(self.db, "phase"), "tags")
        library.add_root(self.other_connection(), str(other))
        self.scan(fresh=False)
        self.assertEqual([t["title"] for t in library.search_tracks(self.db, "late", 5)["tracks"]], ["Late"])

    def test_a_folder_added_by_another_process_during_a_walk_step_is_not_dropped(self):
        other = Path(self.temporary.name) / "other"
        other.mkdir()
        (other / "late.mp3").write_bytes(mp3("Late", "L", "L"))
        library.start_scan(self.db)
        original = library._walk_directory
        added = []

        def adding(db, run, directory, root_id, root_path):
            if not added:
                added.append(True)
                library.add_root(self.other_connection(), str(other))
            return original(db, run, directory, root_id, root_path)

        with patch.object(library, "_walk_directory", adding):
            self.scan(fresh=False)
        self.assertEqual([t["title"] for t in library.search_tracks(self.db, "late", 5)["tracks"]], ["Late"])

    def test_a_listing_error_that_comes_and_goes_does_not_delete_a_track(self):
        self.scan()
        real_scandir = os.scandir
        target = "Money"  # the file 02.mp3 of the Dark Side folder

        class Flaky:
            def __init__(self, entry):
                self._entry = entry
                self.name = entry.name
                self.path = entry.path

            def is_dir(self, **kwargs):
                return self._entry.is_dir(**kwargs)

            def is_file(self, **kwargs):
                return self._entry.is_file(**kwargs)

            def stat(self, **kwargs):
                if self.path.endswith("Dark/02.mp3"):
                    raise OSError("stale file handle")
                return self._entry.stat(**kwargs)

        class Listing:
            """What os.scandir returns: an iterator that is also a context manager."""

            def __init__(self, path):
                self._entries = real_scandir(path)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._entries.close()

            def __iter__(self):
                return (Flaky(entry) for entry in self._entries)

        def flaky_scandir(path):
            return Listing(path)

        with patch("os.scandir", flaky_scandir):
            self.scan()
        titles = {row[0] for row in self.db.execute("SELECT title FROM tracks")}
        self.assertIn(target, titles)

    def test_a_scan_that_stops_short_of_folders_keeps_their_tracks(self):
        self.scan()
        before = library.scan_progress(self.db)["tracks"]
        self.add("New/n.mp3", mp3("Newcomer", "N", "N"))
        with patch.object(library, "MAX_TRACKS_TOTAL", 0):
            self.scan()
        self.assertGreaterEqual(library.scan_progress(self.db)["tracks"], before)
        titles = {row[0] for row in self.db.execute("SELECT title FROM tracks")}
        self.assertIn("Money", titles)

    def test_tracks_of_a_folder_removed_during_a_tag_read_do_not_come_back(self):
        original = library.read_tags
        removed = []

        def removing(path):
            if not removed:
                removed.append(True)
                # Another process: its own connection, made and closed in this thread.
                other = library.connect(str(self.home / "library.sqlite3"))
                try:
                    library.remove_root(other, str(self.music))
                finally:
                    other.close()
            return original(path)

        with patch.object(library, "read_tags", removing):
            self.scan()
        self.assertEqual(library.scan_progress(self.db)["tracks"], 0)

    def test_a_scan_cut_short_at_the_end_finishes_at_the_next_step(self):
        library.start_scan(self.db)
        original = library.regroup
        failed = []

        def failing_once(db):
            # Only the grouping that ends the scan, not the interim ones.
            if not failed and any(frame.function == "_finish_scan" for frame in inspect.stack()):
                failed.append(True)
                raise sqlite3.OperationalError("database is locked")
            return original(db)

        with patch.object(library, "regroup", failing_once):
            with self.assertRaises(sqlite3.OperationalError):
                for _ in range(100):
                    library.scan_step(self.db, 5)
        self.assertTrue(failed, "the end of the scan was reached")
        self.assertNotEqual(library._meta(self.db, "phase"), "idle", "an unfinished end is not reported as finished")
        for _ in range(10):
            if not library.scan_step(self.db, 5)["scanning"]:
                break
        self.assertFalse(library.scan_progress(self.db)["scanning"])
        self.assertGreater(library.list_artists(self.db, 0, 50)["total"], 0)

    def test_an_unreadable_folder_only_protects_its_own_tracks(self):
        self.add("Rock/sub/a.mp3", mp3("Upper A", "U", "Upper", album_artist="U"))
        self.add("rock/sub/b.mp3", mp3("Lower B", "L", "Lower", album_artist="L"))
        self.scan()
        (self.music / "rock" / "sub" / "b.mp3").unlink()
        real_scandir = os.scandir
        target = str(self.music / "Rock")

        def failing(path):
            if str(path) == target:
                raise PermissionError("denied")
            return real_scandir(path)

        with patch("os.scandir", failing):
            self.scan()
        titles = {row[0] for row in self.db.execute("SELECT title FROM tracks")}
        self.assertIn("Upper A", titles, "the unreadable folder keeps its tracks")
        self.assertNotIn("Lower B", titles, "a folder that differs only in case does not")

    def test_a_query_does_not_wait_for_a_writer(self):
        import threading

        self.scan()
        writer = self.other_connection()
        writer.execute("BEGIN IMMEDIATE")
        self.addCleanup(lambda: writer.in_transaction and writer.execute("ROLLBACK"))
        result = []

        def query():
            db = library.connect(str(self.home / "library.sqlite3"))
            result.append(library.list_artists(db, 0, 5)["total"])
            db.close()

        thread = threading.Thread(target=query)
        started = time.monotonic()
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive(), "a browse waited for the write lock")
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result, [library.list_artists(self.db, 0, 5)["total"]])

    def test_a_failed_batch_leaves_the_connection_usable(self):
        library.start_scan(self.db)
        with patch.object(library, "_tag_row", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                for _ in range(100):
                    library.scan_step(self.db, 5)
        self.assertFalse(self.db.in_transaction)
        self.db.execute("SELECT 1 FROM tracks LIMIT 1")

    def test_relative_folders_are_refused(self):
        other = Path(self.temporary.name) / "rel"
        other.mkdir()
        previous = os.getcwd()
        os.chdir(self.temporary.name)
        self.addCleanup(os.chdir, previous)
        with self.assertRaisesRegex(library.LibraryError, "absolute"):
            library.add_root(self.db, "rel")

    def test_the_folder_limit_holds_across_connections(self):
        folders = []
        for index in range(library.MAX_ROOTS):
            folder = Path(self.temporary.name) / f"f{index}"
            folder.mkdir()
            folders.append(folder)
        # The root of setUp is the first; all but one slot are filled. Another connection fills the last.
        for folder in folders[: library.MAX_ROOTS - 2]:
            library.add_root(self.db, str(folder))
        db_a, db_b = self.other_connection(), self.other_connection()
        library.add_root(db_a, str(folders[library.MAX_ROOTS - 2]))
        with self.assertRaisesRegex(library.LibraryError, "at most"):
            library.add_root(db_b, str(Path(self.temporary.name)))


class BrowseTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.fixture()
        library.add_root(self.db, str(self.music))
        self.scan()

    def artist(self, name):
        return next(a for a in library.list_artists(self.db, 0, 200)["artists"] if a["name"] == name)

    def album(self, name):
        return next(a for a in library.list_albums(self.db, "", 0, 200)["albums"] if a["name"] == name)

    def test_artists_group_by_album_artist_and_merge_spellings(self):
        names = [a["name"] for a in library.list_artists(self.db, 0, 200)["artists"]]
        # "pink floyd" and "Pink Floyd" are one artist, shown with the spelling most files use.
        self.assertEqual(names, ["ABBA", "Artist X", "Band A", "Beyoncé", "Flac Band", "Pink Floyd", "Various Artists"])
        self.assertEqual(self.artist("Pink Floyd")["album_count"], 2)

    def test_a_compilation_without_an_album_artist_is_various_artists(self):
        hits = self.album("Hits 1")
        self.assertEqual((hits["artist"], hits["track_count"]), (library.VARIOUS_ARTISTS, 2))

    def test_artists_ignore_a_leading_the_when_sorted(self):
        self.add("The Zoo/z.mp3", mp3("Z", "The Zoo", "Zoo Album", album_artist="The Zoo"))
        self.scan()
        names = [a["name"] for a in library.list_artists(self.db, 0, 200)["artists"]]
        self.assertLess(names.index("Pink Floyd"), names.index("The Zoo"))
        self.assertGreater(names.index("The Zoo"), names.index("Pink Floyd"))

    def test_artist_albums_are_oldest_first(self):
        albums = library.artist_albums(self.db, self.artist("Pink Floyd")["id"], 0, 50)["albums"]
        self.assertEqual([a["name"] for a in albums], ["The Dark Side of the Moon", "The Wall"])
        self.assertEqual(albums[1]["year"], 1979)
        with self.assertRaises(library.LibraryError):
            library.artist_albums(self.db, "nope", 0, 50)

    def test_album_tracks_come_in_disc_then_track_order(self):
        tracks = library.album_tracks(self.db, self.album("The Wall")["id"], 0, 50)["tracks"]
        # Disc 1 first, then disc 2, each by track number.
        self.assertEqual([t["title"] for t in tracks], ["In the Flesh", "The Thin Ice", "Hey You"])
        with self.assertRaises(library.LibraryError):
            library.album_tracks(self.db, "nope", 0, 50)

    def test_album_sorts(self):
        def names(sort):
            return [a["name"] for a in library.list_albums(self.db, sort, 0, 200)["albums"]]

        by_year = names("byYear")
        self.assertLess(by_year.index("The Dark Side of the Moon"), by_year.index("The Wall"))
        self.assertLess(by_year.index("The Wall"), by_year.index("Gold"))
        # Albums with no year come last.
        self.assertIn(by_year[-1], {"Hits 1", "Own Album", "Flac Album", "Unknown Album"})
        self.assertEqual(names("bogus"), names("alphabeticalByName"))
        by_name = names("alphabeticalByName")
        # "The Dark Side..." sorts under D, ahead of Gold.
        self.assertLess(by_name.index("The Dark Side of the Moon"), by_name.index("Gold"))
        sorts = library.list_albums(self.db, "", 0, 5)["sorts"]
        self.assertEqual([s["id"] for s in sorts], [s[0] for s in library.ALBUM_SORTS])

    def test_paging_covers_everything_once(self):
        everything = [a["id"] for a in library.list_albums(self.db, "", 0, 200)["albums"]]
        paged = []
        offset = 0
        while True:
            page = library.list_albums(self.db, "", offset, 3)["albums"]
            if not page:
                break
            paged += [a["id"] for a in page]
            offset += 3
        self.assertEqual(paged, everything)
        self.assertEqual(len(set(paged)), len(paged))

    def test_ids_survive_a_rescan(self):
        before = {a["name"]: a["id"] for a in library.list_albums(self.db, "", 0, 200)["albums"]}
        self.scan()
        after = {a["name"]: a["id"] for a in library.list_albums(self.db, "", 0, 200)["albums"]}
        self.assertEqual(before, after)

    def test_tracks_carry_their_own_ids(self):
        track = library.search_tracks(self.db, "speak", 5)["tracks"][0]
        meta = track["provider_meta"]
        self.assertEqual(meta["library.id"], library.group_id(track["path"]))
        self.assertEqual(meta["library.album"], self.album("The Dark Side of the Moon")["id"])
        self.assertEqual(meta["library.artist"], self.artist("Pink Floyd")["id"])

    def test_a_compilation_track_links_to_its_own_artist_when_it_has_albums(self):
        one = library.search_tracks(self.db, "one", 5)["tracks"][0]
        two = library.search_tracks(self.db, "two", 5)["tracks"][0]
        band_a = self.artist("Band A")["id"]
        various = self.artist("Various Artists")["id"]
        self.assertEqual(one["provider_meta"]["library.artist"], band_a)
        self.assertEqual(two["provider_meta"]["library.artist"], various)
        self.assertEqual(one["provider_meta"]["library.album"], self.album("Hits 1")["id"])


class SearchTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.fixture()
        library.add_root(self.db, str(self.music))
        self.scan()

    def titles(self, query, limit=20):
        return [t["title"] for t in library.search_tracks(self.db, query, limit)["tracks"]]

    def test_words_match_in_any_order_across_fields(self):
        self.assertEqual(self.titles("floyd money"), ["Money"])
        self.assertEqual(self.titles("money floyd"), ["Money"])

    def test_accents_and_case_do_not_matter(self):
        self.assertEqual(self.titles("BEYONCE"), ["Halo"])
        self.assertEqual(self.titles("beyoncé halo"), ["Halo"])

    def test_title_beats_album_beats_nothing_and_exact_beats_prefix(self):
        self.add("Rank/a.mp3", mp3("Money", "Someone", "Other"))
        self.add("Rank/b.mp3", mp3("Moneyball", "Someone", "Other"))
        self.add("Rank/c.mp3", mp3("Easy Money", "Someone", "Other"))
        self.add("Rank/d.mp3", mp3("Unrelated", "Someone", "Money Album"))
        self.scan()
        found = self.titles("money")
        # Exact title, then a title that starts with it, then an album that starts with it,
        # then a title that holds it as a later word.
        # (The fixture has a "Money" of its own, so there are two exact matches.)
        self.assertEqual(found[:5], ["Money", "Money", "Moneyball", "Unrelated", "Easy Money"])

    def test_the_limit_applies(self):
        self.assertEqual(len(self.titles("a", 3)), 3)

    def test_letters_in_order_are_a_fallback(self):
        self.assertIn("Money", self.titles("mny"))
        self.assertEqual(self.titles("zzzz"), [])
        self.assertEqual(self.titles("   "), [])

    def test_like_wildcards_in_the_query_are_literal(self):
        self.assertEqual(self.titles("100%"), [])
        self.assertEqual(self.titles("_"), [])

    def test_results_are_stable(self):
        self.assertEqual(self.titles("a", 50), self.titles("a", 50))


class QueueFileTests(LibraryTestCase):
    def setUp(self):
        super().setUp()
        self.fixture()
        library.add_root(self.db, str(self.music))
        self.scan()

    def test_an_album_becomes_a_private_m3u8_in_playing_order(self):
        album = next(a for a in library.list_albums(self.db, "", 0, 200)["albums"] if a["name"] == "The Wall")
        result = library.write_queue_file(self.db, album["id"])
        self.assertEqual(result["count"], 3)
        self.assertTrue(result["path"].endswith(".m3u8"))
        self.assertEqual(os.stat(result["path"]).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(result["path"])).st_mode & 0o777, 0o700)
        lines = Path(result["path"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "#EXTM3U")
        # No #EXTINF lines: CLIAMP then reads the tags of each file itself.
        self.assertFalse([line for line in lines if line.startswith("#EXTINF")])
        paths = [line for line in lines if not line.startswith("#")]
        self.assertEqual([os.path.relpath(p, self.music) for p in paths],
                         ["Pink Floyd/Wall/01.mp3", "Pink Floyd/Wall/02.mp3", "Pink Floyd/Wall/d2.mp3"])

    def test_old_queue_files_are_removed_and_new_ones_are_unique(self):
        album = library.list_albums(self.db, "", 0, 1)["albums"][0]["id"]
        old = library.write_queue_file(self.db, album)["path"]
        os.utime(old, (time.time() - 3 * 3600, time.time() - 3 * 3600))
        first = library.write_queue_file(self.db, album)["path"]
        second = library.write_queue_file(self.db, album)["path"]
        self.assertFalse(os.path.exists(old))
        self.assertNotEqual(first, second)

    def test_an_unknown_album_is_an_error(self):
        with self.assertRaises(library.LibraryError):
            library.write_queue_file(self.db, "nope")


class RequestTests(LibraryTestCase):
    def test_accepted_requests_are_normalized(self):
        self.assertEqual(library.normalize_library_request({"op": "roots"}), {"op": "roots"})
        self.assertEqual(
            library.normalize_library_request({"op": "artists"}), {"op": "artists", "offset": 0, "limit": 60})
        self.assertEqual(
            library.normalize_library_request({"op": "albums", "sort": "newest", "offset": 5, "limit": 200}),
            {"op": "albums", "sort": "newest", "offset": 5, "limit": 200})
        self.assertEqual(
            library.normalize_library_request({"op": "scan", "fresh": True, "budget": 3}),
            {"op": "scan", "fresh": True, "budget": 3})
        self.assertEqual(
            library.normalize_library_request({"op": "search", "query": "money"}),
            {"op": "search", "query": "money", "limit": 50})
        self.assertEqual(
            library.normalize_library_request({"op": "add_root", "path": "/mnt/nas"}), {"op": "add_root", "path": "/mnt/nas"})
        self.assertEqual(
            library.normalize_library_request({"op": "album_tracks", "album": "abc"}),
            {"op": "album_tracks", "album": "abc", "offset": 0, "limit": 60})
        self.assertEqual(library.normalize_library_request({"op": "queue_file", "album": "abc"}), {"op": "queue_file", "album": "abc"})

    def test_everything_else_is_refused(self):
        refused = (
            [], "x", None, {}, {"op": 5}, {"op": ""}, {"op": "drop_tables"}, {"op": "roots", "extra": 1},
            {"op": "add_root"}, {"op": "add_root", "path": ""}, {"op": "add_root", "path": "/a\nb"},
            {"op": "add_root", "path": "x" * 4097}, {"op": "remove_root", "path": 5},
            {"op": "scan", "budget": 0}, {"op": "scan", "budget": library.SCAN_BUDGET_MAX + 1},
            {"op": "scan", "fresh": "yes"}, {"op": "scan", "path": "/x"},
            {"op": "artists", "limit": 0}, {"op": "artists", "limit": library.PAGE_MAX + 1},
            {"op": "artists", "offset": -1}, {"op": "artists", "offset": True},
            {"op": "artist_albums"}, {"op": "artist_albums", "artist": ""},
            {"op": "album_tracks", "album": "x" * 513}, {"op": "albums", "sort": "x" * 65},
            {"op": "albums", "album": "x"}, {"op": "search"}, {"op": "search", "query": ""},
            {"op": "search", "query": "q" * 513}, {"op": "search", "query": "q", "limit": library.SEARCH_LIMIT_MAX + 1},
            {"op": "queue_file"}, {"op": "queue_file", "album": "a", "offset": 0},
        )
        for request in refused:
            with self.subTest(request=request), self.assertRaises(ValidationError):
                library.normalize_library_request(request)


class OutputTests(LibraryTestCase):
    def test_a_page_of_huge_tags_is_trimmed_to_the_output_cap(self):
        rows = []
        for index in range(200):
            rows.append({"path": f"/m/{index}", "title": "t" * 250, "artist": "a" * 250, "album": "b" * 250,
                         "provider_meta": {"k": "v" * 500}})
        result = {"ok": True, "tracks": rows, "total": 200}
        encoded = library.fit_output(result)
        self.assertLessEqual(len(encoded), library.MAX_OUTPUT_BYTES)
        decoded = json.loads(encoded)
        self.assertTrue(decoded["truncated"])
        self.assertLess(len(decoded["tracks"]), 200)
        self.assertGreater(len(decoded["tracks"]), 0)

    def test_a_small_page_is_not_touched(self):
        self.assertNotIn("truncated", json.loads(library.fit_output({"ok": True, "tracks": [{"path": "/a"}]})))


class ProcessTests(LibraryTestCase):
    def run_helper(self, request):
        env = {"PATH": "/usr/bin", "HOME": str(self.home), "XDG_RUNTIME_DIR": str(Path(self.temporary.name) / "run")}
        completed = subprocess.run(
            [sys.executable, "-I", str(Path(library.__file__)), request if isinstance(request, str) else json.dumps(request)],
            env=env, capture_output=True, timeout=60, cwd=self.temporary.name,
        )
        return completed.returncode, json.loads(completed.stdout.decode()) if completed.stdout else None, completed.stderr

    def test_the_helper_is_one_json_line_in_one_json_line_out(self):
        self.fixture()
        code, response, _ = self.run_helper({"op": "add_root", "path": str(self.music)})
        self.assertEqual((code, response["ok"]), (0, True))
        self.assertEqual(response["roots"][0]["path"], str(self.music))
        self.assertTrue(response["scanning"])
        for _ in range(60):
            code, response, _ = self.run_helper({"op": "scan", "budget": 5})
            if not response["scanning"]:
                break
        self.assertEqual(response["roots"][0]["tracks"], 12)
        code, response, _ = self.run_helper({"op": "search", "query": "money"})
        self.assertEqual([t["title"] for t in response["tracks"]], ["Money"])

    def test_errors_are_json_with_a_failing_exit_code(self):
        for request in ({"op": "nope"}, {"op": "add_root", "path": "/does/not/exist"}, "not json", "[]"):
            with self.subTest(request=request):
                code, response, stderr = self.run_helper(request)
                self.assertEqual(code, 1)
                self.assertFalse(response["ok"])
                self.assertTrue(response["error"])
                self.assertEqual(stderr, b"")


class ScaleTests(LibraryTestCase):
    """The speed budget at a realistic size, without creating files."""

    def test_browse_and_search_stay_fast_with_many_tracks(self):
        count = 30_000
        rows = []
        words = ["love", "night", "river", "fire", "dream", "stone", "light", "road", "heart", "wind", "gold", "blue"]
        root = library.add_root(self.db, str(self.music)) or 1
        for index in range(count):
            a, b, c = words[index % 12], words[(index // 12) % 12], words[(index // 144) % 12]
            title, artist, album = f"{a} of the {b} {index}", f"The {c} {a} {index // 100}", f"{b} {c} {index // 10}"
            rows.append((
                f"{self.music}/{index // 100}/{index}.mp3", f"{self.music}/{index // 100}", 1, 1000 + index, 10,
                title, artist, album, artist, "", 1990 + index % 30, index % 10, 1,
                library.fold(title), library.fold(artist), library.fold(album), 1,
            ))
        self.db.execute("BEGIN")
        self.db.executemany(library._UPSERT, rows)
        self.db.execute("COMMIT")
        started = time.monotonic()
        library.regroup(self.db)
        regroup_seconds = time.monotonic() - started
        for call in (
            lambda: library.search_tracks(self.db, "love night", 20),
            lambda: library.search_tracks(self.db, "l", 20),
            lambda: library.list_albums(self.db, "newest", 0, 60),
            lambda: library.list_artists(self.db, 600, 60),
        ):
            started = time.monotonic()
            call()
            self.assertLess(time.monotonic() - started, 1.0)
        self.assertLess(regroup_seconds, 10.0)


if __name__ == "__main__":
    unittest.main()
