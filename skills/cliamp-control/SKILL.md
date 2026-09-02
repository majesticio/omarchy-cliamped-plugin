---
name: cliamp-control
description: Control and inspect a local CLIAMP music session and its CLIAMPed Omarchy panel. Use for playback, queue, volume, seeking, speed, EQ, modes, devices, collections, radio sources, or opening and navigating CLIAMPed. Do not use for unrelated media players.
---

# CLIAMP Control

Use CLIAMP as the playback source of truth and CLIAMPed only for shell UI state.

## Workflow

1. Inspect the session with `/usr/bin/cliamp status --json` before making decisions that depend on current state.
2. Prefer the documented `/usr/bin/cliamp` subcommands for ordinary playback and settings. Run `/usr/bin/cliamp <command> --help` when an argument is uncertain.
3. Use `/usr/bin/omarchy-shell io.github.majesticio.cliamped ...` only for opening, closing, navigating, or selecting CLIAMPed's panel visualizer.
4. Use CLIAMP's newline-framed socket API only for provider browsing, queue inspection, or other structured operations unavailable from the CLI. Read [references/ipc.md](references/ipc.md) before doing so.
5. Report the resulting state when it materially confirms the requested action. Do not claim success from exit status alone when `/usr/bin/cliamp status --json` can verify it.

If no session is running, say so for read-only requests. For a requested playback action, prefer opening CLIAMPed so its supervised daemon has explicit plugin ownership and teardown. If `/usr/bin/cliamp --daemon --provider radio` is started directly, identify it as a user-owned persistent session and report that lifecycle; do not replace an already-running user session.

## Ordinary controls

Use `/usr/bin/cliamp play`, `pause`, `toggle`, `next`, `prev`, or `stop`. Other common controls are:

```bash
/usr/bin/cliamp volume <relative-dB>
/usr/bin/cliamp seek <relative-seconds>
/usr/bin/cliamp speed <0.25-2.0>
/usr/bin/cliamp eq <preset>
/usr/bin/cliamp shuffle <on|off|toggle>
/usr/bin/cliamp repeat <off|all|one|cycle>
/usr/bin/cliamp mono <on|off|toggle>
/usr/bin/cliamp device <name|list>
/usr/bin/cliamp history --json --limit 20
```

Before using `/usr/bin/cliamp next` to satisfy a request to change tracks or stations, check the status fields. When shuffle is off, `index == total - 1`, and repeat is off, `next` stops playback instead of selecting another item. Inspect the queue and play a different index explicitly, or ask which item to play. Preserve shuffle and repeat settings unless the user requested a change to them; do not alter unrelated settings as a fallback.

For an absolute volume request, read the current dB value and send only the required relative delta. Keep volume within CLIAMP's `-30` to `+6` dB range.

Use `/usr/bin/cliamp playlist ...` for local collection creation and maintenance. Preserve the user's terminology: CLIAMPed calls these collections in its interface even though the CLI command remains `playlist`.

## CLIAMPed UI

```bash
/usr/bin/omarchy-shell io.github.majesticio.cliamped open
/usr/bin/omarchy-shell io.github.majesticio.cliamped tab favorites
/usr/bin/omarchy-shell io.github.majesticio.cliamped tab browse
/usr/bin/omarchy-shell io.github.majesticio.cliamped tab queue
/usr/bin/omarchy-shell io.github.majesticio.cliamped tab files
/usr/bin/omarchy-shell io.github.majesticio.cliamped tab more
/usr/bin/omarchy-shell io.github.majesticio.cliamped visualizer Spectrum
/usr/bin/omarchy-shell io.github.majesticio.cliamped visualizer Canyon
/usr/bin/omarchy-shell io.github.majesticio.cliamped visualizer Pulse
/usr/bin/omarchy-shell io.github.majesticio.cliamped close
```

CLIAMPed's three panel visualizers are separate from CLIAMP's TUI visualizer modes. Use the shell endpoint above when the user names Spectrum, Canyon, or Pulse; use `/usr/bin/cliamp vis` only when they explicitly mean the CLIAMP TUI.
