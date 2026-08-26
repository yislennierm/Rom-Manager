import json
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
import hashlib
import zipfile
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, quote
import urllib.request

from jsonschema import Draft202012Validator

from utils.paths import (
    PROVIDER_FILE,
    SCHEMA_FILE,
    cache_status,
    console_cache_dir,
    files_xml_path,
    roms_json_path,
    slugify,
)


def _provider_identifier(entry: Dict) -> str:
    return entry.get("archive_id") or entry.get("provider") or entry.get("name") or "default"


def entry_provider_slug(entry: Dict) -> str:
    return slugify(_provider_identifier(entry))


def provider_label(entry: Dict) -> str:
    return entry.get("provider") or entry.get("name") or entry.get("archive_id") or "Provider"


def load_providers() -> Dict:
    with open(PROVIDER_FILE) as fh:
        return json.load(fh)


def save_providers(providers: Dict) -> None:
    os.makedirs(os.path.dirname(PROVIDER_FILE), exist_ok=True)
    with open(PROVIDER_FILE, "w") as fh:
        json.dump(providers, fh, indent=2)


def _select_provider_entry(entry, provider_id: Optional[str]) -> Dict:
    if isinstance(entry, list):
        if provider_id:
            target = slugify(provider_id)
            for variant in entry:
                if not isinstance(variant, dict):
                    continue
                slug = entry_provider_slug(variant)
                archive = variant.get("archive_id")
                if slug == target or (archive and slugify(archive) == target):
                    return variant
            raise KeyError(f"Provider variant '{provider_id}' not found.")
        # default to first entry if not specified
        for variant in entry:
            if isinstance(variant, dict):
                return variant
        raise KeyError("Provider entry list is empty.")
    if isinstance(entry, dict):
        return entry
    raise KeyError("Invalid provider entry.")


def resolve_system(
    console: str,
    manufacturer: Optional[str] = None,
    providers: Optional[Dict] = None,
    provider_id: Optional[str] = None,
) -> Tuple[str, Dict]:
    if providers is None:
        providers = load_providers()

    console_root = providers.get("console_root", {})

    if manufacturer:
        systems = console_root.get(manufacturer)
        if not systems or console not in systems:
            raise KeyError(f"Console '{console}' not found under manufacturer '{manufacturer}'.")
        entry = systems[console]
        return manufacturer, _select_provider_entry(entry, provider_id)

    for maker, systems in console_root.items():
        if console in systems:
            entry = systems[console]
            return maker, _select_provider_entry(entry, provider_id)

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
            if isinstance(entry, list):
                for variant in entry:
                    if isinstance(variant, dict):
                        yield manufacturer, console, variant
            else:
                yield manufacturer, console, entry


