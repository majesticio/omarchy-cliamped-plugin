#!/usr/bin/python3 -I
"""Bounded, authenticated newline-framed client for CLIAMP's Unix socket."""

from __future__ import annotations

import json
import math
import ctypes
import os
import pwd
import signal
import socket
import stat
import struct
import sys
import time
import unicodedata  # Loaded before exposing the trusted plugin directory.
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Python isolated mode intentionally omits the script directory. The QML side
# invokes this helper with -I, so expose only this resolved bundle directory for
# the duration of the sibling import, after the standard-library imports above.
_BUNDLE_DIRECTORY = os.path.dirname(os.path.realpath(__file__))
_inserted_bundle_directory = _BUNDLE_DIRECTORY not in sys.path
if _inserted_bundle_directory:
    sys.path.insert(0, _BUNDLE_DIRECTORY)
try:
    from cliamped_security import (
        MAX_BATCH_REQUEST_BYTES,
        MAX_BATCH_REQUESTS,
        MAX_DEADLINE_SECONDS,
        MAX_ERROR_BYTES,
        MAX_OUTPUT_BYTES,
        MAX_REQUEST_BYTES,
        MAX_WIRE_RESPONSE_BYTES,
        ValidationError,
        deadline_seconds_for_request,
        display_text,
        encode_json,
        normalize_request,
        normalize_response,
    )
finally:
    if _inserted_bundle_directory:
        sys.path.remove(_BUNDLE_DIRECTORY)


TRUSTED_CLIAMP_PATH = "/usr/bin/cliamp"
DEFAULT_SOCKET_BASENAME = "cliamp.sock"

# A batch is serialized on one authenticated connection. Each individual frame
# has its own limit and all frames together share this additional ceiling.
MAX_BATCH_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_SOCKET_PATH_BYTES = 103
MAX_PID_FILE_BYTES = 64
MAX_CMDLINE_BYTES = 4096
RECEIVE_CHUNK_BYTES = 16 * 1024
_LIBC = ctypes.CDLL(None, use_errno=True)


class IPCError(RuntimeError):
    """Base error for a rejected or failed IPC exchange."""


class SecurityError(IPCError):
    """The socket, peer, PID file, or executable identity was not trusted."""


class ProtocolError(IPCError):
    """The peer violated the bounded newline/JSON protocol."""


def _arm_helper_parent_death() -> None:
    """Ensure this one-shot helper cannot outlive its QML owner."""

    expected_parent = os.getppid()
    if expected_parent <= 1:
        raise SecurityError("the IPC helper has no live owner")
    if _LIBC.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise SecurityError("cannot arm IPC-helper owner cleanup")
    if os.getppid() != expected_parent:
        raise SecurityError("the IPC-helper owner exited during startup")


@dataclass(frozen=True)
class PeerIdentity:
    pid: int
    uid: int
    gid: int
    session_mode: str


@dataclass
class _Endpoint:
    path: str
    directory_fd: int
    socket_name: str
    socket_stat: os.stat_result

    def close(self) -> None:
        os.close(self.directory_fd)


def default_socket_path() -> str:
    """Resolve the one supported CLIAMP socket path without trusting HOME."""

    config_root = os.environ.get("XDG_CONFIG_HOME")
    if not config_root:
        config_root = os.path.join(pwd.getpwuid(os.geteuid()).pw_dir, ".config")
    if not os.path.isabs(config_root):
        raise SecurityError("XDG_CONFIG_HOME must be an absolute path")
    return _validate_socket_path(os.path.join(config_root, "cliamp", DEFAULT_SOCKET_BASENAME))


def _validate_socket_path(value: str | os.PathLike[str]) -> str:
    try:
        path = os.fsdecode(os.fspath(value))
    except (TypeError, UnicodeError) as error:
        raise SecurityError("invalid CLIAMP socket path") from error
    if not os.path.isabs(path) or "\x00" in path:
        raise SecurityError("CLIAMP socket path must be absolute")
    try:
        encoded = os.fsencode(path)
    except UnicodeError as error:
        raise SecurityError("CLIAMP socket path has invalid encoding") from error
    if len(encoded) > MAX_SOCKET_PATH_BYTES:
        raise SecurityError("CLIAMP socket path is too long")
    return os.path.normpath(path)


