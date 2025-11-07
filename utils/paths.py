import json
import os
import re
from typing import Dict, List, Optional


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
PROVIDER_FILE = os.path.join(DATA_DIR, "providers", "providers.json")
SCHEMA_FILE = os.path.join(DATA_DIR, "schema", "provider_schema.json")
LEGACY_EXPORTS_DIR = os.path.join(DATA_DIR, "xml")

_slug_re = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    if not value:
        return "default"
    return _slug_re.sub("_", value.lower()).strip("_") or "default"


def _slug_to_display(slug: str) -> str:
    if not slug:
        return ""
    return slug.replace("_", " ").title()


def manufacturer_slug(name: str) -> str:
    return _slugify(name)


def console_slug(name: str) -> str:
    return _slugify(name)


def console_cache_dir(manufacturer: str, console: str) -> str:
    return os.path.join(CACHE_DIR, manufacturer_slug(manufacturer), console_slug(console))


def console_dirs(manufacturer: str, console: str, ensure: bool = False) -> dict:
    base = console_cache_dir(manufacturer, console)
    directories = {
        "base": base,
        "metadata": os.path.join(base, "metadata"),
        "listings": os.path.join(base, "listings"),
        "exports": os.path.join(base, "exports"),
        "torrents": os.path.join(base, "torrents"),
    }
    if ensure:
        for path in directories.values():
            os.makedirs(path, exist_ok=True)
    return directories


def path_prefix(manufacturer: str, console: str) -> str:
    return f"{manufacturer_slug(manufacturer)}_{console_slug(console)}"


def metadata_file_path(manufacturer: str, console: str, filename: Optional[str] = None) -> str:
    dirs = console_dirs(manufacturer, console, ensure=True)
    if filename:
        return os.path.join(dirs["metadata"], filename)
    return os.path.join(dirs["metadata"], f"{path_prefix(manufacturer, console)}_meta.sqlite")


def files_xml_path(manufacturer: str, console: str, filename: Optional[str] = None) -> str:
    dirs = console_dirs(manufacturer, console, ensure=True)
    if filename:
        return os.path.join(dirs["listings"], filename)
    return os.path.join(dirs["listings"], f"{path_prefix(manufacturer, console)}_files.xml")


def roms_json_path(manufacturer: str, console: str) -> str:
    dirs = console_dirs(manufacturer, console, ensure=True)
    return os.path.join(dirs["exports"], f"{path_prefix(manufacturer, console)}_roms.json")


def torrent_file_path(manufacturer: str, console: str, filename: Optional[str] = None) -> str:
    dirs = console_dirs(manufacturer, console, ensure=True)
    if filename:
        return os.path.join(dirs["torrents"], filename)
    return os.path.join(dirs["torrents"], f"{path_prefix(manufacturer, console)}_archive.torrent")


def list_cached_consoles() -> List[Dict]:
    results: List[Dict] = []
    if not os.path.isdir(CACHE_DIR):
        return results

    for manufacturer_slug_name in sorted(os.listdir(CACHE_DIR)):
        manufacturer_path = os.path.join(CACHE_DIR, manufacturer_slug_name)
        if not os.path.isdir(manufacturer_path):
            continue

        for console_slug_name in sorted(os.listdir(manufacturer_path)):
            console_path = os.path.join(manufacturer_path, console_slug_name)
            if not os.path.isdir(console_path):
                continue

            exports_dir = os.path.join(console_path, "exports")
            if not os.path.isdir(exports_dir):
                continue

            rom_json = None
            for fname in os.listdir(exports_dir):
                if fname.endswith("_roms.json"):
                    rom_json = os.path.join(exports_dir, fname)
                    break

            if not rom_json:
                continue

            display_manufacturer = _slug_to_display(manufacturer_slug_name)
            display_console = _slug_to_display(console_slug_name)

            rom_count = 0
            try:
                with open(rom_json) as fh:
                    data = json.load(fh)
                if isinstance(data, list) and data:
                    rom_count = len(data)
                    first_entry = data[0]
                    display_manufacturer = first_entry.get("manufacturer", display_manufacturer)
                    display_console = first_entry.get("console", display_console)
                elif isinstance(data, list):
                    rom_count = len(data)
                elif isinstance(data, dict):
                    rom_count = len(data.get("roms", []))
                    meta = data.get("meta", {})
                    display_manufacturer = meta.get("manufacturer", display_manufacturer)
                    display_console = meta.get("console", display_console)
            except Exception:
                pass

            results.append({
                "manufacturer": display_manufacturer,
                "manufacturer_slug": manufacturer_slug_name,
                "console": display_console,
                "console_slug": console_slug_name,
                "roms_path": rom_json,
                "rom_count": rom_count,
            })

    return results


def cache_status(manufacturer: str, console: str) -> Dict[str, object]:
    """Return presence information for cached assets of a console."""
    dirs = console_dirs(manufacturer, console, ensure=False)

    metadata_present = False
    metadata_files: List[str] = []
    metadata_dir = dirs["metadata"]
    if os.path.isdir(metadata_dir):
        metadata_files = [
            os.path.join(metadata_dir, fname)
            for fname in os.listdir(metadata_dir)
            if fname.endswith(".sqlite")
        ]
        metadata_present = bool(metadata_files)

    listings_present = False
    listings_files: List[str] = []
    listings_dir = dirs["listings"]
    if os.path.isdir(listings_dir):
        listings_files = [
            os.path.join(listings_dir, fname)
            for fname in os.listdir(listings_dir)
            if fname.endswith(".xml")
        ]
        listings_present = bool(listings_files)

    torrents_present = False
    torrent_files: List[str] = []
    torrent_dir = dirs["torrents"]
    if os.path.isdir(torrent_dir):
        torrent_files = [
            os.path.join(torrent_dir, fname)
            for fname in os.listdir(torrent_dir)
            if fname.endswith(".torrent")
        ]
        torrents_present = bool(torrent_files)

    exports_dir = dirs["exports"]
    rom_json_path = os.path.join(exports_dir, f"{path_prefix(manufacturer, console)}_roms.json")
    rom_json_present = os.path.isfile(rom_json_path)

    return {
        "metadata": metadata_present,
        "metadata_files": metadata_files,
        "listings": listings_present,
        "listings_files": listings_files,
        "torrent": torrents_present,
        "torrent_files": torrent_files,
        "rom_json": rom_json_present,
        "rom_json_path": rom_json_path,
    }
