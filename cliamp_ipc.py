#!/usr/bin/env python3
"""Small newline-framed client for CLIAMP's Unix-socket API."""

import json
import os
import socket
import sys
from typing import Any


def send_request(request: dict[str, Any]) -> dict[str, Any]:
    config_root = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    socket_path = os.path.join(config_root, "cliamp", "cliamp.sock")
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(35)
    try:
        client.connect(socket_path)
        client.sendall(payload)
        response = bytearray()
        while b"\n" not in response:
            chunk = client.recv(65536)
            if not chunk:
                break
            response.extend(chunk)
    finally:
        client.close()

    line = bytes(response).partition(b"\n")[0]
    if not line:
        raise RuntimeError("CLIAMP returned no response")
    decoded = json.loads(line)
    if not isinstance(decoded, dict):
        raise RuntimeError("CLIAMP returned an unexpected response")
    return decoded


def fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}))
    raise SystemExit(1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("missing IPC request")
    try:
        request = json.loads(sys.argv[1])
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        response = send_request(request)
    except (OSError, TimeoutError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as error:
        fail(f"CLIAMP IPC failed: {error}")
    print(json.dumps(response, separators=(",", ":")))


if __name__ == "__main__":
    main()
