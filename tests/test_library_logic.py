"""Runs the Library state machine of BarWidget.qml under Node.

The QML cannot run without Quickshell, so this takes the real functions and the
property defaults out of BarWidget.qml and evaluates them with the QML-only
parts (timers, processes) replaced by recorders. Properties become fields of one
object that the functions reach as free variables, the way QML scopes them.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "BarWidget.qml").read_text(encoding="utf-8")


def matching_brace(text, start):
    """The index of the brace that closes the one at ``start``, skipping strings, comments and regexes."""

    depth = 0
    index = start
    while index < len(text):
        character = text[index]
        if character in "\"'`":
            quote = character
            index += 1
            while text[index] != quote:
                index += 2 if text[index] == "\\" else 1
        elif text.startswith("//", index):
            index = text.index("\n", index)
            continue
        elif text.startswith("/*", index):
            index = text.index("*/", index) + 1
        elif character == "/" and re.search(r"[(,=:!&|?{};]\s*$", text[max(0, index - 40):index]):
            index += 1  # a regex literal: skip to its closing slash
            while text[index] != "/":
                index += 2 if text[index] == "\\" else 1
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError("unbalanced braces")


def functions(source):
    found = []
    for match in re.finditer(r"^  function (\w+)\(", source, re.M):
        brace = source.index("{", source.index(")", match.end()))
        found.append(source[match.start():matching_brace(source, brace) + 1])
    return found


def properties(source):
    """(name, readonly, expression) of the property declarations of the root item."""

    result = []
    pattern = re.compile(r"^  (readonly )?property (?:\w+) (\w+):", re.M)
    for match in pattern.finditer(source):
        rest = source[match.end():]
        # A binding ends at the next declaration or comment; a closing ] or } at the
        # same indentation still belongs to it.
        end = re.search(r"\n  (?=\w|//)", rest)
        expression = rest[:end.start()] if end else rest
        result.append((match.group(2), bool(match.group(1)), expression.strip()))
    return result


def exit_handler(process_id):
    """The body of the ``onExited`` handler of the Process with this id."""

    start = SOURCE.index(f"id: {process_id}\n")
    marker = "onExited: function(exitCode) {"
    handler = SOURCE.index(marker, start)
    brace = handler + len(marker) - 1
    return SOURCE[brace + 1:matching_brace(SOURCE, brace)]


def prelude():
    lines = [
        "const assert = require('node:assert/strict');",
        "const log = [];",
        "const timer = (name) => ({ name, running: false, interval: 0, restart() { this.running = true; log.push(name + '.restart'); }, stop() { this.running = false; log.push(name + '.stop'); } });",
        "const proc = (name) => ({ name, running: false, command: [], launchPending: false, signal(n) { log.push(name + '.signal' + n); } });",
        "const libraryScanTimer = timer('libraryScanTimer'), libraryWatchdog = timer('libraryWatchdog'), libraryKill = timer('libraryKill'),",
        "  libraryScanWatchdog = timer('libraryScanWatchdog'), libraryScanKill = timer('libraryScanKill'), providerWatchdog = timer('providerWatchdog'),",
        "  actionRefresh = timer('actionRefresh'), queueRefresh = timer('queueRefresh'), providerKill = timer('providerKill');",
        "const libraryProcess = proc('libraryProcess'), libraryScanProcess = proc('libraryScanProcess'), providerProcess = proc('providerProcess');",
        "const Qt = { callLater(fn) { fn(); }, resolvedUrl(p) { return 'file:///bundle/' + p; } };",
        "const Quickshell = { env() { return ''; } };",
        "const panelLoader = { item: { opened: true, libraryTab: 'library' } };",
        "const root = {};",
    ]
    for name, readonly, expression in properties(SOURCE):
        if not expression:
            continue
        if readonly or expression.startswith("{") and "return" in expression:
            body = expression[1:-1] if expression.startswith("{") and expression.endswith("}") else "return (" + expression + ")"
            lines.append(
                f"Object.defineProperty(root, {name!r}, {{ enumerable: true, get() {{ with (root) {{ {body} }} }} }});"
            )
        else:
            lines.append(f"try {{ root.{name} = ({expression}); }} catch (e) {{ root.{name} = undefined; }}")
    lines.append("const providerOut = { text: '' }, libraryOut = { text: '' }, libraryScanOut = { text: '' };")
    lines.append("with (root) {")
    lines.extend(functions(SOURCE))
    # The handlers of the three processes, run as the shell runs them when a process exits.
    lines.append("function providerExited(exitCode) {" + exit_handler("providerProcess") + "}")
    lines.append("function libraryExited(exitCode) {" + exit_handler("libraryProcess") + "}")
    lines.append("function libraryScanExited(exitCode) {" + exit_handler("libraryScanProcess") + "}")
    lines.append("}")
    names = [re.match(r"  function (\w+)", f).group(1) for f in functions(SOURCE)]
    lines.append("with (root) { " + " ".join(f"root.{name} = {name};" for name in names) + " }")
    lines.append("root.sessionReady = true; root.destroying = false;")
    return "\n".join(lines)


HELPERS = """
// The stubs mark a process as running when it is started; a scenario that wants the
// next request to start calls settle() first, as the exit of a process would.
function settle() {
  libraryProcess.running = false; libraryScanProcess.running = false; providerProcess.running = false;
  libraryHelperQueue = []; libraryHelperCurrent = null; libraryHelperBusy = false;
  ipcQueue = []; ipcCurrent = null; ipcBusy = false;
}
function helperRequests() { return [libraryHelperCurrent, ...libraryHelperQueue].filter(Boolean).map(i => i.request); }
function ipcRequests() { return [ipcCurrent, ...ipcQueue].filter(Boolean).map(i => i.request); }
"""


def run(script):
    completed = subprocess.run(
        ["node", "-e", prelude() + "\nwith (root) {\n" + HELPERS + script + "\n}"], capture_output=True, text=True, timeout=60
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr[-3000:])
    return completed.stdout


@unittest.skipUnless(shutil.which("node"), "Node is needed to exercise QML JavaScript")
class LibraryLogicTests(unittest.TestCase):
    def test_the_extractor_finds_the_library_code(self):
        names = {re.match(r"  function (\w+)", f).group(1) for f in functions(SOURCE)}
        for name in ("applyLibraryResult", "applyLibraryScan", "scanLibraryStep", "enqueueLibraryHelper", "finishLibraryWrite"):
            self.assertIn(name, names)
        self.assertIn("libraryView", {p[0] for p in properties(SOURCE)})

    def test_folders_first_run_scan_and_lists(self):
        run("""
