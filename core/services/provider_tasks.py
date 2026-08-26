"""Provider-related task helpers shared across CLI/TUI and backend."""

from __future__ import annotations

import os
import time
import urllib.request
from typing import Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

import requests

from core.providers import (
    entry_provider_slug,
    export_roms_to_json,
    load_providers,
    resolve_system,
    validate_providers_schema,
)
from utils.paths import (
    console_dirs,
    files_xml_path,
    metadata_file_path,
    path_prefix,
    torrent_file_path,
)


def fetch_console_metadata(
    console: str,
    manufacturer: Optional[str],
    provider_slug: Optional[str] = None,
    force: bool = False,
) -> Dict[str, str]:
    """Download metadata assets for a specific provider entry."""
    providers = load_providers()
    manufacturer_key, system = resolve_system(console, manufacturer, providers, provider_slug)
    slug_value = provider_slug or entry_provider_slug(system)

    files = system.get("files", {})
    if "files_xml" not in files:
        raise RuntimeError(f"Provider entry for {manufacturer_key} {console} lacks a files_xml URL.")

    console_dirs(manufacturer_key, console, slug_value, ensure=True)
    prefix = path_prefix(manufacturer_key, console, slug_value)

    meta_url = files.get("meta_sqlite")
    meta_filename = _filename_from_url(meta_url, f"{prefix}_meta.sqlite") if meta_url else None
    meta_path = metadata_file_path(manufacturer_key, console, meta_filename, slug_value) if meta_filename else None

    files_xml_url = files.get("files_xml")
    xml_filename = _filename_from_url(files_xml_url, f"{prefix}_files.xml") if files_xml_url else None
    xml_path = files_xml_path(manufacturer_key, console, xml_filename, slug_value) if xml_filename else None

    torrent_url = files.get("torrent")
    torrent_filename = _filename_from_url(torrent_url, f"{prefix}_archive.torrent") if torrent_url else None
    torrent_path = torrent_file_path(manufacturer_key, console, torrent_filename, slug_value) if torrent_filename else None

    summary: Dict[str, str] = {}

    if meta_url and meta_path:
        _download(meta_url, meta_path, f"{console} metadata DB", force=force)
        summary["meta_sqlite"] = meta_path

    if files_xml_url and xml_path:
        try:
            _download(files_xml_url, xml_path, f"{console} file listing XML", force=force)
            summary["files_xml"] = xml_path
        except Exception as err:
            print(f"⚠️ Skipped XML download due to error: {err}")

    if torrent_url and torrent_path:
        try:
            _download(torrent_url, torrent_path, f"{console} torrent", force=force)
            if not _looks_like_torrent(torrent_path):
                if os.path.exists(torrent_path):
                    os.remove(torrent_path)
                raise RuntimeError("Downloaded torrent metadata is not a valid .torrent file.")
            summary["torrent"] = torrent_path
        except Exception as err:
            print(f"⚠️ Skipped torrent download due to error: {err}")

    return summary


def export_console_roms(
    console: str,
    manufacturer: Optional[str],
    provider_slug: Optional[str] = None,
    write: bool = True,
) -> Tuple[list[Dict], str]:
    """Export ROM listings for a provider to JSON."""
    providers = load_providers()
    manufacturer_key, entry = resolve_system(console, manufacturer, providers, provider_slug)
    slug_value = provider_slug or entry_provider_slug(entry)
    roms, json_path = export_roms_to_json(
        manufacturer_key,
        console,
        entry,
        provider_slug=slug_value,
        write=write,
    )
    return roms, json_path


def validate_providers() -> Tuple[bool, list[Dict[str, object]]]:
    """Validate providers.json against its schema."""
    return validate_providers_schema()


def _filename_from_url(url: Optional[str], fallback: str) -> str:
    if not url:
        return fallback
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    return name or fallback


def _looks_like_torrent(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            prefix = fh.read(1)
    except OSError:
        return False
    return prefix == b"d"


def _download(url: str, destination: str, label: str, force: bool = False) -> None:
    if os.path.exists(destination) and not force:
        print(f"✅ {label} already exists: {destination}")
        return

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    print(f"⬇️ Downloading {label} …")
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "ROMs-Manager/1.0"},
                timeout=60,
                stream=True,
            )
            response.raise_for_status()
            with open(destination, "wb") as out:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        out.write(chunk)
            print(f"✅ Saved to {destination}")
            return
        except requests.RequestException as err:
            last_error = err
            if os.path.exists(destination):
                os.remove(destination)
            if attempt < 3:
                time.sleep(1.5 * attempt)

    try:
        urllib.request.urlretrieve(url, destination)
        print(f"✅ Saved to {destination}")
        return
    except (HTTPError, URLError) as err:
        if os.path.exists(destination):
            os.remove(destination)
        last_error = err

    print(f"❌ Failed to download {label}: {last_error}")
    raise RuntimeError(f"Failed to download {label}: {last_error}") from last_error
