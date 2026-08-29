# CLIAMPed

CLIAMPed is a theme-aware Omarchy bar widget and control center for [CLIAMP](https://github.com/bjarneo/cliamp). It follows the active Omarchy theme while bringing CLIAMP playback, providers, local media, and a 32-column multi-frequency visualizer into the shell.

The high-desert presentation is CLIAMPed's visual personality; its colors come from the current theme rather than a bundled palette.

## Features

- Playback, seeking, volume, speed, EQ, shuffle, repeat, and mono controls
- A live sub/bass/low/mid/high/air spectrum driven by `cliamp visstream`
- Eleven curated CLIAMP stations over HTTPS
- CLIAMP provider and playlist browsing, with provider search where supported
- Real queue inspection, play-next, removal, and clearing
- Multiple-file and recursive whole-folder playback
- Recently played tracks, lyrics, and audio-output switching
- Keyboard control while the panel is focused
- Automatic attachment to an existing CLIAMP TUI or daemon
- A self-started background daemon when CLIAMP is not already running

CLIAMPed uses CLIAMP's newline-framed Unix-socket IPC and serializes requests so concurrent panel actions cannot corrupt responses. File and folder selection runs in a separate Zenity process, keeping native picker failures outside Quickshell.

## Stations, providers, and playlists

These views have distinct jobs:

- **Stations** is CLIAMPed's curated quick picker.
- **Providers** reflects CLIAMP's configured hierarchy. Select a provider such as Radio or Local, then select one of its playlists or search it when supported.
- **Queue** is CLIAMP's active playback list.

Loading a provider playlist replaces the active list, matching CLIAMP's `provider.load` behavior. Selecting a station or provider search result starts it immediately.

## Local files and folders

The Files tab offers **Choose Files…** and **Add Folder…**. Folder imports:

- recurse through subfolders;
- sort paths in natural filename order (`2` before `10`);
- ignore hidden files, AppleDouble `._` metadata, and unsupported formats;
- play the first track and put every remaining track in CLIAMP's Queue.

Supported extensions are MP3, FLAC, Ogg Vorbis, Opus, WAV, M4A, AAC, and WMA.

## Installation

Requirements:

- Omarchy with shell plugin support
- CLIAMP 1.63 or newer available as `cliamp`
- Python 3
- Zenity

Install and enable the plugin:

```bash
omarchy plugin add https://github.com/majesticio/omarchy-cliamped-plugin.git --enable
```

If CLIAMP is already running, CLIAMPed attaches to it and does not stop it. Otherwise, it starts `cliamp --daemon --provider radio`; that process survives plugin and shell reloads, and the widget reattaches afterward.

Remove it with:

```bash
omarchy plugin remove io.github.majesticio.cliamped
```

## Controls

Bar:

- Left click: open or close CLIAMPed
- Middle click: play or pause
- Right click: next queued track or station

Focused panel:

- Space: play or pause
- Left / Right: seek ten seconds, or previous / next on a live stream
- Up / Down: volume by 2 dB
- N / P: next / previous
- S / R / M: shuffle / repeat / mono
- V: cycle CLIAMP's visualizer only when attached to a TUI; CLIAMP does not expose visualizer switching in daemon mode
- Escape: close the panel

## Development

Validate the package and run its helper tests:

```bash
omarchy plugin validate .
python3 -m unittest discover -s tests -v
```

The compatibility entry points `cliamp-ipc.py` and `cliamp-ipc.sh` are intentionally retained for panels cached from earlier development builds.

## Relationship to CLIAMP

CLIAMPed is an independent community companion for CLIAMP and is not an official CLIAMP project. CLIAMP remains the playback engine and source of truth for its queue, providers, and media state.

## License

MIT. See `NOTICE` for attribution to CLIAMP's MIT-licensed Quickshell band-stream pattern.
