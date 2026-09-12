#!/usr/bin/python3 -I
"""Choose a bounded set of local audio files and hand it to CLIAMP."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from typing import Any

# Python isolated mode intentionally omits the script directory. Expose only
# this resolved bundle directory while importing the two trusted siblings, then
# remove it before handling any user-controlled paths or process output.
_BUNDLE_DIRECTORY = os.path.dirname(os.path.realpath(__file__))
_inserted_bundle_directory = _BUNDLE_DIRECTORY not in sys.path
if _inserted_bundle_directory:
    sys.path.insert(0, _BUNDLE_DIRECTORY)
try:
    from cliamp_ipc import send_requests
    from cliamped_security import (
        MAX_OUTPUT_BYTES,
        MAX_PATH_BYTES,
        display_text,
        encode_json,
    )
finally:
    if _inserted_bundle_directory:
        sys.path.remove(_BUNDLE_DIRECTORY)


AUDIO_FILTER = "Audio files | *.mp3 *.flac *.ogg *.opus *.wav *.m4a *.aac *.wma"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".wav", ".m4a", ".aac", ".wma"}

# A file-picker invocation is deliberately much smaller than the generic IPC
# model limits. In particular, CLIAMP returns queue state for every mutation,
# so a conservative file count also bounds the daemon's cumulative replies.
MAX_SELECTIONS = 64
# Twenty mutations plus the final queue response remain below the IPC batch's
# aggregate response ceiling even when every normalized queue model reaches its
# own aggregate model-byte ceiling.
MAX_ACCEPTED_FILES = 20
MAX_VISITED_ENTRIES = 8_192
MAX_TRAVERSAL_DEPTH = 16
MAX_SELECTION_PATH_BYTES = 64 * 1024
MAX_ACCEPTED_PATH_BYTES = 64 * 1024
MAX_VISITED_PATH_BYTES = 1024 * 1024
TRAVERSAL_DEADLINE_SECONDS = 8.0

ZENITY_EXECUTABLE = "/usr/bin/zenity"
ZENITY_DIRECTORY = "/usr/bin"
ZENITY_DEADLINE_SECONDS = 300.0
MAX_ZENITY_STDOUT_BYTES = MAX_SELECTION_PATH_BYTES + MAX_SELECTIONS
MAX_ZENITY_STDERR_BYTES = 16 * 1024
PROCESS_TERMINATION_GRACE_SECONDS = 1.0

FFPROBE_EXECUTABLE = "/usr/bin/ffprobe"
METADATA_DEADLINE_SECONDS = 5.0
METADATA_FILE_SECONDS = 1.0

IPC_BATCH_DEADLINE_SECONDS = 30.0
MAX_ERROR_BYTES = 512
MAX_FDINFO_BYTES = 4_096

_stop_requested = False
_LIBC = ctypes.CDLL(None, use_errno=True)

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_INSPECTION_FLAGS = (
    getattr(os, "O_PATH", os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class PickerBoundaryError(ValueError):
    """A selection or child process exceeded the supported safety boundary."""


class _TraversalState:
    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.visited_entries = 0
        self.visited_path_bytes = 0
        self.accepted_path_bytes = 0
        self.device: int | None = None
        self.mount_id: int | None = None
        self.seen_directories: set[tuple[int, int]] = set()
        self.seen_paths: set[str] = set()
        self.tracks: list[str] = []


def natural_key(value: str) -> list[tuple[int, object]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
    ]


def _sort_key(value: str) -> tuple[list[tuple[int, object]], bytes]:
    # The byte tie-breaker makes case-fold-equivalent names stable.
    return natural_key(value), os.fsencode(value)


def _encoded_path_length(value: str) -> int:
    try:
        return len(value.encode("utf-8", "strict"))
    except UnicodeError as error:
        raise PickerBoundaryError("a selected path contains invalid Unicode") from error


def _validate_path_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PickerBoundaryError("every selected path must be a non-empty string")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise PickerBoundaryError("a selected path contains control characters")

    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        raise PickerBoundaryError("selected paths must be absolute")
    normalized = os.path.normpath(expanded)
    if normalized.startswith("//"):
        raise PickerBoundaryError("selected paths must use the local absolute-path form")
    if _encoded_path_length(normalized) > MAX_PATH_BYTES:
        raise PickerBoundaryError(f"a selected path exceeds {MAX_PATH_BYTES} bytes")
    return normalized


def _prepare_selections(selections: Sequence[str]) -> list[str]:
    if isinstance(selections, (str, bytes)) or not isinstance(selections, Sequence):
        raise PickerBoundaryError("selections must be a bounded list of paths")
    if len(selections) > MAX_SELECTIONS:
        raise PickerBoundaryError(f"at most {MAX_SELECTIONS} paths may be selected")

    result: list[str] = []
    aggregate = 0
    for selection in selections:
        normalized = _validate_path_text(selection)
        aggregate += _encoded_path_length(normalized)
        if aggregate > MAX_SELECTION_PATH_BYTES:
            raise PickerBoundaryError("selected paths exceed the aggregate byte limit")
        result.append(normalized)
    return result


def _open_path_without_symlinks(path: str) -> tuple[int, os.stat_result]:
    """Open an absolute path component-by-component beneath no-follow dirfds."""

    current_fd = os.open(
        os.path.sep, _INSPECTION_FLAGS | getattr(os, "O_DIRECTORY", 0)
    )
    components = Path(path).parts[1:]
    if not components:
        return current_fd, os.fstat(current_fd)

    try:
        for index, component in enumerate(components):
            try:
                expected = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except OSError as error:
                raise PickerBoundaryError("a selected path is unavailable") from error
            if stat.S_ISLNK(expected.st_mode):
                raise PickerBoundaryError("symbolic-link selections are not supported")
            intermediate = index < len(components) - 1
            if intermediate and not stat.S_ISDIR(expected.st_mode):
                raise PickerBoundaryError("a selected path has an invalid parent component")
            flags = _INSPECTION_FLAGS | (
                getattr(os, "O_DIRECTORY", 0) if intermediate else 0
            )
            next_fd = -1
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
                current = os.fstat(next_fd)
            except OSError as error:
                if next_fd >= 0:
                    os.close(next_fd)
                raise PickerBoundaryError("a selected path changed during validation") from error
            if (
                current.st_dev != expected.st_dev
                or current.st_ino != expected.st_ino
                or stat.S_IFMT(current.st_mode) != stat.S_IFMT(expected.st_mode)
            ):
                os.close(next_fd)
                raise PickerBoundaryError("a selected path changed during validation")
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, os.fstat(current_fd)
    except BaseException:
        os.close(current_fd)
        raise


def _mount_id(descriptor: int) -> int:
    """Return Linux's mount identity for an already validated open object."""

    info_descriptor = -1
    try:
        info_descriptor = os.open(
            f"/proc/self/fdinfo/{descriptor}",
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        encoded = os.read(info_descriptor, MAX_FDINFO_BYTES + 1)
    except OSError as error:
        raise PickerBoundaryError("a selected path mount could not be validated") from error
    finally:
        if info_descriptor >= 0:
            os.close(info_descriptor)
    if len(encoded) > MAX_FDINFO_BYTES:
        raise PickerBoundaryError("a selected path mount record is too large")
    try:
        lines = encoded.decode("ascii", "strict").splitlines()
    except UnicodeDecodeError as error:
        raise PickerBoundaryError("a selected path mount record is invalid") from error
    for line in lines:
        if line.startswith("mnt_id:\t"):
            value = line.removeprefix("mnt_id:\t")
            if value.isascii() and value.isdigit() and 0 < len(value) <= 20:
                return int(value, 10)
    raise PickerBoundaryError("a selected path mount record is missing")


def _open_stable_entry(
    directory_fd: int,
    name: str,
    expected: os.stat_result,
    *,
    directory: bool,
) -> tuple[int, os.stat_result]:
    flags = _INSPECTION_FLAGS | (getattr(os, "O_DIRECTORY", 0) if directory else 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise PickerBoundaryError("a selected path changed during traversal") from error
    try:
        current = os.fstat(descriptor)
        if (
            current.st_dev != expected.st_dev
            or current.st_ino != expected.st_ino
            or stat.S_IFMT(current.st_mode) != stat.S_IFMT(expected.st_mode)
        ):
            raise PickerBoundaryError("a selected path changed during traversal")
        return descriptor, current
    except BaseException:
        os.close(descriptor)
        raise


def _check_deadline(state: _TraversalState) -> None:
    if _stop_requested:
        raise PickerBoundaryError("file selection was interrupted")
    if time.monotonic() >= state.deadline:
        raise PickerBoundaryError("file traversal exceeded its wall-clock limit")


def _alarm_expired(_signum: int, _frame: object) -> None:
    raise PickerBoundaryError("file traversal exceeded its wall-clock limit")


@contextmanager
def _wall_clock_alarm(seconds: float) -> Iterator[None]:
    """Interrupt a blocking filesystem syscall in the standalone main thread."""

    supported = (
        hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )
    if not supported:
        yield
        return

    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    # Do not disturb a caller that already owns the process-wide real-time timer.
    if previous_timer[0] > 0:
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _alarm_expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _visible_audio_name(name: str) -> bool:
    if not name or name.startswith(".") or name.startswith("._"):
        return False
    return Path(name).suffix.lower() in AUDIO_EXTENSIONS


def _account_visited_path(state: _TraversalState, path: str) -> None:
    encoded_length = _encoded_path_length(path)
    if encoded_length > MAX_PATH_BYTES:
        raise PickerBoundaryError(f"a traversed path exceeds {MAX_PATH_BYTES} bytes")
    state.visited_path_bytes += encoded_length
    if state.visited_path_bytes > MAX_VISITED_PATH_BYTES:
        raise PickerBoundaryError("traversed paths exceed the aggregate byte limit")


def _accept_track(state: _TraversalState, path: str) -> None:
    if path in state.seen_paths:
        return
    encoded_length = _encoded_path_length(path)
    if encoded_length > MAX_PATH_BYTES:
        raise PickerBoundaryError(f"an audio path exceeds {MAX_PATH_BYTES} bytes")
    if len(state.tracks) >= MAX_ACCEPTED_FILES:
        raise PickerBoundaryError(f"a selection may contain at most {MAX_ACCEPTED_FILES} audio files")
    if state.accepted_path_bytes + encoded_length > MAX_ACCEPTED_PATH_BYTES:
        raise PickerBoundaryError("audio paths exceed the aggregate byte limit")
    state.accepted_path_bytes += encoded_length
    state.seen_paths.add(path)
    state.tracks.append(path)


def _walk_directory(
    directory_fd: int,
    directory_path: str,
    root_device: int,
    root_mount_id: int,
    depth: int,
    state: _TraversalState,
) -> None:
    _check_deadline(state)
    records: list[tuple[str, os.stat_result]] = []
    try:
        with os.scandir(directory_fd) as iterator:
            for entry in iterator:
                _check_deadline(state)
                state.visited_entries += 1
                if state.visited_entries > MAX_VISITED_ENTRIES:
                    raise PickerBoundaryError(
                        f"file traversal may inspect at most {MAX_VISITED_ENTRIES} entries"
                    )

                name = entry.name
                child_path = os.path.join(directory_path, name)
                _account_visited_path(state, child_path)
                if name.startswith("."):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as error:
                    raise PickerBoundaryError("a directory entry could not be inspected safely") from error
                if stat.S_ISLNK(info.st_mode) or info.st_dev != root_device:
                    # Never follow links or cross onto a different device below
                    # the selected root.
                    continue
                if stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode):
                    records.append((name, info))
    except PickerBoundaryError:
        raise
    except OSError as error:
        raise PickerBoundaryError("a selected directory could not be read safely") from error

    records.sort(key=lambda record: _sort_key(record[0]))
    _check_deadline(state)
    for name, expected in records:
        _check_deadline(state)
        child_path = os.path.join(directory_path, name)
        if stat.S_ISREG(expected.st_mode):
            if not _visible_audio_name(name):
                continue
            child_fd, current = _open_stable_entry(
                directory_fd, name, expected, directory=False
            )
            try:
                if current.st_dev == root_device and _mount_id(child_fd) == root_mount_id:
                    _accept_track(state, child_path)
            finally:
                os.close(child_fd)
            continue

        if depth >= MAX_TRAVERSAL_DEPTH:
            raise PickerBoundaryError(
                f"file traversal may descend at most {MAX_TRAVERSAL_DEPTH} levels"
            )
        child_fd, current = _open_stable_entry(
            directory_fd, name, expected, directory=True
        )
        try:
            identity = (current.st_dev, current.st_ino)
            if (
                current.st_dev != root_device
                or _mount_id(child_fd) != root_mount_id
                or identity in state.seen_directories
            ):
                continue
            state.seen_directories.add(identity)
            readable_fd = os.open(".", _DIRECTORY_FLAGS, dir_fd=child_fd)
            try:
                _walk_directory(
                    readable_fd,
                    child_path,
                    root_device,
                    root_mount_id,
                    depth + 1,
                    state,
                )
            finally:
                os.close(readable_fd)
        finally:
            os.close(child_fd)


def _expand_one(path: str, state: _TraversalState) -> None:
    _check_deadline(state)
    try:
        inspection_fd, info = _open_path_without_symlinks(path)
    except PickerBoundaryError:
        raise
    try:
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            return
        mount_id = _mount_id(inspection_fd)
        if state.device is None:
            state.device = info.st_dev
            state.mount_id = mount_id
        elif state.device != info.st_dev or state.mount_id != mount_id:
            raise PickerBoundaryError("all selections must be on one filesystem and mount")

        if stat.S_ISREG(info.st_mode):
            if _visible_audio_name(os.path.basename(path)) and not any(
                part.startswith(".") for part in Path(path).parts
            ):
                _accept_track(state, path)
            return

        try:
            directory_fd = os.open(".", _DIRECTORY_FLAGS, dir_fd=inspection_fd)
        except OSError as error:
            raise PickerBoundaryError("a selected directory could not be opened safely") from error
        try:
            current = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(current.st_mode)
                or current.st_dev != info.st_dev
                or current.st_ino != info.st_ino
            ):
                raise PickerBoundaryError("a selected directory changed during traversal")
            identity = (current.st_dev, current.st_ino)
            if identity in state.seen_directories:
                return
            state.seen_directories.add(identity)
            _walk_directory(directory_fd, path, current.st_dev, mount_id, 0, state)
        finally:
            os.close(directory_fd)
    finally:
        os.close(inspection_fd)