def list_providers_with_status() -> List[Dict[str, object]]:
    providers = load_providers()
    results: List[Dict[str, object]] = []

    for manufacturer, console, entry in iter_providers(providers):
        slug = entry_provider_slug(entry)
        status = cache_status(manufacturer, console, slug)
        rom_extensions = entry.get("rom_extensions") or []
        results.append({
            "manufacturer": manufacturer,
            "console": console,
            "entry": entry,
            "provider_slug": slug,
            "provider_label": provider_label(entry),
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


def export_roms_to_json(
    manufacturer: str,
    console: str,
    provider_entry: Dict,
    provider_slug: Optional[str] = None,
    write: bool = True,
) -> Tuple[List[Dict], str]:
    files = provider_entry.get("files", {})
    xml_url = files.get("files_xml")
    if not xml_url:
        raise ValueError("Provider entry does not define files_xml; cannot export ROM list.")

    slug_value = provider_slug or entry_provider_slug(provider_entry)
    xml_filename = _filename_from_url(xml_url, f"{manufacturer.lower()}_{console.lower()}_files.xml")
    xml_path = files_xml_path(manufacturer, console, xml_filename, slug_value)
    if not os.path.exists(xml_path):
        # fall back to legacy path if present
        legacy_xml_path = files_xml_path(manufacturer, console, xml_filename, None)
        if not os.path.exists(legacy_xml_path):
            raise FileNotFoundError(f"Listing XML not found at {xml_path}. Fetch metadata first.")
        xml_path = legacy_xml_path

    tree = ET.parse(xml_path)
    root = tree.getroot()

    extensions = provider_entry.get("rom_extensions") or []
    if not extensions:
        extensions = [".zip", ".bin", ".sms", ".gg", ".chd", ".gdi"]
    extensions = [ext.lower() for ext in extensions]
    archive_extensions = [".zip", ".7z", ".rar"]
    path_prefixes = [
        prefix.lower()
        for prefix in (provider_entry.get("path_prefixes") or [])
        if isinstance(prefix, str) and prefix
    ]

    roms: List[Dict] = []
    torrent_url = files.get("torrent")
    base_url = provider_entry.get("base_url")
    archive_cache_dir = os.path.join(console_cache_dir(manufacturer, console, slug_value), "archives")

    for f in root.findall("file"):
        name = f.get("name")
        if not name:
            continue
        name_lower = name.lower()
        if path_prefixes and not any(name_lower.startswith(prefix) for prefix in path_prefixes):
            continue
        if not any(name_lower.endswith(ext) for ext in extensions):
            if not any(name_lower.endswith(ext) for ext in archive_extensions):
                continue

        bundle_url = urljoin(base_url.rstrip("/") + "/", quote(name)) if base_url else None
        rom = {
            "name": name,
            "size": f.get("size") or f.findtext("size"),
            "md5": f.findtext("md5"),
            "crc32": f.findtext("crc32"),
            "sha1": f.findtext("sha1"),
            "console": console,
            "manufacturer": manufacturer,
            "torrent_url": torrent_url,
            "http_url": bundle_url,
        }
        roms.append(rom)
        if provider_entry.get("expand_archives") and bundle_url and _is_archive_name(name):
            roms.extend(
                _expand_archive_members(
                    archive_name=name,
                    archive_url=bundle_url,
                    archive_cache_dir=archive_cache_dir,
                    manufacturer=manufacturer,
                    console=console,
                    torrent_url=torrent_url,
                    allowed_extensions=extensions,
                    expected_archive_size=_coerce_int(f.get("size") or f.findtext("size")),
                    max_bytes=int(provider_entry.get("expand_archives_max_bytes") or 128 * 1024 * 1024),
                )
            )

    json_path = roms_json_path(manufacturer, console, slug_value) if slug_value else roms_json_path(manufacturer, console)

    if write:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        payload = {
            "manufacturer": manufacturer,
            "console": console,
            "libretro_guid": provider_entry.get("libretro_guid") or provider_entry.get("guid"),
            "provider_label": provider_entry.get("provider") or provider_entry.get("name"),
            "archive_id": provider_entry.get("archive_id"),
            "arcade_family": provider_entry.get("arcade_family"),
            "romset_version": provider_entry.get("romset_version"),
            "preferred_cores": provider_entry.get("preferred_cores"),
            "compatible_cores": provider_entry.get("compatible_cores"),
            "zip_preserve": provider_entry.get("zip_preserve"),
            "compatibility_notes": provider_entry.get("compatibility_notes"),
            "exported_at": datetime.utcnow().isoformat(),
            "roms": roms,
        }
        with open(json_path, "w") as out:
            json.dump(payload, out, indent=2)

    return roms, json_path


def _is_archive_name(name: str) -> bool:
    return os.path.splitext(name.lower())[1] in {".zip", ".7z", ".rar"}


def _expand_archive_members(
    *,
    archive_name: str,
    archive_url: str,
    archive_cache_dir: str,
    manufacturer: str,
    console: str,
    torrent_url: Optional[str],
    allowed_extensions: List[str],
    expected_archive_size: Optional[int],
    max_bytes: int,
) -> List[Dict]:
    archive_path = _download_archive_for_indexing(
        archive_name=archive_name,
        archive_url=archive_url,
        archive_cache_dir=archive_cache_dir,
        expected_size=expected_archive_size,
        max_bytes=max_bytes,
    )
    if not archive_path:
        return []

    members: List[Dict] = []
    with tempfile.TemporaryDirectory(prefix="roms-manager-provider-archive-") as temp_dir:
        extract_root = os.path.join(temp_dir, "extract")
        os.makedirs(extract_root, exist_ok=True)
        if not _extract_archive(archive_path, extract_root):
            return []
        _expand_nested_archives(extract_root)
        for root, _, files in os.walk(extract_root):
            for filename in sorted(files):
                path = os.path.join(root, filename)
                rel = os.path.relpath(path, extract_root)
                if not _is_exportable_rom(rel, allowed_extensions):
                    continue
                members.append(
                    _archive_member_record(
                        path=path,
                        relative_name=rel,
                        archive_name=archive_name,
                        archive_path=archive_path,
                        archive_url=archive_url,
                        manufacturer=manufacturer,
                        console=console,
                        torrent_url=torrent_url,
                    )
                )
    return members


def _download_archive_for_indexing(
    *,
    archive_name: str,
    archive_url: str,
    archive_cache_dir: str,
    expected_size: Optional[int],
    max_bytes: int,
) -> Optional[str]:
    os.makedirs(archive_cache_dir, exist_ok=True)
    target = os.path.join(archive_cache_dir, os.path.basename(archive_name))
    if os.path.exists(target) and os.path.getsize(target) > 0:
        if not expected_size or os.path.getsize(target) == expected_size:
            return target

    try:
        request = urllib.request.Request(archive_url, headers={"User-Agent": "ROMs-Manager/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response:
            total = int(response.headers.get("Content-Length") or 0)
            expected_total = expected_size or total
            if total and total > max_bytes:
                return None
            temp_target = f"{target}.tmp"
            downloaded = 0
            with open(temp_target, "wb") as out:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        return None
                    out.write(chunk)
            if expected_total and os.path.getsize(temp_target) != expected_total:
                return None
            os.replace(temp_target, target)
        return target
    except Exception:
        return None


def _coerce_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_archive(source: str, target: str) -> bool:
    suffix = os.path.splitext(source.lower())[1]
    if suffix == ".zip" and zipfile.is_zipfile(source):
        try:
            with zipfile.ZipFile(source) as archive:
                archive.extractall(target)
            return True
        except Exception:
            pass
    if suffix in {".zip", ".7z", ".rar"}:
        result = subprocess.run(
            ["7z", "x", f"-o{target}", source, "-y"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        return result.returncode == 0
    return False


def _expand_nested_archives(root: str) -> None:
    for _ in range(4):
        nested = []
        for walk_root, _, files in os.walk(root):
            for filename in files:
                path = os.path.join(walk_root, filename)
                if _is_archive_name(path):
                    nested.append(path)
        if not nested:
            return
        for path in nested:
            target = os.path.join(os.path.dirname(path), os.path.splitext(os.path.basename(path))[0])
            os.makedirs(target, exist_ok=True)
            if _extract_archive(path, target):
                try:
                    os.remove(path)
                except OSError:
                    pass


def _is_exportable_rom(name: str, allowed_extensions: List[str]) -> bool:
    suffix = os.path.splitext(name.lower())[1]
    if suffix in {".txt", ".png", ".jpg", ".jpeg", ".xml", ".sqlite"}:
        return False
    if allowed_extensions and suffix in allowed_extensions:
        return True
    return suffix in {".sgx", ".pce", ".bin", ".rom", ".zip"}


def _archive_member_record(
    *,
    path: str,
    relative_name: str,
    archive_name: str,
    archive_path: str,
    archive_url: str,
    manufacturer: str,
    console: str,
    torrent_url: Optional[str],
) -> Dict:
    payload = _read_bytes(path)
    return {
        "name": relative_name,
        "size": len(payload),
        "md5": hashlib.md5(payload).hexdigest(),
        "crc32": f"{_crc32(payload):08x}",
        "sha1": hashlib.sha1(payload).hexdigest(),
        "console": console,
        "manufacturer": manufacturer,
        "torrent_url": torrent_url,
        "http_url": archive_url,
        "_archive_member": True,
        "_archive_member_path": relative_name,
        "_source_bundle": archive_name,
        "_source_bundle_size": os.path.getsize(archive_path) if os.path.exists(archive_path) else None,
    }


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _crc32(payload: bytes) -> int:
    import zlib

    return zlib.crc32(payload) & 0xFFFFFFFF


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


def remove_provider(
    manufacturer: str,
    console: str,
    provider_slug: Optional[str] = None,
    remove_cache: bool = False,
) -> Dict:
    providers = load_providers()
    console_root = providers.get("console_root", {})
    systems = console_root.get(manufacturer)
    if not systems or console not in systems:
        raise KeyError(f"Provider {manufacturer}/{console} not found.")

    entry = systems[console]
    removed_entry = None
    if isinstance(entry, list):
        variants = [variant for variant in entry if isinstance(variant, dict)]
        if not variants:
            raise ValueError(f"No provider entries found for {manufacturer}/{console}.")
        if provider_slug:
            target_slug = slugify(provider_slug)
        elif len(variants) == 1:
            provider_slug = entry_provider_slug(variants[0])
            target_slug = provider_slug
        else:
            raise ValueError("Multiple providers configured; specify provider_slug to remove a specific entry.")
        remaining = []
        for variant in entry:
            if entry_provider_slug(variant) == target_slug and removed_entry is None:
                removed_entry = variant
            else:
                remaining.append(variant)
        if removed_entry is None:
            raise KeyError(f"Provider variant '{provider_slug}' not found for {manufacturer}/{console}.")
        if not remaining:
            systems.pop(console)
        elif len(remaining) == 1:
            systems[console] = remaining[0]
        else:
            systems[console] = remaining
    else:
        removed_entry = entry
        systems.pop(console)

    if not systems:
        console_root.pop(manufacturer)

    save_providers(providers)

    if remove_cache:
        slug_value = provider_slug or entry_provider_slug(removed_entry)
        cache_dir = console_cache_dir(manufacturer, console, slug_value)
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir)

    return removed_entry


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
