import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import cliamp_ipc
from cliamped_security import (
    MAX_PROVIDERS,
    MAX_TEXT_BYTES,
    MAX_WIRE_RESPONSE_BYTES,
    ValidationError,
    normalize_request,
    normalize_response,
)


class UnixPeer:
    """A same-process Unix peer whose executable can be authenticated in tests."""

    def __init__(self, replies, *, pid=None):
        self.temporary = tempfile.TemporaryDirectory()
        os.chmod(self.temporary.name, 0o700)
        self.path = os.path.join(self.temporary.name, "cliamp.sock")
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        os.chmod(self.path, 0o600)
        Path(self.path + ".pid").write_text(str(os.getpid() if pid is None else pid), encoding="ascii")
        os.chmod(self.path + ".pid", 0o600)
        self.listener.listen(1)
        self.replies = list(replies)
        self.requests = []
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        try:
            connection, _ = self.listener.accept()
            with connection:
                for reply in self.replies:
                    request = bytearray()
                    while b"\n" not in request:
                        chunk = connection.recv(4096)
                        if not chunk:
                            return
                        request.extend(chunk)
                    self.requests.append(json.loads(bytes(request).partition(b"\n")[0]))
                    if callable(reply):
                        reply(connection)
                    else:
                        connection.sendall(reply)
        except OSError:
            # Expected when a deadline or validation failure closes the client.
            pass

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # Some rejection tests fail before connecting. Wake a blocked accept so
        # no daemon test thread survives into later process-guardian tests.
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as wake:
                wake.connect(self.path)
        except OSError:
            pass
        self.listener.close()
        self.thread.join(timeout=1)
        self.assert_thread_stopped()
        self.temporary.cleanup()

    def assert_thread_stopped(self):
        if self.thread.is_alive():
            raise AssertionError("temporary Unix peer did not stop")


def send_to(peer, request=None, **options):
    return cliamp_ipc.send_request(
        request or {"cmd": "status"},
        socket_path=peer.path,
        trusted_executable=sys.executable,
        **options,
    )


class AuthenticatedTransportTests(unittest.TestCase):
    def test_rejects_a_current_user_owned_executable_trust_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "cliamp"
            executable.write_bytes(b"stub")
            executable.chmod(0o500)
            with self.assertRaisesRegex(cliamp_ipc.SecurityError, "owned by this user"):
                cliamp_ipc._validate_trusted_executable(str(executable))

    def test_authenticates_peer_and_returns_normalized_status(self):
        with UnixPeer([b'{"ok":true,"state":"playing","ignored":"value"}\n']) as peer:
            response = send_to(peer)

        self.assertEqual(response["state"], "playing")
        self.assertIn(response["session_mode"], {"tui", "unknown"})
        self.assertNotIn("ignored", response)
        self.assertEqual(peer.requests, [{"cmd": "status"}])

    def test_requires_response_newline(self):
        with UnixPeer([b'{"ok":true}']) as peer:
            with self.assertRaisesRegex(cliamp_ipc.ProtocolError, "before the response newline"):
                send_to(peer)

    def test_rejects_trailing_non_whitespace(self):
        with UnixPeer([b'{"ok":true}\nsecond frame']) as peer:
            with self.assertRaisesRegex(cliamp_ipc.ProtocolError, "trailing data"):
                send_to(peer)

    def test_caps_unterminated_response_frame(self):
        with UnixPeer([b"x" * (MAX_WIRE_RESPONSE_BYTES + 1)]) as peer:
            with self.assertRaisesRegex(cliamp_ipc.ProtocolError, "byte limit"):
                send_to(peer)

    def test_slow_drip_cannot_extend_absolute_deadline(self):
        def slow_reply(connection):
            connection.sendall(b"{")
            time.sleep(0.08)
            connection.sendall(b'}\n')

        with UnixPeer([slow_reply]) as peer:
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                send_to(peer, deadline_seconds=0.03)
            self.assertLess(time.monotonic() - started, 0.2)

    def test_rejects_pid_file_that_does_not_match_peer(self):
        with UnixPeer([b'{"ok":true}\n'], pid=os.getpid() + 1) as peer:
            with self.assertRaisesRegex(cliamp_ipc.SecurityError, "PID file"):
                send_to(peer)

    @unittest.skipUnless(os.path.exists("/usr/bin/true"), "requires a second trusted executable")
    def test_rejects_peer_whose_executable_is_not_the_trust_anchor(self):
        with UnixPeer([b'{"ok":true}\n']) as peer:
            with self.assertRaisesRegex(cliamp_ipc.SecurityError, "trusted CLIAMP"):
                cliamp_ipc.send_request(
                    {"cmd": "status"},
                    socket_path=peer.path,
                    trusted_executable="/usr/bin/true",
                )

    def test_rejects_non_socket_and_symlink_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            regular = Path(directory) / "cliamp.sock"
            regular.write_text("not a socket")
            os.chmod(regular, 0o600)
            with self.assertRaisesRegex(cliamp_ipc.SecurityError, "not a Unix socket"):
                cliamp_ipc.send_request(
                    {"cmd": "status"},
                    socket_path=regular,
                    trusted_executable=sys.executable,
                )

        with UnixPeer([b'{"ok":true}\n']) as peer:
            alias = os.path.join(peer.temporary.name, "alias.sock")
            os.symlink(peer.path, alias)
            with self.assertRaisesRegex(cliamp_ipc.SecurityError, "not a Unix socket"):
                cliamp_ipc.send_request(
                    {"cmd": "status"},
                    socket_path=alias,
                    trusted_executable=sys.executable,
                )

    def test_serialized_batch_uses_one_connection_and_can_discard_intermediates(self):
        replies = [b'{"ok":true}\n', b'{"ok":true,"tracks":[]}\n']
        with UnixPeer(replies) as peer:
            responses = cliamp_ipc.send_requests(
                [
                    {"cmd": "track.play", "track": {"path": "/music/one.mp3"}},
                    {"cmd": "queue.list"},
                ],
                socket_path=peer.path,
                trusted_executable=sys.executable,
                retain_responses=False,
            )

        self.assertEqual(responses, [{"ok": True, "tracks": []}])
        self.assertEqual(len(peer.requests), 2)


