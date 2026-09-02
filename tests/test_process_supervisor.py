import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cliamped_process


class VisualizerFrameTests(unittest.TestCase):
    def test_accepts_exactly_ten_finite_normalized_bands(self):
        frame = cliamped_process.normalize_visualizer_frame(
            b'{"ok":true,"bands":[0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,1],"visualizer":"bars"}'
        )
        self.assertLessEqual(len(frame), cliamped_process.MAX_VIS_OUTPUT_BYTES)
        self.assertEqual(len(json.loads(frame)["bands"]), 10)

    def test_rejects_wrong_band_count_non_finite_and_oversize_frames(self):
        invalid = (
            b'{"ok":true,"bands":[]}',
            b'{"ok":true,"bands":[0,0,0,0,0,0,0,0,0,NaN]}',
            b"{" + b"x" * cliamped_process.MAX_VIS_FRAME_BYTES + b"}",
        )
        for frame in invalid:
            with self.subTest(frame=frame[:30]):
                with self.assertRaises(cliamped_process.ProcessBoundaryError):
                    cliamped_process.normalize_visualizer_frame(frame)

    def test_huge_json_integer_is_a_controlled_boundary_error(self):
        huge = b"9" * 400
        frame = b'{"ok":true,"bands":[' + huge + b",0,0,0,0,0,0,0,0,0]}"
        with self.assertRaises(cliamped_process.ProcessBoundaryError):
            cliamped_process.normalize_visualizer_frame(frame)


class ProcessEnvironmentTests(unittest.TestCase):
    def test_environment_is_allowlisted_and_paths_must_be_absolute(self):
        environment = cliamped_process._clean_env(
            {
                "HOME": "/home/test",
                "XDG_CONFIG_HOME": "relative",
                "LD_PRELOAD": "/tmp/attack.so",
                "PYTHONPATH": "/tmp/attack",
                "LANG": "en_US.UTF-8",
            }
        )
        self.assertEqual(environment["HOME"], "/home/test")
        self.assertNotIn("XDG_CONFIG_HOME", environment)
        self.assertNotIn("LD_PRELOAD", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual(environment["PATH"], "/usr/bin")

    def test_executable_validation_rejects_a_writable_object(self):
        with tempfile.TemporaryDirectory() as directory:
            bin_directory = Path(directory)
            executable = bin_directory / "cliamp"
            executable.write_bytes(b"stub")
            executable.chmod(0o777)
            with (
                patch.object(cliamped_process, "TRUSTED_BIN_DIRECTORY", bin_directory),
                patch.object(cliamped_process, "CLIAMP", executable),
                self.assertRaises(cliamped_process.ProcessBoundaryError),
            ):
                cliamped_process._validate_cliamp()

    def test_executable_validation_rejects_current_user_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            bin_directory = Path(directory)
            executable = bin_directory / "cliamp"
            executable.write_bytes(b"stub")
            executable.chmod(0o500)
            with (
                patch.object(cliamped_process, "TRUSTED_BIN_DIRECTORY", bin_directory),
                patch.object(cliamped_process, "CLIAMP", executable),
                self.assertRaises(cliamped_process.ProcessBoundaryError),
            ):
                cliamped_process._validate_cliamp()


class ProcessGuardianTests(unittest.TestCase):
    def test_guardian_terminates_the_leader_and_its_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            bin_directory = Path(directory)
            child_pid_path = bin_directory / "child.pid"
            executable = bin_directory / "cliamp"
            executable.write_text(
                "#!/bin/sh\n"
                "/usr/bin/sleep 60 &\n"
                "child=$!\n"
                f"printf '%s\\n' \"$child\" > '{child_pid_path}'\n"
                "wait \"$child\"\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            with (
                patch.object(cliamped_process, "TRUSTED_BIN_DIRECTORY", bin_directory),
                patch.object(cliamped_process, "CLIAMP", executable),
                patch.object(cliamped_process.os, "geteuid", return_value=os.geteuid() + 1),
            ):
                process = cliamped_process._spawn([], capture_stdout=False)
                guardian = cliamped_process._start_guardian(process)
                deadline = time.monotonic() + 2
                while not child_pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                child_pid = int(child_pid_path.read_text(encoding="ascii"))
                cliamped_process._finish_guardian(process, guardian)

            self.assertIsNotNone(process.poll())
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                try:
                    state = Path(f"/proc/{child_pid}/stat").read_text().split()[2]
                except OSError:
                    state = "gone"
                if state in {"gone", "Z"}:
                    break
                time.sleep(0.02)
            self.assertIn(state, {"gone", "Z"})

    def test_fallback_kills_descendant_after_leader_has_exited(self):
        with tempfile.TemporaryDirectory() as directory:
            bin_directory = Path(directory)
            child_pid_path = bin_directory / "child.pid"
            executable = bin_directory / "cliamp"
            executable.write_text(
                "#!/bin/sh\n"
                "trap '' TERM\n"
                "/usr/bin/sleep 60 &\n"
                "child=$!\n"
                f"printf '%s\\n' \"$child\" > '{child_pid_path}'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            with (
                patch.object(cliamped_process, "TRUSTED_BIN_DIRECTORY", bin_directory),
                patch.object(cliamped_process, "CLIAMP", executable),
                patch.object(cliamped_process, "TERMINATION_GRACE_SECONDS", 0.1),
                patch.object(cliamped_process.os, "geteuid", return_value=os.geteuid() + 1),
            ):
                process = cliamped_process._spawn([], capture_stdout=False)
                deadline = time.monotonic() + 2
                while not child_pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                child_pid = int(child_pid_path.read_text(encoding="ascii"))
                process.wait(timeout=2)
                cliamped_process._terminate_group(process)

            deadline = time.monotonic() + 1
            state = "unknown"
            while time.monotonic() < deadline:
                try:
                    state = Path(f"/proc/{child_pid}/stat").read_text().split()[2]
                except OSError:
                    state = "gone"
                if state in {"gone", "Z"}:
                    break
                time.sleep(0.02)
            self.assertIn(state, {"gone", "Z"})

    def test_early_guardian_exit_falls_back_to_group_teardown(self):
        process = SimpleNamespace(poll=lambda: None)
        with (
            patch.object(cliamped_process.os, "close"),
            patch.object(cliamped_process.os, "waitpid", return_value=(4242, 1)),
            patch.object(cliamped_process, "_terminate_group") as terminate,
        ):
            cliamped_process._finish_guardian(process, (91, 4242))
        terminate.assert_called_once_with(process)


if __name__ == "__main__":
    unittest.main()
