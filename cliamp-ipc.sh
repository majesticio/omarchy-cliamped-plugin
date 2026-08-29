#!/usr/bin/env bash
# Compatibility launcher for CLIAMPed development panels cached before the Python IPC migration.
exec python3 "$(dirname "$0")/cliamp_ipc.py" "$@"
