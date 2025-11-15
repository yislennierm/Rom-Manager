import json
import os
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, quote

from jsonschema import Draft202012Validator

from utils.paths import (
    PROVIDER_FILE,
    SCHEMA_FILE,
    cache_status,
    console_cache_dir,
    files_xml_path,
    roms_json_path,
)


def load_providers() -> Dict:
    with open(PROVIDER_FILE) as fh:
        return json.load(fh)


def save_providers(providers: Dict) -> None:
    os.makedirs(os.path.dirname(PROVIDER_FILE), exist_ok=True)
    with open(PROVIDER_FILE, "w") as fh:
        json.dump(providers, fh, indent=2)


def resolve_system(console: str, manufacturer: Optional[str] = None, providers: Optional[Dict] = None) -> Tuple[str, Dict]:
    if providers is None:
        providers = load_providers()

    console_root = providers.get("console_root", {})

    if manufacturer:
        systems = console_root.get(manufacturer)
        if not systems or console not in systems:
            raise KeyError(f"Console '{console}' not found under manufacturer '{manufacturer}'.")
        return manufacturer, systems[console]

    for maker, systems in console_root.items():
        if console in systems:
            return maker, systems[console]

    raise KeyError(f"Console '{console}' not found in providers.json.")


def _filename_from_url(url: Optional[str], fallback: str) -> str:
    if not url:
        return fallback
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    return name or fallback


def iter_providers(providers: Optional[Dict] = None):
    if providers is None:
        providers = load_providers()
    console_root = providers.get("console_root", {})
    for manufacturer, systems in console_root.items():
        for console, entry in systems.items():
            yield manufacturer, console, entry


def list_providers_with_status() -> List[Dict[str, object]]:
    providers = load_providers()
    results: List[Dict[str, object]] = []

    for manufacturer, console, entry in iter_providers(providers):
        status = cache_status(manufacturer, console)
        rom_extensions = entry.get("rom_extensions") or []
        results.append({
            "manufacturer": manufacturer,
            "console": console,
            "entry": entry,
            "status": status,
            "rom_extensions": rom_extensions,
        })

    results.sort(key=lambda item: (item["manufacturer"].lower(), item["console"].lower()))
    return results


def validate_providers_schema(providers: Optional[Dict] = None) -> Tuple[bool, List[Dict[str, object]]]:
    if providers is None:
        providers = load_providers()

    with open(SCHEMA_FILE) as fh:
        schema = json.load(fh)

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(providers), key=lambda e: e.path)
    if not errors:
        return True, []

    issues: List[Dict[str, object]] = []
    for err in errors:
        issues.append({
            "path": list(err.path),
            "message": err.message,
            "validator": err.validator,
        })
    return False, issues


def export_roms_to_json(manufacturer: str, console: str, provider_entry: Dict, write: bool = True) -> Tuple[List[Dict], str]:
    files = provider_entry.get("files", {})
    xml_url = files.get("files_xml")
    if not xml_url:
        raise ValueError("Provider entry does not define files_xml; cannot export ROM list.")

    xml_filename = _filename_from_url(xml_url, f"{manufacturer.lower()}_{console.lower()}_files.xml")
    xml_path = files_xml_path(manufacturer, console, xml_filename)
    if not os.path.exists(xml_path):
        raise FileNotFoundError(f"Listing XML not found at {xml_path}. Fetch metadata first.")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    extensions = provider_entry.get("rom_extensions") or []
    if not extensions:
        extensions = [".zip", ".bin", ".sms", ".gg", ".chd", ".gdi"]
    extensions = [ext.lower() for ext in extensions]
    archive_extensions = [".zip", ".7z", ".rar"]

    roms: List[Dict] = []
    torrent_url = files.get("torrent")
    base_url = provider_entry.get("base_url")

    for f in root.findall("file"):
        name = f.get("name")
        if not name:
            continue
        name_lower = name.lower()
        if not any(name_lower.endswith(ext) for ext in extensions):
            if not any(name_lower.endswith(ext) for ext in archive_extensions):
                continue

        roms.append({
            "name": name,
            "size": f.get("size"),
            "md5": f.findtext("md5"),
            "crc32": f.findtext("crc32"),
            "sha1": f.findtext("sha1"),
            "console": console,
            "manufacturer": manufacturer,
            "torrent_url": torrent_url,
            "http_url": urljoin(base_url.rstrip("/") + "/", quote(name)) if base_url else None,
        })

    json_path = roms_json_path(manufacturer, console)
    if write:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        payload = {
            "manufacturer": manufacturer,
            "console": console,
            "libretro_guid": provider_entry.get("libretro_guid") or provider_entry.get("guid"),
            "provider_label": provider_entry.get("provider") or provider_entry.get("name"),
            "archive_id": provider_entry.get("archive_id"),
            "exported_at": datetime.utcnow().isoformat(),
            "roms": roms,
        }
        with open(json_path, "w") as out:
            json.dump(payload, out, indent=2)

    return roms, json_path


def add_provider(
    manufacturer: str,
    console: str,
    entry: Dict,
    overwrite: bool = False,
) -> Dict:
    providers = load_providers()
    console_root = providers.setdefault("console_root", {})
    systems = console_root.setdefault(manufacturer, {})

    if console in systems and not overwrite:
        raise ValueError(f"Provider for {manufacturer}/{console} already exists. Use overwrite=True to replace.")

    systems[console] = entry

    ok, issues = validate_providers_schema(providers)
    if not ok:
        raise ValueError(f"Provider entry invalid: {issues[0]['message']}")

    save_providers(providers)
    return entry


def remove_provider(manufacturer: str, console: str, remove_cache: bool = False) -> Dict:
    providers = load_providers()
    console_root = providers.get("console_root", {})
    systems = console_root.get(manufacturer)
    if not systems or console not in systems:
        raise KeyError(f"Provider {manufacturer}/{console} not found.")

    removed = systems.pop(console)
    if not systems:
        console_root.pop(manufacturer)

    save_providers(providers)

    if remove_cache:
        cache_dir = console_cache_dir(manufacturer, console)
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir)

    return removed


def load_cached_roms() -> List[Dict]:
    """Load ROM entries from all cached providers (combined list)."""
    roms: List[Dict] = []
    for provider in list_providers_with_status():
        status = provider["status"]
        rom_json_path = status.get("rom_json_path")
        if not status.get("rom_json") or not rom_json_path or not os.path.isfile(rom_json_path):
            continue
        try:
            with open(rom_json_path) as fh:
                entries = json.load(fh)
        except Exception:
            continue

        if isinstance(entries, list):
            for entry in entries:
                entry.setdefault("manufacturer", provider["manufacturer"])
                entry.setdefault("console", provider["console"])
                roms.append(entry)
    return roms
