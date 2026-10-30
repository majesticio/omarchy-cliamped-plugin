import os
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import cliamped_tags
from cliamped_tags import read_tags


def syncsafe(value):
    return bytes([(value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F])


def id3_frame(frame_id, text, *, version=3, encoding=0):
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}[encoding]
    body = bytes([encoding]) + text.encode(codec)
    if version == 2:
        return frame_id + len(body).to_bytes(3, "big") + body
    size = syncsafe(len(body)) if version == 4 else struct.pack(">I", len(body))
    return frame_id + size + b"\x00\x00" + body


def id3_tag(frames, *, version=3):
    body = b"".join(frames)
    return b"ID3" + bytes([version, 0, 0]) + syncsafe(len(body)) + body


def flac_file(comments, *, picture=0):
    vendor = b"test"
    entries = b"".join(struct.pack("<I", len(c)) + c for c in comments)
    comment = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments)) + entries
    blocks = [(0, b"\x00" * 34)]
    if picture:
        blocks.append((6, b"\x00" * picture))
    blocks.append((4, comment))
    out = b"fLaC"
    for index, (kind, body) in enumerate(blocks):
        last = 0x80 if index == len(blocks) - 1 else 0
        out += bytes([last | kind]) + len(body).to_bytes(3, "big") + body
    return out


def ogg_page(packet_bytes, *, serial=1, sequence=0, flags=0):
    segments = []
    remaining = len(packet_bytes)
    while remaining >= 255:
        segments.append(255)
        remaining -= 255
    segments.append(remaining)
    header = b"OggS\x00" + bytes([flags]) + struct.pack("<qIII", 0, serial, sequence, 0) + bytes([len(segments)])
    return header + bytes(segments) + packet_bytes


def vorbis_comment_body(comments):
    vendor = b"test"
    entries = b"".join(struct.pack("<I", len(c)) + c for c in comments)
    return struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments)) + entries


