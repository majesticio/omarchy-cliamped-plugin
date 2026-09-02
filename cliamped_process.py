#!/usr/bin/python3 -I
"""Supervise the long-lived CLIAMP processes used by the shell plugin.

Only this trusted wrapper is connected to Quickshell.  It gives CLIAMP its own
process group, forwards termination to that group, and validates every
visualizer frame before allowing it into the long-lived QML process.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
import unicodedata


CLIAMP = Path("/usr/bin/cliamp")
TRUSTED_BIN_DIRECTORY = Path("/usr/bin")
MAX_VIS_FRAME_BYTES = 2_048
MAX_VIS_OUTPUT_BYTES = 512
VISUALIZER_BANDS = 10
VISUALIZER_HEARTBEAT_SECONDS = 5.0
MAX_FRAMES_PER_SECOND = 60
TERMINATION_GRACE_SECONDS = 2.0

_stop_requested = False
_LIBC = ctypes.CDLL(None, use_errno=True)


class ProcessBoundaryError(RuntimeError):
    """Raised when a managed process violates a plugin boundary."""


def _open_cliamp() -> int:
    """Open and validate the fixed CLIAMP object used for a descriptor-backed exec."""

    if not CLIAMP.is_absolute() or CLIAMP.parent != TRUSTED_BIN_DIRECTORY:
        raise ProcessBoundaryError("the CLIAMP executable path is not supported")
    directory_fd = -1
    executable_fd = -1
    try:
        directory_fd = os.open(
            TRUSTED_BIN_DIRECTORY,
            os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        directory = os.fstat(directory_fd)
        executable_fd = os.open(
            CLIAMP.name,
            os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
        executable = os.fstat(executable_fd)
    except OSError as error:
        if executable_fd >= 0:
            os.close(executable_fd)
        raise ProcessBoundaryError("the supported /usr/bin/cliamp executable is unavailable") from error
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)

    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_mode & 0o022
        or directory.st_uid == os.geteuid()
        or not stat.S_ISREG(executable.st_mode)
        or not executable.st_mode & 0o111
        or executable.st_mode & 0o6022
        or executable.st_uid != directory.st_uid
        or executable.st_uid == os.geteuid()
    ):
        os.close(executable_fd)
        raise ProcessBoundaryError("the CLIAMP executable is not a protected package object")
    return executable_fd


def _validate_cliamp() -> str:
    """Compatibility/test seam for validating the fixed executable."""

    descriptor = _open_cliamp()
    os.close(descriptor)
    return str(CLIAMP)


def _clean_env(source: dict[str, str] | os._Environ[str] = os.environ) -> dict[str, str]:
    """Build the small environment required by CLIAMP and desktop audio."""

    allowed = (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "WAYLAND_DISPLAY",
        "DISPLAY",
        "LANG",
        "LC_ALL",
        "PIPEWIRE_REMOTE",
        "PULSE_SERVER",
    )
    environment: dict[str, str] = {"PATH": "/usr/bin"}
    for name in allowed:
        value = source.get(name)
        if not isinstance(value, str) or not value or "\0" in value:
            continue
        try:
            if len(value.encode("utf-8", "strict")) > 4_096:
                continue
        except UnicodeError:
            continue
        if name in {"HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"} and not os.path.isabs(value):
            continue
        environment[name] = value
    environment.setdefault("LANG", "C.UTF-8")
    return environment


def _set_parent_death_signal(expected_parent: int) -> None:
    """Ask Linux to terminate CLIAMP if its Python supervisor is killed."""

    if _LIBC.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        os._exit(127)
    if os.getppid() != expected_parent:
        os._exit(127)


def _arm_supervisor_parent_death() -> None:
    """Ensure the QML-owned supervisor cannot outlive its initial parent."""

    expected_parent = os.getppid()
    if expected_parent <= 1:
        raise ProcessBoundaryError("the process supervisor has no live owner")
    if _LIBC.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise ProcessBoundaryError("cannot arm process-owner cleanup")
    if os.getppid() != expected_parent:
        raise ProcessBoundaryError("the process supervisor owner exited during startup")


def _spawn(arguments: list[str], *, capture_stdout: bool) -> subprocess.Popen[bytes]:
    executable_fd = _open_cliamp()
    expected_parent = os.getpid()
    try:
        return subprocess.Popen(
            [str(CLIAMP), *arguments],
            executable=f"/proc/self/fd/{executable_fd}",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_clean_env(),
            start_new_session=True,
            close_fds=True,
            pass_fds=(executable_fd,),
            preexec_fn=lambda: _set_parent_death_signal(expected_parent),
        )
    finally:
        os.close(executable_fd)


def _guardian_main(read_descriptor: int, process_group: int) -> None:
    """Outlive the supervisor and tear down its CLIAMP process group on EOF."""

    try:
        os.setsid()
    except OSError:
        pass
    if read_descriptor != 3:
        os.dup2(read_descriptor, 3)
        os.close(read_descriptor)
    null_descriptor = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(null_descriptor, descriptor)
    if null_descriptor > 3:
        os.close(null_descriptor)
    for entry in os.listdir("/proc/self/fd"):
        try:
            descriptor = int(entry)
            if descriptor > 3:
                os.close(descriptor)
        except (OSError, ValueError):
            pass

    try:
        while os.read(3, 64):
            pass
    except OSError:
        pass
    finally:
        try:
            os.close(3)
        except OSError:
            pass

    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        os._exit(0)
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            os._exit(0)
        time.sleep(0.05)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os._exit(0)


def _start_guardian(process: subprocess.Popen[bytes]) -> tuple[int, int]:
    read_descriptor, write_descriptor = os.pipe2(os.O_CLOEXEC)
    try:
        guardian_pid = os.fork()
    except OSError:
        os.close(read_descriptor)
        os.close(write_descriptor)
        raise
    if guardian_pid == 0:
        os.close(write_descriptor)
        _guardian_main(read_descriptor, process.pid)
        os._exit(127)
    os.close(read_descriptor)
    return write_descriptor, guardian_pid


def _finish_guardian(
    process: subprocess.Popen[bytes], guardian: tuple[int, int] | None
) -> None:
    """Trigger guardian cleanup, reap the leader, and bound guardian shutdown."""

    if guardian is None:
        _terminate_group(process)
        return
    write_descriptor, guardian_pid = guardian
    try:
        os.close(write_descriptor)
    except OSError:
        pass
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS + 1.0
    while time.monotonic() < deadline:
        process.poll()
        try:
            waited, _status = os.waitpid(guardian_pid, os.WNOHANG)
        except ChildProcessError:
            _terminate_group(process)
            return
        if waited == guardian_pid:
            # A guardian can fail independently. Verify the process group is
            # gone instead of treating guardian exit as proof of teardown.
            _terminate_group(process)
            return
        time.sleep(0.05)
    try:
        os.kill(guardian_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(guardian_pid, 0)
    except ChildProcessError:
        pass
    _terminate_group(process)


def _request_stop(_signum: int, _frame: object) -> None:
    global _stop_requested
    _stop_requested = True


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate a complete managed process group, escalating after a grace period."""

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    group_exists = True
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            group_exists = False
            break
        time.sleep(0.05)
    if group_exists:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGHUP, _request_stop)


