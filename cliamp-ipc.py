#!/usr/bin/python3 -I
"""Compatibility entry point for panels cached from older plugin releases."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cliamp_ipc import main


if __name__ == "__main__":
    main()
