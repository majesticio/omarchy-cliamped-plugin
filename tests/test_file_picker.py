import io
import json
import sys
import tempfile
import types
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


class MusicFolderPickerTests(unittest.TestCase):
    @patch("cliamp_file_picker.expand_selections")
    @patch("cliamp_file_picker._run_zenity", return_value=(0, b"/mnt/nas/music\n", b""))
    def test_returns_one_validated_folder_without_walking_it(self, run_zenity, expand_selections):
        path, cancelled = cliamp_file_picker.choose_music_folder()

        self.assertEqual((path, cancelled), ("/mnt/nas/music", False))
        command = run_zenity.call_args.args[0]
        self.assertIn("--directory", command)
        # One folder only, and never an import of its audio files.
        self.assertNotIn("--multiple", command)
        expand_selections.assert_not_called()

    @patch("cliamp_file_picker._run_zenity", return_value=(1, b"", b""))
    def test_cancel_is_not_an_error(self, run_zenity):
        self.assertEqual(cliamp_file_picker.choose_music_folder(), (None, True))

    @patch("cliamp_file_picker._run_zenity", return_value=(5, b"", b"display unavailable\n"))
    def test_chooser_failure_surfaces_its_reason(self, run_zenity):
        with self.assertRaisesRegex(cliamp_file_picker.PickerBoundaryError, "display unavailable"):
            cliamp_file_picker.choose_music_folder()

    def test_rejects_several_relative_or_malformed_selections(self):
        for stdout in (b"/a\n/b\n", b"relative/path\n", b"/bad\x01path\n", b"\xff\xfe\n", b"\n"):
            with self.subTest(stdout=stdout), patch(
                "cliamp_file_picker._run_zenity", return_value=(0, stdout, b"")
            ), self.assertRaises(cliamp_file_picker.PickerBoundaryError):
                cliamp_file_picker.choose_music_folder()

    def test_refuses_names_that_the_allowlist_would_refuse_later(self):
        # A zero-width space and a right-to-left mark pass the chooser's own
        # control check but not the IPC allowlist: say so while the user can pick again.
        for name in ("/mnt/mu\u200bsic", "/mnt/mu\u200esic", "/mnt/mu\u202esic"):
            with self.subTest(name=name), patch(
                "cliamp_file_picker._run_zenity", return_value=(0, name.encode() + b"\n", b"")
            ), self.assertRaisesRegex(cliamp_file_picker.PickerBoundaryError, "invisible formatting"):
                cliamp_file_picker.choose_music_folder()

    def run_main(self, argv, folder):
        output = io.BytesIO()
        stdout = types.SimpleNamespace(buffer=output, flush=lambda: None)
        with patch.object(sys, "argv", ["cliamp_file_picker.py", *argv]), patch.object(
            sys, "stdout", stdout
        ), patch("cliamp_file_picker._arm_helper_parent_death"), patch(
            "cliamp_file_picker.choose_music_folder", return_value=folder
        ):
            try:
                cliamp_file_picker.main()
            except SystemExit as exit_:
                return json.loads(output.getvalue()), exit_.code
        return json.loads(output.getvalue()), 0

    def test_main_emits_the_chosen_folder(self):
        response, code = self.run_main(["--music-folder"], ("/mnt/nas/music", False))
        self.assertEqual(code, 0)
        self.assertEqual(response, {"ok": True, "path": "/mnt/nas/music", "source_kind": "music_folder"})

    def test_main_reports_a_cancel(self):
        response, code = self.run_main(["--music-folder"], (None, True))
        self.assertEqual((code, response), (0, {"ok": True, "cancelled": True, "count": 0}))

    def test_the_mode_takes_no_extra_arguments(self):
        # An extra argument must not fall through to the import path.
        with patch.object(sys, "argv", ["cliamp_file_picker.py", "--music-folder", "/etc"]), patch(
            "cliamp_file_picker._arm_helper_parent_death"
        ), patch("cliamp_file_picker.choose_paths", return_value=([], True)) as choose_paths, patch(
            "cliamp_file_picker.choose_music_folder"
        ) as choose_folder, patch.object(sys, "stdout", types.SimpleNamespace(buffer=io.BytesIO(), flush=lambda: None)):
            cliamp_file_picker.main()
        choose_folder.assert_not_called()
        choose_paths.assert_called_once()


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