// A library with no folder opens on Folders, once.
assert.equal(libraryView, 'artists');
applyLibraryResult('libRoots', { op: 'roots' }, { ok: true, roots: [], scanning: false });
assert.equal(libraryView, 'folders'); assert.equal(libraryTop, 'folders');
assert.equal(libraryRootsLoaded, true); assert.equal(libraryScanning, false);
// The user moves on; a later look does not bring them back.
libraryTop = 'artists'; libraryView = 'artists';
applyLibraryResult('libRoots', { op: 'roots' }, { ok: true, roots: [], scanning: false });
assert.equal(libraryView, 'artists');

// Adding a folder asks the helper, and the helper's reply starts the scan.
settle();
addLibraryRoot('/mnt/nas/music');
assert.equal(libraryHelperCurrent.request.op, 'add_root');
assert.deepEqual(JSON.parse(libraryProcess.command[3]), { op: 'add_root', path: '/mnt/nas/music' });
assert.deepEqual(libraryProcess.command.slice(0, 3), ['/usr/bin/python3', '-I', '/bundle/cliamped_library.py']);
assert.equal(libraryScanning, false, 'nothing scans until the helper accepted the folder');
const roots = [{ path: '/mnt/nas/music', tracks: 0, available: true }];
applyLibraryResult('libRoots', { op: 'add_root', path: '/mnt/nas/music' }, { ok: true, roots, scanning: true });
assert.equal(libraryScanning, true); assert.equal(libraryScanFresh, false);
assert.equal(libraryScanTimer.running, true);

// A step asks the helper to scan within a budget, and nothing else.
libraryScanTimer.running = false;
scanLibraryStep();
assert.deepEqual(libraryScanProcess.command.slice(0, 3), ['/usr/bin/python3', '-I', '/bundle/cliamped_library.py']);
assert.deepEqual(JSON.parse(libraryScanProcess.command[3]), { op: 'scan', budget: 8 });
assert.equal(libraryScanProcess.launchPending, true); assert.equal(libraryScanProcess.running, true);
assert.equal(libraryScanWatchdog.running, true);
libraryScanProcess.running = false;
// While scanning, the lists catch up and the next step is scheduled.
libraryView = 'artists';
libraryHelperQueue = []; libraryHelperBusy = false;
settle();
applyLibraryScan({ ok: true, scanning: true, phase: 'tags', dirs: 0, pending: 40, tracks: 24, roots });
assert.equal(libraryScanning, true); assert.equal(libraryScanTimer.interval, 100);
assert.equal(libraryScanSummary, 'READING TAGS… 40 FILES LEFT, 24 TRACKS INDEXED');
assert.deepEqual(helperRequests().map(r => r.op), ['artists'], 'new tracks reload the visible list');
// The same count again does not reload it.
settle();
applyLibraryScan({ ok: true, scanning: true, phase: 'tags', dirs: 0, pending: 30, tracks: 24, roots });
assert.deepEqual(helperRequests(), []);
// A busy reply (another step is running) waits longer.
applyLibraryScan({ ok: true, scanning: true, busy: true, phase: 'tags', dirs: 0, pending: 30, tracks: 24, roots });
assert.equal(libraryScanTimer.interval, 2000);
// The end of the scan reads the lists once more and stops the steps.
libraryScanTimer.running = false;
applyLibraryScan({ ok: true, scanning: false, phase: 'idle', dirs: 0, pending: 0, tracks: 40, roots: [{ path: '/mnt/nas/music', tracks: 40, available: true }] });
assert.equal(libraryScanning, false); assert.equal(libraryScanTimer.running, false);
assert.equal(libraryRoots[0].tracks, 40);
assert.deepEqual(helperRequests().map(r => r.op), ['artists'], 'the end of a scan reads the list once more');
""")

    def test_a_scan_is_bounded_and_failures_stop_it(self):
        run("""
