#!/usr/bin/python3 -I
"""Bounded readers for the tags of audio files, using only the standard library.

A library of tens of thousands of files cannot afford a process for each file,
and the files are untrusted: tags come from the internet and from old rips.
Every read here is bounded by a byte budget and a structural limit, a damaged
or hostile file yields fewer fields and never an exception, and every value is
cleaned with ``display_text`` before it leaves this module.

Supported: ID3v2.2/2.3/2.4 and ID3v1 (MP3 and others that carry ID3), FLAC and
Ogg Vorbis/Opus comments, and MP4/M4A iTunes atoms. Other files return no tags,
and the caller falls back to the file name.
"""

from __future__ import annotations

import os
import re
import stat
import struct
from typing import BinaryIO

from cliamped_security import display_text

MAX_FIELD_BYTES = 256
# The most bytes read from one file for its tags, apart from skipping over
# large frames and atoms with a seek.
READ_BUDGET = 2 * 1024 * 1024
MAX_FRAMES = 512
MAX_COMMENTS = 256
MAX_ATOM_DEPTH = 6
MAX_ATOMS = 4096
MAX_MOOV_BYTES = 8 * 1024 * 1024
MAX_OGG_PAGES = 64

TAG_FIELDS = ("title", "artist", "album", "album_artist", "genre")


class _Budget:
    """Counts the bytes read from one file."""

    def __init__(self, limit: int = READ_BUDGET) -> None:
        self.left = limit

    def read(self, stream: BinaryIO, count: int) -> bytes:
        if count < 0 or count > self.left:
            raise ValueError("tag read exceeds its budget")
        data = stream.read(count)
        self.left -= len(data)
        return data


def _clean(value: str) -> str:
    return display_text(value, maximum=MAX_FIELD_BYTES)


def _number(value: object) -> int:
    """The leading digits of ``value`` ("3/12" is 3), or 0."""

    match = re.match(r"\s*(\d{1,9})", str(value))
    return int(match.group(1)) if match else 0


def _year(value: object) -> int:
    match = re.search(r"(\d{4})", str(value))
    year = int(match.group(1)) if match else 0
    return year if 0 < year <= 9999 else 0


def _result(
    title: str = "",
    artist: str = "",
    album: str = "",
    album_artist: str = "",
    genre: str = "",
    year: object = 0,
    track: object = 0,
    disc: object = 0,
) -> dict[str, object]:
    return {
        "title": _clean(title),
        "artist": _clean(artist),
        "album": _clean(album),
        "album_artist": _clean(album_artist),
        "genre": _clean(genre),
        "year": _year(year),
        "track": min(_number(track), 1_000_000),
        "disc": min(_number(disc), 1_000),
    }


def has_tags(tags: dict[str, object]) -> bool:
    return any(tags.get(field) for field in TAG_FIELDS)


# ID3 ----------------------------------------------------------------------

_ID3V1_GENRES = (
    "Blues,Classic Rock,Country,Dance,Disco,Funk,Grunge,Hip-Hop,Jazz,Metal,"
    "New Age,Oldies,Other,Pop,R&B,Rap,Reggae,Rock,Techno,Industrial,Alternative,"
    "Ska,Death Metal,Pranks,Soundtrack,Euro-Techno,Ambient,Trip-Hop,Vocal,"
    "Jazz+Funk,Fusion,Trance,Classical,Instrumental,Acid,House,Game,Sound Clip,"
    "Gospel,Noise,Alt. Rock,Bass,Soul,Punk,Space,Meditative,Instrumental Pop,"
    "Instrumental Rock,Ethnic,Gothic,Darkwave,Techno-Industrial,Electronic,"
    "Pop-Folk,Eurodance,Dream,Southern Rock,Comedy,Cult,Gangsta,Top 40,"
    "Christian Rap,Pop/Funk,Jungle,Native American,Cabaret,New Wave,Psychedelic,"
    "Rave,Showtunes,Trailer,Lo-Fi,Tribal,Acid Punk,Acid Jazz,Polka,Retro,"
    "Musical,Rock & Roll,Hard Rock"
).split(",")

_ID3V22_FRAMES = {
    b"TT2": "title", b"TP1": "artist", b"TAL": "album", b"TP2": "album_artist",
    b"TCO": "genre", b"TYE": "year", b"TRK": "track", b"TPA": "disc",
}
_ID3V23_FRAMES = {
    b"TIT2": "title", b"TPE1": "artist", b"TALB": "album", b"TPE2": "album_artist",
    b"TCON": "genre", b"TYER": "year", b"TDRC": "year", b"TRCK": "track", b"TPOS": "disc",
}
_MAX_TEXT_FRAME = 4096


