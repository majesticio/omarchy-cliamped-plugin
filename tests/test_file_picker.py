import tempfile
from pathlib import Path
import subprocess
import unittest
from unittest.mock import call, patch

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
    @patch("cliamp_file_picker.subprocess.run")
    def test_folder_picker_launches_in_directory_mode(self, run, expand_selections):
        run.return_value = subprocess.CompletedProcess([], 0, stdout="/music/Album\n", stderr="")

        paths, cancelled = cliamp_file_picker.choose_paths(True, [])

        self.assertFalse(cancelled)
        self.assertEqual(paths, ["/music/track.mp3"])
        command = run.call_args.args[0]
        self.assertIn("--directory", command)
        self.assertIn("--multiple", command)
        expand_selections.assert_called_once_with(["/music/Album"])


class LoadPathsTests(unittest.TestCase):
    @patch("cliamp_file_picker.send_request")
    def test_plays_first_track_then_queues_the_rest(self, send_request):
        send_request.side_effect = [
            {"ok": True},
            {"ok": True},
            {"ok": True, "tracks": [{"title": "1"}, {"title": "2"}]},
        ]

        response = cliamp_file_picker.load_paths(["/music/1.mp3", "/music/2.mp3"])

        self.assertTrue(response["ok"])
        self.assertEqual(send_request.call_args_list, [
            call({"cmd": "track.play", "track": {"title": "1", "path": "/music/1.mp3"}}),
            call({"cmd": "track.queue", "track": {"title": "2", "path": "/music/2.mp3"}}),
            call({"cmd": "queue.list"}),
        ])

    @patch("cliamp_file_picker.send_request")
    def test_stops_after_cliamp_rejects_a_track(self, send_request):
        send_request.return_value = {"ok": False, "error": "unsupported"}
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            cliamp_file_picker.load_paths(["/music/bad.mp3"])


if __name__ == "__main__":
    unittest.main()