startLibraryScan(true);
assert.equal(libraryScanFresh, true); assert.equal(libraryScanStepsLeft, 4000);
scanLibraryStep();
assert.equal(JSON.parse(libraryScanProcess.command[3]).fresh, true, 'the first step begins a new scan');
assert.equal(libraryScanFresh, false);
libraryScanProcess.running = false;
scanLibraryStep();
assert.equal(JSON.parse(libraryScanProcess.command[3]).fresh, undefined, 'later steps continue it');
// Steps are counted: when they run out the scan stops.
libraryScanProcess.running = false; libraryScanStepsLeft = 0; libraryScanning = true;
const before = libraryScanProcess.command;
scanLibraryStep();
assert.equal(libraryScanning, false); assert.equal(libraryScanProcess.command, before);
// A step cannot start while another runs.
libraryScanStepsLeft = 5; libraryScanProcess.running = true;
scanLibraryStep(); assert.equal(libraryScanStepsLeft, 5);
// A step that fails to start ends the scan with a message.
libraryScanning = true; handleLibraryScanStartFailure();
assert.equal(libraryScanning, false); assert.match(libraryError, /could not start/i);
// Nothing starts after teardown.
libraryScanProcess.running = false; destroying = true; libraryScanStepsLeft = 5;
scanLibraryStep(); assert.equal(libraryScanStepsLeft, 5);
""")

    def test_helper_requests_are_serialized_bounded_and_deduplicated(self):
        run("""
// One at a time, in order.
settle();
assert.equal(enqueueLibraryHelper('libArtists', { op: 'artists', offset: 0, limit: 60 }), true);
assert.equal(libraryHelperBusy, true);
libraryProcess.running = true;
enqueueLibraryHelper('libAlbums', { op: 'albums', offset: 0, limit: 60 });
assert.equal(libraryHelperQueue.length, 1);
libraryProcess.running = false;
finishLibraryHelper();
assert.equal(libraryHelperCurrent.request.op, 'albums');
// Only the newest search waits.
libraryProcess.running = true;
enqueueLibraryHelper('libSearch', { op: 'search', query: 'a', limit: 50 });
enqueueLibraryHelper('libSearch', { op: 'search', query: 'ab', limit: 50 });
assert.deepEqual(libraryHelperQueue.map(i => i.request.query), ['ab']);
// The queue is bounded.
libraryHelperQueue = [];
for (let i = 0; i < 40; ++i) enqueueLibraryHelper('libAlbums', { op: 'albums', offset: i, limit: 60 });
assert.equal(libraryHelperQueue.length, 12);
assert.match(libraryError, /busy/);
// An oversized request is refused, not truncated.
libraryHelperQueue = [];
assert.equal(enqueueLibraryHelper('libSearch', { op: 'search', query: 'x'.repeat(20000), limit: 5 }), false);
// Nothing is queued after teardown, and a start failure frees the queue.
destroying = true; assert.equal(enqueueLibraryHelper('libRoots', { op: 'roots' }), false); destroying = false;
libraryHelperBusy = true; libraryProcess.running = false;
handleLibraryHelperStartFailure();
assert.equal(libraryHelperBusy, false); assert.match(libraryError, /could not start/i);
""")

    def test_pages_append_in_order_and_stale_pages_are_dropped(self):
        run("""
libraryView = 'artists';
const artists = n => Array.from({ length: n }, (_, i) => ({ id: 'a' + i, name: 'Artist ' + i, album_count: 1 }));
applyLibraryResult('libArtists', { op: 'artists', offset: 0, limit: 60 }, { ok: true, artists: artists(60), total: 150 });
assert.equal(libraryArtists.length, 60); assert.equal(libraryArtistsMore, true); assert.equal(libraryLoaded, true);
// The next page appends only when it follows the list.
applyLibraryResult('libArtists', { op: 'artists', offset: 60, limit: 60 }, { ok: true, artists: artists(60), total: 150 });
assert.equal(libraryArtists.length, 120);
applyLibraryResult('libArtists', { op: 'artists', offset: 30, limit: 60 }, { ok: true, artists: artists(60), total: 150 });
assert.equal(libraryArtists.length, 120, 'a page that does not follow the list is dropped');
applyLibraryResult('libArtists', { op: 'artists', offset: 120, limit: 60 }, { ok: true, artists: artists(30), total: 150 });
assert.equal(libraryArtists.length, 150); assert.equal(libraryArtistsMore, false);
// A reply for a view that was left is dropped.
libraryView = 'albums'; libraryAlbums = [];
applyLibraryResult('libArtists', { op: 'artists', offset: 0, limit: 60 }, { ok: true, artists: artists(5), total: 5 });
assert.equal(libraryArtists.length, 150);
// The list is capped.
libraryView = 'artists'; libraryArtists = [];
applyLibraryResult('libArtists', { op: 'artists', offset: 0, limit: 60 }, { ok: true, artists: artists(200), total: 5000 });
applyLibraryResult('libArtists', { op: 'artists', offset: 200, limit: 60 }, { ok: true, artists: artists(200), total: 5000 });
applyLibraryResult('libArtists', { op: 'artists', offset: 400, limit: 60 }, { ok: true, artists: artists(200), total: 5000 });
applyLibraryResult('libArtists', { op: 'artists', offset: 600, limit: 60 }, { ok: true, artists: artists(200), total: 5000 });
assert.equal(libraryArtists.length, maxLibraryItems); assert.equal(libraryArtistsMore, false);
// Hostile items are skipped.
libraryArtists = []; libraryView = 'artists';
applyLibraryResult('libArtists', { op: 'artists', offset: 0, limit: 60 }, { ok: true, total: 3,
  artists: [null, 5, 'x', { id: '', name: 'No id' }, { id: 'ok', name: 'Fine', album_count: 2 }, { id: 'a\\u0000b', name: 'Control' }] });
