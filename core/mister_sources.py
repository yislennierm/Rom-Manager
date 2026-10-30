"""Portable MiSTer source validation and mappings, shared by server and client."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from core.client_downloads import DownloadError
from utils.catalog import _provider_download_score, _provider_runtime_playable, provider_downloadable, _release_qualifiers
from core.download_preparation import zip_member_source, archive_rom_source

SNES_GUID = "bf3926b2-106d-56c9-8166-0fe29c33bad0"
NES_GUID = "fad2a671-5d13-54b9-8562-3b058c610348"
GENESIS_GUID = "126fc9a7-f783-5a73-9c17-b1e497cc8edd"
SYSTEMS = {
    SNES_GUID: {"id": "snes", "core": "SNES", "folder": "SNES", "extensions": (".sfc", ".smc", ".bin")},
    NES_GUID: {"id": "nes", "core": "NES", "folder": "NES", "extensions": (".nes",)},
    GENESIS_GUID: {"id": "genesis", "core": "MegaDrive", "folder": "MegaDrive", "name": "Mega Drive / Genesis", "extensions": (".md", ".bin", ".gen")},
}
SYSTEM_GUIDS = {mapping['id']: guid for guid, mapping in SYSTEMS.items()}
MAX_ROM_SIZE = 64 * 1024 * 1024
LEASE_SECONDS = 120


class MisterError(DownloadError):
    pass


def entry_key(entry):
    """Stable across UI enrichment, independent of display ordering."""
    fields = {key: entry.get(key) for key in ("_key", "name", "rom_name", "md5", "sha1", "crc32", "http_url")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def prepare_rom(entry, guid):
    mapping = SYSTEMS.get(str(guid).lower())
    if mapping is None:
        raise MisterError("No MiSTer installation mapping for this console.")
    providers = [p for p in (entry.get("_providers") or []) if isinstance(p, dict) and isinstance(p.get("rom"), dict) and _provider_runtime_playable(p) and provider_downloadable(p)
                 and (not _release_qualifiers((p.get("rom") or {}).get("name"))
                      or _release_qualifiers(entry.get("name")) == _release_qualifiers((p.get("rom") or {}).get("name")))]
    candidates = [p.get("rom", {}) for p in sorted(providers, key=_provider_download_score)]
    # A catalog entry's checksum may describe a different archive/member. Never
    # borrow it to validate a provider's file.
    if not candidates and not entry.get("_providers"):
        candidates = [entry]
    archive_fallbacks = []
    for source in candidates:
        if not isinstance(source, dict):
            continue
        if source.get('_archive_member') or source.get('_archive_member_path') or source.get('archive_member_path'):
            try:
                preparation = zip_member_source(source, mapping['extensions'])
                name = preparation['member'].rsplit('/', 1)[-1]
                safe_name = re.sub(r'[\x00-\x1f<>:"|?*]', '_', name).strip(' .')
                if not safe_name or len(safe_name.encode()) > 200:
                    continue
                return {'system': mapping['id'], 'core': mapping['core'], 'title': str(entry.get('name') or name),
                        'size': preparation['size'], 'checksum': preparation['checksum'],
                        'destination': 'ROMs Manager/' + safe_name, 'source_kind': 'zip_member', '_preparation': preparation}
            except (DownloadError, ValueError, TypeError):
                continue
        url = source.get("http_url")
        if not isinstance(url, str):
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                continue
        except ValueError:
            continue
        url_name = unquote(parsed.path.rsplit("/", 1)[-1])
        name = source.get("rom_name") or source.get("name") or url_name
        if not isinstance(name, str) or any(c in name for c in ("/", "\\", "\x00")) or name in (".", ".."):
            continue
        if Path(name).suffix.lower() not in mapping["extensions"] or Path(url_name).suffix.lower() not in mapping["extensions"]:
            try:
                preparation = archive_rom_source(source, mapping['extensions'], entry.get('name') or name)
                safe_name = re.sub(r'[\x00-\x1f<>:"|?*/\\]', '_', str(entry.get('name') or Path(name).stem)).strip(' .')
                if safe_name and len(safe_name.encode()) < 190:
                    archive_fallbacks.append({'system': mapping['id'], 'core': mapping['core'], 'title': str(entry.get('name') or name),
                        'size': 0, 'checksum': None, 'destination': 'ROMs Manager/' + safe_name + mapping['extensions'][0],
                        'source_kind': 'archive', '_preparation': preparation})
            except (DownloadError, ValueError, TypeError):
                pass
            continue
        if source.get("requires_auth") or source.get("headers"):
            continue
        try:
            size = int(source.get("size") or source.get("_size_bytes") or 0)
        except (ValueError, TypeError):
            continue
        if not 0 < size <= MAX_ROM_SIZE:
            continue
        for algorithm, length in (("sha256", 64), ("sha1", 40), ("md5", 32), ("crc32", 8)):
            checksum = str(source.get(algorithm) or (source.get("crc") if algorithm == "crc32" else "") or "").lower()
            if re.fullmatch("[0-9a-f]{%d}" % length, checksum):
                safe_name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(" .")
                if not safe_name or len(safe_name.encode()) > 200:
                    continue
                return {"system": mapping['id'], "core": mapping['core'], "title": str(entry.get("name") or name),
                        "source_url": url, "size": size, "checksum": {"algorithm": algorithm, "value": checksum},
                        "destination": "ROMs Manager/" + safe_name}
    if archive_fallbacks:
        return archive_fallbacks[0]
    raise MisterError("Requires a direct %s ROM, a catalogued ZIP member, or a checksummed ZIP/7z archive. Torrents and custom authentication flows are unsupported." % ', '.join(mapping['extensions']))


def compatibility(entry, guid):
    try:
        plan = prepare_rom(entry, guid)
        return {"supported": True, "entry_key": entry_key(entry), "reason": None,
                "size": plan['size'] or None, "format": 'ROM (archive)' if plan.get('source_kind') == 'archive' else Path(plan['destination']).suffix[1:].upper(),
                "source_kind": plan.get('source_kind', 'direct')}
    except MisterError as exc:
        return {"supported": False, "entry_key": entry_key(entry), "reason": str(exc)}