class SchemaBoundaryTests(unittest.TestCase):
    def test_cli_starts_in_isolated_mode_and_emits_only_bounded_json(self):
        helper = Path(cliamp_ipc.__file__).resolve()
        completed = subprocess.run(
            ["/usr/bin/python3", "-I", str(helper), '{"cmd":"unsupported"}'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr, b"")
        self.assertLessEqual(len(completed.stdout), MAX_TEXT_BYTES * 4)
        self.assertFalse(json.loads(completed.stdout)["ok"])

    def test_provider_count_and_fields_are_capped_and_normalized(self):
        providers = [
            {
                "key": f"provider-{index}",
                "name": ("A" * 900) + "\n\N{RIGHT-TO-LEFT OVERRIDE}<img>",
                "searchable": False,
                "untrusted_extra": "discard me",
            }
            for index in range(MAX_PROVIDERS + 5)
        ]
        # Unknown item fields are rejected instead of being retained.
        with self.assertRaisesRegex(ValidationError, "unsupported fields"):
            normalize_response({"cmd": "provider.list"}, {"ok": True, "providers": providers})

        for provider in providers:
            provider.pop("untrusted_extra")
        response = normalize_response(
            {"cmd": "provider.list"}, {"ok": True, "providers": providers}
        )
        self.assertEqual(len(response["providers"]), MAX_PROVIDERS)
        self.assertTrue(response["truncated"])
        name = response["providers"][0]["name"]
        self.assertLessEqual(len(name.encode("utf-8")), MAX_TEXT_BYTES)
        self.assertNotIn("\n", name)
        self.assertNotIn("\N{RIGHT-TO-LEFT OVERRIDE}", name)

    def test_rejects_malformed_types_nonfinite_numbers_and_unknown_requests(self):
        with self.assertRaisesRegex(ValidationError, "boolean"):
            normalize_response(
                {"cmd": "device", "name": "list"},
                {"ok": True, "devices": [{"name": "speaker", "active": 1}]},
            )
        with self.assertRaisesRegex(ValidationError, "outside"):
            normalize_response(
                {"cmd": "status"}, {"ok": True, "position": float("nan")}
            )
        with self.assertRaisesRegex(ValidationError, "outside"):
            normalize_response(
                {"cmd": "status"}, {"ok": True, "position": 10**10000}
            )
        with self.assertRaisesRegex(ValidationError, "unsupported IPC command"):
            normalize_request({"cmd": "unsafe.future.command"})

    def test_action_requests_use_exact_supported_ranges_and_enums(self):
        for request in (
            {"cmd": "speed", "value": 2.01},
            {"cmd": "shuffle", "name": "cycle"},
            {"cmd": "mono", "name": "yes"},
            {"cmd": "repeat", "name": "toggle"},
            {"cmd": "eq", "name": "Flat", "band": 2, "value": 1},
            {"cmd": "eq", "band": 2},
        ):
            with self.subTest(request=request), self.assertRaises(ValidationError):
                normalize_request(request)

        self.assertEqual(
            normalize_request({"cmd": "repeat", "name": "cycle"}),
            {"cmd": "repeat", "name": "cycle"},
        )
        self.assertEqual(
            normalize_request({"cmd": "eq", "band": 2, "value": 1}),
            {"cmd": "eq", "band": 2, "value": 1},
        )

    def test_opaque_response_identifiers_are_not_unicode_normalized(self):
        decomposed = "Cafe\u0301"
        track = normalize_response(
            {"cmd": "queue.list"},
            {"ok": True, "tracks": [{"path": f"/music/{decomposed}.flac"}]},
        )["tracks"][0]
        device = normalize_response(
            {"cmd": "device", "name": "list"},
            {"ok": True, "devices": [{"name": decomposed, "active": True}]},
        )["devices"][0]
        self.assertEqual(track["path"], f"/music/{decomposed}.flac")
        self.assertEqual(device["name"], decomposed)

    def test_round_tripped_track_without_album_art_remains_valid(self):
        track = normalize_response(
            {"cmd": "queue.list"},
            {"ok": True, "tracks": [{"path": "/music/plain.flac"}]},
        )["tracks"][0]
        # QML materializes absent optional fields as bounded empty values.
        track["album_art_url"] = ""
        request = normalize_request({"cmd": "track.play", "track": track})
        self.assertEqual(request["track"]["album_art_url"], "")

    def test_rejects_duplicate_json_fields(self):
        with self.assertRaisesRegex(cliamp_ipc.ProtocolError, "duplicate"):
            cliamp_ipc._decode_response(b'{"ok":true,"ok":false}')

    def test_rejects_pathological_json_integer_as_a_protocol_error(self):
        with self.assertRaisesRegex(cliamp_ipc.ProtocolError, "invalid JSON"):
            cliamp_ipc._decode_response(b'{"ok":true,"position":' + (b"9" * 5000) + b"}")


if __name__ == "__main__":
    unittest.main()
