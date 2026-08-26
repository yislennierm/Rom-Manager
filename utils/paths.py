import json
import os
import re
from typing import Dict, List, Optional

from data.storage.storage_config_loader import load_storage_config
from utils.library_sync import load_modules, rdb_json_path


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_ROOT_OVERRIDE = os.environ.get("ROMS_MANAGER_DATA_ROOT")
if DATA_ROOT_OVERRIDE:
    DATA_DIR = os.path.abspath(DATA_ROOT_OVERRIDE)
else:
    DATA_DIR = os.path.join(PROJECT_ROOT, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
PROVIDER_FILE = os.path.join(DATA_DIR, "providers", "providers.json")
SCHEMA_FILE = os.path.join(DATA_DIR, "schema", "provider_schema.json")
if not os.path.exists(SCHEMA_FILE):
    fallback_schema = os.path.join(PROJECT_ROOT, "data", "schema", "provider_schema.json")
    if os.path.exists(fallback_schema):
        SCHEMA_FILE = fallback_schema
LEGACY_EXPORTS_DIR = os.path.join(DATA_DIR, "xml")

_slug_re = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    if not value:
        return "default"
    return _slug_re.sub("_", value.lower()).strip("_") or "default"


def slugify(value: str) -> str:
    return _slugify(value)


def _slug_to_display(slug: str) -> str:
    if not slug:
        return ""
    return slug.replace("_", " ").title()


def manufacturer_slug(name: str) -> str:
    return _slugify(name)


def console_slug(name: str) -> str:
    return _slugify(name)


def provider_slug(value: str | None) -> str | None:
    if not value:
        return None
    return _slugify(value)


def console_cache_dir(manufacturer: str, console: str, provider: str | None = None) -> str:
    base = os.path.join(CACHE_DIR, manufacturer_slug(manufacturer), console_slug(console))
    if provider:
        base = os.path.join(base, _slugify(provider))
    return base


def console_dirs(
    manufacturer: str,
    console: str,
    provider: str | None = None,
    ensure: bool = False,
) -> dict:
    base = console_cache_dir(manufacturer, console, provider)
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


def path_prefix(manufacturer: str, console: str, provider: str | None = None) -> str:
    base = f"{manufacturer_slug(manufacturer)}_{console_slug(console)}"
    if provider:
        base = f"{base}_{_slugify(provider)}"
    return base


def metadata_file_path(
    manufacturer: str,
    console: str,
    filename: Optional[str] = None,
    provider: str | None = None,
) -> str:
    dirs = console_dirs(manufacturer, console, provider, ensure=True)
    if filename:
        return os.path.join(dirs["metadata"], filename)
    return os.path.join(dirs["metadata"], f"{path_prefix(manufacturer, console, provider)}_meta.sqlite")


def files_xml_path(
    manufacturer: str,
    console: str,
    filename: Optional[str] = None,
    provider: str | None = None,
) -> str:
    dirs = console_dirs(manufacturer, console, provider, ensure=True)
    if filename:
        return os.path.join(dirs["listings"], filename)
    return os.path.join(dirs["listings"], f"{path_prefix(manufacturer, console, provider)}_files.xml")


def roms_json_path(manufacturer: str, console: str, provider: str | None = None) -> str:
    dirs = console_dirs(manufacturer, console, provider, ensure=True)
    return os.path.join(dirs["exports"], f"{path_prefix(manufacturer, console, provider)}_roms.json")


def torrent_file_path(
    manufacturer: str,
    console: str,
    filename: Optional[str] = None,
    provider: str | None = None,
) -> str:
    dirs = console_dirs(manufacturer, console, provider, ensure=True)
    if filename:
        return os.path.join(dirs["torrents"], filename)
    return os.path.join(dirs["torrents"], f"{path_prefix(manufacturer, console, provider)}_archive.torrent")


def _client_sync_state() -> Dict:
    state_path = os.path.join(DATA_DIR, "client", "sync_state.json")
    if not os.path.exists(state_path):
        return {}
    try:
        with open(state_path, encoding="utf-8") as handle:
            payload = json.loads(handle.read())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _assigned_console_state() -> Dict[str, Dict]:
    assigned = _client_sync_state().get("assigned")
    return assigned if isinstance(assigned, dict) else {}


def _revoked_console_guids() -> set[str]:
    revoked = _client_sync_state().get("revoked")
    if not isinstance(revoked, dict):
        return set()
    return {guid for guid in revoked.keys() if guid}


def list_cached_consoles() -> List[Dict]:
    """Return locally synced library consoles whose libretro RDB exports exist.

    Backend-assigned consoles are the primary TUI library. Active frontend
    consoles remain included for local/standalone use and are marked separately.
    """

    config = load_storage_config() or {}
    frontends = config.get("frontends", {})
    active_guids: List[str] = []
    for entry in frontends.values():
        if entry.get("active"):
            active_guids.extend([guid for guid in entry.get("supported_guids") or [] if guid])

    assigned = _assigned_console_state()
    revoked_guids = _revoked_console_guids()
    assigned_guids = [guid for guid in assigned.keys() if guid]
    active_available_guids = [guid for guid in active_guids if guid not in revoked_guids]
    candidate_guids = list(dict.fromkeys([*assigned_guids, *active_available_guids]))
    if not candidate_guids:
        return []

    modules = load_modules()
    module_lookup = {module.get("guid"): module for module in modules if module.get("guid")}

    results: List[Dict] = []
    seen: set[str] = set()

    for guid in candidate_guids:
        if guid in seen:
            continue
        assigned_entry = assigned.get(guid) or {}
        module = module_lookup.get(guid) or {}
        name = assigned_entry.get("module") or module.get("name") or ""
        manufacturer = assigned_entry.get("manufacturer")
        console = assigned_entry.get("console")
        if not manufacturer or not console:
            parts = [segment.strip() for segment in name.split(" - ", 1)]
            if len(parts) == 2:
                manufacturer, console = parts
            elif parts:
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
            "assigned": guid in assigned,
            "frontend_active": guid in active_guids,
        })
        seen.add(guid)

    results.sort(key=lambda item: (item["manufacturer"].lower(), item["console"].lower()))
    return results