def _syncsafe(data: bytes) -> int:
    value = 0
    for byte in data:
        if byte & 0x80:
            raise ValueError("not a syncsafe integer")
        value = (value << 7) | byte
    return value


def _decode_id3_text(data: bytes) -> str:
    if not data:
        return ""
    encoding, body = data[0], data[1:]
    if encoding == 1:
        codec = "utf-16"
    elif encoding == 2:
        codec = "utf-16-be"
    elif encoding == 3:
        codec = "utf-8"
    else:
        codec = "latin-1"
    text = body.decode(codec, errors="replace")
    # A text frame may hold several values separated by NUL; the first one is shown.
    return text.split("\x00", 1)[0]


def _genre_name(text: str) -> str:
    match = re.fullmatch(r"\((\d{1,3})\)(.*)", text.strip())
    if match:
        index = int(match.group(1))
        if match.group(2):
            return match.group(2)
        return _ID3V1_GENRES[index] if index < len(_ID3V1_GENRES) else ""
    if text.strip().isdigit():
        index = int(text.strip())
        return _ID3V1_GENRES[index] if index < len(_ID3V1_GENRES) else ""
    return text


def _read_id3v2(stream: BinaryIO, budget: _Budget) -> dict[str, str] | None:
    stream.seek(0)
    header = budget.read(stream, 10)
    if len(header) < 10 or header[:3] != b"ID3":
        return None
    major, flags = header[3], header[5]
    if major not in (2, 3, 4):
        return None
    try:
        tag_size = _syncsafe(header[6:10])
    except ValueError:
        return None
    end = 10 + tag_size
    position = 10
    if flags & 0x40 and major in (3, 4):
        # An extended header precedes the frames.
        extended = budget.read(stream, 4)
        if len(extended) < 4:
            return None
        try:
            size = _syncsafe(extended) if major == 4 else struct.unpack(">I", extended)[0] + 4
        except ValueError:
            return None
        if major == 4:
            size = max(size, 4)
        position += size
        stream.seek(position)
    frames = _ID3V22_FRAMES if major == 2 else _ID3V23_FRAMES
    id_size, header_size = (3, 6) if major == 2 else (4, 10)
    found: dict[str, str] = {}
    for _ in range(MAX_FRAMES):
        if position + header_size > end:
            break
        raw = budget.read(stream, header_size)
        if len(raw) < header_size or raw[0] == 0:
            break
        frame_id = raw[:id_size]
        try:
            if major == 2:
                size = int.from_bytes(raw[3:6], "big")
            elif major == 3:
                size = struct.unpack(">I", raw[4:8])[0]
            else:
                size = _syncsafe(raw[4:8])
        except ValueError:
            break
        position += header_size + size
        if size < 0 or position > end:
            break
        field = frames.get(frame_id)
        if field is None or size == 0 or size > _MAX_TEXT_FRAME:
            stream.seek(position)
            continue
        text = _decode_id3_text(budget.read(stream, size))
        if field not in found and text.strip():
            found[field] = text
    return found


def _read_id3v1(stream: BinaryIO, budget: _Budget) -> dict[str, str] | None:
    try:
        stream.seek(-128, os.SEEK_END)
    except OSError:
        return None
    block = budget.read(stream, 128)
    if len(block) < 128 or block[:3] != b"TAG":
        return None

    def text(part: bytes) -> str:
        return part.split(b"\x00", 1)[0].decode("latin-1", errors="replace").strip()

    found = {
        "title": text(block[3:33]),
        "artist": text(block[33:63]),
        "album": text(block[63:93]),
        "year": text(block[93:97]),
    }
    if block[125] == 0 and block[126] != 0:
        found["track"] = str(block[126])
    genre = block[127]
    if genre < len(_ID3V1_GENRES):
        found["genre"] = _ID3V1_GENRES[genre]
    return {key: value for key, value in found.items() if value}


def _read_id3(stream: BinaryIO, budget: _Budget) -> dict[str, object] | None:
    found = _read_id3v2(stream, budget) or {}
    if not found.get("title"):
        # ID3v1 sits at the end of the file and fills what ID3v2 did not give.
        old = _read_id3v1(stream, budget) or {}
        for key, value in old.items():
            found.setdefault(key, value)
    if not found:
        return None
    return _result(
        title=found.get("title", ""),
        artist=found.get("artist", ""),
        album=found.get("album", ""),
        album_artist=found.get("album_artist", ""),
        genre=_genre_name(found.get("genre", "")),
        year=found.get("year", 0),
        track=found.get("track", 0),
        disc=found.get("disc", 0),
    )


# Vorbis comments (FLAC, Ogg Vorbis, Opus) -------------------------------------


