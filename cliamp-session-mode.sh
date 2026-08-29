#!/usr/bin/env bash
set -euo pipefail

config_root="${XDG_CONFIG_HOME:-${HOME}/.config}"
pid_file="${config_root}/cliamp/cliamp.sock.pid"

if [[ ! -r "${pid_file}" ]]; then
  printf 'unknown\n'
  exit 0
fi

cliamp_pid="$(<"${pid_file}")"
if [[ ! "${cliamp_pid}" =~ ^[0-9]+$ ]] || [[ ! -r "/proc/${cliamp_pid}/cmdline" ]]; then
  printf 'unknown\n'
  exit 0
fi

command_line="$(tr '\0' ' ' < "/proc/${cliamp_pid}/cmdline")"
if [[ " ${command_line} " == *" --daemon "* ]]; then
  printf 'headless\n'
else
  printf 'tui\n'
fi
