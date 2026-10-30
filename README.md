# CLIAMPed

CLIAMPed is a theme-aware Omarchy bar widget and control center for [CLIAMP](https://github.com/bjarneo/cliamp). It brings playback, radio discovery, favorites, collections, your own music library, local media, and three reactive visualizers into the shell.

Its visualizer and controls take their colors from the current Omarchy theme rather than a bundled palette.

## Features

- Playback, seeking, volume, speed, EQ, shuffle, repeat, and mono controls
- Spectrum, Canyon, and Voxtype-inspired Pulse views driven by `cliamp visstream`
- A synchronized mini visualizer in the bar
- Eleven curated CLIAMP stations over HTTPS
- Live song titles and station context for any stream whose metadata CLIAMP exposes
- CLIAMP source and collection browsing, including Radio Browser favorites and its paged catalog
- Real queue inspection, play-next, removal, and clearing
- A Library tab that indexes your music folders, such as a NAS share, and lets you search, browse by artist and album, favorite tracks and build playlists, with any CLIAMP
- Multiple-file and recursive whole-folder playback
- Recently played tracks, lyrics, and audio-output switching
- Keyboard control while the panel is focused
- Automatic attachment to an existing CLIAMP TUI or daemon
- A self-started background daemon when CLIAMP is not already running

CLIAMPed uses CLIAMP's version 2 Unix-socket API with authenticated peers, correlated request and job IDs, bounded frames, and absolute deadlines. It serializes requests so concurrent panel actions cannot corrupt responses. File and folder selection runs behind a bounded Zenity supervisor, keeping native picker failures outside Quickshell. The complete runtime trust model and limits are documented in [`SECURITY.md`](SECURITY.md).

## Theme gallery

CLIAMPed reads its panel, text, accent, urgent, and bar colors from Omarchy, so the same controls adapt to both dark and light themes.

| Hacker Bunker | Catppuccin Latte | Santa Fe |
|:---:|:---:|:---:|
| Favorites · Spectrum | Browse · Canyon | Queue · Pulse |
| ![CLIAMPed Favorites with Spectrum in Hacker Bunker](screenshots/cliamped-hacker-bunker.png) | ![CLIAMPed Browse with Canyon in Catppuccin Latte](screenshots/cliamped-catppuccin-latte.png) | ![CLIAMPed Queue with Pulse in Santa Fe](screenshots/cliamped-santa-fe.png) |

## Favorites, sources, and collections

These views have distinct jobs:

- **Favorites** collects Radio Browser stations starred from Browse.
- **Browse** reflects CLIAMP's configured source hierarchy. Radio exposes the eleven CLIAMP Radio channels plus its paged Radio Browser directory; Local exposes saved TOML collections and Recently Played when available.
- **Queue** is CLIAMP's active playback list.

Selecting a collection replaces the active list and starts its first track. Selecting a station or source search result also starts it immediately.
CLIAMPed resolves CLIAMP Radio's built-in M3U index through CLIAMP's `url.load` IPC endpoint before playback.
Now Playing and the bar show live song metadata from any station, including directory entries and custom URLs. The panel keeps the artist and station beneath the song title. Streams without live metadata retain their supplied title or station name.
The Radio Browser directory loads automatically; use ☆ and ★ on directory or search-result cards to add or remove Favorites. Because CLIAMP's search IPC omits the playlist IDs required by its native favorite endpoint, searched-station favorites are stored separately in `~/.config/cliamp/cliamped_search_favorites.json` and merged into the same Favorites view.

## Library

The Library tab keeps its own index of the music folders you choose, so it works with the CLIAMP you already have. Scanning, browsing and searching need nothing from CLIAMP; playing, playlists and favorites use CLIAMP's public API. The index is a SQLite file, `~/.local/share/cliamped/library.sqlite3`. CLIAMPed reads the tags itself (ID3, FLAC, Ogg Vorbis and Opus, MP4/M4A) and falls back to the file name.

- **Folders** is where you start. **Add Music Folder…** opens the native chooser; pick the folder where your music lives, such as a mounted NAS share. A scan reads it in the background: the counts rise while it runs, and the lists fill in. **Rescan** looks for new, changed and removed files, and **Remove** takes a folder out of the Library without touching your files.
- **Artists** lists artists, then their albums, then the tracks of an album. **Albums** lists every album and sorts by name, artist, recently added or year.
- The search box finds tracks by title, artist or album. Words may come in any order and accents do not count. Selecting a result plays it. The artist and the album shown on a result are links: select one to open it, and use **Back** to return to the results.
- **Play Album** loads the album into the Queue and plays it, and selecting a track in an album starts there. The star at the end of a track row toggles CLIAMP's ♥ favorite.
- **Playlists** lists your saved playlists, including Favorites and Recently Played. The **＋** on a track, and **Save Album**, add to a playlist or create one. These are CLIAMP's own saved playlists, so CLIAMP shows them too.

A first scan of a large share can take several minutes, because every file is opened once; after that only new and changed files are read. The Library scans once each time the shell starts, when you first open the tab. A scan runs in short steps, so you can use the panel meanwhile, and one that the shell interrupts continues the next time you open the Library tab. If a share is not mounted, or a folder holds no audio while the index holds some, CLIAMPed keeps the saved index and marks the folder not readable. The scan skips dot files (such as the `._` files macOS leaves on a share), `@eaDir` and `#recycle`, and does not follow symlinks.

Lists load 60 items at a time with **Load More**, and the panel keeps at most 600 items of a list. An album shows at most 200 tracks and a search at most 50 results. The Library plays through CLIAMP's `url.load` and queue, so it needs CLIAMP 2.0 or newer, as the rest of CLIAMPed does. Albums are loaded from a short-lived playlist file in `$XDG_RUNTIME_DIR/cliamped`, so CLIAMP reads the tags of each file itself.

## Local files and folders

The Queue tab offers **Choose Files…** and **Add Folder…**, under the box that adds a path or URL. CLIAMPed closes its layer panel before opening the native picker so the picker stays visible, then returns to Queue. Folder imports:

- recurse through subfolders;
- sort paths in natural filename order (`2` before `10`);
- ignore hidden files, AppleDouble `._` metadata, and unsupported formats;
- stay on one filesystem, never follow symlinks, and enforce traversal/depth/time limits;
- admit at most 20 audio files per selection;
- play the first track and put every remaining track in CLIAMP's Queue.

Supported extensions are MP3, FLAC, Ogg Vorbis, Opus, WAV, M4A, AAC, and WMA.
When `/usr/bin/ffprobe` is available, file and folder imports read embedded title, artist, album, and genre tags before queueing. Missing tags, unavailable probes, and probe failures fall back to the filename. Metadata probing has a five-second budget per import. Tracks already loaded by CLIAMP use the metadata it supplies.

## Installation

Requirements:

- Omarchy with shell plugin support
- CLIAMP 2.0 or newer installed at `/usr/bin/cliamp`
- Python 3 installed at `/usr/bin/python3`
- Zenity installed at `/usr/bin/zenity`

Install and enable the plugin:

```bash
omarchy plugin add https://github.com/majesticio/omarchy-cliamped-plugin.git --enable
```

If CLIAMP is already running, CLIAMPed attaches to it and never stops it. Otherwise, it starts a supervised `/usr/bin/cliamp --daemon --provider radio` session owned by the plugin. Plugin-owned sessions are stopped during plugin or shell teardown so their audio/provider process tree cannot be orphaned.

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
- V: cycle Spectrum, Canyon, and the Voxtype-inspired Pulse history; clicking the visualizer cycles them too
- Escape: close the panel

Shell IPC:

```bash
/usr/bin/omarchy-shell io.github.majesticio.cliamped open
/usr/bin/omarchy-shell io.github.majesticio.cliamped tab browse
/usr/bin/omarchy-shell io.github.majesticio.cliamped tab library
/usr/bin/omarchy-shell io.github.majesticio.cliamped visualizer Pulse
/usr/bin/omarchy-shell io.github.majesticio.cliamped close
```

The `tab` endpoint accepts `favorites`, `browse`, `queue`, `library`, or `more`. The old name `files` opens `queue`, where the file pickers now are. The `visualizer` endpoint accepts `Spectrum`, `Canyon`, or `Pulse`.

## Optional Codex skill

This repository includes an automatically discoverable [`cliamp-control`](skills/cliamp-control/SKILL.md) skill for controlling CLIAMP playback and the CLIAMPed interface through Codex. After installing the plugin, link the bundled skill into your personal skill directory:

```bash
mkdir -p ~/.codex/skills
ln -s ~/.config/omarchy/plugins/io.github.majesticio.cliamped/skills/cliamp-control ~/.codex/skills/cliamp-control
```

Start a new Codex session after linking it. The skill uses CLIAMP's public commands for ordinary controls, its structured socket API for provider operations, and CLIAMPed's shell IPC for tabs and visualizers; no MCP server is required.

## Development

Validate the package and run its helper tests:

```bash
omarchy plugin validate .
/usr/bin/python3 -m unittest discover -s tests -v
```

The isolated Python compatibility entry point `cliamp-ipc.py` is retained for panels cached from earlier development builds.

## Relationship to CLIAMP

CLIAMPed is an independent community companion for CLIAMP and is not an official CLIAMP project. CLIAMP remains the playback engine and source of truth for its queue, providers, and media state.

## License

MIT. See `NOTICE` for attribution to CLIAMP's MIT-licensed Quickshell band-stream pattern.