def _validate_private_directory(directory_fd: int) -> None:
    info = os.fstat(directory_fd)
    if not stat.S_ISDIR(info.st_mode):
        raise SecurityError("CLIAMP socket parent is not a directory")
    if info.st_uid != os.geteuid():
        raise SecurityError("CLIAMP socket directory has the wrong owner")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SecurityError("CLIAMP socket directory must be private (0700 or stricter)")


def _validate_socket_object(info: os.stat_result) -> None:
    if not stat.S_ISSOCK(info.st_mode):
        raise SecurityError("CLIAMP endpoint is not a Unix socket")
    if info.st_uid != os.geteuid():
        raise SecurityError("CLIAMP socket has the wrong owner")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise SecurityError("CLIAMP socket permissions must be 0600")


def _open_endpoint(path: str) -> _Endpoint:
    parent, socket_name = os.path.split(path)
    if not parent or not socket_name:
        raise SecurityError("invalid CLIAMP socket path")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        directory_fd = os.open(parent, flags)
    except OSError as error:
        raise SecurityError(f"cannot open CLIAMP socket directory: {error.strerror}") from error
    try:
        _validate_private_directory(directory_fd)
        info = os.stat(socket_name, dir_fd=directory_fd, follow_symlinks=False)
        _validate_socket_object(info)
        return _Endpoint(path, directory_fd, socket_name, info)
    except OSError as error:
        os.close(directory_fd)
        raise SecurityError(f"cannot inspect CLIAMP socket: {error.strerror}") from error
    except Exception:
        os.close(directory_fd)
        raise


