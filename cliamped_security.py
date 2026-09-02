#!/usr/bin/python3 -I
"""Shared validation limits for CLIAMPed's untrusted IPC boundary.

The CLIAMP daemon is a local process, but much of the metadata it returns comes
from media files and network providers.  Keep that data small and structurally
predictable before it is serialized to the long-lived QML process.
"""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Callable, Mapping
from typing import Any, TypeVar


MAX_REQUEST_BYTES = 64 * 1024
MAX_BATCH_REQUEST_BYTES = 256 * 1024
MAX_WIRE_RESPONSE_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_MODEL_BYTES = 192 * 1024

MAX_ERROR_BYTES = 1024
MAX_TEXT_BYTES = 512
MAX_LYRIC_TEXT_BYTES = 2048
MAX_PATH_BYTES = 4096
MAX_IDENTIFIER_BYTES = 512
MAX_PROVIDER_META_ITEMS = 16

MAX_BATCH_REQUESTS = 256
MAX_PROVIDERS = 32
MAX_PLAYLISTS = 256
MAX_TRACKS = 256
MAX_HISTORY_ITEMS = 64
MAX_LYRIC_LINES = 512
MAX_DEVICES = 64

DEFAULT_DEADLINE_SECONDS = 12.0
PROVIDER_DEADLINE_SECONDS = 70.0
MAX_DEADLINE_SECONDS = 75.0

_T = TypeVar("_T")


class ValidationError(ValueError):
    """An IPC request or response violated the supported bounded schema."""


def _utf8_length(value: str) -> int:
    try:
        return len(value.encode("utf-8", "strict"))
    except UnicodeError as error:
        raise ValidationError("string contains invalid Unicode") from error


def _truncate_utf8(value: str, maximum: int) -> str:
    encoded = value.encode("utf-8", "strict")
    if len(encoded) <= maximum:
        return value
    ellipsis = "…".encode("utf-8")
    if maximum < len(ellipsis):
        return encoded[:maximum].decode("utf-8", "ignore")
    prefix = encoded[: maximum - len(ellipsis)].decode("utf-8", "ignore")
    return prefix + "…"


def display_text(value: Any, *, maximum: int = MAX_TEXT_BYTES) -> str:
    """Return NFC display text without controls, bidi marks, or excess bytes."""

    if not isinstance(value, str):
        raise ValidationError("display field must be a string")
    try:
        value = unicodedata.normalize("NFC", value)
    except UnicodeError as error:
        raise ValidationError("display field contains invalid Unicode") from error

    cleaned: list[str] = []
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cs", "Cf"}:
            continue
        if category == "Cc":
            cleaned.append(" ")
        else:
            cleaned.append(character)
    # Each modeled display value is a single line. Lyrics already arrive as a
    # list of lines, so collapsing whitespace here does not remove structure.
    normalized = " ".join("".join(cleaned).split())
    return _truncate_utf8(normalized, maximum)


def error_text(value: Any) -> str:
    if not isinstance(value, str):
        return "CLIAMP request failed"
    result = display_text(value, maximum=MAX_ERROR_BYTES)
    return result or "CLIAMP request failed"


def _opaque_string(
    value: Any,
    *,
    field: str,
    maximum: int = MAX_IDENTIFIER_BYTES,
    allow_empty: bool = False,
) -> str:
    """Validate a semantic string without changing the bytes used by CLIAMP."""

    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    if not value and not allow_empty:
        raise ValidationError(f"{field} must not be empty")
    try:
        if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
            raise ValidationError(f"{field} contains control characters")
    except UnicodeError as error:
        raise ValidationError(f"{field} contains invalid Unicode") from error
    if _utf8_length(value) > maximum:
        raise ValidationError(f"{field} exceeds {maximum} bytes")
    return value


def _path(value: Any, *, field: str = "path") -> str:
    return _opaque_string(value, field=field, maximum=MAX_PATH_BYTES)