def run_daemon() -> int:
    process = _spawn(["--daemon", "--provider", "radio"], capture_stdout=False)
    guardian: tuple[int, int] | None = None
    try:
        guardian = _start_guardian(process)
        while process.poll() is None and not _stop_requested:
            time.sleep(0.1)
        result = process.returncode
        return 0 if _stop_requested else result if result is not None and result >= 0 else 1
    finally:
        _finish_guardian(process, guardian)


def _clean_visualizer_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    value = "".join(character for character in value if character.isprintable())
    return value[:32]


def normalize_visualizer_frame(line: bytes) -> bytes:
    """Validate one CLIAMP frame and return a compact bounded JSON frame."""

    if not line or len(line) > MAX_VIS_FRAME_BYTES:
        raise ProcessBoundaryError("invalid visualizer frame size")
    try:
        decoded = line.decode("utf-8", "strict")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ProcessBoundaryError("invalid visualizer frame") from error
    if type(payload) is not dict or payload.get("ok") is not True:
        raise ProcessBoundaryError("invalid visualizer response")
    bands = payload.get("bands")
    if type(bands) is not list or len(bands) != VISUALIZER_BANDS:
        raise ProcessBoundaryError("invalid visualizer band count")

    normalized: list[float] = []
    for value in bands:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProcessBoundaryError("invalid visualizer band")
        try:
            number = float(value)
        except (OverflowError, ValueError) as error:
            raise ProcessBoundaryError("invalid visualizer band") from error
        if not math.isfinite(number) or number < 0.0 or number > 1.0:
            raise ProcessBoundaryError("invalid visualizer band")
        normalized.append(round(number, 6))

    output: dict[str, object] = {"ok": True, "bands": normalized}
    visualizer = _clean_visualizer_name(payload.get("visualizer"))
    if visualizer:
        output["visualizer"] = visualizer
    encoded = json.dumps(output, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(encoded) > MAX_VIS_OUTPUT_BYTES:
        raise ProcessBoundaryError("normalized visualizer frame is too large")
    return encoded


def run_visualizer(fps: int) -> int:
    process = _spawn(["visstream", "--fps", str(fps)], capture_stdout=True)
    guardian: tuple[int, int] | None = None
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffered = bytearray()
    last_valid_frame = time.monotonic()
    rate_window = last_valid_frame
    frames_in_window = 0

    try:
        guardian = _start_guardian(process)
        while process.poll() is None and not _stop_requested:
            now = time.monotonic()
            if now - last_valid_frame > VISUALIZER_HEARTBEAT_SECONDS:
                raise ProcessBoundaryError("visualizer heartbeat expired")
            events = selector.select(min(0.5, VISUALIZER_HEARTBEAT_SECONDS - (now - last_valid_frame)))
            if not events:
                continue
            chunk = os.read(process.stdout.fileno(), 4_096)
            if not chunk:
                break
            buffered.extend(chunk)
            if b"\n" not in buffered and len(buffered) > MAX_VIS_FRAME_BYTES:
                raise ProcessBoundaryError("unterminated visualizer frame is too large")

            while b"\n" in buffered:
                line, _, remainder = buffered.partition(b"\n")
                buffered = bytearray(remainder)
                if len(line) > MAX_VIS_FRAME_BYTES:
                    raise ProcessBoundaryError("visualizer frame is too large")
                now = time.monotonic()
                if now - rate_window >= 1.0:
                    rate_window = now
                    frames_in_window = 0
                frames_in_window += 1
                if frames_in_window > MAX_FRAMES_PER_SECOND:
                    raise ProcessBoundaryError("visualizer frame rate exceeded")
                frame = normalize_visualizer_frame(bytes(line))
                try:
                    sys.stdout.buffer.write(frame)
                    sys.stdout.buffer.flush()
                except BrokenPipeError:
                    return 0
                last_valid_frame = now
        if _stop_requested:
            return 0
        result = process.returncode
        return result if result is not None and result >= 0 else 1
    finally:
        selector.close()
        process.stdout.close()
        _finish_guardian(process, guardian)


def main(argv: list[str] | None = None) -> int:
    global _stop_requested
    _stop_requested = False
    arguments = list(sys.argv[1:] if argv is None else argv)
    _install_signal_handlers()
    try:
        if argv is None:
            _arm_supervisor_parent_death()
        if arguments == ["daemon"]:
            return run_daemon()
        if len(arguments) == 2 and arguments[0] == "visstream":
            try:
                fps = int(arguments[1], 10)
            except ValueError as error:
                raise ProcessBoundaryError("invalid visualizer frame rate") from error
            if fps < 1 or fps > 30:
                raise ProcessBoundaryError("visualizer frame rate must be between 1 and 30")
            return run_visualizer(fps)
        return 2
    except (OSError, ProcessBoundaryError, subprocess.SubprocessError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
