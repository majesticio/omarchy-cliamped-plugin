import concurrent.futures
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest

from cliamped_search_favorites import (
    FavoritesSecurityError,
    MAX_FAVORITES,
    MAX_FILE_BYTES,
    load_favorites,
    save_favorites,
    toggle_favorite,
)


class SearchFavoritesSecurityTests(unittest.TestCase):
    def test_normalizes_text_and_rejects_non_http_or_malformed_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "favorites.json"
            path.write_text(json.dumps([
                {
                    "title": "  Cafe\u0301\nFM  ",
                    "path": "https://radio.example/live",
                    "artist": "DJ\u0000Name",
                },
                {"title": "File", "path": "file:///etc/passwd"},
                {"title": 42, "path": "https://radio.example/number"},
                {"title": "Credentials", "path": "https://u:p@radio.example/live"},
            ]), encoding="utf-8")

            self.assertEqual(load_favorites(path), [{
                "title": "Caf\u00e9 FM",
                "path": "https://radio.example/live",
                "artist": "DJName",
                "stream": True,
                "realtime": True,
            }])

    def test_preserves_opaque_url_codepoints_and_rejects_whitespace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "favorites.json"
            decomposed = "cafe\u0301"
            url = f"https://radio.example/{decomposed}"
            toggle_favorite({"title": "Station", "path": url}, path)
            self.assertEqual(load_favorites(path)[0]["path"], url)
            with self.assertRaisesRegex(FavoritesSecurityError, "whitespace"):
                toggle_favorite({"title": "Bad", "path": f" {url}"}, path)

    def test_rejects_ambiguous_or_invalid_url_authorities(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "favorites.json"
            invalid = (
                "https://%65xample.com/live",
                "https://radio_example/live",
                "https://radio..example/live",
                "https://radio.example\\evil/live",
            )
            for url in invalid:
                with self.subTest(url=url), self.assertRaisesRegex(
                    FavoritesSecurityError, "invalid host"
                ):
                    toggle_favorite({"title": "Bad", "path": url}, path)

    def test_rejects_oversized_and_over_count_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "favorites.json"
            path.write_bytes(b"[" + b" " * MAX_FILE_BYTES + b"]")
            with self.assertRaises(FavoritesSecurityError):
                load_favorites(path)

            entries = [
                {"title": f"Station {index}", "path": f"https://radio.example/{index}"}
                for index in range(MAX_FAVORITES + 1)
            ]
            path.write_text(json.dumps(entries), encoding="utf-8")
            with self.assertRaises(FavoritesSecurityError):
                load_favorites(path)

    def test_symlink_and_fifo_are_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim.json"
            victim.write_text('[{"title":"secret"}]', encoding="utf-8")
            favorite = root / "favorites.json"
            favorite.symlink_to(victim)

            with self.assertRaises(FavoritesSecurityError):
                load_favorites(favorite)
            with self.assertRaises(FavoritesSecurityError):
                toggle_favorite(
                    {"title": "Safe", "path": "https://radio.example/safe"},
                    favorite,
                )
            self.assertEqual(victim.read_text(encoding="utf-8"), '[{"title":"secret"}]')

            favorite.unlink()
            os.mkfifo(favorite)
            started = time.monotonic()
            with self.assertRaises(FavoritesSecurityError):
                load_favorites(favorite)
            self.assertLess(time.monotonic() - started, 1.0)

    def test_requires_a_private_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o755)
            try:
                with self.assertRaises(FavoritesSecurityError):
                    load_favorites(root / "favorites.json")
            finally:
                os.chmod(root, 0o700)

    def test_hardlinked_lock_is_rejected_without_changing_victim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "favorites.json"
            victim = root / "victim.txt"
            victim.write_text("untouched", encoding="utf-8")
            os.chmod(victim, 0o644)
            os.link(victim, root / ".favorites.json.lock")

            with self.assertRaises(FavoritesSecurityError):
                load_favorites(path)
            self.assertEqual(victim.read_text(encoding="utf-8"), "untouched")
            self.assertEqual(stat.S_IMODE(victim.stat().st_mode), 0o644)

    def test_predictable_old_temp_symlink_cannot_overwrite_victim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "favorites.json"
            victim = root / "victim.txt"
            victim.write_text("unchanged", encoding="utf-8")
            old_temporary = root / "favorites.json.tmp"
            old_temporary.symlink_to(victim)

            toggle_favorite(
                {"title": "Safe", "path": "https://radio.example/safe"},
                path,
            )

            self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")
            self.assertTrue(old_temporary.is_symlink())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(load_favorites(path)[0]["title"], "Safe")

    def test_save_rejects_invalid_items_instead_of_coercing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "favorites.json"
            with self.assertRaises(FavoritesSecurityError):
                save_favorites([{"title": ["not text"], "path": "https://radio.example"}], path)
            self.assertFalse(path.exists())

    def test_concurrent_toggles_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "favorites.json"
            tracks = [
                {"title": f"Station {index}", "path": f"https://radio.example/{index}"}
                for index in range(24)
            ]
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(lambda track: toggle_favorite(track, path), tracks))

            favorites = load_favorites(path)
            self.assertEqual(len(favorites), len(tracks))
            self.assertEqual({item["path"] for item in favorites},
                             {track["path"] for track in tracks})


if __name__ == "__main__":
    unittest.main()