def atom(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def mp4_item(kind, value):
    data = atom(b"data", struct.pack(">II", 1, 0) + value.encode("utf-8"))
    return atom(kind, data)


def mp4_file(items, *, moov_last=True, big_mdat=0):
    ilst = atom(b"ilst", b"".join(items))
    meta = atom(b"meta", b"\x00\x00\x00\x00" + ilst)
    moov = atom(b"moov", atom(b"udta", meta))
    ftyp = atom(b"ftyp", b"M4A \x00\x00\x00\x00M4A mp42isom")
    mdat = atom(b"mdat", b"\x00" * big_mdat)
    return ftyp + (mdat + moov if moov_last else moov + mdat)


class TagFixtureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def write(self, name, data):
        path = Path(self.directory.name) / name
        path.write_bytes(data)
        return str(path)


class Id3Tests(TagFixtureTests):
    def test_id3v23_text_frames(self):
        path = self.write("a.mp3", id3_tag([
            id3_frame(b"TIT2", "Money"), id3_frame(b"TPE1", "Pink Floyd"),
            id3_frame(b"TALB", "The Dark Side of the Moon"), id3_frame(b"TPE2", "Pink Floyd"),
            id3_frame(b"TCON", "Progressive Rock"), id3_frame(b"TYER", "1973"),
            id3_frame(b"TRCK", "6/10"), id3_frame(b"TPOS", "1/2"),
        ]) + b"\x00" * 64)
        self.assertEqual(read_tags(path), {
            "title": "Money", "artist": "Pink Floyd", "album": "The Dark Side of the Moon",
            "album_artist": "Pink Floyd", "genre": "Progressive Rock", "year": 1973, "track": 6, "disc": 1,
        })

    def test_id3v24_with_utf8_and_a_full_date(self):
        path = self.write("b.mp3", id3_tag([
            id3_frame(b"TIT2", "Sigur Rós – Hoppípolla", version=4, encoding=3),
            id3_frame(b"TDRC", "2005-10-18", version=4, encoding=3),
        ], version=4))
        tags = read_tags(path)
        self.assertEqual(tags["title"], "Sigur Rós – Hoppípolla")
        self.assertEqual(tags["year"], 2005)

    def test_id3v22_three_letter_frames(self):
        path = self.write("c.mp3", id3_tag([
            id3_frame(b"TT2", "Old Song", version=2), id3_frame(b"TP1", "Old Band", version=2),
            id3_frame(b"TRK", "4", version=2),
        ], version=2))
        tags = read_tags(path)
        self.assertEqual((tags["title"], tags["artist"], tags["track"]), ("Old Song", "Old Band", 4))

    def test_utf16_text_and_multi_value_frames(self):
        path = self.write("d.mp3", id3_tag([
            id3_frame(b"TIT2", "Björk – Jóga", encoding=1),
            id3_frame(b"TPE1", "First\x00Second", encoding=3, version=4),
        ], version=3))
        tags = read_tags(path)
        self.assertEqual(tags["title"], "Björk – Jóga")

    def test_genre_numbers_become_names(self):
        for raw, expected in (("(17)", "Rock"), ("(17)Hard Rock", "Hard Rock"), ("17", "Rock"), ("(255)", "")):
            path = self.write("g.mp3", id3_tag([id3_frame(b"TIT2", "T"), id3_frame(b"TCON", raw)]))
            self.assertEqual(read_tags(path)["genre"], expected, raw)

    def test_id3v1_is_the_fallback(self):
        block = b"TAG" + b"Old Title".ljust(30, b"\x00") + b"Old Artist".ljust(30, b"\x00") \
            + b"Old Album".ljust(30, b"\x00") + b"1999" + b"\x00" * 28 + bytes([0, 7, 13])
        path = self.write("v1.mp3", b"\xff\xfb" * 600 + block)
        self.assertEqual(
            (read_tags(path)["title"], read_tags(path)["artist"], read_tags(path)["year"],
             read_tags(path)["track"], read_tags(path)["genre"]),
            ("Old Title", "Old Artist", 1999, 7, "Pop"),
        )

    def test_a_big_cover_frame_is_skipped_not_read(self):
        picture = b"APIC" + struct.pack(">I", 1_500_000) + b"\x00\x00" + b"\x00" * 1_500_000
        data = id3_tag([id3_frame(b"TIT2", "Before"), picture, id3_frame(b"TPE1", "After")])
        # The tag size field is rewritten to cover the picture.
        body_size = len(data) - 10
        data = data[:6] + syncsafe(body_size) + data[10:]
        tags = read_tags(self.write("pic.mp3", data))
        self.assertEqual((tags["title"], tags["artist"]), ("Before", "After"))

    def test_text_with_control_and_bidi_characters_is_cleaned(self):
        path = self.write("x.mp3", id3_tag([id3_frame(b"TIT2", "Evil‮Name\x07 ok", encoding=3, version=4)], version=4))
        title = read_tags(path)["title"]
        self.assertNotIn("‮", title)
        self.assertNotIn("\x07", title)
        self.assertIn("ok", title)

    def test_long_values_are_truncated(self):
        path = self.write("l.mp3", id3_tag([id3_frame(b"TIT2", "x" * 3000, encoding=3, version=4)], version=4))
        self.assertLessEqual(len(read_tags(path)["title"].encode()), cliamped_tags.MAX_FIELD_BYTES)


class VorbisTests(TagFixtureTests):
    COMMENTS = [b"title=Halo", b"ARTIST=Beyonc\xc3\xa9", b"Album=I Am... Sasha Fierce",
                b"ALBUMARTIST=Beyonc\xc3\xa9", b"genre=Pop", b"DATE=2008-11-14",
                b"TRACKNUMBER=1/12", b"DISCNUMBER=2"]

    def expected(self):
        return {"title": "Halo", "artist": "Beyoncé", "album": "I Am... Sasha Fierce",
                "album_artist": "Beyoncé", "genre": "Pop", "year": 2008, "track": 1, "disc": 2}

    def test_flac(self):
        self.assertEqual(read_tags(self.write("a.flac", flac_file(self.COMMENTS))), self.expected())

    def test_flac_with_a_large_picture_block_before_the_comments(self):
        data = flac_file(self.COMMENTS, picture=1_800_000)
        self.assertEqual(read_tags(self.write("p.flac", data)), self.expected())

    def test_ogg_vorbis(self):
        data = ogg_page(b"\x01vorbis" + b"\x00" * 23, flags=2) \
            + ogg_page(b"\x03vorbis" + vorbis_comment_body(self.COMMENTS) + b"\x01", sequence=1)
        self.assertEqual(read_tags(self.write("a.ogg", data)), self.expected())

    def test_opus_with_a_comment_longer_than_one_page_lace(self):
        long_comments = self.COMMENTS + [b"COMMENT=" + b"z" * 600]
        data = ogg_page(b"OpusHead" + b"\x01" + b"\x00" * 10, flags=2) \
            + ogg_page(b"OpusTags" + vorbis_comment_body(long_comments), sequence=1)
        self.assertEqual(read_tags(self.write("a.opus", data)), self.expected())

    def test_a_second_logical_stream_is_ignored(self):
        data = ogg_page(b"\x01vorbis" + b"\x00" * 23, flags=2, serial=1) \
            + ogg_page(b"\x01vorbis" + b"\x00" * 23, flags=2, serial=2) \
            + ogg_page(b"\x03vorbis" + vorbis_comment_body([b"TITLE=Right"]) + b"\x01", serial=1, sequence=1)
        self.assertEqual(read_tags(self.write("m.ogg", data))["title"], "Right")


class Mp4Tests(TagFixtureTests):
    def items(self):
        return [
            mp4_item(b"\xa9nam", "Dancing Queen"), mp4_item(b"\xa9ART", "ABBA"),
            mp4_item(b"\xa9alb", "Gold"), mp4_item(b"aART", "ABBA"), mp4_item(b"\xa9gen", "Pop"),
            mp4_item(b"\xa9day", "1992-09-21"),
            atom(b"trkn", atom(b"data", struct.pack(">II", 0, 0) + struct.pack(">HHHH", 0, 5, 19, 0))),
            atom(b"disk", atom(b"data", struct.pack(">II", 0, 0) + struct.pack(">HHH", 0, 1, 2))),
        ]

    def expected(self):
        return {"title": "Dancing Queen", "artist": "ABBA", "album": "Gold", "album_artist": "ABBA",
                "genre": "Pop", "year": 1992, "track": 5, "disc": 1}

    def test_m4a_with_moov_after_a_large_mdat(self):
        # moov at the end is the layout of files that were not made for streaming.
        self.assertEqual(read_tags(self.write("a.m4a", mp4_file(self.items(), big_mdat=3_000_000))), self.expected())

    def test_m4a_with_moov_first(self):
        self.assertEqual(read_tags(self.write("b.m4a", mp4_file(self.items(), moov_last=False))), self.expected())


class RobustnessTests(TagFixtureTests):
    def test_garbage_and_truncated_files_give_empty_tags_not_errors(self):
        empty = read_tags(self.write("empty.mp3", b""))
        self.assertEqual({k: v for k, v in empty.items() if v}, {})
        for name, data in {
            "trunc.mp3": id3_tag([id3_frame(b"TIT2", "Hello")])[:14],
            "lie.mp3": b"ID3\x03\x00\x00" + syncsafe(900_000_000) + b"TIT2" + struct.pack(">I", 800_000_000) + b"\x00\x00",
            "bad.flac": b"fLaC" + b"\xff" * 40,
            "bad.ogg": b"OggS" + b"\x00" * 40,
            "bad.m4a": struct.pack(">I4s", 0xFFFFFFF0, b"moov") + b"\x00" * 16,
            "zero.m4a": struct.pack(">I4s", 8, b"free") * 5000,
            "text.mp3": b"this is not audio at all" * 100,
            "nosync.mp3": b"ID3\x03\x00\x00\xff\xff\xff\xff",
            "unknown.wav": b"RIFF....WAVE",
        }.items():
            with self.subTest(name=name):
                tags = read_tags(self.write(name, data))
                self.assertEqual(set(tags), {"title", "artist", "album", "album_artist", "genre", "year", "track", "disc"})

    def test_deeply_nested_atoms_stop_at_the_depth_limit(self):
        inner = mp4_item(b"\xa9nam", "Deep")
        for _ in range(40):
            inner = atom(b"udta", inner)
        data = atom(b"ftyp", b"M4A \x00\x00\x00\x00") + atom(b"moov", inner)
        self.assertEqual(read_tags(self.write("deep.m4a", data))["title"], "")

    def test_a_hostile_tag_never_reads_more_than_its_budget(self):
        reads = []
        original = cliamped_tags._Budget.read

        def counting(budget, stream, count):
            data = original(budget, stream, count)
            reads.append(len(data))
            return data

        frames = [id3_frame(b"TXXX", "x" * 2000) for _ in range(5000)]
        path = self.write("many.mp3", id3_tag(frames))
        cliamped_tags._Budget.read = counting
        try:
            read_tags(path)
        finally:
            cliamped_tags._Budget.read = original
        self.assertLess(sum(reads), cliamped_tags.READ_BUDGET + 1)

    def test_a_missing_file_gives_empty_tags(self):
        self.assertEqual(read_tags(os.path.join(self.directory.name, "none.mp3"))["title"], "")


class SafeOpenTests(TagFixtureTests):
    def test_symlinks_fifos_and_directories_are_not_read(self):
        real = self.write("real.mp3", id3_tag([id3_frame(b"TIT2", "Real")]))
        link = os.path.join(self.directory.name, "link.mp3")
        os.symlink(real, link)
        fifo = os.path.join(self.directory.name, "fifo.mp3")
        os.mkfifo(fifo)
        folder = os.path.join(self.directory.name, "dir.mp3")
        os.mkdir(folder)
        self.assertEqual(read_tags(real)["title"], "Real")
        for path in (link, fifo, folder):
            with self.subTest(path=os.path.basename(path)):
                started = time.monotonic()
                self.assertEqual(read_tags(path)["title"], "")
                self.assertLess(time.monotonic() - started, 1.0, "a FIFO must not hold the reader")


class LongFieldTests(TagFixtureTests):
    def test_a_long_comment_is_skipped_and_the_fields_after_it_are_kept(self):
        comments = [b"TITLE=Before", b"LYRICS=" + b"la " * 3000, b"ARTIST=After"]
        tags = read_tags(self.write("long.flac", flac_file(comments)))
        self.assertEqual((tags["title"], tags["artist"]), ("Before", "After"))

    def test_an_ogg_file_with_big_cover_art_still_gives_its_tags(self):
        art = b"METADATA_BLOCK_PICTURE=" + b"A" * 3_000_000
        comment = b"\x03vorbis" + vorbis_comment_body([b"TITLE=Covered", art, b"ARTIST=Band"]) + b"\x01"
        data = ogg_page(b"\x01vorbis" + b"\x00" * 23, flags=2)
        # One packet over many pages: 255 laced segments of 255 bytes fill a page.
        offset, sequence = 0, 1
        while offset < len(comment):
            chunk = comment[offset:offset + 255 * 255]
            offset += len(chunk)
            segments = [255] * (len(chunk) // 255) + [len(chunk) % 255] if offset >= len(comment) else [255] * 255
            header = b"OggS\x00" + bytes([1 if sequence > 1 else 0]) + struct.pack("<qIII", 0, 1, sequence, 0) + bytes([len(segments)])
            data += header + bytes(segments) + chunk
            sequence += 1
        tags = read_tags(self.write("art.ogg", data))
        self.assertEqual((tags["title"], tags["artist"]), ("Covered", "Band"))


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is needed to make real files")
class RealFileTests(TagFixtureTests):
    def make(self, name, *extra):
        path = os.path.join(self.directory.name, name)
        command = [
            "ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-metadata", "title=Real Title", "-metadata", "artist=Real Artist", "-metadata", "album=Real Album",
            "-metadata", "album_artist=Real AA", "-metadata", "track=3", "-metadata", "date=2001",
            "-metadata", "genre=Jazz", *extra, path,
        ]
        subprocess.run(command, check=True, timeout=60)
        return path

    def check(self, path):
        tags = read_tags(path)
        self.assertEqual((tags["title"], tags["artist"], tags["album"], tags["album_artist"]),
                         ("Real Title", "Real Artist", "Real Album", "Real AA"), path)
        self.assertEqual((tags["track"], tags["year"], tags["genre"]), (3, 2001, "Jazz"), path)

    def test_formats_written_by_ffmpeg(self):
        for name, extra in (
            ("a.mp3", ("-id3v2_version", "3")), ("b.mp3", ("-id3v2_version", "4")),
            ("c.flac", ()), ("d.m4a", ()), ("e.ogg", ("-c:a", "libvorbis")), ("f.opus", ("-c:a", "libopus")),
        ):
            with self.subTest(name=name):
                try:
                    path = self.make(name, *extra)
                except subprocess.CalledProcessError:
                    self.skipTest(f"this ffmpeg cannot write {name}")
                self.check(path)

    def test_m4a_with_moov_at_the_end(self):
        path = self.make("end.m4a")  # ffmpeg writes moov last unless +faststart
        self.check(path)


if __name__ == "__main__":
    unittest.main()