def cache_status(manufacturer: str, console: str, provider: str | None = None) -> Dict[str, object]:
    """Return presence information for cached assets of a console or provider."""
    dirs = console_dirs(manufacturer, console, provider, ensure=False)
    legacy_dirs = None

    def _dir(path_key: str) -> str:
        path = dirs[path_key]
        if provider and not os.path.exists(dirs["base"]):
            nonlocal legacy_dirs
            if legacy_dirs is None:
                legacy_dirs = console_dirs(manufacturer, console, None, ensure=False)
            return legacy_dirs[path_key]
        return path

    metadata_present = False
    metadata_files: List[str] = []
    metadata_dir = _dir("metadata")
    if os.path.isdir(metadata_dir):
        metadata_files = [
            os.path.join(metadata_dir, fname)
            for fname in os.listdir(metadata_dir)
            if fname.endswith(".sqlite")
        ]
        metadata_present = bool(metadata_files)

    listings_present = False
    listings_files: List[str] = []
    listings_dir = _dir("listings")
    if os.path.isdir(listings_dir):
        listings_files = [
            os.path.join(listings_dir, fname)
            for fname in os.listdir(listings_dir)
            if fname.endswith(".xml")
        ]
        listings_present = bool(listings_files)

    torrents_present = False
    torrent_files: List[str] = []
    torrent_dir = _dir("torrents")
    if os.path.isdir(torrent_dir):
        torrent_files = [
            os.path.join(torrent_dir, fname)
            for fname in os.listdir(torrent_dir)
            if fname.endswith(".torrent")
        ]
        torrents_present = bool(torrent_files)

    exports_dir = _dir("exports")
    rom_json_path = os.path.join(
        exports_dir,
        f"{path_prefix(manufacturer, console, provider)}_roms.json",
    )
    if provider and not os.path.isfile(rom_json_path):
        legacy_path = os.path.join(
            console_dirs(manufacturer, console, None, ensure=False)["exports"],
            f"{path_prefix(manufacturer, console)}_roms.json",
        )
        rom_json_path = legacy_path
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