def expand_selections(
    selections: Sequence[str], *, deadline_seconds: float = TRAVERSAL_DEADLINE_SECONDS
) -> list[str]:
    if (
        isinstance(deadline_seconds, bool)
        or not isinstance(deadline_seconds, (int, float))
        or not math.isfinite(float(deadline_seconds))
        or deadline_seconds <= 0
        or deadline_seconds > TRAVERSAL_DEADLINE_SECONDS
    ):
        raise PickerBoundaryError("invalid file-traversal deadline")

    deadline = time.monotonic() + float(deadline_seconds)
    state = _TraversalState(deadline)
    with _wall_clock_alarm(float(deadline_seconds)):
        for selection in _prepare_selections(selections):
            _expand_one(selection, state)
        _check_deadline(state)
    return state.tracks


def _open_zenity() -> int:
    return _open_package_executable(ZENITY_EXECUTABLE, ZENITY_DIRECTORY)


def _open_package_executable(executable_path: str, directory_path: str) -> int:
    """Open and validate the fixed package object for descriptor-backed exec."""

    if (
        not os.path.isabs(executable_path)
        or os.path.dirname(executable_path) != directory_path
        or not hasattr(os, "O_PATH")
    ):
        raise PickerBoundaryError("the supported helper path is invalid")
    directory_fd = -1
    executable_fd = -1
    try:
        directory_fd = os.open(
            directory_path,
            os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        directory = os.fstat(directory_fd)
        executable_fd = os.open(
            os.path.basename(executable_path),
            os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
        executable = os.fstat(executable_fd)
    except OSError as error:
        if executable_fd >= 0:
            os.close(executable_fd)
        raise PickerBoundaryError("the supported helper executable is unavailable") from error
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
        raise PickerBoundaryError("the helper executable is not a protected package object")
    return executable_fd


def _validate_zenity() -> str:
    """Compatibility/test seam for validating the fixed executable."""

    descriptor = _open_zenity()
    os.close(descriptor)
    return ZENITY_EXECUTABLE


def _clean_environment(source: os._Environ[str] | dict[str, str] = os.environ) -> dict[str, str]:
    allowed = (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "WAYLAND_DISPLAY",
        "DISPLAY",
        "LANG",
        "LC_ALL",
    )
    environment: dict[str, str] = {"PATH": "/usr/bin", "LANG": "C.UTF-8"}
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
    return environment


def _set_parent_death_signal(expected_parent: int) -> None:
    """Ask Linux to terminate Zenity when this short-lived helper disappears."""

    if _LIBC.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        os._exit(127)
    if os.getppid() != expected_parent:
        os._exit(127)


def _arm_helper_parent_death() -> None:
    """Ensure this one-shot helper cannot outlive its QML owner."""

    expected_parent = os.getppid()
    if expected_parent <= 1:
        raise PickerBoundaryError("the file picker has no live owner")
    if _LIBC.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise PickerBoundaryError("cannot arm file-picker owner cleanup")
    if os.getppid() != expected_parent:
        raise PickerBoundaryError("the file-picker owner exited during startup")


def _guardian_main(read_descriptor: int, process_group: int) -> None:
    """Outlive the picker and tear down its Zenity process group on EOF."""

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
    deadline = time.monotonic() + PROCESS_TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            os._exit(0)
        time.sleep(0.02)
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


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        if process.poll() is None:
            try:
                process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        return

    group_exists = True
    deadline = time.monotonic() + PROCESS_TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            group_exists = False
            break
        time.sleep(0.02)
    if group_exists:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=PROCESS_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _finish_guardian(
    process: subprocess.Popen[bytes], guardian: tuple[int, int] | None
) -> None:
    """Trigger guardian cleanup and bound both guardian and leader reaping."""

    if guardian is None:
        _terminate_process_group(process)
        return
    write_descriptor, guardian_pid = guardian
    try:
        os.close(write_descriptor)
    except OSError:
        pass
    deadline = time.monotonic() + PROCESS_TERMINATION_GRACE_SECONDS + 1.0
    while time.monotonic() < deadline:
        process.poll()
        try:
            waited, _status = os.waitpid(guardian_pid, os.WNOHANG)
        except ChildProcessError:
            _terminate_process_group(process)
            return
        if waited == guardian_pid:
            # Guardian exit is not proof that its teardown completed.
            _terminate_process_group(process)
            return
        time.sleep(0.02)
    try:
        os.kill(guardian_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(guardian_pid, 0)
    except ChildProcessError:
        pass
    _terminate_process_group(process)


def _request_stop(_signum: int, _frame: object) -> None:
    global _stop_requested
    _stop_requested = True


def _install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGHUP, _request_stop)


def _collect_process_output(
    process: subprocess.Popen[bytes], deadline: float
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise PickerBoundaryError("the file chooser pipes are unavailable")

    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": MAX_ZENITY_STDOUT_BYTES, "stderr": MAX_ZENITY_STDERR_BYTES}
    streams = {"stdout": process.stdout, "stderr": process.stderr}
    try:
        for name, stream in streams.items():
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)

        while selector.get_map():
            if _stop_requested:
                raise PickerBoundaryError("file selection was interrupted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PickerBoundaryError("the file chooser exceeded its wall-clock limit")
            events = selector.select(min(remaining, 0.25))
            if not events:
                continue
            for key, _mask in events:
                name = key.data
                maximum = limits[name]
                chunk = os.read(key.fileobj.fileno(), min(65_536, maximum + 1 - len(buffers[name])))
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                buffers[name].extend(chunk)
                if len(buffers[name]) > maximum:
                    raise PickerBoundaryError(f"the file chooser {name} exceeded its byte limit")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PickerBoundaryError("the file chooser exceeded its wall-clock limit")
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise PickerBoundaryError("the file chooser exceeded its wall-clock limit") from error
        return bytes(buffers["stdout"]), bytes(buffers["stderr"])
    finally:
        selector.close()
        for stream in streams.values():
            if not stream.closed:
                stream.close()


def _run_zenity(arguments: list[str]) -> tuple[int, bytes, bytes]:
    started = time.monotonic()
    if _stop_requested:
        raise PickerBoundaryError("file selection was interrupted")
    executable_fd = _open_zenity()
    expected_parent = os.getpid()
    try:
        process = subprocess.Popen(
            [ZENITY_EXECUTABLE, *arguments],
            executable=f"/proc/self/fd/{executable_fd}",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_clean_environment(),
            start_new_session=True,
            close_fds=True,
            pass_fds=(executable_fd,),
            preexec_fn=lambda: _set_parent_death_signal(expected_parent),
        )
    finally:
        os.close(executable_fd)

    guardian: tuple[int, int] | None = None
    try:
        guardian = _start_guardian(process)
        stdout, stderr = _collect_process_output(process, started + ZENITY_DEADLINE_SECONDS)
        returncode = process.returncode
        if returncode is None:
            raise PickerBoundaryError("the file chooser did not exit cleanly")
        return returncode, stdout, stderr
    finally:
        _finish_guardian(process, guardian)


def _parse_zenity_selections(stdout: bytes) -> list[str]:
    if len(stdout) > MAX_ZENITY_STDOUT_BYTES:
        raise PickerBoundaryError("the file chooser output exceeded its byte limit")
    try:
        decoded = stdout.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise PickerBoundaryError("the file chooser returned invalid UTF-8") from error
    if any(
        character != "\n" and (ord(character) < 0x20 or ord(character) == 0x7F)
        for character in decoded
    ):
        raise PickerBoundaryError("the file chooser returned a path with control characters")
    selections = [line for line in decoded.split("\n") if line]
    if len(selections) > MAX_SELECTIONS:
        raise PickerBoundaryError(f"at most {MAX_SELECTIONS} paths may be selected")
    # Validation happens before any filesystem work or queue mutation.
    return _prepare_selections(selections)


def choose_paths(folder_mode: bool, explicit: Sequence[str]) -> tuple[list[str], bool]:
    if explicit:
        return expand_selections(explicit), False

    arguments = [
        "--file-selection",
        "--multiple",
        "--separator=\n",
        "--title=Add an audio folder to CLIAMP" if folder_mode else "--title=Choose audio for CLIAMP",
    ]
    if folder_mode:
        arguments.append("--directory")
    else:
        arguments.extend([f"--file-filter={AUDIO_FILTER}", "--file-filter=All files | *"])

    returncode, stdout, stderr = _run_zenity(arguments)
    if returncode == 1:
        return [], True
    if returncode != 0:
        reason = display_text(stderr.decode("utf-8", "replace"), maximum=MAX_ERROR_BYTES)
        raise PickerBoundaryError(reason or "the file chooser failed")
    return expand_selections(_parse_zenity_selections(stdout)), False


def _validate_queue_paths(paths: Sequence[str]) -> list[str]:
    deadline = time.monotonic() + TRAVERSAL_DEADLINE_SECONDS
    state = _TraversalState(deadline)
    with _wall_clock_alarm(TRAVERSAL_DEADLINE_SECONDS):
        normalized = _prepare_selections(paths)
        if len(normalized) > MAX_ACCEPTED_FILES:
            raise PickerBoundaryError(f"at most {MAX_ACCEPTED_FILES} audio files may be queued")
        aggregate = 0
        result: list[str] = []
        seen: set[str] = set()
        device: int | None = None
        mount_id: int | None = None
        for path in normalized:
            _check_deadline(state)
            descriptor, info = _open_path_without_symlinks(path)
            try:
                current_mount_id = _mount_id(descriptor)
            finally:
                os.close(descriptor)
            if not stat.S_ISREG(info.st_mode) or not _visible_audio_name(os.path.basename(path)):
                raise PickerBoundaryError("every queued path must be a supported regular audio file")
            if device is None:
                device = info.st_dev
                mount_id = current_mount_id
            elif device != info.st_dev or mount_id != current_mount_id:
                raise PickerBoundaryError("all queued paths must be on one filesystem and mount")
            if path in seen:
                continue
            aggregate += _encoded_path_length(path)
            if aggregate > MAX_ACCEPTED_PATH_BYTES:
                raise PickerBoundaryError("queued audio paths exceed the aggregate byte limit")
            seen.add(path)
            result.append(path)
        _check_deadline(state)
        if not result:
            raise PickerBoundaryError("no audio files were provided")
        return result


def _file_metadata(path: str, deadline: float) -> dict[str, str]:
    """Best-effort tags from a bounded, local-only probe of a validated file."""
    if _stop_requested:
        raise PickerBoundaryError("file selection was interrupted")
    if time.monotonic() >= deadline:
        return {}
    executable_fd = file_fd = -1
    process = None
    guardian = None
    try:
        executable_fd = _open_package_executable(FFPROBE_EXECUTABLE, "/usr/bin")
        file_fd, file_info = _open_path_without_symlinks(path)
        if not stat.S_ISREG(file_info.st_mode):
            return {}
        expected_parent = os.getpid()
        process = subprocess.Popen(
            [FFPROBE_EXECUTABLE, "-v", "error", "-probesize", "1048576",
             "-analyzeduration", "0", "-protocol_whitelist", "file",
             "-format_whitelist", "mp3,flac,ogg,wav,mov,aac,asf",
             "-show_entries", "format_tags=title,artist,album,genre:stream_tags=title,artist,album,genre",
             "-of", "json", f"/proc/self/fd/{file_fd}"],
            executable=f"/proc/self/fd/{executable_fd}",
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin", "LANG": "C.UTF-8"},
            start_new_session=True, close_fds=True, pass_fds=(executable_fd, file_fd),
            preexec_fn=lambda: _set_parent_death_signal(expected_parent),
        )
        guardian = _start_guardian(process)
        stdout, _stderr = _collect_process_output(
            process, min(deadline, time.monotonic() + METADATA_FILE_SECONDS)
        )
        if process.returncode != 0:
            return {}
        data = json.loads(stdout)
        if not isinstance(data, dict):
            return {}
        containers = [data.get("format", {})]
        streams = data.get("streams", [])
        if isinstance(streams, list):
            containers.extend(streams)
        result = {}
        for container in containers:
            tags = container.get("tags", {}) if isinstance(container, dict) else {}
            if not isinstance(tags, dict):
                continue
            for key, value in tags.items():
                field = key.lower()
                if field in {"title", "artist", "album", "genre"} and isinstance(value, str):
                    text = " ".join(display_text(value).split())
                    if text and field not in result:
                        result[field] = text
        return result
    except (OSError, ValueError, RecursionError, PickerBoundaryError):
        if _stop_requested:
            raise PickerBoundaryError("file selection was interrupted")
        return {}
    finally:
        if process is not None:
            _finish_guardian(process, guardian)
        for descriptor in (file_fd, executable_fd):
            if descriptor >= 0:
                os.close(descriptor)


def load_paths(paths: Sequence[str]) -> dict[str, Any]:
    validated = _validate_queue_paths(paths)
    requests: list[dict[str, Any]] = []
    metadata_deadline = time.monotonic() + METADATA_DEADLINE_SECONDS
    for index, path in enumerate(validated):
        track = {"title": display_text(Path(path).stem), "path": path}
        track.update(_file_metadata(path, metadata_deadline))
        requests.append({
            "cmd": "track.play" if index == 0 else "track.queue",
            "track": track,
        })
    requests.append({"cmd": "queue.list"})

    responses = send_requests(
        requests,
        deadline_seconds=IPC_BATCH_DEADLINE_SECONDS,
        stop_on_error=True,
        retain_responses=False,
    )
    if not responses:
        raise RuntimeError("CLIAMP returned no response")
    response = responses[-1]
    if response.get("ok") is not True:
        raise RuntimeError(str(response.get("error") or "CLIAMP rejected the file"))
    return response


def _emit(value: object) -> None:
    """Write exactly one UTF-8 JSON line within the shared helper-output cap."""

    encoded = encode_json(value, maximum=MAX_OUTPUT_BYTES)
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


def main() -> None:
    global _stop_requested
    _stop_requested = False
    _install_signal_handlers()
    arguments = sys.argv[1:]
    folder_mode = bool(arguments and arguments[0] == "--folder")
    if arguments and arguments[0] in ("--folder", "--files"):
        arguments = arguments[1:]
    source_kind = "folder" if folder_mode else "files"

    try:
        _arm_helper_parent_death()
        paths, cancelled = choose_paths(folder_mode, arguments)
        if not paths:
            if cancelled:
                _emit({"ok": True, "cancelled": True, "count": 0})
                return
            raise PickerBoundaryError("That selection contains no supported audio files.")
        response = load_paths(paths)
        response["selected_count"] = len(paths)
        response["source_kind"] = source_kind
        _emit(response)
    except (OSError, TimeoutError, RuntimeError, ValueError) as error:
        reason = display_text(str(error), maximum=MAX_ERROR_BYTES)
        _emit({"ok": False, "error": f"Could not play selected files: {reason}"})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
