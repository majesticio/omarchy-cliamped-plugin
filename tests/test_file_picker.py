import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import cliamp_file_picker


class ExpandSelectionsTests(unittest.TestCase):
    def test_recurses_naturally_and_ignores_hidden_or_unsupported_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            album = root / "Album"
            disc = album / "Disc 2"
            hidden = album / ".cache"
            disc.mkdir(parents=True)
            hidden.mkdir()
            for relative in (
                "10 Finale.flac",
                "2 Prelude.flac",
                "Disc 2/01 Return.mp3",
                "._2 Prelude.flac",
                ".cache/secret.mp3",
                "cover.jpg",
            ):
                path = album / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            result = cliamp_file_picker.expand_selections([str(album)])

            self.assertEqual(
                [Path(path).name for path in result],
                ["2 Prelude.flac", "10 Finale.flac", "01 Return.mp3"],
            )

    def test_deduplicates_explicit_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            track = Path(temporary) / "track.mp3"
            track.touch()
            self.assertEqual(
                cliamp_file_picker.expand_selections([str(track), str(track)]),
                [str(track.resolve())],
            )

    @patch("cliamp_file_picker.expand_selections", return_value=["/music/track.mp3"])
    @patch("cliamp_file_picker._run_zenity", return_value=(0, b"/music/Album\n", b""))
    def test_folder_picker_launches_in_directory_mode(self, run_zenity, expand_selections):

        paths, cancelled = cliamp_file_picker.choose_paths(True, [])

        self.assertFalse(cancelled)
        self.assertEqual(paths, ["/music/track.mp3"])
        command = run_zenity.call_args.args[0]
        self.assertIn("--directory", command)
        self.assertIn("--multiple", command)
        expand_selections.assert_called_once_with(["/music/Album"])


class LoadPathsTests(unittest.TestCase):
    @patch("cliamp_file_picker.send_requests")
    def test_plays_first_track_then_queues_the_rest(self, send_requests):
        send_requests.return_value = [
            {"ok": True, "tracks": [{"title": "1"}, {"title": "2"}]},
        ]

        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "1.mp3"
            second = Path(temporary) / "2.mp3"
            first.touch()
            second.touch()
            response = cliamp_file_picker.load_paths([str(first), str(second)])

        self.assertTrue(response["ok"])
        self.assertEqual(send_requests.call_args.args[0], [
            {"cmd": "track.play", "track": {"title": "1", "path": str(first)}},
            {"cmd": "track.queue", "track": {"title": "2", "path": str(second)}},
            {"cmd": "queue.list"},
        ])
        self.assertEqual(send_requests.call_args.kwargs, {
            "deadline_seconds": cliamp_file_picker.IPC_BATCH_DEADLINE_SECONDS,
            "stop_on_error": True,
            "retain_responses": False,
        })

    @patch("cliamp_file_picker.send_requests")
    def test_stops_after_cliamp_rejects_a_track(self, send_requests):
        send_requests.return_value = [{"ok": False, "error": "unsupported"}]
        with tempfile.TemporaryDirectory() as temporary:
            track = Path(temporary) / "bad.mp3"
            track.touch()
            with self.assertRaisesRegex(RuntimeError, "unsupported"):
                cliamp_file_picker.load_paths([str(track)])


if __name__ == "__main__":
    unittest.main()