assert.deepEqual(libraryArtists.map(a => a.id), ['ok']);
""")

    def test_album_search_and_back_navigation(self):
        run("""
// A search, then an album opened from a result: Back searches again.
searchLibrary('  money  ');
assert.deepEqual(libraryHelperCurrent.request, { op: 'search', query: 'money', limit: 50 });
applyLibraryResult('libSearch', libraryHelperCurrent.request, { ok: true, tracks: [
  { path: '/m/a.mp3', title: 'Money', artist: 'Pink Floyd', album: 'Dark Side', provider_meta: { 'library.album': 'alb1', 'library.artist': 'art1' } }] });
assert.equal(libraryTracks.length, 1); assert.equal(libraryLoaded, true);
assert.equal(trackLink(libraryTracks[0], 'library.album'), 'alb1');
settle();
openTrackAlbum(libraryTracks[0]);
assert.equal(libraryView, 'tracks'); assert.equal(libraryReturnView, 'search');
assert.equal(libraryHelperCurrent.request.op, 'album_tracks'); assert.equal(libraryHelperCurrent.request.album, 'alb1');
applyLibraryResult('libTracks', libraryHelperCurrent.request, { ok: true, tracks: [{ path: '/m/a.mp3', title: 'Speak to Me' }, { path: '/m/b.mp3', title: 'Money' }] });
assert.equal(libraryTracks.length, 2);
settle();
libraryBack();
assert.equal(libraryView, 'search'); assert.deepEqual(helperRequests().map(r => r.op), ['search'], 'Back reads the results again');
// The artist of a result, then an album of that artist, then Back twice.
applyLibraryResult('libSearch', libraryHelperCurrent.request, { ok: true, tracks: [{ path: '/m/a.mp3', title: 'Money', artist: 'Pink Floyd', provider_meta: { 'library.artist': 'art1' } }] });
settle();
openTrackArtist(libraryTracks[0]);
assert.equal(libraryView, 'artistAlbums'); assert.equal(libraryArtistReturn, 'search'); assert.equal(libraryArtistName, 'Pink Floyd');
applyLibraryResult('libAlbums', libraryHelperCurrent.request, { ok: true, total: 1, albums: [{ id: 'alb1', name: 'Dark Side', artist: 'Pink Floyd', track_count: 2 }] });
assert.equal(libraryAlbums.length, 1);
settle();
openLibraryAlbum('alb1', 'Dark Side'); assert.equal(libraryReturnView, 'artistAlbums');
libraryBack(); assert.equal(libraryView, 'artistAlbums'); assert.equal(libraryAlbums.length, 1, 'the albums are still there');
settle();
libraryBack(); assert.equal(libraryView, 'search');
// A reply for a search that was replaced is dropped.
settle();
searchLibrary('abc');
applyLibraryResult('libSearch', { op: 'search', query: 'old', limit: 50 }, { ok: true, tracks: [{ path: '/old.mp3' }] });
assert.notEqual(libraryTracks[0] && libraryTracks[0].path, '/old.mp3');
// A track without links does nothing.
libraryView = 'search'; openTrackAlbum({ path: '/x.mp3', provider_meta: {} }); assert.equal(libraryView, 'search');
// The sort cycles and reloads.
libraryView = 'albums'; librarySorts = [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }, { id: 'c', label: 'C' }];
librarySort = ''; cycleLibrarySort(); assert.equal(librarySort, 'b', 'the default is the first, so the next is the second');
cycleLibrarySort(); assert.equal(librarySort, 'c'); cycleLibrarySort(); assert.equal(librarySort, 'a');
""")

    def test_playing_an_album_goes_through_a_queue_file(self):
        run("""
