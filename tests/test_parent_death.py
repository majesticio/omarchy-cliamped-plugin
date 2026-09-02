import os
from pathlib import Path
import subprocess
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
PYTHON = "/usr/bin/python3"


class ParentDeathTests(unittest.TestCase):
    def assert_arm_function_kills_orphan(self, module_name, function_name):
        child_code = (
            "import os,sys,time;"
            f"sys.path.insert(0,{str(ROOT)!r});"
            f"import {module_name};"
            f"{module_name}.{function_name}();"
            "print(os.getpid(),flush=True);"
            "time.sleep(30)"
        )
        launcher_code = (
            "import subprocess;"
            f"child=subprocess.Popen([{PYTHON!r},'-I','-c',{child_code!r}],"
            "stdout=subprocess.PIPE,text=True);"
            "print(child.stdout.readline(),end='',flush=True)"
        )
        launcher = subprocess.Popen(
            [PYTHON, "-I", "-c", launcher_code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = launcher.communicate(timeout=5)
        self.assertEqual(launcher.returncode, 0, stderr)
        child_pid = int(stdout.strip())

        try:
            deadline = time.monotonic() + 2
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
        finally:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass

    def test_supervisor_is_killed_when_its_initial_owner_exits(self):
        self.assert_arm_function_kills_orphan(
            "cliamped_process", "_arm_supervisor_parent_death"
        )

    def test_one_shot_helpers_are_killed_when_their_initial_owner_exits(self):
        for module_name in (
            "cliamp_ipc",
            "cliamp_file_picker",
            "cliamped_search_favorites",
        ):
            with self.subTest(module=module_name):
                self.assert_arm_function_kills_orphan(
                    module_name, "_arm_helper_parent_death"
                )


if __name__ == "__main__":
    unittest.main()