def _same_object(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _validate_endpoint_stability(endpoint: _Endpoint) -> None:
    try:
        current = os.stat(
            endpoint.socket_name,
            dir_fd=endpoint.directory_fd,
            follow_symlinks=False,
        )
    except OSError as error:
        raise SecurityError("CLIAMP socket changed while connecting") from error
    _validate_socket_object(current)
    if not _same_object(endpoint.socket_stat, current):
        raise SecurityError("CLIAMP socket was replaced while connecting")


def _read_bounded_fd(file_descriptor: int, maximum: int, *, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= maximum:
        chunk = os.read(file_descriptor, min(4096, maximum + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    value = b"".join(chunks)
    if len(value) > maximum:
        raise SecurityError(f"{label} exceeds its byte limit")
    return value


def _read_expected_pid(endpoint: _Endpoint) -> int:
    pid_name = endpoint.socket_name + ".pid"
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        pid_fd = os.open(pid_name, flags, dir_fd=endpoint.directory_fd)
    except OSError as error:
        raise SecurityError(f"cannot open CLIAMP PID file: {error.strerror}") from error
    try:
        info = os.fstat(pid_fd)
        if not stat.S_ISREG(info.st_mode):
            raise SecurityError("CLIAMP PID file is not a regular file")
        if info.st_uid != os.geteuid():
            raise SecurityError("CLIAMP PID file has the wrong owner")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise SecurityError("CLIAMP PID file permissions must be 0600")
        raw = _read_bounded_fd(pid_fd, MAX_PID_FILE_BYTES, label="CLIAMP PID file")
    finally:
        os.close(pid_fd)
    try:
        text = raw.decode("ascii", "strict").strip()
        if not text.isascii() or not text.isdecimal():
            raise ValueError
        pid = int(text, 10)
    except (UnicodeError, ValueError) as error:
        raise SecurityError("CLIAMP PID file is invalid") from error
    if pid <= 1:
        raise SecurityError("CLIAMP PID file contains an invalid process ID")
    return pid


def _validate_trusted_executable(path: str) -> os.stat_result:
    if not isinstance(path, str) or not os.path.isabs(path) or "\x00" in path:
        raise SecurityError("trusted CLIAMP executable path must be absolute")
    try:
        info = os.stat(path)
    except OSError as error:
        raise SecurityError(f"cannot stat trusted CLIAMP executable: {error.strerror}") from error
    if not stat.S_ISREG(info.st_mode):
        raise SecurityError("trusted CLIAMP executable is not a regular file")
    if not info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise SecurityError("trusted CLIAMP executable is not executable")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise SecurityError("trusted CLIAMP executable is group/world writable")
    # An account that owns an object can chmod and replace it even when its
    # current owner-write bit is clear, so ownership itself is disqualifying.
    if info.st_uid == os.geteuid():
        raise SecurityError("trusted CLIAMP executable is owned by this user")
    return info


def _peer_credentials(client: socket.socket) -> tuple[int, int, int]:
    if not hasattr(socket, "SO_PEERCRED"):
        raise SecurityError("this platform cannot authenticate Unix-socket peers")
    size = struct.calcsize("3i")
    try:
        raw = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size)
    except OSError as error:
        raise SecurityError("cannot read CLIAMP peer credentials") from error
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != size:
        raise SecurityError("CLIAMP peer credentials are malformed")
    pid, uid, gid = struct.unpack("3i", raw)
    if pid <= 1 or uid != os.geteuid():
        raise SecurityError("CLIAMP peer identity does not match this user")
    return pid, uid, gid


def _session_mode(pid: int) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(f"/proc/{pid}/cmdline", flags)
    except OSError:
        return "unknown"
    try:
        raw = _read_bounded_fd(descriptor, MAX_CMDLINE_BYTES, label="CLIAMP command line")
    except (OSError, SecurityError):
        return "unknown"
    finally:
        os.close(descriptor)
    arguments = [part for part in raw.split(b"\0") if part]
    return "headless" if b"--daemon" in arguments else ("tui" if arguments else "unknown")


def _validate_peer(
    client: socket.socket,
    endpoint: _Endpoint,
    trusted_executable: str,
    trusted_before: os.stat_result,
) -> PeerIdentity:
    pid, uid, gid = _peer_credentials(client)
    expected_pid = _read_expected_pid(endpoint)
    if pid != expected_pid:
        raise SecurityError("CLIAMP PID file does not match the connected peer")
    try:
        peer_executable = os.stat(f"/proc/{pid}/exe")
    except OSError as error:
        raise SecurityError("cannot validate CLIAMP peer executable") from error
    if not _same_object(trusted_before, peer_executable):
        raise SecurityError("connected peer is not the trusted CLIAMP executable")
    trusted_after = _validate_trusted_executable(trusted_executable)
    if not _same_object(trusted_before, trusted_after):
        raise SecurityError("trusted CLIAMP executable changed while connecting")
    return PeerIdentity(pid=pid, uid=uid, gid=gid, session_mode=_session_mode(pid))


def _remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("CLIAMP IPC absolute deadline exceeded")
    return value


def _validated_deadline(seconds: float) -> float:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise ValueError("deadline_seconds must be a finite number")
    value = float(seconds)
    if not math.isfinite(value) or value <= 0 or value > MAX_DEADLINE_SECONDS:
        raise ValueError(f"deadline_seconds must be from 0 to {MAX_DEADLINE_SECONDS}")
    return time.monotonic() + value


def _reject_constant(value: str) -> None:
    raise ProtocolError(f"invalid JSON numeric constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("CLIAMP response contains duplicate object fields")
        result[key] = value
    return result


def _decode_response(line: bytes) -> dict[str, Any]:
    if not line:
        raise ProtocolError("CLIAMP returned an empty response")
    try:
        text = line.decode("utf-8", "strict")
        decoded = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ProtocolError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ProtocolError("CLIAMP returned invalid JSON") from error
    if not isinstance(decoded, dict):
        raise ProtocolError("CLIAMP returned a non-object response")
    return decoded


def _read_frame(client: socket.socket, deadline: float) -> bytes:
    response = bytearray()
    while True:
        client.settimeout(_remaining(deadline))
        remaining_capacity = MAX_WIRE_RESPONSE_BYTES + 1 - len(response)
        chunk = client.recv(max(1, min(RECEIVE_CHUNK_BYTES, remaining_capacity)))
        if not chunk:
            raise ProtocolError("CLIAMP closed the socket before the response newline")
        newline = chunk.find(b"\n")
        if newline >= 0:
            if len(response) + newline > MAX_WIRE_RESPONSE_BYTES:
                raise ProtocolError("CLIAMP response frame exceeds its byte limit")
            response.extend(chunk[:newline])
            trailing = chunk[newline + 1 :]
            if trailing.strip(b" \t\r\n"):
                raise ProtocolError("CLIAMP returned trailing data after its response")
            return bytes(response)
        response.extend(chunk)
        if len(response) > MAX_WIRE_RESPONSE_BYTES:
            raise ProtocolError("CLIAMP response frame exceeds its byte limit")


# --- CLIAMP v2 protocol adapter -------------------------------------------
#
# The installed CLIAMP (v2.0.1) rejects this file's pre-v2 raw request shape
# ({"cmd": "status", ...}) outright with {"error":{"code":"invalid_version"}}
# — confirmed by connecting to the socket directly. Its real v2 protocol was
# captured off the wire (a socat relay in front of the real socket, observing
# exactly what `cliamp remote call` sends), not guessed:
#
#   request:  {"version": 2, "id": "cliamp", "method": "operation.submit",
#              "operation": "<op>", "params": {...}}
#   response: {"version": 2, "id": "cliamp", "ok": true, "snapshot": {...}}
#             for synchronous ops (e.g. "runtime.status"), or
#             {"version": 2, "id": "cliamp", "ok": true,
#              "job": {"state": "succeeded", "snapshot": {...}, "result": {...}}}
#             for everything else (CLIAMP v2 treats most operations as async
#             jobs, but the daemon completes them synchronously in practice —
#             the response already carries the finished job), or
#             {"version": 2, "id": "cliamp", "ok": false,
#              "error": {"code": "...", "message": "..."}}
#
# normalize_request()'s per-command field whitelisting/bounds-checking below
# is untouched and still runs first — this adapter only changes the wire
# envelope around an already-validated v1-shaped request, and flattens an
# already-received v2 response back into the flat shape normalize_response()
# expects. Neither of those two security-critical functions (both in
# cliamped_security.py) needed to change at all.
#
# CLIAMP v2's operation names match the old "cmd" names 1:1 for every command
# this plugin actually sends, with one exception: bare "status" is now
# "runtime.status". The one command with no v2 equivalent at all is "vis"
# (switching the visualizer's render mode) — CLIAMP v2's operation list
# (`cliamp remote capabilities`) has nothing for it, so it fails cleanly
# instead of silently doing nothing.
_V2_OPERATION_NAMES = {"status": "runtime.status"}
_V2_UNSUPPORTED_COMMANDS = {"vis"}


def _to_v2_envelope(v1_request: dict[str, Any]) -> dict[str, Any]:
    command = v1_request["cmd"]
    if command in _V2_UNSUPPORTED_COMMANDS:
        raise ValidationError(f"{command} has no CLIAMP v2 operation")
    operation = _V2_OPERATION_NAMES.get(command, command)
    params = {key: value for key, value in v1_request.items() if key != "cmd"}
    return {
        "version": 2,
        "id": "cliamp",
        "method": "operation.submit",
        "operation": operation,
        "params": params,
    }


def _v2_response_to_v1(v2_response: Any) -> dict[str, Any]:
    if not isinstance(v2_response, dict):
        return {"ok": False, "error": "malformed CLIAMP response"}
    if v2_response.get("ok") is not True:
        error = v2_response.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        return {"ok": False, "error": message if isinstance(message, str) else "CLIAMP request failed"}
    flat: dict[str, Any] = {"ok": True}
    snapshot = v2_response.get("snapshot")
    if isinstance(snapshot, dict):
        flat.update(snapshot)
    job = v2_response.get("job")
    if isinstance(job, dict):
        job_snapshot = job.get("snapshot")
        if isinstance(job_snapshot, dict):
            flat.update(job_snapshot)
        job_result = job.get("result")
        if isinstance(job_result, dict):
            flat.update(job_result)
    return flat


# CLIAMP v2 treats almost every operation as an async job: operation.submit's
# immediate reply is often just {"job":{"state":"queued",...}} with no
# result/snapshot yet — confirmed on the wire (a fresh socat capture of
# `cliamp remote call provider.list --wait` showed exactly two requests: one
# operation.submit, answered with state "queued", then one
# {"method":"job.get","job_id":...}, answered with the finished job). Only
# the synchronous read-only ops (runtime.status/runtime.snapshot) skip the
# job envelope entirely and answer in one round trip. So: after the first
# response, if it's a non-terminal job, poll job.get until it finishes or
# this request's own deadline runs out (_remaining() already raises
# TimeoutError past the deadline, so this can't loop forever even without
# the attempt cap below).
_V2_TERMINAL_JOB_STATES = {"succeeded", "failed", "cancelled", "canceled"}
_V2_POLL_INTERVAL_SECONDS = 0.05
_V2_POLL_MAX_ATTEMPTS = 200


def _v2_job_is_terminal(decoded: Mapping[str, Any]) -> bool:
    job = decoded.get("job")
    if not isinstance(job, dict):
        return True  # no job envelope (e.g. runtime.status) — already final
    return job.get("state") in _V2_TERMINAL_JOB_STATES


def _await_v2_completion(
    client: socket.socket,
    deadline: float,
    decoded: dict[str, Any],
    cumulative_response_bytes: int,
) -> tuple[dict[str, Any], int]:
    if decoded.get("ok") is not True or _v2_job_is_terminal(decoded):
        return decoded, cumulative_response_bytes
    job = decoded.get("job")
    job_id = job.get("id") if isinstance(job, dict) else None
    if not isinstance(job_id, str) or not job_id:
        return decoded, cumulative_response_bytes

    attempts = 0
    while attempts < _V2_POLL_MAX_ATTEMPTS:
        attempts += 1
        client.settimeout(_remaining(deadline))
        poll_payload = (
            json.dumps({"version": 2, "id": "cliamp", "method": "job.get", "job_id": job_id})
            + "\n"
        ).encode("utf-8")
        client.sendall(poll_payload)
        _remaining(deadline)
        line = _read_frame(client, deadline)
        cumulative_response_bytes += len(line) + 1
        if cumulative_response_bytes > MAX_BATCH_RESPONSE_BYTES:
            raise ProtocolError("IPC batch exceeds its aggregate response byte limit")
        decoded = _decode_response(line)
        if decoded.get("ok") is not True or _v2_job_is_terminal(decoded):
            return decoded, cumulative_response_bytes
        time.sleep(_V2_POLL_INTERVAL_SECONDS)
        _remaining(deadline)
    return decoded, cumulative_response_bytes


def _encode_requests(requests: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[bytes]]:
    if isinstance(requests, (str, bytes, bytearray)) or not isinstance(requests, Sequence):
        raise ValidationError("requests must be a sequence of objects")
    if not requests or len(requests) > MAX_BATCH_REQUESTS:
        raise ValidationError(f"request batch must contain 1 to {MAX_BATCH_REQUESTS} items")
    normalized: list[dict[str, Any]] = []
    payloads: list[bytes] = []
    total = 0
    for request in requests:
        clean = normalize_request(request)
        payload = encode_json(_to_v2_envelope(clean), maximum=MAX_REQUEST_BYTES) + b"\n"
        total += len(payload)
        if total > MAX_BATCH_REQUEST_BYTES:
            raise ValidationError("request batch exceeds its aggregate byte limit")
        normalized.append(clean)
        payloads.append(payload)
    return normalized, payloads


def send_requests(
    requests: Sequence[dict[str, Any]],
    *,
    deadline_seconds: float | None = None,
    stop_on_error: bool = True,
    retain_responses: bool = True,
    socket_path: str | os.PathLike[str] | None = None,
    trusted_executable: str = TRUSTED_CLIAMP_PATH,
) -> list[dict[str, Any]]:
    """Send a bounded sequential batch over one authenticated connection.

    All requests share one absolute monotonic deadline and one aggregate wire
    response limit. With retain_responses false, successful intermediate bodies
    are discarded and the returned list contains only the last response.
    """

    if type(stop_on_error) is not bool or type(retain_responses) is not bool:
        raise ValueError("batch boolean options must be booleans")
    normalized, payloads = _encode_requests(requests)
    if deadline_seconds is None:
        deadline_seconds = max(deadline_seconds_for_request(item) for item in normalized)
    deadline = _validated_deadline(deadline_seconds)
    path = default_socket_path() if socket_path is None else _validate_socket_path(socket_path)
    trusted_before = _validate_trusted_executable(trusted_executable)
    endpoint = _open_endpoint(path)
    client: socket.socket | None = None
    retained: list[dict[str, Any]] = []
    cumulative_response_bytes = 0
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(_remaining(deadline))
        client.connect(path)
        _remaining(deadline)
        _validate_endpoint_stability(endpoint)
        peer = _validate_peer(client, endpoint, trusted_executable, trusted_before)
        for request, payload in zip(normalized, payloads, strict=True):
            client.settimeout(_remaining(deadline))
            client.sendall(payload)
            _remaining(deadline)
            line = _read_frame(client, deadline)
            cumulative_response_bytes += len(line) + 1
            if cumulative_response_bytes > MAX_BATCH_RESPONSE_BYTES:
                raise ProtocolError("IPC batch exceeds its aggregate response byte limit")
            decoded = _decode_response(line)
            decoded, cumulative_response_bytes = _await_v2_completion(
                client, deadline, decoded, cumulative_response_bytes
            )
            flat = _v2_response_to_v1(decoded)
            response = normalize_response(request, flat, session_mode=peer.session_mode)
            _remaining(deadline)
            if retain_responses:
                retained.append(response)
            else:
                retained[:] = [response]
            if stop_on_error and not response["ok"]:
                break
        return retained
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            endpoint.close()


def send_request(
    request: dict[str, Any],
    *,
    deadline_seconds: float | None = None,
    socket_path: str | os.PathLike[str] | None = None,
    trusted_executable: str = TRUSTED_CLIAMP_PATH,
) -> dict[str, Any]:
    """Send one supported request and return a bounded normalized response."""

    responses = send_requests(
        [request],
        deadline_seconds=deadline_seconds,
        socket_path=socket_path,
        trusted_executable=trusted_executable,
    )
    return responses[0]


def _bounded_failure(message: Any) -> dict[str, Any]:
    try:
        safe = display_text(str(message), maximum=MAX_ERROR_BYTES)
    except (UnicodeError, ValidationError):
        safe = "unexpected IPC failure"
    return {"ok": False, "error": safe or "unexpected IPC failure"}


def fail(message: Any) -> None:
    try:
        payload = encode_json(_bounded_failure(message), maximum=MAX_OUTPUT_BYTES)
        sys.stdout.buffer.write(payload + b"\n")
        sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        pass
    raise SystemExit(1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("missing IPC request")
    try:
        _arm_helper_parent_death()
        raw_request = sys.argv[1]
        if len(raw_request.encode("utf-8", "strict")) > MAX_REQUEST_BYTES:
            raise ValidationError("IPC request exceeds its byte limit")
        request = json.loads(
            raw_request,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        response = send_request(request)
        payload = encode_json(response, maximum=MAX_OUTPUT_BYTES)
    except Exception as error:
        fail(f"CLIAMP IPC failed: {error}")
    try:
        sys.stdout.buffer.write(payload + b"\n")
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