settle();
libraryAlbumId = 'alb1';
playLibraryAlbum(3);
assert.deepEqual(libraryHelperCurrent.request, { op: 'queue_file', album: 'alb1' });
assert.equal(libraryPendingIndex, 3);
// The helper's file is loaded by CLIAMP in one request, after the queue is cleared.
applyLibraryResult('libQueueFile', libraryHelperCurrent.request, { ok: true, path: '/run/user/1000/cliamped/queue-ab.m3u8', count: 10 });
const sent = ipcRequests();
assert.deepEqual(sent.map(r => r.cmd), ['queue.clear', 'url.load']);
assert.deepEqual(sent[1], { cmd: 'url.load', path: '/run/user/1000/cliamped/queue-ab.m3u8', play: true });
// When it is loaded, playback moves to the chosen track.
settle();
libraryPendingIndex = 3;
// Emulates the libLoad branch of the response handler.
{
  const startAt = libraryPendingIndex; libraryPendingIndex = 0;
  if (startAt > 0) enqueueIpc('play', { cmd: 'queue.play', index: startAt }, []);
}
assert.deepEqual(ipcCurrent.request, { cmd: 'queue.play', index: 3 });
// Without a session the failure is shown, not swallowed.
settle(); sessionReady = false; libraryError = '';
applyLibraryResult('libQueueFile', { op: 'queue_file', album: 'alb1' }, { ok: true, path: '/q.m3u8', count: 1 });
assert.match(libraryError, /not ready/);
""")

    def test_playlist_writes_are_counted_and_only_a_clean_run_succeeds(self):
        run("""
const tracks = Array.from({ length: 50 }, (_, i) => ({ path: '/m/' + i + '.mp3', title: 'T' + i }));
openLibraryPicker(tracks, 'An album');
assert.equal(libraryPickerOpen, true); assert.equal(libraryPickerTracks.length, 50);
settle();
createLibraryPlaylist('  Road trip  ');
// The playlist is created first; nothing is added until that succeeded.
assert.deepEqual(ipcRequests().map(r => r.cmd), ['playlist.create']);
assert.equal(ipcRequests()[0].playlist, 'Road trip'); assert.equal(ipcRequests()[0].provider, 'local');
assert.equal(libraryWritesPending, 1);
settle(); // the create has been sent and answered
finishLibraryWrite(true);
const cmds = ipcRequests();
assert.deepEqual(cmds.map(r => r.cmd), ['playlist.add_many', 'playlist.add_many', 'playlist.add_many']);
assert.deepEqual(cmds.map(r => r.tracks.length), [24, 24, 2]);
assert.equal(libraryWritesPending, 3); assert.equal(libraryPickerOpen, true, 'the picker waits for the adds');
// All three succeed: the notice shows and the picker closes.
for (let i = 0; i < 3; ++i) finishLibraryWrite(true);
assert.equal(libraryPickerOpen, false); assert.match(libraryNotice, /Added 50 tracks to “Road trip”/);
// A create that fails (the playlist exists) adds nothing and keeps the picker open.
settle(); libraryNotice = ''; libraryError = '';
openLibraryPicker(tracks, 'An album'); settle();
createLibraryPlaylist('Road trip');
settle(); // the create was sent and failed
finishLibraryWrite(false);
assert.deepEqual(ipcRequests().filter(r => r.cmd.startsWith('playlist.')), [], 'no add runs after a failed create');
assert.equal(libraryPickerOpen, true); assert.equal(libraryNotice, ''); assert.equal(libraryWritesPending, 0);
assert.deepEqual(libraryWriteQueued, []);
// One failure: no success notice, the picker stays open for another try.
libraryNotice = ''; settle(); closeLibraryPicker();
openLibraryPicker(tracks.slice(0, 1), 'One');
settle();
addToLibraryPlaylist('Road trip', 'Road trip');
assert.equal(libraryWritesPending, 1);
finishLibraryWrite(false);
assert.equal(libraryPickerOpen, true); assert.equal(libraryNotice, ''); assert.equal(libraryWritesPending, 0);
// A second batch cannot start while one is pending.
settle(); libraryError = '';
addToLibraryPlaylist('Road trip', 'Road trip'); assert.equal(libraryWritesPending, 1);
addToLibraryPlaylist('Road trip', 'Road trip'); assert.match(libraryError, /busy/); assert.equal(libraryWritesPending, 1);
finishLibraryWrite(true);
// An empty name is refused; Favorites cannot be a target.
libraryError = ''; createLibraryPlaylist('   '); assert.match(libraryError, /name/);
libraryPlaylists = [{ id: 'Favorites', name: 'Favorites' }, { id: 'Recently Played', name: 'Recently Played' }, { id: 'Mine', name: 'Mine' }];
assert.deepEqual(libraryTargetPlaylists.map(p => p.id), ['Mine']);
""")

    def test_favorites_set_drives_the_stars(self):
        run("""
settle();
refreshLibraryFavorites();
assert.deepEqual(ipcCurrent.request, { cmd: 'provider.tracks', provider: 'local', playlist: 'Favorites', offset: 0, limit: 256 });
assert.equal(isLibraryFavorite({ path: '/m/a.mp3' }), false);
libraryFavorites = { '/m/a.mp3': true };
assert.equal(isLibraryFavorite({ path: '/m/a.mp3' }), true);
assert.equal(isLibraryFavorite(null), false);
// Toggling asks CLIAMP with a validated track.
settle();
toggleTrackFavorite({ path: '/m/b.mp3', title: 'B', provider_meta: { 'library.id': 'x' } });
assert.equal(ipcCurrent.request.cmd, 'playlist.bookmark'); assert.equal(ipcCurrent.request.provider, 'local');
assert.equal(ipcCurrent.request.track.path, '/m/b.mp3');
toggleTrackFavorite({ title: 'no path' });
""")

    def test_a_scan_asked_for_during_a_step_is_not_lost(self):
        run("""
