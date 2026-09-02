# CLIAMP IPC

Read this reference only for structured operations not covered by the public CLI.

CLIAMP listens on `${XDG_CONFIG_HOME:-$HOME/.config}/cliamp/cliamp.sock`. Requests and responses are JSON objects terminated by one newline. Never issue concurrent requests on the same connection or reuse a response from a previous command.

From this repository, use the serialized client rather than writing ad hoc socket code:

```bash
/usr/bin/python3 -I ../../cliamp_ipc.py '{"cmd":"status"}'
```

Resolve `../../cliamp_ipc.py` relative to this skill directory. In an installed Omarchy plugin it remains inside the same plugin bundle.

Useful read operations:

```json
{"cmd":"status"}
{"cmd":"queue.list"}
{"cmd":"provider.list"}
{"cmd":"provider.playlists","provider":"radio"}
{"cmd":"provider.catalog","provider":"radio","offset":0,"limit":18}
{"cmd":"provider.search","provider":"radio","query":"jazz","limit":18}
{"cmd":"history","limit":24}
{"cmd":"lyrics"}
{"cmd":"device","name":"list"}
```

Mutating operations used by CLIAMPed:

```json
{"cmd":"play"}
{"cmd":"volume","value":-4}
{"cmd":"track.play","track":{"path":"https://example.invalid/stream"}}
{"cmd":"track.queue","track":{"path":"/absolute/path/to/audio.flac"}}
{"cmd":"queue.play","index":0}
{"cmd":"queue.remove","index":0}
{"cmd":"queue.clear"}
{"cmd":"provider.load","provider":"radio","playlist":"<id>"}
{"cmd":"provider.favorite","provider":"radio","playlist":"<id>"}
```

The IPC `volume` value is an absolute dB target, unlike the relative delta accepted by `cliamp volume`. Keep absolute targets within `-30` to `+6` dB.

Use IDs and track objects returned by the same provider/session. Do not invent playlist IDs. Treat `queue.clear`, collection loading, and track replacement as destructive to the active queue and run them only when the user requested the corresponding change.

CLIAMP's provider search response does not supply the playlist IDs required by `provider.favorite`. CLIAMPed therefore keeps searched-station favorites in `~/.config/cliamp/cliamped_search_favorites.json`; manage those through the panel rather than fabricating an IPC favorite request.
