import tempfile
import unittest
from pathlib import Path

from cliamped_search_favorites import load_favorites, toggle_favorite


class SearchFavoritesTests(unittest.TestCase):
    def test_toggle_adds_and_removes_station(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "favorites.json"
            track = {"title": "Jazz FM", "path": "https://radio.example/jazz"}
            self.assertEqual(toggle_favorite(track, path)[0]["title"], "Jazz FM")
            self.assertEqual(toggle_favorite(track, path), [])
            self.assertEqual(load_favorites(path), [])

    def test_load_ignores_invalid_and_duplicate_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "favorites.json"
            path.write_text('[{"title":"A","path":"https://a"},{"title":"Again","path":"https://a"},{}]')
            self.assertEqual(len(load_favorites(path)), 1)


if __name__ == "__main__":
    unittest.main()