settle();
const roots = [{ path: '/m', tracks: 5, available: true }];
// A step is under way when the user presses Rescan.
startLibraryScan(true);
libraryScanTimer.running = false; scanLibraryStep();
assert.equal(libraryScanProcess.running, true); assert.equal(libraryScanRequested, false);
startLibraryScan(true);
assert.equal(libraryScanRequested, true); assert.equal(libraryScanFresh, true);
assert.equal(libraryScanTimer.running, false, 'the timer waits for the running step');
// The step that was running ends the old scan: the new request must survive.
libraryScanProcess.running = false;
applyLibraryScan({ ok: true, scanning: false, phase: 'idle', dirs: 0, pending: 0, tracks: 5, roots });
assert.equal(libraryScanning, true); assert.equal(libraryScanTimer.running, true, 'the next step is armed');
scanLibraryStep();
assert.equal(JSON.parse(libraryScanProcess.command[3]).fresh, true, 'the requested scan begins');
// The same for a folder added while a step runs.
settle(); libraryScanProcess.running = false; libraryScanTimer.running = false; libraryScanning = false; libraryScanFresh = false;
startLibraryScan(false); scanLibraryStep();
applyLibraryResult('libRoots', { op: 'add_root' }, { ok: true, roots, scanning: true });
libraryScanProcess.running = false;
applyLibraryScan({ ok: true, scanning: false, phase: 'idle', dirs: 0, pending: 0, tracks: 5, roots });
assert.equal(libraryScanning, true); assert.equal(libraryScanTimer.running, true);
""")

    def test_progress_does_not_throw_away_the_pages_the_user_opened(self):
        run("""
settle();
const roots = [{ path: '/m', tracks: 5, available: true }];
libraryView = 'artists'; libraryScanning = true;
libraryArtists = Array.from({ length: 120 }, (_, i) => ({ id: 'a' + i, name: 'A' + i, album_count: 1 }));
applyLibraryScan({ ok: true, scanning: true, phase: 'tags', dirs: 0, pending: 9, tracks: 70, roots });
assert.deepEqual(helperRequests(), [], 'a list the user paged through stays while the scan runs');
assert.equal(libraryArtistsStale, true);
assert.equal(libraryArtists.length, 120);
// A list that was not paged follows the scan.
libraryArtists = libraryArtists.slice(0, 30);
applyLibraryScan({ ok: true, scanning: true, phase: 'tags', dirs: 0, pending: 5, tracks: 80, roots });
assert.deepEqual(helperRequests().map(r => r.op), ['artists']);
// The end of the scan reads it again, whatever its length.
settle(); libraryArtists = Array.from({ length: 120 }, (_, i) => ({ id: 'a' + i, name: 'A' + i, album_count: 1 }));
applyLibraryScan({ ok: true, scanning: false, phase: 'idle', dirs: 0, pending: 0, tracks: 90, roots });
assert.deepEqual(helperRequests().map(r => r.op), ['artists']);
// Back to the artists after a scan shows the new list, not the old one.
settle(); libraryArtistsStale = true; libraryView = 'artistAlbums'; libraryArtistReturn = 'artists';
libraryBack();
assert.equal(libraryView, 'artists'); assert.deepEqual(helperRequests().map(r => r.op), ['artists']);
""")

    def test_what_needs_cliamp_says_so_and_folders_do_not(self):
        run("""
settle(); sessionReady = false; libraryError = '';
openLibraryPlaylist('Mine', 'Mine'); assert.match(libraryError, /CLIAMP is not running/);
libraryError = ''; toggleTrackFavorite({ path: '/m/a.mp3' }); assert.match(libraryError, /CLIAMP is not running/);
libraryError = ''; libraryAlbumId = 'alb'; playLibraryAlbum(0); assert.match(libraryError, /CLIAMP is not running/);
assert.deepEqual(helperRequests(), [], 'nothing is asked of the helper for an album that cannot play');
libraryPlaylistId = 'p'; libraryError = ''; playLibraryPlaylist(0); assert.match(libraryError, /CLIAMP is not running/);
libraryPickerTracks = [{ path: '/m/a.mp3' }]; libraryError = ''; addToLibraryPlaylist('Mine', 'Mine');
assert.match(libraryError, /CLIAMP is not running/); assert.equal(libraryWritesPending, 0);
libraryError = ''; showLibraryTop('playlists'); assert.match(libraryError, /CLIAMP is not running/);
// Folders, browsing and search need no CLIAMP.
libraryError = ''; settle();
addLibraryRoot('/mnt/music'); assert.equal(libraryError, ''); assert.equal(helperRequests()[0].op, 'add_root');
settle(); searchLibrary('abc'); assert.equal(libraryError, '');
settle(); startLibraryScan(true); scanLibraryStep(); assert.equal(libraryError, '');
""")

    def test_a_failed_play_leaves_no_start_index_and_does_not_empty_the_queue(self):
        run("""
