#!/usr/bin/env python3
"""Quick proof-of-concept for inspecting libretro .rdb files.

Given a URL (or local path) to an .rdb file, this script downloads/parses it
using msgpack and prints the first few entries plus summary metadata.

Usage:
    python tools/rdb_poc.py https://example.com/rdb/Sega%20-%20Dreamcast.rdb
"""

from __future__ import annotations

import argparse
import io
import sys
import textwrap
from pathlib import Path
from typing import Iterable

try:
    import msgpack  # type: ignore
except Exception as exc:  # pragma: no cover
    print("msgpack is required. Install with `pip install msgpack`.", file=sys.stderr)
    raise

try:
    import requests
except Exception:  # pragma: no cover
    print("requests is required. Install with `pip install requests`.", file=sys.stderr)
    raise


def fetch_bytes(source: str) -> bytes:
    path = Path(source)
    if path.exists():
        return path.read_bytes()
    response = requests.get(source, timeout=60)
    response.raise_for_status()
    return response.content


def _detect_payload_offset(blob: bytes, max_search: int = 4096) -> int:
    """Heuristically locate the MsgPack payload inside an .rdb file."""

    for offset in range(0, min(max_search, len(blob))):
        view = memoryview(blob)[offset:]
        unpacker = msgpack.Unpacker(io.BytesIO(view), raw=False)
        try:
            first = next(unpacker)
        except Exception:
            continue
        if isinstance(first, dict) and any(k in first for k in ("name", "title", "serial")):
            return offset
    return 0


def iter_records(blob: bytes, offset: int) -> Iterable[dict]:
    unpacker = msgpack.Unpacker(io.BytesIO(blob[offset:]), raw=False)
    for obj in unpacker:
        if isinstance(obj, dict):
            yield obj


def _format_hex(value):
    if isinstance(value, bytes):
        return value.hex()
    return value


def summarize(records: list[dict], limit: int = 5) -> str:
    if not records:
        return "(no entries)"
    lines = []
    for idx, entry in enumerate(records[:limit], 1):
        name = entry.get("name") or entry.get("title") or "Unnamed"
        md5 = _format_hex(entry.get("md5") or entry.get("MD5") or "—")
        crc = _format_hex(entry.get("crc32") or entry.get("CRC") or "—")
        region = entry.get("region") or "—"
        size = entry.get("size") or entry.get("rom_size") or "—"
        serial = _format_hex(entry.get("serial") or entry.get("Serial") or "—")
        developer = entry.get("developer") or entry.get("Developer") or "—"
        publisher = entry.get("publisher") or entry.get("Publisher") or "—"
        release = entry.get("releaseyear") or entry.get("releasedate") or "—"
        lines.append(
            f"{idx}. {name}\n"
            f"    Region: {region}    Size: {size}\n"
            f"    CRC32: {crc}    MD5: {md5}\n"
            f"    Serial: {serial}\n"
            f"    Dev: {developer}    Pub: {publisher}    Release: {release}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect libretro .rdb files")
    parser.add_argument("source", help="URL or local path to the .rdb file")
    parser.add_argument("--limit", type=int, default=5, help="Number of entries to display")
    args = parser.parse_args()

    try:
        blob = fetch_bytes(args.source)
    except Exception as exc:
        print(f"Failed to fetch {args.source}: {exc}", file=sys.stderr)
        return 1

    offset = _detect_payload_offset(blob)
    if offset:
        print(f"Detected MsgPack payload at offset {offset} bytes")
    try:
        records = list(iter_records(blob, offset))
    except Exception as exc:
        print(f"Failed to parse RDB: {exc}", file=sys.stderr)
        return 1

    print(f"Loaded {len(records)} entries from {args.source}")
    print("\n" + textwrap.indent(summarize(records, args.limit), prefix="  "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
