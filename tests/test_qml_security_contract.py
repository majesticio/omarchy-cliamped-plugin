import re
from pathlib import Path
import unittest


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
            r"(?:providers|providerPlaylists|providerResults|searchFavorites|queueTracks|historyItems|lyricLines|audioDevices)\s*=\s*response\.",
        )
        for normalizer in (
            "normalizeProviders",
            "normalizePlaylists",
            "normalizeTracks",
            "normalizeHistory",
            "normalizeLyrics",
            "normalizeDevices",
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
        for source in (self.picker_helper, self.ipc_helper, self.favorites_helper):
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
        self.assertEqual(self.panel.count("TextInput {"), 2)
        self.assertIn("maximumLength: 256", self.panel)
        self.assertIn("maximumLength: 4096", self.panel)


if __name__ == "__main__":
    unittest.main()