settle(); sessionReady = true;
// The queue has no room for the two requests: it is left as it was.
ipcQueue = Array.from({ length: maxIpcQueueItems - 1 }, () => ({ kind: 'status', request: { cmd: 'status' } }));
libraryPendingIndex = 4; libraryError = '';
applyLibraryResult('libQueueFile', { op: 'queue_file', album: 'a' }, { ok: true, path: '/q.m3u8', count: 3 });
assert.equal(libraryPendingIndex, 0); assert.match(libraryError, /not ready/);
assert.equal(ipcQueue.filter(i => i.request.cmd === 'queue.clear').length, 0, 'the queue is not cleared');
""")

    def test_the_real_exit_handlers_apply_replies_and_survive_failures(self):
        run("""
const track = i => ({ path: '/m/' + i + '.mp3', title: 'T' + i });
const reply = (handler, out, value, code = 0) => { out.text = typeof value === 'string' ? value : JSON.stringify(value); handler(code); };

// --- CLIAMP's reply to url.load: play from the chosen track.
settle(); libraryPendingIndex = 2;
ipcCurrent = { kind: 'libLoad', request: { cmd: 'url.load', path: '/q.m3u8', play: true } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: true, tracks: [track(0), track(1), track(2)] });
assert.equal(queueTracks.length, 3); assert.equal(libraryPendingIndex, 0);
assert.deepEqual(ipcRequests().map(r => r.cmd), ['queue.play']); assert.equal(ipcRequests()[0].index, 2);
assert.equal(ipcCurrent.request.cmd, 'queue.play', 'the queue moved on to the next request');
settle(); libraryPendingIndex = 0;
ipcCurrent = { kind: 'libLoad', request: { cmd: 'provider.load', provider: 'local', playlist: 'P' } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: true, tracks: [track(0)] });
assert.deepEqual(ipcRequests().map(r => r.cmd), ['play']);
// A load that fails leaves no start index and an error.
settle(); libraryPendingIndex = 5; libraryError = '';
ipcCurrent = { kind: 'libLoad', request: { cmd: 'url.load', path: '/q.m3u8', play: true } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: false, error: 'no tracks found' }, 1);
assert.equal(libraryPendingIndex, 0); assert.match(libraryError, /no tracks found/); assert.equal(ipcBusy, false);
// Garbage on stdout is a failure, not an exception.
settle(); libraryError = '';
ipcCurrent = { kind: 'libLoad', request: { cmd: 'url.load', path: '/q.m3u8' } }; ipcBusy = true;
reply(providerExited, providerOut, 'not json at all', 0);
assert.match(libraryError, /failed/i); assert.equal(ipcBusy, false);

// --- favorites: a toggle flips the star and asks CLIAMP again; the list pages merge.
settle(); libraryFavorites = { '/m/a.mp3': true, '/m/keep.mp3': true };
ipcCurrent = { kind: 'libFavorite', request: { cmd: 'playlist.bookmark', provider: 'local', track: track('a') } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: true });
assert.equal(libraryFavorites['/m/a.mp3'], undefined, 'a favorite that was toggled is a favorite no more');
assert.equal(libraryFavorites['/m/keep.mp3'], true, 'a different track is untouched');
settle(); libraryFavorites = {};
ipcCurrent = { kind: 'libFavorite', request: { cmd: 'playlist.bookmark', provider: 'local', track: { path: '/m/x.mp3' } } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: true });
assert.equal(libraryFavorites['/m/x.mp3'], true);
assert.deepEqual(ipcRequests().map(r => r.cmd + ':' + r.playlist), ['provider.tracks:Favorites']);
settle();
ipcCurrent = { kind: 'libFavorites', request: { cmd: 'provider.tracks', playlist: 'Favorites', offset: 0, limit: 256 } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: true, tracks: Array.from({ length: 256 }, (_, i) => track(i)) });
assert.equal(Object.keys(libraryFavorites).length, 256);
assert.equal(ipcRequests()[0].offset, 256, 'a full page asks for the next one');
settle();
ipcCurrent = { kind: 'libFavorites', request: { cmd: 'provider.tracks', playlist: 'Favorites', offset: 256, limit: 256 } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: true, tracks: [track(900), track(901)] });
assert.equal(Object.keys(libraryFavorites).length, 258); assert.deepEqual(ipcRequests(), []);
// A favorites list that cannot be read is not an error for the user.
settle(); libraryError = '';
ipcCurrent = { kind: 'libFavorites', request: { cmd: 'provider.tracks', playlist: 'Favorites', offset: 0, limit: 256 } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: false, error: 'unknown playlist' }, 1);
assert.equal(libraryError, '');

// --- playlist tracks: a reply for a view that was left is dropped.
settle(); libraryView = 'playlistTracks'; libraryPlaylistId = 'Mine'; libraryTracks = [];
ipcCurrent = { kind: 'libTracks', request: { cmd: 'provider.tracks', provider: 'local', playlist: 'Other', offset: 0, limit: 200 } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: true, tracks: [track(1)] });
assert.equal(libraryTracks.length, 0);
settle();
ipcCurrent = { kind: 'libTracks', request: { cmd: 'provider.tracks', provider: 'local', playlist: 'Mine', offset: 0, limit: 200 } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: true, tracks: [track(1), track(2)] });
assert.equal(libraryTracks.length, 2); assert.equal(libraryLoaded, true);

