import json
import os
import re
from typing import Dict, List, Optional

from data.storage.storage_config_loader import load_storage_config
from utils.library_sync import load_modules, rdb_json_path


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
    """Return activated consoles whose libretro RDB exports exist locally."""

    config = load_storage_config() or {}
    frontends = config.get("frontends", {})
    active_guids: List[str] = []
    for entry in frontends.values():
        if entry.get("active"):
            active_guids.extend([guid for guid in entry.get("supported_guids") or [] if guid])

    if not active_guids:
        return []

    modules = load_modules()
    module_lookup = {module.get("guid"): module for module in modules if module.get("guid")}

    results: List[Dict] = []
    seen: set[str] = set()

    for guid in active_guids:
        if guid in seen:
            continue
        module = module_lookup.get(guid)
        if not module:
            continue
        name = module.get("name") or ""
        parts = [segment.strip() for segment in name.split("-", 1)]
        if len(parts) == 2:
            manufacturer, console = parts
        else:
            if parts:
                manufacturer = parts[0]
                console = parts[-1]
            else:
                manufacturer = console = "Unknown"

        rdb_path = rdb_json_path(name)
        if not rdb_path.exists():
            continue
        try:
            payload = json.loads(rdb_path.read_text())
            rom_count = payload.get("entry_count")
            if rom_count is None and isinstance(payload.get("entries"), list):
                rom_count = len(payload["entries"])
        except Exception:
            rom_count = None

        results.append({
            "manufacturer": manufacturer,
            "manufacturer_slug": manufacturer_slug(manufacturer),
            "console": console,
            "console_slug": console_slug(console),
            "roms_path": str(rdb_path),
            "rom_count": rom_count,
            "guid": guid,
            "module_name": name,
        })
        seen.add(guid)

    results.sort(key=lambda item: (item["manufacturer"].lower(), item["console"].lower()))
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
