import re
from pathlib import Path
import unittest

from cliamped_security import ValidationError, normalize_request


ROOT = Path(__file__).resolve().parents[1]


class QmlSecurityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bar = (ROOT / "BarWidget.qml").read_text(encoding="utf-8")
        cls.panel = (ROOT / "Panel.qml").read_text(encoding="utf-8")
        cls.bands = (ROOT / "BandStream.qml").read_text(encoding="utf-8")
        cls.visualizer = (ROOT / "DesertVisualizer.qml").read_text(encoding="utf-8")
        cls.safe_text = (ROOT / "SafeText.qml").read_text(encoding="utf-8")
        cls.process_helper = (ROOT / "cliamped_process.py").read_text(encoding="utf-8")
        cls.picker_helper = (ROOT / "cliamp_file_picker.py").read_text(encoding="utf-8")
        cls.ipc_helper = (ROOT / "cliamp_ipc.py").read_text(encoding="utf-8")
        cls.favorites_helper = (ROOT / "cliamped_search_favorites.py").read_text(encoding="utf-8")
        cls.library_helper = (ROOT / "cliamped_library.py").read_text(encoding="utf-8")

    def test_all_text_primitives_are_plain_text(self):
        self.assertIn("textFormat: Text.PlainText", self.safe_text)
        for name, source in (
            ("BarWidget.qml", self.bar),
            ("Panel.qml", self.panel),
            ("DesertVisualizer.qml", self.visualizer),
        ):
            with self.subTest(file=name):
                self.assertNotRegex(source, r"\bText\s*\{")
                self.assertIn("SafeText {", source)

    def test_no_process_uses_inherited_path_or_a_shell(self):
        combined = self.bar + self.panel + self.bands
        for unsafe in ('["python3"', '["cliamp"', '["bash"', 'cliamp-session-mode.sh'):
            with self.subTest(command=unsafe):
                self.assertNotIn(unsafe, combined)
        self.assertIn('["/usr/bin/python3"', combined)
        self.assertEqual(combined.count("clearEnvironment: true"), combined.count("Process {"))

    def test_models_are_normalized_before_resident_assignment(self):
        self.assertNotRegex(
            self.bar + self.panel,
            r"(?:providers|providerPlaylists|providerResults|searchFavorites|queueTracks|historyItems|lyricLines|audioDevices|libraryArtists|libraryAlbums|libraryTracks|librarySorts)\s*=\s*response\.",
        )
        for normalizer in (
            "normalizeProviders",
            "normalizePlaylists",
            "normalizeTracks",
            "normalizeHistory",
            "normalizeLyrics",
            "normalizeDevices",
            "normalizeLibraryArtists",
            "normalizeLibraryAlbums",
            "normalizeLibrarySorts",
        ):
            self.assertIn(normalizer, self.bar)
        self.assertIn("maxIpcQueueItems", self.bar)
        self.assertIn("maxModelCharacters", self.bar)

    def test_processes_have_deadlines_and_destruction_cleanup(self):
        for timer in (
            "providerWatchdog",
            "searchFavoriteWatchdog",
            "daemonStartupDeadline",
            "audioPickerWatchdog",
        ):
            self.assertIn(timer, self.bar + self.panel)
        self.assertIn("Component.onDestruction", self.bar)
        self.assertIn("Component.onDestruction", self.panel)
        self.assertIn("Component.onDestruction", self.bands)
        self.assertIn("daemonStartFailures >= maxDaemonStartFailures", self.bar)
        self.assertIn("root.destroying) return", self.panel)
        self.assertIn("audioPicker.signal(9)", self.panel)
        combined = self.bar + self.panel + self.bands
        self.assertEqual(combined.count("property bool launchPending"), combined.count("Process {"))
        self.assertEqual(combined.count("onRunningChanged:"), combined.count("Process {"))

    def test_manifest_and_panel_cache_key_stay_aligned(self):
        manifest = (ROOT / "manifest.json").read_text(encoding="utf-8")
        version = re.search(r'"version"\s*:\s*"([^"]+)"', manifest).group(1)
        self.assertIn(f'Panel.qml") + "?v={version}"', self.bar)

    def test_every_helper_is_bound_to_its_qml_parent_lifetime(self):
        self.assertIn("_arm_supervisor_parent_death()", self.process_helper)
        for source in (self.picker_helper, self.ipc_helper, self.favorites_helper, self.library_helper):
            self.assertIn("_arm_helper_parent_death()", source)

    def test_qml_preserves_opaque_protocol_values(self):
        self.assertIn("function opaqueText", self.bar)
        self.assertIn("var providerMeta = Object.create(null)", self.bar)
        self.assertIn("var nativeNames = Object.create(null)", self.bar)
        self.assertIn("var path = opaqueText(item.path, 4096)", self.bar)
        self.assertIn("var id = opaqueText(item.id, 512)", self.bar)

    def test_failed_volume_requests_release_optimistic_state(self):
        self.assertIn("function settleVolumeRequest(kind)", self.bar)
        # Start failure, errored exit, and successful exit all settle the
        # optimistic volume guard so later status responses are admitted.
        self.assertGreaterEqual(self.bar.count("settleVolumeRequest(kind)"), 4)

    def test_visualizer_uses_the_bounded_supervisor(self):
        self.assertIn('"-I", root.helperPath(), "visstream"', self.bands)
        self.assertNotIn('["cliamp", "visstream"', self.bands)
        self.assertIn("response.bands.length !== 10", self.bands)
        self.assertIn("failureCount >= 6", self.bands)

    def test_text_inputs_have_explicit_limits(self):
        # Provider search, Library search, playlist name and the queue input.
        self.assertEqual(self.panel.count("TextInput {"), 4)
        self.assertEqual(self.panel.count("maximumLength: 256"), 2)
        self.assertEqual(self.panel.count("maximumLength: 128"), 1)
        self.assertEqual(self.panel.count("maximumLength: 4096"), 1)
        # Every input must stop the panel shortcuts from taking its keys.
        blocked = re.search(r"blocked:\s*([^\n]+)", self.panel).group(1)
        for field in ("queueInput", "providerSearch", "librarySearch", "playlistNameInput"):
            self.assertIn(f"{field}.activeFocus", blocked)

    def test_library_tab_replaces_files_and_the_pickers_moved_to_queue(self):
        tabs = re.search(r"model:\s*\[(.*?)\]\s*Rectangle", self.panel, re.S).group(1)
        self.assertIn('key: "library"', tabs)
        self.assertNotIn('key: "files"', tabs)
        self.assertEqual(tabs.count("key:"), 5)
        # The picker returns to Queue, where its buttons and status now live.
        self.assertNotIn('libraryTab = "files"', self.panel)
        self.assertNotIn('libraryTab === "files"', self.panel)
        self.assertIn("CHOOSE FILES", self.panel)
        self.assertIn("ADD FOLDER", self.panel)
        self.assertIn('requested === "files" ? "queue"', self.bar)

    def test_library_requests_are_served_by_two_bounded_queues(self):
        # Requests to CLIAMP keep their kinds; the helper's requests have their own queue.
        for kind in ("libTracks", "libLoad", "libFavorite", "libFavorites", "libPlaylists", "libPlaylistWrite"):
            with self.subTest(kind=kind):
                self.assertIn(f'kind === "{kind}"', self.bar)
        for kind in ("libArtists", "libAlbums", "libTracks", "libSearch", "libRoots", "libQueueFile"):
            with self.subTest(helper_kind=kind):
                self.assertIn(f'kind === "{kind}"', self.bar)
        self.assertIn("|| isLibraryKind(kind)", self.bar)
        self.assertIn("root.libraryError = message", self.bar)
        self.assertIn("function libraryRequestIsCurrent(item)", self.bar)
        self.assertIn("if (pending.length >= 12)", self.bar)
        # Every resident Library list is capped.
        self.assertIn("slice(0, maxLibraryItems)", self.bar)
        # A page is appended only when it follows the list on screen.
        self.assertIn("request.offset === libraryArtists.length", self.bar)
        self.assertIn("request.offset === libraryAlbums.length", self.bar)
        # The start index of an album comes from CLIAMP's queue, which is not
        # trimmed to the resident 128 tracks.
        self.assertIn("if (startAt > 0)", self.bar)
        self.assertNotIn("startAt < root.queueTracks.length", self.bar)

    def test_library_folder_picker_returns_to_the_library_not_the_queue(self):
        # The folder-only picker mode, its argument, and where its result goes.
        self.assertIn('"--music-folder"', self.panel)
        self.assertIn('root.pickerMode === "music"', self.panel)
        self.assertIn("addLibraryRoot(response.path)", self.panel)
        exit_handler = self.panel[self.panel.index("onExited: function(exitCode)"):]
        music_branch = exit_handler[exit_handler.index('if (root.pickerMode === "music") {'):]
        music_branch = music_branch[: music_branch.index("if (root.audioPickerTimedOut)")]
        self.assertIn('root.libraryTab = "library"', music_branch)
        self.assertNotIn('"queue"', music_branch)
        # The picker is the only way to name a folder: QML never takes a typed path.
        self.assertNotIn("addLibraryRoot(librarySearch", self.panel)
        self.assertNotIn("addLibraryRoot(playlistNameInput", self.panel)

    def test_playlist_writes_are_chunked_and_never_exceed_the_helper_limit(self):
        self.assertIn("i += 24", self.bar)
        self.assertIn("tracks.slice(i, i + 24)", self.bar)
        # A failed write must not leave the pending counter stuck.
        # Every way a write can end goes through one counter: the exit
        # handler, a helper that did not start, and the success branch.
        self.assertEqual(self.bar.count("finishLibraryWrite("), 4)  # definition + three callers
        self.assertIn("function finishLibraryWrite(succeeded)", self.bar)
        # Only a clean run earns the success notice and closes the picker.
        self.assertIn("if (!libraryWriteFailed) {", self.bar)
        # A refused or failed request must not read as success.
        self.assertIn("libraryWriteFailed = true", self.bar)
        # Favorites and Recently Played are CLIAMP's own lists, not add targets.
        self.assertIn('"Favorites" && id !== "Recently Played"', self.bar)

    def test_result_links_use_ids_from_cliamp_and_back_restores_the_list(self):
        # The IDs come from the track's provider data and are opaque; QML never builds them.
        self.assertIn('trackLink(track, "library.album")', self.bar)
        self.assertIn('trackLink(track, "library.artist")', self.bar)
        self.assertIn("opaqueText(track.provider_meta[key], 512)", self.bar)
        self.assertNotIn("md5", self.bar.lower())
        # Opening an album or an artist from search results replaces the shared
        # track list, so Back must read the results again.
        self.assertIn("function returnToLibraryTrackList(view)", self.bar)
        self.assertIn("searchLibrary(librarySearchQuery)", self.bar)
        self.assertIn("libraryArtistReturn = libraryView", self.bar)
        # A track without the IDs (an older playlist entry) shows plain text.
        self.assertIn("enabled: libraryRowLinks.artistLinked", self.panel)
        self.assertIn("enabled: libraryRowLinks.albumLinked", self.panel)

    def test_library_scan_runs_in_short_bounded_steps(self):
        # Each step is one helper process with a deadline; the steps are counted.
        self.assertIn("libraryScanStepsLeft = 4000", self.bar)
        self.assertIn('{ op: "scan", budget: 8 }', self.bar)
        for timer in ("libraryScanWatchdog", "libraryScanKill", "libraryWatchdog", "libraryKill"):
            self.assertIn(f"id: {timer}", self.bar)
        self.assertIn("libraryScanTimer.stop()", self.bar)
        self.assertIn("libraryScanProcess.signal(9)", self.bar)
        self.assertIn("libraryProcess.signal(9)", self.bar)
        # The next step is asked for only while the helper says that the scan goes on.
        self.assertIn("var scanning = response.scanning === true", self.bar)
        # A refused folder starts no scan: the scan starts from the helper's reply.
        add = self.bar[self.bar.index("function addLibraryRoot(path)"):]
        add = add[: add.index("function removeLibraryRoot(path)")]
        self.assertNotIn("startLibraryScan", add)
        # Background refreshes must not wipe an action's error message.
        refresh = self.bar[self.bar.index("function refreshLibraryRoots()"):]
        refresh = refresh[: refresh.index("function addLibraryRoot(path)")]
        self.assertNotIn("runLibraryHelper", refresh)

    def test_every_op_sent_to_the_library_helper_is_in_its_allowlist(self):
        from cliamped_library import normalize_library_request

        sent = set(re.findall(r'\{\s*op:\s*"([a-z_]+)"', self.bar + self.panel))
        self.assertTrue({"roots", "add_root", "remove_root", "scan", "artists", "albums",
                         "artist_albums", "album_tracks", "search", "queue_file"} <= sent)
        for operation in sorted(sent):
            with self.subTest(op=operation):
                try:
                    normalize_library_request({"op": operation})
                except ValidationError as error:
                    # Missing fields are fine here; an unknown operation is not.
                    self.assertNotIn("unsupported library operation", str(error))

    def test_every_command_sent_by_qml_is_in_the_helper_allowlist(self):
        sent = set(re.findall(r'cmd:\s*"([A-Za-z_.]+)"', self.bar + self.panel))
        # What the Library asks of stock CLIAMP: playing, playlists and favorites.
        self.assertTrue({"url.load", "queue.clear", "queue.play", "provider.tracks", "provider.load",
                         "playlist.create", "playlist.add", "playlist.add_many", "playlist.bookmark"} <= sent)
        for command in sorted(sent):
            with self.subTest(command=command):
                try:
                    normalize_request({"cmd": command})
                except ValidationError as error:
                    # Missing fields are fine here; an unknown command is not.
                    self.assertNotIn("unsupported IPC command", str(error))


if __name__ == "__main__":
    unittest.main()