def _parse_vorbis_comment(data: bytes) -> dict[str, object] | None:
    try:
        (vendor_length,) = struct.unpack_from("<I", data, 0)
        offset = 4 + vendor_length
        (count,) = struct.unpack_from("<I", data, offset)
        offset += 4
    except struct.error:
        return None
    fields: dict[str, str] = {}
    for _ in range(min(count, MAX_COMMENTS)):
        try:
            (length,) = struct.unpack_from("<I", data, offset)
        except struct.error:
            break
        offset += 4
        if offset + length > len(data):
            break
        if length > _MAX_TEXT_FRAME:
            # Lyrics and embedded pictures are long; the fields after them are still wanted.
            offset += length
            continue
        entry = data[offset:offset + length].decode("utf-8", errors="replace")
        offset += length
        key, sep, value = entry.partition("=")
        if sep:
            fields.setdefault(key.strip().upper().replace(" ", ""), value)
    if not fields:
        return None
    return _result(
        title=fields.get("TITLE", ""),
        artist=fields.get("ARTIST", ""),
        album=fields.get("ALBUM", ""),
        album_artist=fields.get("ALBUMARTIST", "") or fields.get("ALBUM_ARTIST", ""),
        genre=fields.get("GENRE", ""),
        year=fields.get("DATE", "") or fields.get("YEAR", ""),
        track=fields.get("TRACKNUMBER", "") or fields.get("TRACK", ""),
        disc=fields.get("DISCNUMBER", "") or fields.get("DISC", ""),
    )


def _read_flac(stream: BinaryIO, budget: _Budget) -> dict[str, object] | None:
    stream.seek(0)
    if budget.read(stream, 4) != b"fLaC":
        return None
    for _ in range(128):
        header = budget.read(stream, 4)
        if len(header) < 4:
            return None
        last = header[0] & 0x80
        block_type = header[0] & 0x7F
        length = int.from_bytes(header[1:4], "big")
        if block_type == 4:
            return _parse_vorbis_comment(budget.read(stream, length)) if length <= READ_BUDGET else None
        # Pictures and padding are skipped with a seek, not read.
        stream.seek(length, os.SEEK_CUR)
        if last:
            return None
    return None


def _ogg_packets(stream: BinaryIO, budget: _Budget, wanted: int) -> list[bytes]:
    """The first ``wanted`` packets of the first logical stream of an Ogg file."""

    packets: list[bytes] = []
    current = b""
    serial: int | None = None
    stream.seek(0)
    for _ in range(MAX_OGG_PAGES):
        header = budget.read(stream, 27)
        if len(header) < 27 or header[:4] != b"OggS":
            break
        segments = header[26]
        table = budget.read(stream, segments)
        if len(table) < segments:
            break
        page_serial = struct.unpack_from("<I", header, 14)[0]
        body_length = sum(table)
        if serial is None:
            serial = page_serial
        if page_serial != serial:
            stream.seek(body_length, os.SEEK_CUR)
            continue
        body = budget.read(stream, body_length)
        offset = 0
        for lace in table:
            current += body[offset:offset + lace]
            offset += lace
            if lace < 255:
                packets.append(current)
                current = b""
                if len(packets) >= wanted:
                    return packets
    return packets


def _read_ogg(stream: BinaryIO, budget: _Budget) -> dict[str, object] | None:
    packets = _ogg_packets(stream, budget, 2)
    if len(packets) < 2:
        return None
    first, second = packets
    if first.startswith(b"\x01vorbis") and second.startswith(b"\x03vorbis"):
        return _parse_vorbis_comment(second[7:])
    if first.startswith(b"OpusHead") and second.startswith(b"OpusTags"):
        return _parse_vorbis_comment(second[8:])
    return None


# MP4 ------------------------------------------------------------------------

_MP4_TEXT = {
    b"\xa9nam": "title", b"\xa9ART": "artist", b"\xa9alb": "album",
    b"aART": "album_artist", b"\xa9gen": "genre", b"\xa9day": "year",
}
_CONTAINERS = {b"moov", b"udta", b"meta", b"ilst"}


def _mp4_value(item: bytes) -> bytes | None:
    """The payload of the first ``data`` atom of an ilst item."""

    offset = 0
    while offset + 16 <= len(item):
        size, kind = struct.unpack_from(">I4s", item, offset)
        if size < 16 or offset + size > len(item):
            return None
        if kind == b"data":
            return item[offset + 16:offset + size]
        offset += size
    return None


def _walk_mp4(data: bytes, depth: int, found: dict[str, object], counter: list[int]) -> None:
    offset = 0
    while offset + 8 <= len(data) and counter[0] < MAX_ATOMS:
        counter[0] += 1
        size, kind = struct.unpack_from(">I4s", data, offset)
        header = 8
        if size == 1 and offset + 16 <= len(data):
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        elif size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            return
        body = data[offset + header:offset + size]
        if kind == b"meta":
            body = body[4:]  # version and flags
        if kind in _CONTAINERS and depth < MAX_ATOM_DEPTH:
            if kind == b"ilst":
                _read_ilst(body, found)
            else:
                _walk_mp4(body, depth + 1, found, counter)
        offset += size


