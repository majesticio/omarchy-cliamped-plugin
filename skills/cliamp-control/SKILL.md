---
name: cliamp-control
description: Control and inspect a local CLIAMP music session and its CLIAMPed Omarchy panel. Use for playback, queue, volume, seeking, speed, EQ, modes, devices, collections, radio sources, or opening and navigating CLIAMPed. Do not use for unrelated media players.
---

# CLIAMP Control

Use CLIAMP as the playback source of truth and CLIAMPed only for shell UI state.

## Workflow

1. Inspect the session with `cliamp status --json` before making decisions that depend on current state.
2. Prefer the documented `cliamp` subcommands for ordinary playback and settings. Run `<command> --help` when an argument is uncertain.
3. Use `omarchy-shell io.github.majesticio.cliamped ...` only for opening, closing, navigating, or selecting CLIAMPed's panel visualizer.
4. Use CLIAMP's newline-framed socket API only for provider browsing, queue inspection, or other structured operations unavailable from the CLI. Read [references/ipc.md](references/ipc.md) before doing so.
5. Report the resulting state when it materially confirms the requested action. Do not claim success from exit status alone when `cliamp status --json` can verify it.

If no session is running, say so for read-only requests. For a requested playback action, starting `cliamp --daemon --provider radio` or opening CLIAMPed is in scope; do not replace an already-running user session.

## Ordinary controls

Use `cliamp play`, `pause`, `toggle`, `next`, `prev`, or `stop`. Other common controls are:

```bash
cliamp volume <relative-dB>
cliamp seek <relative-seconds>
cliamp speed <0.25-2.0>
cliamp eq <preset>
cliamp shuffle <on|off|toggle>
cliamp repeat <off|all|one|cycle>
cliamp mono <on|off|toggle>
cliamp device <name|list>
cliamp history --json --limit 20
```

Before using `cliamp next` to satisfy a request to change tracks or stations, check the status fields. When shuffle is off, `index == total - 1`, and repeat is off, `next` stops playback instead of selecting another item. Inspect the queue and play a different index explicitly, or ask which item to play. Preserve shuffle and repeat settings unless the user requested a change to them; do not alter unrelated settings as a fallback.

For an absolute volume request, read the current dB value and send only the required relative delta. Keep volume within CLIAMP's `-30` to `+6` dB range.

Use `cliamp playlist ...` for local collection creation and maintenance. Preserve the user's terminology: CLIAMPed calls these collections in its interface even though the CLI command remains `playlist`.

## CLIAMPed UI

```bash
omarchy-shell io.github.majesticio.cliamped open
omarchy-shell io.github.majesticio.cliamped tab favorites
omarchy-shell io.github.majesticio.cliamped tab browse
omarchy-shell io.github.majesticio.cliamped tab queue
omarchy-shell io.github.majesticio.cliamped tab files
omarchy-shell io.github.majesticio.cliamped tab more
omarchy-shell io.github.majesticio.cliamped visualizer Spectrum
omarchy-shell io.github.majesticio.cliamped visualizer Canyon
omarchy-shell io.github.majesticio.cliamped visualizer Pulse
omarchy-shell io.github.majesticio.cliamped close
```

CLIAMPed's three panel visualizers are separate from CLIAMP's TUI visualizer modes. Use the shell endpoint above when the user names Spectrum, Canyon, or Pulse; use `cliamp vis` only when they explicitly mean the CLIAMP TUI.