// --- the write counter reaches zero on success and on failure.
settle(); libraryWritesPending = 2; libraryWriteFailed = false; libraryWriteNotice = 'Added.';
ipcCurrent = { kind: 'libPlaylistWrite', request: { cmd: 'playlist.add_many' } }; ipcBusy = true;
reply(providerExited, providerOut, { ok: true });
assert.equal(libraryWritesPending, 1);
settle(); ipcCurrent = { kind: 'libPlaylistWrite', request: { cmd: 'playlist.add_many' } }; ipcBusy = true; libraryError = '';
reply(providerExited, providerOut, { ok: false, error: 'disk full' }, 1);
assert.equal(libraryWritesPending, 0); assert.equal(libraryWriteFailed, true); assert.match(libraryError, /disk full/);
assert.equal(libraryNotice === 'Added.', false, 'a failed run earns no success notice');

// --- the library helper's handler: a reply is applied, a failure is shown, the queue goes on.
settle(); libraryView = 'artists'; libraryArtists = [];
libraryHelperCurrent = { kind: 'libArtists', request: { op: 'artists', offset: 0, limit: 60 } }; libraryHelperBusy = true;
enqueueLibraryHelper('libAlbums', { op: 'albums', offset: 0, limit: 60 });
reply(libraryExited, libraryOut, { ok: true, total: 1, artists: [{ id: 'a', name: 'A', album_count: 1 }] });
assert.equal(libraryArtists.length, 1);
assert.equal(libraryHelperCurrent.request.op, 'albums', 'the next request started');
settle(); libraryError = ''; libraryPendingIndex = 3;
libraryHelperCurrent = { kind: 'libQueueFile', request: { op: 'queue_file', album: 'x' } }; libraryHelperBusy = true;
reply(libraryExited, libraryOut, { ok: false, error: 'that album is no longer in the library' }, 1);
assert.match(libraryError, /no longer in the library/); assert.equal(libraryPendingIndex, 0); assert.equal(libraryHelperBusy, false);
libraryHelperCurrent = { kind: 'libRoots', request: { op: 'roots' } }; libraryHelperBusy = true; libraryHelperTimedOut = true; libraryError = '';
reply(libraryExited, libraryOut, '', 143);
assert.match(libraryError, /deadline/); assert.equal(libraryHelperTimedOut, false);

// --- the scan step's handler.
settle(); libraryScanning = true; libraryError = '';
reply(libraryScanExited, libraryScanOut, { ok: false, error: 'database is locked' }, 1);
assert.equal(libraryScanning, false); assert.match(libraryError, /database is locked/);
libraryScanning = true; libraryScanTimedOut = true; libraryError = '';
reply(libraryScanExited, libraryScanOut, '', 143);
assert.equal(libraryScanning, false); assert.match(libraryError, /deadline/); assert.equal(libraryScanTimedOut, false);
libraryScanning = true;
reply(libraryScanExited, libraryScanOut, { ok: true, scanning: true, phase: 'walk', dirs: 4, pending: 0, tracks: 0, roots: [{ path: '/m', tracks: 0, available: true }] });
assert.equal(libraryScanning, true); assert.equal(libraryScanTimer.running, true);
destroying = true; libraryScanning = true; libraryScanTimer.running = false;
reply(libraryScanExited, libraryScanOut, { ok: true, scanning: true, phase: 'walk', dirs: 4, pending: 0, tracks: 0, roots: [] });
assert.equal(libraryScanTimer.running, false, 'nothing is armed after teardown');
""")

    def test_normalizers_reject_hostile_input(self):
        run("""
assert.deepEqual(normalizeLibraryAlbums([null, 3, { id: 'a', name: 'N', artist: 'A', year: 99999, track_count: -4 }, { name: 'no id' }]),
  [{ id: 'a', name: 'N', artist: 'A', year: 9999, track_count: 0 }]);
assert.deepEqual(normalizeLibraryRoots([{ path: '/a', tracks: 3, available: true }, { path: '', tracks: 1 }, { path: '/b\\n', tracks: 1 }, 'x']),
  [{ path: '/a', tracks: 3, available: true }]);
assert.equal(normalizeLibraryRoots(Array.from({ length: 100 }, (_, i) => ({ path: '/r' + i, tracks: 1, available: true }))).length, 64);
assert.deepEqual(normalizeLibrarySorts([{ id: 'x', label: 'X' }, { id: 'y'.repeat(100), label: 'no' }, null]), [{ id: 'x', label: 'X' }]);
assert.deepEqual(normalizeLibraryArtists('nope'), []);
// The folder a user picked must be sendable, or the user is told.
// (Zero-width characters pass here and are refused by the helper and the chooser.)
addLibraryRoot('/bad\\u202ename'); assert.match(libraryError, /control or invisible/);
""")


if __name__ == "__main__":
    unittest.main()