def _read_ilst(data: bytes, found: dict[str, object]) -> None:
    offset = 0
    items = 0
    while offset + 8 <= len(data) and items < MAX_COMMENTS:
        items += 1
        size, kind = struct.unpack_from(">I4s", data, offset)
        if size < 8 or offset + size > len(data):
            return
        payload = _mp4_value(data[offset + 8:offset + size])
        offset += size
        if payload is None:
            continue
        if kind in _MP4_TEXT:
            found.setdefault(_MP4_TEXT[kind], payload[:_MAX_TEXT_FRAME].decode("utf-8", errors="replace"))
        elif kind == b"trkn" and len(payload) >= 4:
            found.setdefault("track", struct.unpack_from(">H", payload, 2)[0])
        elif kind == b"disk" and len(payload) >= 4:
            found.setdefault("disc", struct.unpack_from(">H", payload, 2)[0])
        elif kind == b"gnre" and len(payload) >= 2 and "genre" not in found:
            index = struct.unpack_from(">H", payload, 0)[0] - 1
            if 0 <= index < len(_ID3V1_GENRES):
                found["genre"] = _ID3V1_GENRES[index]


def _read_mp4(stream: BinaryIO, budget: _Budget) -> dict[str, object] | None:
    stream.seek(0)
    try:
        size_of_file = os.fstat(stream.fileno()).st_size
    except (OSError, ValueError):
        return None
    position = 0
    for _ in range(MAX_ATOMS):
        if position + 8 > size_of_file:
            return None
        stream.seek(position)
        header = budget.read(stream, 8)
        if len(header) < 8:
            return None
        size, kind = struct.unpack(">I4s", header)
        header_size = 8
        if size == 1:
            extended = budget.read(stream, 8)
            if len(extended) < 8:
                return None
            size = struct.unpack(">Q", extended)[0]
            header_size = 16
        elif size == 0:
            size = size_of_file - position
        if size < header_size:
            return None
        if kind == b"moov":
            if size - header_size > MAX_MOOV_BYTES:
                return None
            moov = budget.read(stream, size - header_size)
            found: dict[str, object] = {}
            # Walk it as a one-atom file: moov is at most MAX_MOOV_BYTES, so its size fits 32 bits.
            _walk_mp4(struct.pack(">I4s", len(moov) + 8, b"moov") + moov, 0, found, [0])
            return _result(
                title=str(found.get("title", "")),
                artist=str(found.get("artist", "")),
                album=str(found.get("album", "")),
                album_artist=str(found.get("album_artist", "")),
                genre=str(found.get("genre", "")),
                year=found.get("year", 0),
                track=found.get("track", 0),
                disc=found.get("disc", 0),
            ) if found else None
        # mdat and the rest are skipped with a seek: moov can follow a huge mdat.
        position += size
    return None


# Entry point ------------------------------------------------------------------

_ID3_EXTENSIONS = {".mp3", ".aac", ".aacp"}


def _open_regular_file(path: str) -> BinaryIO:
    """Open ``path`` for reading, refusing a symlink or anything that is not a regular file.

    A scan checked the file when it walked the folder, but reads it later, and a
    share can change meanwhile. O_NOFOLLOW stops a swapped-in symlink, and O_NONBLOCK
    with the check of the type stops a FIFO or a device from holding the reader.
    """

    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("not a regular file")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def read_tags(path: str) -> dict[str, object]:
    """The tags of the audio file at ``path``. Always a dict; empty fields mean unknown."""

    extension = os.path.splitext(path)[1].lower()
    readers = []
    if extension == ".flac":
        readers = [_read_flac]
    elif extension in {".ogg", ".opus"}:
        readers = [_read_ogg]
    elif extension in {".m4a", ".m4b", ".alac", ".mp4"}:
        readers = [_read_mp4]
    elif extension in _ID3_EXTENSIONS:
        readers = [_read_id3]
    # An MP4 moov atom, and the comment header of an Ogg file that carries cover art,
    # can be larger than the budget of the others.
    large = {".m4a", ".m4b", ".alac", ".mp4", ".ogg", ".opus"}
    limit = MAX_MOOV_BYTES + 65536 if extension in large else READ_BUDGET
    for reader in readers:
        try:
            with _open_regular_file(path) as stream:
                tags = reader(stream, _Budget(limit))
        except (OSError, ValueError, struct.error, UnicodeError, OverflowError):
            continue
        if tags is not None and has_tags(tags):
            return tags
    return _result()
