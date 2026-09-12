from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

import cliamp_file_picker as picker

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node is needed to exercise QML JavaScript')
class NowPlayingTests(unittest.TestCase):
    def test_live_titles_and_local_tags(self):
        source = (ROOT / 'BarWidget.qml').read_text()
        functions = []
        for name in ('cleanText', 'opaqueText', 'cleanNumber', 'cleanInteger', 'normalizeTrack', 'metadataText', 'nowPlayingMetadata'):
            start = source.index('  function ' + name + '(')
            end = source.find('\n  function ', start + 1)
            functions.append(source[start:end])
        script = '\n'.join(functions) + '''
const assert = require('node:assert/strict');
function stationNameFor(path) { return path === 'https://builtin/stream' ? 'Lofi' : ''; }
function display(track) { return nowPlayingMetadata(normalizeTrack(track)); }
assert.deepEqual(display({path:'https://directory/stream', title:'Station', station:'Station',
 stream_title:' Artist   - Song  -  Remix '}),
 {title:'Song - Remix', artist:'Artist', album:'', station:'Station'});
assert.equal(display({path:'https://builtin/stream', title:'Lofi Stream', stream_title:'Artist - Song'}).title, 'Song');
assert.deepEqual(display({path:'https://directory/stream', title:'Station', artist:'Old artist', stream_title:'Evening Show'}),
 {title:'Evening Show', artist:'', album:'', station:''});
assert.equal(display({path:'https://directory/stream', title:'Station', stream_title:'Artist - '}).title, 'Artist -');
assert.deepEqual(display({path:'/music/file.flac', title:' Tagged Song ', artist:' Artist ', album:' Album '}),
 {title:'Tagged Song', artist:'Artist', album:'Album', station:''});
assert.equal(display({path:'/music/untagged.mp3'}).title, 'untagged');
assert.equal(display({path:'https://directory/stream', title:'Station'}).title, 'Station');
assert.equal(display({path:'https://directory/stream', station:'Station'}).title, 'Station');
// A new status with no tags must not retain the previous song or artist.
assert.equal(display({path:'https://directory/stream', title:'Next station'}).artist, '');
assert.equal(display({path:'/music/a.mp3', title:'x'.repeat(400)}).title.length, 256);
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)


class FileMetadataTests(unittest.TestCase):
    @unittest.skipUnless(Path('/usr/bin/ffprobe').exists(), 'ffprobe is optional')
    def test_reads_real_embedded_tags_and_passes_them_to_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'filename.wav'
            with wave.open(str(path), 'wb') as audio:
                audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\0\0' * 800)
            info = b'INFO'
            for key, value in ((b'INAM', b'Tagged title'), (b'IART', b'Tagged artist'), (b'IPRD', b'Tagged album'), (b'IGNR', b'Jazz')):
                value += b'\0'
                info += key + struct.pack('<I', len(value)) + value + b'\0' * (len(value) % 2)
            content = path.read_bytes() + b'LIST' + struct.pack('<I', len(info)) + info
            path.write_bytes(content[:4] + struct.pack('<I', len(content)-8) + content[8:])
            with patch.object(picker, 'send_requests', return_value=[{'ok': True}]) as send:
                picker.load_paths([str(path)])
            track = send.call_args.args[0][0]['track']
            self.assertEqual(track, {'path': str(path), 'title': 'Tagged title',
                'artist': 'Tagged artist', 'album': 'Tagged album', 'genre': 'Jazz'})

    def test_unavailable_probe_and_expired_budget_fall_back(self):
        with patch.object(picker, '_open_package_executable', side_effect=OSError('missing')):
            self.assertEqual(picker._file_metadata('/music/song.mp3', time.monotonic()+1), {})
        with patch.object(picker, '_open_package_executable') as open_probe:
            self.assertEqual(picker._file_metadata('/music/song.mp3', time.monotonic()-1), {})
            open_probe.assert_not_called()

    def test_cancellation_is_not_swallowed(self):
        with patch.object(picker, '_stop_requested', True):
            with self.assertRaises(picker.PickerBoundaryError):
                picker._file_metadata('/music/song.mp3', time.monotonic()+1)
