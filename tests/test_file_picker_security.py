import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import cliamp_file_picker as picker


class TraversalBoundaryTests(unittest.TestCase):
    def test_traversal_is_natural_and_never_follows_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "album"
            nested = root / "Disc 2"
            outside = Path(temporary) / "outside"
            nested.mkdir(parents=True)
            outside.mkdir()
            (root / "10 Finale.flac").touch()
            (root / "2 Prelude.flac").touch()
            (nested / "01 Return.mp3").touch()
            (outside / "escaped.mp3").touch()
            (root / "linked-file.mp3").symlink_to(outside / "escaped.mp3")
            (root / "linked-directory").symlink_to(outside, target_is_directory=True)

            result = picker.expand_selections([str(root)])

            self.assertEqual(
                [Path(path).name for path in result],
                ["2 Prelude.flac", "10 Finale.flac", "01 Return.mp3"],
            )

    def test_rejects_a_symlink_in_any_selected_path_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real"
            real.mkdir()
            (real / "song.mp3").touch()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)

            with self.assertRaisesRegex(picker.PickerBoundaryError, "symbolic-link"):
                picker.expand_selections([str(alias / "song.mp3")])

    def test_enforces_accepted_file_limit_without_returning_a_partial_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(3):
                (root / f"{index}.mp3").touch()

            with patch.object(picker, "MAX_ACCEPTED_FILES", 2):
                with self.assertRaisesRegex(picker.PickerBoundaryError, "at most 2"):
                    picker.expand_selections([str(root)])

    def test_enforces_visited_entry_limit_even_for_unsupported_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(3):
                (root / f"cover-{index}.jpg").touch()

            with patch.object(picker, "MAX_VISITED_ENTRIES", 2):
                with self.assertRaisesRegex(picker.PickerBoundaryError, "inspect at most 2"):
                    picker.expand_selections([str(root)])

    def test_enforces_depth_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deep = root / "one" / "two"
            deep.mkdir(parents=True)
            (deep / "song.mp3").touch()

            with patch.object(picker, "MAX_TRAVERSAL_DEPTH", 1):
                with self.assertRaisesRegex(picker.PickerBoundaryError, "descend at most 1"):
                    picker.expand_selections([str(root)])

    def test_enforces_individual_and_aggregate_path_byte_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            track = Path(temporary) / "song.mp3"
            track.touch()
            path = str(track)

            with patch.object(picker, "MAX_PATH_BYTES", len(path.encode()) - 1):
                with self.assertRaisesRegex(picker.PickerBoundaryError, "exceeds"):
                    picker.expand_selections([path])
            with patch.object(picker, "MAX_ACCEPTED_PATH_BYTES", len(path.encode()) - 1):
                with self.assertRaisesRegex(picker.PickerBoundaryError, "aggregate"):
                    picker.expand_selections([path])

    def test_rejects_relative_control_and_excess_selection_paths(self):
        with self.assertRaisesRegex(picker.PickerBoundaryError, "absolute"):
            picker.expand_selections(["song.mp3"])
        with self.assertRaisesRegex(picker.PickerBoundaryError, "control"):
            picker.expand_selections(["/tmp/song\n.mp3"])
        with patch.object(picker, "MAX_SELECTIONS", 1):
            with self.assertRaisesRegex(picker.PickerBoundaryError, "at most 1"):
                picker.expand_selections(["/tmp/a.mp3", "/tmp/b.mp3"])

    def test_monotonic_deadline_is_checked_before_filesystem_work(self):
        with patch.object(picker.time, "monotonic", side_effect=[0.0, 1.0]):
            with self.assertRaisesRegex(picker.PickerBoundaryError, "wall-clock"):
                picker.expand_selections(["/tmp/missing.mp3"], deadline_seconds=0.5)

    def test_rejects_separately_selected_files_from_different_devices(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "one.mp3"
            second = Path(temporary) / "two.mp3"
            first.touch()
            second.touch()
            original = picker._open_path_without_symlinks

            def different_devices(path):
                descriptor, result = original(path)
                device = 100 if path == str(first) else 200
                return descriptor, SimpleNamespace(
                    st_mode=result.st_mode,
                    st_dev=device,
                    st_ino=result.st_ino,
                )

            with patch.object(picker, "_open_path_without_symlinks", side_effect=different_devices):
                with self.assertRaisesRegex(picker.PickerBoundaryError, "one filesystem"):
                    picker.expand_selections([str(first), str(second)])

    def test_skips_a_same_device_entry_on_a_nested_mount(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "song.mp3").touch()
            with patch.object(picker, "_mount_id", side_effect=[10, 20]):
                self.assertEqual(picker.expand_selections([str(root)]), [])

    def test_rejects_separately_selected_roots_on_different_mounts(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "one.mp3"
            second = Path(temporary) / "two.mp3"
            first.touch()
            second.touch()
            with patch.object(picker, "_mount_id", side_effect=[10, 20]):
                with self.assertRaisesRegex(picker.PickerBoundaryError, "filesystem and mount"):
                    picker.expand_selections([str(first), str(second)])


class ZenityBoundaryTests(unittest.TestCase):
    def test_executable_validation_rejects_a_writable_package_object(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "zenity"
            executable.write_bytes(b"stub")
            executable.chmod(0o777)
            with (
                patch.object(picker, "ZENITY_DIRECTORY", str(directory)),
                patch.object(picker, "ZENITY_EXECUTABLE", str(executable)),
                self.assertRaisesRegex(picker.PickerBoundaryError, "protected package object"),
            ):
                picker._validate_zenity()

    def test_executable_validation_rejects_current_user_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "zenity"
            executable.write_bytes(b"stub")
            executable.chmod(0o500)
            with (
                patch.object(picker, "ZENITY_DIRECTORY", str(directory)),
                patch.object(picker, "ZENITY_EXECUTABLE", str(executable)),
                self.assertRaisesRegex(picker.PickerBoundaryError, "protected package object"),
            ):
                picker._validate_zenity()

    def test_environment_is_allowlisted(self):
        environment = picker._clean_environment({
            "HOME": "/home/test",
            "DISPLAY": ":1",
            "LD_PRELOAD": "/tmp/evil.so",
            "PYTHONPATH": "/tmp/evil",
            "PATH": "/tmp/evil",
            "XDG_RUNTIME_DIR": "relative",
        })

        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertEqual(environment["HOME"], "/home/test")
        self.assertEqual(environment["DISPLAY"], ":1")
        self.assertNotIn("LD_PRELOAD", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("XDG_RUNTIME_DIR", environment)

    def test_uses_descriptor_bound_zenity_clean_environment_and_a_new_session(self):
        executable_fd = os.open("/usr/bin/zenity", os.O_PATH | os.O_CLOEXEC)
        with (
            patch.object(picker, "_open_zenity", return_value=executable_fd),
            patch.object(picker.subprocess, "Popen") as popen,
            patch.object(
                picker,
                "_collect_process_output",
                return_value=(b"/music/song.mp3\n", b""),
            ) as collect,
            patch.object(picker, "_start_guardian", return_value=(91, 92)),
            patch.object(picker, "_finish_guardian") as finish,
        ):
            process = popen.return_value
            process.returncode = 0
            code, stdout, stderr = picker._run_zenity(["--file-selection"])

        self.assertEqual((code, stdout, stderr), (0, b"/music/song.mp3\n", b""))
        command = popen.call_args.args[0]
        options = popen.call_args.kwargs
        self.assertEqual(command[0], "/usr/bin/zenity")
        self.assertEqual(options["executable"], f"/proc/self/fd/{executable_fd}")
        self.assertEqual(options["pass_fds"], (executable_fd,))
        self.assertTrue(options["start_new_session"])
        self.assertTrue(options["close_fds"])
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stdout"], subprocess.PIPE)
        self.assertEqual(options["stderr"], subprocess.PIPE)
        self.assertEqual(options["env"]["PATH"], "/usr/bin")
        self.assertNotIn("LD_PRELOAD", options["env"])
        self.assertTrue(callable(options["preexec_fn"]))
        deadline = collect.call_args.args[1]
        self.assertLessEqual(deadline - time.monotonic(), picker.ZENITY_DEADLINE_SECONDS)
        finish.assert_called_once_with(process, (91, 92))

    def test_always_finishes_guardian_after_a_deadline_error(self):
        executable_fd = os.open("/usr/bin/zenity", os.O_PATH | os.O_CLOEXEC)
        with (
            patch.object(picker, "_open_zenity", return_value=executable_fd),
            patch.object(picker.subprocess, "Popen") as popen,
            patch.object(
                picker,
                "_collect_process_output",
                side_effect=picker.PickerBoundaryError("timeout"),
            ),
            patch.object(picker, "_start_guardian", return_value=(91, 92)),
            patch.object(picker, "_finish_guardian") as finish,
        ):
            with self.assertRaisesRegex(picker.PickerBoundaryError, "timeout"):
                picker._run_zenity(["--file-selection"])
        finish.assert_called_once_with(popen.return_value, (91, 92))

    def test_early_guardian_exit_falls_back_to_group_teardown(self):
        process = SimpleNamespace(poll=lambda: None)
        with (
            patch.object(picker.os, "close"),
            patch.object(picker.os, "waitpid", return_value=(4242, 1)),
            patch.object(picker, "_terminate_process_group") as terminate,
        ):
            picker._finish_guardian(process, (91, 4242))
        terminate.assert_called_once_with(process)

    def test_stream_reader_rejects_stdout_above_its_cap(self):
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        os.write(stdout_write, b"12345")
        os.close(stdout_write)
        os.close(stderr_write)

        class PipeProcess:
            def __init__(self):
                self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
                self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        with patch.object(picker, "MAX_ZENITY_STDOUT_BYTES", 4):
            with self.assertRaisesRegex(picker.PickerBoundaryError, "stdout"):
                picker._collect_process_output(PipeProcess(), time.monotonic() + 1)

    def test_parser_rejects_ambiguous_control_delimiters(self):
        with self.assertRaisesRegex(picker.PickerBoundaryError, "control"):
            picker._parse_zenity_selections(b"/tmp/one.mp3\r/tmp/two.mp3\n")

    def test_tree_termination_escalates_from_term_to_kill(self):
        process = SimpleNamespace(pid=4242, poll=lambda: None, wait=lambda timeout=None: 0)
        with (
            patch.object(picker.os, "killpg") as killpg,
            patch.object(picker.time, "monotonic", side_effect=[0.0, 2.0]),
        ):
            picker._terminate_process_group(process)

        self.assertEqual(
            killpg.call_args_list,
            [call(4242, signal.SIGTERM), call(4242, signal.SIGKILL)],
        )

    def test_guardian_terminates_zenity_leader_and_descendant(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            child_pid_path = directory / "child.pid"
            executable = directory / "zenity"
            executable.write_text(
                "#!/usr/bin/sh\n"
                "/usr/bin/sleep 60 &\n"
                "child=$!\n"
                f"printf '%s\\n' \"$child\" > '{child_pid_path}'\n"
                "wait \"$child\"\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            with (
                patch.object(picker, "ZENITY_DIRECTORY", str(directory)),
                patch.object(picker, "ZENITY_EXECUTABLE", str(executable)),
                patch.object(picker, "ZENITY_DEADLINE_SECONDS", 0.5),
                patch.object(picker, "PROCESS_TERMINATION_GRACE_SECONDS", 0.2),
                patch.object(picker.os, "geteuid", return_value=os.geteuid() + 1),
                self.assertRaisesRegex(picker.PickerBoundaryError, "wall-clock"),
            ):
                picker._run_zenity([])

            child_pid = int(child_pid_path.read_text(encoding="ascii"))
            deadline = time.monotonic() + 1
            state = "unknown"
            while time.monotonic() < deadline:
                try:
                    state = Path(f"/proc/{child_pid}/stat").read_text().split()[2]
                except FileNotFoundError:
                    state = "gone"
                if state in {"gone", "Z"}:
                    break
                time.sleep(0.02)
            self.assertIn(state, {"gone", "Z"})

    def test_signal_handler_requests_bounded_cleanup(self):
        previous = picker._stop_requested
        try:
            picker._stop_requested = False
            picker._request_stop(signal.SIGTERM, None)
            self.assertTrue(picker._stop_requested)
        finally:
            picker._stop_requested = previous


class IPCBatchBoundaryTests(unittest.TestCase):
    @patch.object(picker, "send_requests")
    def test_loads_all_paths_over_one_bounded_batch_connection(self, send_requests):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "1.mp3"
            second = Path(temporary) / "2.mp3"
            first.touch()
            second.touch()
            send_requests.return_value = [{"ok": True, "tracks": []}]

            response = picker.load_paths([str(first), str(second)])

        self.assertTrue(response["ok"])
        requests = send_requests.call_args.args[0]
        self.assertEqual(
            requests,
            [
                {"cmd": "track.play", "track": {"title": "1", "path": str(first)}},
                {"cmd": "track.queue", "track": {"title": "2", "path": str(second)}},
                {"cmd": "queue.list"},
            ],
        )
        self.assertEqual(send_requests.call_count, 1)
        self.assertEqual(
            send_requests.call_args.kwargs,
            {
                "deadline_seconds": picker.IPC_BATCH_DEADLINE_SECONDS,
                "stop_on_error": True,
                "retain_responses": False,
            },
        )

    @patch.object(picker, "send_requests", return_value=[{"ok": False, "error": "unsupported"}])
    def test_batch_stops_and_surfaces_a_rejected_track(self, _send_requests):
        with tempfile.TemporaryDirectory() as temporary:
            track = Path(temporary) / "bad.mp3"
            track.touch()
            with self.assertRaisesRegex(RuntimeError, "unsupported"):
                picker.load_paths([str(track)])

    def test_direct_queue_api_cannot_exceed_the_file_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            track = Path(temporary) / "song.mp3"
            track.touch()
            with patch.object(picker, "MAX_ACCEPTED_FILES", 2):
                with self.assertRaisesRegex(picker.PickerBoundaryError, "at most 2"):
                    picker.load_paths([str(track), str(track), str(track)])


class IsolatedExecutionTests(unittest.TestCase):
    def test_script_can_import_its_siblings_in_python_isolated_mode(self):
        script = Path(picker.__file__).resolve()
        completed = subprocess.run(
            ["/usr/bin/python3", "-I", str(script), "--files", "relative.mp3"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )

        self.assertEqual(completed.returncode, 1)
        response = json.loads(completed.stdout)
        self.assertFalse(response["ok"])
        self.assertIn("absolute", response["error"])
        self.assertNotIn(b"ModuleNotFoundError", completed.stderr)


if __name__ == "__main__":
    unittest.main()
