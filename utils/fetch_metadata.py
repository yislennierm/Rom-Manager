#!/usr/bin/env python3
import argparse
import os
import sys
import urllib.request
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from core.providers import load_providers, resolve_system
from utils.paths import (
    console_dirs,
    files_xml_path,
    metadata_file_path,
    path_prefix,
    torrent_file_path,
)


def _filename_from_url(url: Optional[str], fallback: str) -> str:
    if not url:
        return fallback
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    return name or fallback


def _download(url: str, destination: str, label: str, force: bool = False) -> bool:
    if os.path.exists(destination) and not force:
        print(f"✅ {label} already exists: {destination}")
        return False

    try:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        print(f"⬇️ Downloading {label} …")
        urllib.request.urlretrieve(url, destination)
        print(f"✅ Saved to {destination}")
        return True
    except (HTTPError, URLError) as err:
        if os.path.exists(destination):
            os.remove(destination)
        print(f"❌ Failed to download {label}: {err}")
        raise


def fetch_console_metadata(console: str, manufacturer: Optional[str], force: bool = False) -> Dict[str, str]:
    providers = load_providers()
    manufacturer_key, system = resolve_system(console, manufacturer, providers)

    files = system.get("files", {})
    if "meta_sqlite" not in files:
        raise RuntimeError(f"Provider entry for {manufacturer_key} {console} lacks a meta_sqlite URL.")

    console_dirs(manufacturer_key, console, ensure=True)
    prefix = path_prefix(manufacturer_key, console)

    meta_url = files.get("meta_sqlite")
    meta_filename = _filename_from_url(meta_url, f"{prefix}_meta.sqlite")
    meta_path = metadata_file_path(manufacturer_key, console, meta_filename)

    files_xml_url = files.get("files_xml")
    xml_filename = _filename_from_url(files_xml_url, f"{prefix}_files.xml") if files_xml_url else None
    xml_path = files_xml_path(manufacturer_key, console, xml_filename) if xml_filename else None

    torrent_url = files.get("torrent")
    torrent_filename = _filename_from_url(torrent_url, f"{prefix}_archive.torrent") if torrent_url else None
    torrent_path = torrent_file_path(manufacturer_key, console, torrent_filename) if torrent_filename else None

    summary = {"meta_sqlite": meta_path}

    try:
        _download(meta_url, meta_path, f"{console} metadata DB", force=force)
    except Exception as err:
        raise RuntimeError(f"Failed to download metadata DB: {err}") from err

    if files_xml_url and xml_path:
        try:
            _download(files_xml_url, xml_path, f"{console} file listing XML", force=force)
            summary["files_xml"] = xml_path
        except Exception as err:
            print(f"⚠️ Skipped XML download due to error: {err}")

    if torrent_url and torrent_path:
        try:
            _download(torrent_url, torrent_path, f"{console} torrent", force=force)
            summary["torrent"] = torrent_path
        except Exception as err:
            print(f"⚠️ Skipped torrent download due to error: {err}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Fetch provider metadata assets for a console.")
    parser.add_argument("--console", default="Dreamcast", help="Console name to fetch")
    parser.add_argument("--manufacturer", help="Manufacturer key (omit to auto-detect)")
    parser.add_argument("--force", action="store_true", help="Redownload assets even if they exist")

    args = parser.parse_args()

    try:
        result = fetch_console_metadata(args.console, args.manufacturer, force=args.force)
    except Exception as exc:
        print(f"❌ Fetch failed: {exc}")
        sys.exit(1)

    print("\n📦 Cached assets:")
    for key, value in result.items():
        print(f"  - {key}: {value}")


if __name__ == "__main__":
    main()
