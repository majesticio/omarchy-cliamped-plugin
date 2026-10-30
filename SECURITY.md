# Runtime security boundaries

CLIAMPed runs inside the long-lived Omarchy shell, so data is admitted only
through bounded helper processes. The installed plugin directory and the
package-managed objects under `/usr/bin` are the trust anchors. Media tags,
provider responses, local selections, socket paths, and persisted favorites
are untrusted.

## IPC

- Each request and response is a required newline-terminated CLIAMP version 2
  JSON envelope. Response IDs must match their requests; submitted operations
  are polled to a terminal job whose ID must match the original submission.
- Requests are limited to 64 KiB, responses to 1 MiB on the wire, and normalized
  helper output to 256 KiB. Serialized batches are capped at 256 requests,
  256 KiB outbound, and 4 MiB inbound.
- Connect, write, and all reads share one monotonic deadline: 12 seconds for
  ordinary operations and 70 seconds for provider/network operations.
- The CLIAMP directory must be private and owned by the effective user; the
  socket and PID file must be owner-only objects. After connecting, the helper
  checks `SO_PEERCRED`, the PID file, the socket inode, and the peer executable
  inode against `/usr/bin/cliamp`.
- The helper's command objects and CLIAMP's version 2 envelopes use strict,
  command-specific schemas. Unknown commands,
  malformed types, duplicate JSON keys, non-finite numbers, unknown item
  fields, oversized fields, and excess aggregate model data are rejected.

Python admits at most 32 providers, 256 playlists/tracks, 64 history/device
items, and 512 lyric lines. QML applies a second, smaller retention boundary of
128 playlists/tracks/favorites, 24 history items, 32 devices, and 256 lyric
lines, with a 128 Ki-character aggregate budget. The resident IPC work queue is
limited to 24 entries and coalesces polls, searches, and volume snapshots.

## Rendering and processes

All QML text primitives inherit `Text.PlainText` from `SafeText.qml`. Metadata
is NFC-normalized, stripped of control and bidirectional-format characters,
and field-limited before it reaches QML. Host-provided labels receive an
additional markup-neutralization pass.

QML starts only absolute `/usr/bin/python3` commands in isolated mode with an
allowlisted environment. Every helper binds its lifetime to its initial QML
parent with a parent-race check. The process supervisor and picker
descriptor-bind the validated `/usr/bin/cliamp` and `/usr/bin/zenity` objects.
Those package objects and their directory must be protected from group/world
writes and must not be owned by the shell's effective user.
CLIAMP and Zenity descendants run in dedicated process groups; guardian
processes tear down those groups on normal completion, deadline expiry,
component destruction, or abrupt supervisor death, escalating from TERM to KILL
after a short grace period.

Visualizer frames are limited to 2 KiB, exactly ten finite bands in `[0, 1]`,
60 frames per second, and a five-second heartbeat. Restart attempts use bounded
backoff and stop after six consecutive unstable runs.

## Music library

The library helper (`cliamped_library.py`) is a one-shot process like the others:
isolated mode, an allowlisted environment, cleared of the parent on exit, and
limited by a QML watchdog (20 seconds for a query, 40 seconds for a scan step).
It takes one JSON request of at most 16 KiB. Every operation has an exact schema,
unknown fields and operations are refused, and a reply is trimmed to the 256 KiB
output cap rather than failing.

A scan is a series of steps that resume where the last stopped, so no process runs
for long: the panel asks for 8 seconds a step, the helper accepts at most 20, and
a step is killed after 40. It reads folders and files, never follows a
symlink, skips names with control, invisible-formatting or surrogate characters
(the characters that the IPC allowlist refuses in a path), dot files, `@eaDir` and `#recycle`,
descends at most 40 levels, and stops reading folders once a scan has queued more than one million files to read. A folder it
cannot read keeps what the index knew below it, and a folder that is missing or
empty while the index holds files is kept as saved, so an unmounted share cannot
wipe the library. A file is opened for its tags long after the walk checked it, so
the reader opens it with `O_NOFOLLOW | O_NONBLOCK` and refuses anything that is
not a regular file: a symlink swapped in by a hostile share, a FIFO or a device
is not read. A file that holds a reader past the wait is tried three times and
then indexed by its name, so one dead file handle cannot keep a scan from ending.
The tag readers (`cliamped_tags.py`) use the standard library only, read at most
2 MiB of a file (8 MiB for an MP4 `moov` atom or an Ogg comment header with cover
art) and skip large frames and pictures with a seek, and a damaged file yields fewer fields, never an
exception; values are cleaned like every other metadata field.

The index is a SQLite file in a private directory of the user (`0700`, checked
for ownership and for write access by others) and is rebuilt when its schema
changes; the folders are the only data kept across a schema change. The panel
sends a folder only from the sandboxed chooser (`--music-folder`: one folder,
validated, never walked). Albums are played from an M3U8 file that the helper
writes with `O_EXCL | O_NOFOLLOW` and mode `0600` into a private directory under
`$XDG_RUNTIME_DIR`; files older than two hours are removed. Removing a folder
only edits the index.

## Files and favorites

Folder imports do not follow symlinks or cross filesystems. An invocation may
select at most 64 roots and admit at most 20 audio files after inspecting no
more than 8,192 entries, descending 16 levels, and spending eight seconds in
traversal. Individual and aggregate path sizes are also bounded. Zenity has a
five-minute absolute deadline and bounded stdout/stderr; the complete import is
sent over one authenticated IPC connection with one 30-second aggregate
deadline. CLIAMP has no transactional batch endpoint, so imports use bounded
sequential frames on that connection and stop on the first rejection; a daemon
failure can leave a strictly bounded partial import.

Optional local metadata probes execute the protected `/usr/bin/ffprobe` package
object against a descriptor-pinned regular file opened without symlink traversal.
They admit only local-file protocol access and supported audio containers, inherit
the picker’s bounded output collection and process-group guardian, and have a
one-second per-file deadline within a five-second import budget (plus bounded
teardown). Missing or unreadable tags fall back to filenames.

Search favorites are limited to a 128 KiB file, 128 strict HTTP(S) records, and
64 KiB of normalized model data. Reads are nonblocking, bounded, regular-file,
owner, link-count, and no-follow checked relative to a private directory
descriptor. Writes use a random exclusive no-follow sibling, mode `0600`, file
and directory `fsync`, and descriptor-relative atomic replacement under a
bounded lock.

## Lifecycle policy

CLIAMPed never terminates a CLIAMP session that was already running. If the
plugin starts a background session, that session is plugin-owned and is
gracefully stopped when the component is unloaded, disabled, or reloaded; an
independent guardian handles forced shell termination. Automatic startup uses
backoff and stops after three short-lived failures; a deliberate user action can
start a fresh bounded attempt sequence.

The regression suite includes slow-drip and oversized frames, wrong socket and
peer identities, malformed/oversized models, symlink/FIFO/hard-link storage,
preplanted temporary names, traversal and path limits, subprocess output and
deadlines, process-group cleanup, isolated execution, and QML security-contract
checks.