def _boolean(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise ValidationError(f"{field} must be a boolean")
    return value


def _integer(
    value: Any,
    *,
    field: str,
    minimum: int = 0,
    maximum: int = 1_000_000_000,
) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValidationError(f"{field} must be an integer from {minimum} to {maximum}")
    return value


def _number(
    value: Any,
    *,
    field: str,
    minimum: float = -1_000_000_000.0,
    maximum: float = 1_000_000_000.0,
) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ValidationError(f"{field} is outside the supported range") from error
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValidationError(f"{field} is outside the supported range")
    return value


def _require_keys(value: Mapping[str, Any], allowed: set[str], *, context: str) -> None:
    unknown = set(value).difference(allowed)
    if unknown:
        raise ValidationError(f"{context} contains unsupported fields")


def _optional_display(
    source: Mapping[str, Any], target: dict[str, Any], field: str, maximum: int = MAX_TEXT_BYTES
) -> None:
    if field in source:
        target[field] = display_text(source[field], maximum=maximum)


def _optional_opaque(
    source: Mapping[str, Any], target: dict[str, Any], field: str, maximum: int = MAX_IDENTIFIER_BYTES
) -> None:
    if field in source:
        target[field] = _opaque_string(
            source[field], field=field, maximum=maximum, allow_empty=True
        )


def _optional_bool(source: Mapping[str, Any], target: dict[str, Any], field: str) -> None:
    if field in source:
        target[field] = _boolean(source[field], field=field)


def _optional_int(
    source: Mapping[str, Any],
    target: dict[str, Any],
    field: str,
    minimum: int = 0,
    maximum: int = 1_000_000_000,
) -> None:
    if field in source:
        target[field] = _integer(source[field], field=field, minimum=minimum, maximum=maximum)


def _optional_number(
    source: Mapping[str, Any],
    target: dict[str, Any],
    field: str,
    minimum: float,
    maximum: float,
) -> None:
    if field in source:
        target[field] = _number(source[field], field=field, minimum=minimum, maximum=maximum)


_TRACK_FIELDS = {
    "title",
    "artist",
    "album",
    "genre",
    "path",
    "album_art_url",
    "year",
    "track_number",
    "duration_secs",
    "index",
    "queue_position",
    "stream",
    "stream_title",
    "station",
    "realtime",
    "feed",
    "bookmark",
    "unplayable",
    "dir_sourced",
    "provider_meta",
}


def normalize_track(value: Any, *, require_path: bool = True) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("track must be an object")
    _require_keys(value, _TRACK_FIELDS, context="track")
    result: dict[str, Any] = {}
    if "path" in value:
        result["path"] = _path(value["path"])
    elif require_path:
        raise ValidationError("track.path is required")

    for field in ("title", "artist", "album", "genre", "stream_title", "station"):
        _optional_display(value, result, field)
    # Album art is never rendered by CLIAMPed, but it may be needed when a
    # provider track is sent back to CLIAMP. Treat it as an opaque path/URL.
    if "album_art_url" in value:
        result["album_art_url"] = _opaque_string(
            value["album_art_url"],
            field="album_art_url",
            maximum=MAX_PATH_BYTES,
            allow_empty=True,
        )
    _optional_int(value, result, "year", 0, 9999)
    _optional_int(value, result, "track_number", 0, 1_000_000)
    _optional_int(value, result, "duration_secs", 0, 315_576_000)
    _optional_int(value, result, "index", -1, 1_000_000_000)
    _optional_int(value, result, "queue_position", 0, 1_000_000_000)
    for field in ("stream", "realtime", "feed", "bookmark", "unplayable", "dir_sourced"):
        _optional_bool(value, result, field)

    if "provider_meta" in value:
        metadata = value["provider_meta"]
        if not isinstance(metadata, dict) or len(metadata) > MAX_PROVIDER_META_ITEMS:
            raise ValidationError("track.provider_meta must be a bounded object")
        normalized_metadata: dict[str, str] = {}
        for key, item in metadata.items():
            normalized_key = _opaque_string(key, field="provider_meta key", maximum=128)
            normalized_metadata[normalized_key] = _opaque_string(
                item,
                field="provider_meta value",
                maximum=MAX_TEXT_BYTES,
                allow_empty=True,
            )
        result["provider_meta"] = normalized_metadata
    return result


def _normalize_provider(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("provider must be an object")
    allowed = {"key", "name", "searchable", "browse_artists", "browse_albums", "catalog"}
    _require_keys(value, allowed, context="provider")
    for required in ("key", "name", "searchable"):
        if required not in value:
            raise ValidationError(f"provider.{required} is required")
    result = {
        "key": _opaque_string(value["key"], field="provider.key", maximum=128),
        "name": display_text(value["name"]),
        "searchable": _boolean(value["searchable"], field="provider.searchable"),
    }
    for field in ("browse_artists", "browse_albums", "catalog"):
        _optional_bool(value, result, field)
    return result


def _normalize_playlist(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("playlist must be an object")
    allowed = {
        "id",
        "name",
        "provider",
        "section",
        "track_count",
        "duration_secs",
        "favoritable",
        "favorite",
    }
    _require_keys(value, allowed, context="playlist")
    for required in ("id", "name", "provider"):
        if required not in value:
            raise ValidationError(f"playlist.{required} is required")
    result = {
        "id": _opaque_string(value["id"], field="playlist.id"),
        "name": display_text(value["name"]),
        "provider": _opaque_string(value["provider"], field="playlist.provider", maximum=128),
    }
    _optional_display(value, result, "section")
    _optional_int(value, result, "track_count", 0, 1_000_000_000)
    _optional_int(value, result, "duration_secs", 0, 315_576_000)
    _optional_bool(value, result, "favoritable")
    _optional_bool(value, result, "favorite")
    return result


def _normalize_history(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("history item must be an object")
    _require_keys(value, {"track", "played_at"}, context="history item")
    if "track" not in value or "played_at" not in value:
        raise ValidationError("history item requires track and played_at")
    return {
        "track": normalize_track(value["track"]),
        "played_at": _opaque_string(value["played_at"], field="played_at", maximum=128),
    }


def _normalize_lyric(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("lyric line must be an object")
    _require_keys(value, {"start", "text"}, context="lyric line")
    if "start" not in value or "text" not in value:
        raise ValidationError("lyric line requires start and text")
    return {
        "start": _number(value["start"], field="lyric.start", minimum=0, maximum=315_576_000),
        "text": display_text(value["text"], maximum=MAX_LYRIC_TEXT_BYTES),
    }


def _normalize_device(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("device must be an object")
    _require_keys(value, {"name", "active"}, context="device")
    if "name" not in value or "active" not in value:
        raise ValidationError("device requires name and active")
    return {
        # Device names are sent back to the daemon as identifiers. Validate
        # them as opaque strings; Text.PlainText handles safe rendering.
        "name": _opaque_string(value["name"], field="device.name", maximum=MAX_TEXT_BYTES),
        "active": _boolean(value["active"], field="device.active"),
    }


def _bounded_model(
    value: Any,
    *,
    field: str,
    maximum: int,
    converter: Callable[[Any], _T],
) -> tuple[list[_T], bool]:
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be an array")
    result: list[_T] = []
    consumed = 2
    truncated = len(value) > maximum
    for item in value[:maximum]:
        normalized = converter(item)
        encoded_size = len(
            json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if consumed + encoded_size + 1 > MAX_MODEL_BYTES:
            truncated = True
            break
        consumed += encoded_size + 1
        result.append(normalized)
    return result, truncated


def _request_track(value: Any) -> dict[str, Any]:
    # Reuse the response track schema so round-tripped provider metadata cannot
    # bypass any field or aggregate boundary.
    return normalize_track(value)


def _copy_command(request: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    _require_keys(request, {"cmd", *allowed}, context="request")
    return {"cmd": request["cmd"]}


_NO_ARGUMENT_COMMANDS = {
    "status",
    "play",
    "pause",
    "toggle",
    "next",
    "prev",
    "stop",
    "queue.clear",
    "lyrics",
    "provider.list",
    "bands",
}


def normalize_request(request: Any) -> dict[str, Any]:
    """Validate and copy a request using CLIAMPed's supported IPC subset."""

    if not isinstance(request, dict):
        raise ValidationError("request must be an object")
    command = _opaque_string(request.get("cmd"), field="cmd", maximum=64)
    if command in _NO_ARGUMENT_COMMANDS:
        return _copy_command(request, set())

    if command in {"volume", "speed", "seek"}:
        result = _copy_command(request, {"value"})
        if "value" not in request:
            raise ValidationError(f"{command} requires value")
        bounds = {
            "volume": (-30.0, 6.0),
            "speed": (0.25, 2.0),
            "seek": (-315_576_000.0, 315_576_000.0),
        }[command]
        result["value"] = _number(request["value"], field="value", minimum=bounds[0], maximum=bounds[1])
        return result

    if command in {"shuffle", "repeat", "mono"}:
        result = _copy_command(request, {"name"})
        if "name" not in request:
            raise ValidationError(f"{command} requires name")
        name = _opaque_string(request["name"], field="name", maximum=16)
        allowed = {
            "shuffle": {"on", "off", "toggle"},
            "mono": {"on", "off", "toggle"},
            "repeat": {"off", "all", "one", "cycle"},
        }[command]
        if name not in allowed:
            raise ValidationError(f"{command}.name is not supported")
        result["name"] = name
        return result

    if command == "vis":
        result = _copy_command(request, {"name"})
        if "name" not in request:
            raise ValidationError("vis requires name")
        result["name"] = _opaque_string(request["name"], field="name", maximum=128)
        return result

    if command == "eq":
        result = _copy_command(request, {"name", "band", "value"})
        has_name = "name" in request
        has_band = "band" in request
        has_value = "value" in request
        if has_name == has_band:
            raise ValidationError("eq requires either a preset name or a band/value pair")
        if has_name:
            if has_value:
                raise ValidationError("eq preset must not include a band value")
            result["name"] = _opaque_string(request["name"], field="name", maximum=128)
        else:
            result["band"] = _integer(request["band"], field="band", minimum=0, maximum=9)
            if not has_value:
                raise ValidationError("eq band requires value")
            result["value"] = _number(request["value"], field="value", minimum=-12, maximum=12)
        return result

    if command == "device":
        result = _copy_command(request, {"name"})
        if "name" not in request:
            raise ValidationError("device requires name")
        result["name"] = _opaque_string(request["name"], field="name", maximum=MAX_TEXT_BYTES)
        return result

    if command in {"track.play", "track.queue"}:
        result = _copy_command(request, {"track"})
        if "track" not in request:
            raise ValidationError(f"{command} requires track")
        result["track"] = _request_track(request["track"])
        return result

    if command in {"queue.play", "queue.enqueue", "queue.remove"}:
        result = _copy_command(request, {"index"})
        if "index" not in request:
            raise ValidationError(f"{command} requires index")
        result["index"] = _integer(request["index"], field="index", maximum=1_000_000_000)
        return result

    if command == "queue.move":
        result = _copy_command(request, {"index", "to"})
        if "index" not in request or "to" not in request:
            raise ValidationError("queue.move requires index and to")
        result["index"] = _integer(request["index"], field="index", maximum=1_000_000_000)
        result["to"] = _integer(request["to"], field="to", maximum=1_000_000_000)
        return result

    if command == "queue.list":
        result = _copy_command(request, {"offset", "limit"})
        if "offset" in request:
            result["offset"] = _integer(request["offset"], field="offset", maximum=1_000_000_000)
        if "limit" in request:
            result["limit"] = _integer(request["limit"], field="limit", minimum=1, maximum=MAX_TRACKS)
        return result

    if command == "history":
        result = _copy_command(request, {"limit"})
        if "limit" in request:
            result["limit"] = _integer(
                request["limit"], field="limit", minimum=1, maximum=MAX_HISTORY_ITEMS
            )
        return result

    if command in {"provider.playlists", "provider.catalog", "provider.search", "provider.load", "provider.favorite"}:
        allowed = {"provider"}
        if command == "provider.catalog":
            allowed.update({"offset", "limit"})
        elif command == "provider.search":
            allowed.update({"query", "limit"})
        elif command in {"provider.load", "provider.favorite"}:
            allowed.add("playlist")
        result = _copy_command(request, allowed)
        if "provider" not in request:
            raise ValidationError(f"{command} requires provider")
        result["provider"] = _opaque_string(request["provider"], field="provider", maximum=128)
        if command == "provider.catalog":
            result["offset"] = _integer(request.get("offset", 0), field="offset", maximum=1_000_000_000)
            result["limit"] = _integer(
                request.get("limit", 18), field="limit", minimum=1, maximum=MAX_PLAYLISTS
            )
        elif command == "provider.search":
            if "query" not in request:
                raise ValidationError("provider.search requires query")
            result["query"] = _opaque_string(
                request["query"], field="query", maximum=MAX_TEXT_BYTES
            )
            result["limit"] = _integer(
                request.get("limit", 18), field="limit", minimum=1, maximum=MAX_TRACKS
            )
        else:
            if "playlist" in allowed:
                if "playlist" not in request:
                    raise ValidationError(f"{command} requires playlist")
                result["playlist"] = _opaque_string(
                    request["playlist"], field="playlist", maximum=MAX_IDENTIFIER_BYTES
                )
        return result

    if command == "url.load":
        result = _copy_command(request, {"path", "play"})
        if "path" not in request:
            raise ValidationError("url.load requires path")
        result["path"] = _path(request["path"])
        if "play" in request:
            result["play"] = _boolean(request["play"], field="play")
        return result

    # Legacy commands remain supported for the bundled control skill and for a
    # serialized file-import batch.
    if command == "queue":
        result = _copy_command(request, {"path"})
        if "path" not in request:
            raise ValidationError("queue requires path")
        result["path"] = _path(request["path"])
        return result
    if command == "load":
        result = _copy_command(request, {"playlist"})
        if "playlist" not in request:
            raise ValidationError("load requires playlist")
        result["playlist"] = _opaque_string(request["playlist"], field="playlist")
        return result

    raise ValidationError(f"unsupported IPC command: {display_text(command, maximum=64)}")


def deadline_seconds_for_request(request: Mapping[str, Any]) -> float:
    command = request.get("cmd")
    if command == "url.load" or (
        isinstance(command, str) and command.startswith("provider.")
    ):
        return PROVIDER_DEADLINE_SECONDS
    return DEFAULT_DEADLINE_SECONDS


def _copy_status_scalars(source: Mapping[str, Any], target: dict[str, Any]) -> None:
    _optional_display(source, target, "state", maximum=32)
    _optional_number(source, target, "position", 0, 315_576_000)
    _optional_number(source, target, "duration", 0, 315_576_000)
    _optional_number(source, target, "volume", -120, 24)
    _optional_display(source, target, "playlist")
    _optional_int(source, target, "index", -1, 1_000_000_000)
    _optional_int(source, target, "total", 0, 1_000_000_000)
    _optional_display(source, target, "visualizer", maximum=128)
    _optional_bool(source, target, "shuffle")
    _optional_display(source, target, "repeat", maximum=32)
    _optional_bool(source, target, "mono")
    _optional_number(source, target, "speed", 0.1, 8)
    _optional_display(source, target, "eq_preset", maximum=128)
    _optional_display(source, target, "device")
    if "eq_bands" in source:
        bands = source["eq_bands"]
        if not isinstance(bands, list) or len(bands) > 10:
            raise ValidationError("eq_bands must have at most 10 values")
        target["eq_bands"] = [
            _number(item, field="eq_bands", minimum=-12, maximum=12) for item in bands
        ]


def _add_model(
    source: Mapping[str, Any],
    target: dict[str, Any],
    field: str,
    maximum: int,
    converter: Callable[[Any], Any],
    *,
    required_output: bool = True,
) -> None:
    if field not in source:
        if required_output:
            target[field] = []
        return
    model, truncated = _bounded_model(
        source[field], field=field, maximum=maximum, converter=converter
    )
    target[field] = model
    if truncated:
        target["truncated"] = True


def normalize_response(
    request: Mapping[str, Any], response: Any, *, session_mode: str = "unknown"
) -> dict[str, Any]:
    """Return the only fields QML may retain for a supported command."""

    if not isinstance(response, dict):
        raise ValidationError("CLIAMP response must be an object")
    if type(response.get("ok")) is not bool:
        raise ValidationError("CLIAMP response requires a boolean ok field")
    if not response["ok"]:
        return {"ok": False, "error": error_text(response.get("error"))}

    command = request["cmd"]
    result: dict[str, Any] = {"ok": True}
    if command == "status":
        _copy_status_scalars(response, result)
        if "track" in response and response["track"] is not None:
            result["track"] = normalize_track(response["track"])
        result["session_mode"] = (
            session_mode if session_mode in {"headless", "tui"} else "unknown"
        )
    elif command == "provider.list":
        _add_model(response, result, "providers", MAX_PROVIDERS, _normalize_provider)
    elif command in {"provider.playlists", "provider.catalog"}:
        _add_model(response, result, "playlists", MAX_PLAYLISTS, _normalize_playlist)
        _optional_int(response, result, "total", 0, 1_000_000_000)
    elif command in {"provider.search", "provider.load", "url.load"}:
        _add_model(response, result, "tracks", MAX_TRACKS, normalize_track)
        _optional_int(response, result, "index", -1, 1_000_000_000)
        _optional_int(response, result, "total", 0, 1_000_000_000)
    elif command in {
        "queue",
        "queue.list",
        "queue.play",
        "queue.enqueue",
        "queue.remove",
        "queue.move",
        "queue.clear",
        "track.play",
        "track.queue",
    }:
        _add_model(
            response,
            result,
            "tracks",
            MAX_TRACKS,
            normalize_track,
            required_output=command == "queue.list",
        )
        _optional_int(response, result, "index", -1, 1_000_000_000)
        _optional_int(response, result, "total", 0, 1_000_000_000)
    elif command == "history":
        _add_model(response, result, "history", MAX_HISTORY_ITEMS, _normalize_history)
    elif command == "lyrics":
        _add_model(response, result, "lyrics", MAX_LYRIC_LINES, _normalize_lyric)
    elif command == "device" and request.get("name") == "list":
        _add_model(response, result, "devices", MAX_DEVICES, _normalize_device)
    elif command == "bands":
        bands = response.get("bands", [])
        if not isinstance(bands, list) or len(bands) != 10:
            raise ValidationError("bands response must contain exactly 10 values")
        result["bands"] = [
            _number(item, field="band", minimum=0, maximum=1) for item in bands
        ]
        _optional_display(response, result, "visualizer", maximum=128)
    else:
        # Mutation replies vary slightly across supported CLIAMP releases. Keep
        # only bounded status scalars that the panel can act on.
        _copy_status_scalars(response, result)

    encoded_size = len(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    if encoded_size > MAX_OUTPUT_BYTES:
        raise ValidationError("normalized response exceeds the output limit")
    return result


def encode_json(value: Any, *, maximum: int = MAX_OUTPUT_BYTES) -> bytes:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > maximum:
        raise ValidationError("JSON output exceeds the configured limit")
    return encoded
