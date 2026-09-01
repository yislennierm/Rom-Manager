import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import zipfile

from utils.internet_archive_auth import ia_cookie_header
from utils.library_sync import load_modules, rdb_json_path
from utils.paths import console_dirs, console_cache_dir, path_prefix, PROVIDER_FILE, slugify


DOWNLOAD_ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar"}

DIRECT_ROM_EXTENSIONS = {
    ".a26",
    ".a52",
    ".a78",
    ".abs",
    ".bin",
    ".bs",
    ".ccd",
    ".cdi",
    ".chd",
    ".col",
    ".cpr",
    ".cso",
    ".cue",
    ".cv",
    ".dc",
    ".fig",
    ".fpk",
    ".gb",
    ".gba",
    ".gbc",
    ".gen",
    ".gg",
    ".img",
    ".iso",
    ".j64",
    ".jag",
    ".lnx",
    ".m3u",
    ".md",
    ".mds",
    ".min",
    ".mv",
    ".nrg",
    ".n64",
    ".nds",
    ".dsi",
    ".ids",
    ".ndd",
    ".neo",
    ".ngc",
    ".ngp",
    ".nes",
    ".pce",
    ".rom",
    ".sc",
    ".sfc",
    ".sgx",
    ".sg",
    ".smc",
    ".sms",
    ".smd",
    ".sv",
    ".swc",
    ".st2",
    ".st",
    ".toc",
    ".tgc",
    ".u1",
    ".u3",
    ".vb",
    ".vboy",
    ".v64",
    ".ws",
    ".wsc",
    ".z64",
}


KNOWN_ROM_NAME_EXTENSIONS = DIRECT_ROM_EXTENSIONS | DOWNLOAD_ARCHIVE_EXTENSIONS | {
    ".gz",
    ".xz",
    ".zst",
}


def resolve_module(manufacturer: str, console: str, guid: Optional[str] = None) -> Optional[Dict]:
    modules = load_modules()
    if guid:
        for module in modules:
            if module.get("guid") == guid:
                return module
    target = f"{manufacturer} - {console}".lower()
    for module in modules:
        if (module.get("name") or "").lower() == target:
            return module
    return None


def build_rom_catalog(
    manufacturer: str,
    console: str,
    module_guid: Optional[str] = None,
    rdb_path: str | Path | None = None,
    expand_local_archives: bool = False,
) -> Dict:
    module = resolve_module(manufacturer, console, module_guid)
    module_name = module.get("name") if module else None
    if not module_name and not rdb_path:
        raise ValueError(f"No module metadata for {manufacturer}/{console}.")

    if rdb_path:
        rdb_file = Path(rdb_path)
    else:
        rdb_file = rdb_json_path(module_name)
    if not rdb_file.exists():
        raise FileNotFoundError(f"RDB export missing for {module_name or console}.")

    entries, entry_count = _load_rdb_entries(rdb_file)
    catalogs = _load_provider_catalogs(manufacturer, console, module_guid)
    lookup = _build_provider_lookup(catalogs, expand_local_archives=expand_local_archives)
    roms = _merge_entries(entries, manufacturer, console, lookup)
    provider_only_count = sum(1 for rom in roms if rom.get("provider_only"))

    return {
        "roms": roms,
        "entry_count": entry_count,
        "catalog_entry_count": len(roms),
        "provider_only_count": provider_only_count,
        "provider_total": len(catalogs),
        "provider_catalogs": catalogs,
        "rdb_path": str(rdb_file),
        "module": module,
    }


def select_preferred_provider(providers: List[Dict]) -> Optional[Dict]:
    candidates = [
        provider
        for provider in providers
        if _provider_runtime_playable(provider) and _provider_has_download_source(provider)
        and provider_downloadable(provider)
    ]
    if not candidates:
        return None
    return min(candidates, key=_provider_download_score)


def provider_download_source(provider_rom: Optional[Dict]) -> Tuple[Optional[str], Optional[str]]:
    """Return a downloader source tuple, preferring exact file URLs over torrents."""
    if not provider_rom:
        return None, None
    http_url = provider_rom.get("http_url")
    if http_url:
        return None, http_url
    return provider_rom.get("torrent_url") or provider_rom.get("torrent"), None


def provider_download_size(provider_rom: Optional[Dict], fallback: object = None) -> object:
    if not provider_rom:
        return fallback
    if provider_rom.get("_archive_member") and provider_rom.get("_source_bundle_size"):
        return provider_rom.get("_source_bundle_size")
    return provider_rom.get("size") or fallback


def _provider_runtime_playable(provider: Dict) -> bool:
    metadata = provider.get("metadata") or {}
    return metadata.get("runtime_playable") is not False


def _provider_has_download_source(provider: Dict) -> bool:
    rom = provider.get("rom") or {}
    return bool(rom.get("http_url") or rom.get("torrent_url") or rom.get("torrent"))


def provider_downloadable(provider: Dict) -> bool:
    metadata = provider.get("metadata") or provider
    if metadata.get("downloadable") is False:
        if not metadata.get("requires_auth"):
            return False
    if metadata.get("requires_auth") is True:
        return bool(ia_cookie_header())
    access = str(metadata.get("download_access") or metadata.get("availability_state") or "").lower()
    if access == "auth_required":
        return bool(ia_cookie_header())
    return access not in {"auth_required", "restricted", "unavailable", "offline"}


def _provider_download_score(provider: Dict) -> Tuple[int, int, int, str]:
    rom = provider.get("rom") or {}
    name = rom.get("name") or ""
    name_lower = name.lower()
    suffix = Path(name).suffix.lower()
    has_http = bool(rom.get("http_url"))
    source_type = 50
    if rom.get("_archive_member"):
        source_type = 30
    elif suffix in DIRECT_ROM_EXTENSIONS:
        source_type = 0
    elif suffix in DOWNLOAD_ARCHIVE_EXTENSIONS:
        source_type = 10
    elif suffix:
        source_type = 20

    release_type = 10
    if "smart-media-card" in name_lower:
        release_type = 0
    elif "cracked-raw" in name_lower or "encrypted-raw" in name_lower:
        release_type = 20

    protocol = 0 if has_http else 1
    size = _coerce_int(rom.get("_source_bundle_size") or rom.get("size")) or 0
    bundle = (rom.get("_source_bundle") or "").lower()
    provider_id = str(provider.get("provider_id") or "").lower()
    bundle_rank = 0
    if "no_intro" in provider_id or "no-intro" in bundle or "no intro" in bundle:
        bundle_rank = -20
    elif "tosec-v2021" in bundle or "rvzstd" in bundle:
        bundle_rank = 20
    return (source_type + bundle_rank, release_type, protocol, size, name_lower)


def _load_rdb_entries(path: Path) -> Tuple[List[Dict], int]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    entries = payload.get("entries")
    if not isinstance(entries, list):
        entries = payload.get("roms")
    if not isinstance(entries, list):
        raise ValueError("RDB payload missing entries or roms array.")
    rom_entries = [entry for entry in entries if _is_rdb_rom_entry(entry)]
    entry_count = len(rom_entries)
    return rom_entries, entry_count


def _is_rdb_rom_entry(entry: Dict) -> bool:
    if not isinstance(entry, dict):
        return False
    return any(entry.get(field) for field in ("name", "description", "rom_name"))


def _load_providers_data() -> Dict:
    try:
        with open(PROVIDER_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _resolve_provider_console(
    manufacturer: str,
    console: str,
    module_guid: Optional[str],
    providers: Optional[Dict] = None,
) -> Tuple[str, str, Optional[Dict]]:
    providers = providers or _load_providers_data()

    root = providers.get("console_root", {})
    consoles = root.get(manufacturer, {})
    if isinstance(consoles, dict) and console in consoles:
        return manufacturer, console, consoles[console]

    if module_guid:
        target = module_guid.lower()
        for maker, systems in root.items():
            if not isinstance(systems, dict):
                continue
            for name, entry in systems.items():
                entries = entry if isinstance(entry, list) else [entry]
                for candidate in entries:
                    if not isinstance(candidate, dict):
                        continue
                    guid = (candidate.get("libretro_guid") or candidate.get("guid") or "").lower()
                    if guid and guid == target:
                        return maker, name, entry

    return manufacturer, console, None


def _provider_slug_from_entry(entry: Dict) -> str:
    raw = entry.get("archive_id") or entry.get("provider") or entry.get("name") or "default"
    return slugify(raw)


def _load_provider_catalogs(
    manufacturer: str,
    console: str,
    module_guid: Optional[str] = None,
) -> List[Dict]:
    providers = _load_providers_data()
    provider_manufacturer, provider_console, provider_entry = _resolve_provider_console(
        manufacturer,
        console,
        module_guid,
        providers=providers,
    )
    if provider_entry is None:
        return []
    base_dir = Path(console_cache_dir(provider_manufacturer, provider_console))
    if not base_dir.is_dir():
        return []

    provider_dirs: List[Tuple[Optional[str], Path]] = []
    subdirs = [d for d in base_dir.iterdir() if d.is_dir()]
    if subdirs:
        for sub in sorted(subdirs):
            provider_dirs.append((sub.name, sub))
    # Include legacy root if exports exist directly under base directory
    legacy_exports = base_dir / "exports"
    if legacy_exports.is_dir():
        provider_dirs.append((None, base_dir))

    labels = _load_provider_labels(manufacturer, console, module_guid, providers=providers)
    registry_metadata = _load_provider_registry_metadata(
        manufacturer,
        console,
        module_guid,
        providers=providers,
    )
    catalogs: List[Dict] = []

    for slug_value, provider_base in provider_dirs:
        if slug_value is not None and slug_value not in labels:
            continue
        exports_dir = provider_base / "exports"
        if not exports_dir.is_dir():
            continue
        prefix = f"{path_prefix(provider_manufacturer, provider_console, slug_value)}_roms"
        for json_file in sorted(exports_dir.glob(f"{prefix}*.json")):
            provider_id = slug_value or _provider_id_from_stem(json_file.stem, prefix)
            label = labels.get(provider_id) or labels.get("default") or _humanize(provider_id)
            try:
                with json_file.open("r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except Exception:
                continue
            metadata = {}
            entries = payload
            if isinstance(payload, dict):
                metadata = payload
                entries = payload.get("roms")
            if not isinstance(entries, list):
                continue
            metadata = {**metadata, **registry_metadata.get(provider_id, {})}
            catalogs.append({
                "id": provider_id,
                "label": label,
                "roms": entries,
                "metadata": metadata,
            })
    return catalogs


def _load_provider_registry_metadata(
    manufacturer: str,
    console: str,
    module_guid: Optional[str] = None,
    providers: Optional[Dict] = None,
) -> Dict[str, Dict]:
    providers = providers or _load_providers_data()
    console_root = providers.get("console_root", {})
    manufacturer_entry = console_root.get(manufacturer, {})
    entry = manufacturer_entry.get(console) if isinstance(manufacturer_entry, dict) else None
    if not entry and module_guid:
        target = module_guid.lower()
        for systems in console_root.values():
            if not isinstance(systems, dict):
                continue
            for value in systems.values():
                candidates = value if isinstance(value, list) else [value]
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    guid = candidate.get("libretro_guid") or candidate.get("guid")
                    if guid and guid.lower() == target:
                        entry = value
                        break
                if entry:
                    break
            if entry:
                break

    metadata_by_id: Dict[str, Dict] = {}
    candidates = entry if isinstance(entry, list) else [entry]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        provider_id = _provider_slug_from_entry(item)
        metadata = dict(item)
        metadata.setdefault("provider_label", item.get("provider") or item.get("name") or item.get("archive_id"))
        metadata_by_id[provider_id] = metadata
    return metadata_by_id


def _load_provider_labels(
    manufacturer: str,
    console: str,
    module_guid: Optional[str] = None,
    providers: Optional[Dict] = None,
) -> Dict[str, str]:
    providers = providers or _load_providers_data()
    console_root = providers.get("console_root", {})
    manufacturer_entry = console_root.get(manufacturer, {})
    entry = manufacturer_entry.get(console) if isinstance(manufacturer_entry, dict) else None
    if not entry and module_guid:
        target = module_guid.lower()
        for systems in console_root.values():
            if not isinstance(systems, dict):
                continue
            for value in systems.values():
                candidates = value if isinstance(value, list) else [value]
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    guid = candidate.get("libretro_guid") or candidate.get("guid")
                    if guid and guid.lower() == target:
                        entry = value
                        break
                if entry:
                    break
            if entry:
                break
    labels: Dict[str, str] = {}
    if isinstance(entry, list):
        for item in entry:
            if not isinstance(item, dict):
                continue
            slug_value = _provider_slug_from_entry(item)
            label = item.get("provider") or item.get("name") or item.get("archive_id") or "Provider"
            labels[slug_value] = label
    elif isinstance(entry, dict):
        slug_value = _provider_slug_from_entry(entry)
        label = entry.get("provider") or entry.get("name") or entry.get("archive_id") or "Provider"
        labels[slug_value] = label
        labels["default"] = label
    return labels


def _provider_id_from_stem(stem: str, prefix: str) -> str:
    suffix = stem[len(prefix):] if stem.startswith(prefix) else stem
    if suffix.startswith("__"):
        suffix = suffix[2:]
    return suffix or "default"


def _slug_identifier(value: Optional[str]) -> str:
    if not value:
        return "default"
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "default"


def _humanize(value: Optional[str]) -> str:
    if not value or value == "default":
        return "Primary Provider"
    return value.replace("_", " ").title()


def _build_provider_lookup(
    catalogs: List[Dict],
    *,
    expand_local_archives: bool = False,
) -> Dict[str, Dict[str, List[Dict]]]:
    by_md5: Dict[str, List[Dict]] = {}
    by_sha1: Dict[str, List[Dict]] = {}
    by_crc32: Dict[str, List[Dict]] = {}
    by_name: Dict[str, List[Dict]] = {}
    by_serial: Dict[str, List[Dict]] = {}
    records: List[Dict] = []

    for catalog in catalogs:
        provider_id = catalog["id"]
        label = catalog["label"]
        metadata = catalog.get("metadata") or {}
        for rom in _expanded_provider_roms(catalog, expand_local_archives=expand_local_archives):
            record = {
                "provider_id": provider_id,
                "provider_label": label,
                "rom": rom,
                "metadata": metadata,
            }
            records.append(record)
            md5 = (rom.get("md5") or "").lower()
            if md5:
                by_md5.setdefault(md5, []).append(record)
            sha1 = (rom.get("sha1") or "").lower()
            if sha1:
                by_sha1.setdefault(sha1, []).append(record)
            crc32 = (rom.get("crc32") or rom.get("crc") or "").lower()
            if crc32:
                by_crc32.setdefault(crc32, []).append(record)
            for serial in _serial_keys(rom):
                by_serial.setdefault(serial, []).append(record)
            for key in _name_keys(rom.get("name")):
                by_name.setdefault(key, []).append(record)
    return {
        "md5": by_md5,
        "sha1": by_sha1,
        "crc32": by_crc32,
        "serial": by_serial,
        "name": by_name,
        "records": records,
    }


def _serial_keys(rom: Dict) -> Set[str]:
    keys: Set[str] = set()
    for value in (rom.get("serial"), rom.get("product_code"), rom.get("name")):
        if not isinstance(value, str):
            continue
        for match in re.findall(r"\b[A-Z]{4}[- ]?[0-9]{5}\b", value.upper()):
            keys.add(match.replace("-", "").replace(" ", "").lower())
    return keys


def _name_keys(value: Optional[str]) -> Set[str]:
    if not value:
        return set()
    base = os.path.basename(value)
    while True:
        root, ext = os.path.splitext(base)
        if not ext:
            base = root or base
            break
        if ext.lower() in KNOWN_ROM_NAME_EXTENSIONS:
            base = root
        else:
            base = root + ext
            break
    return _normalized_name_keys(base)


def _normalized_name_keys(value: str) -> Set[str]:
    keys: Set[str] = set()
    value = value.lower().strip()
    if not value:
        return keys

    def add(text: str) -> None:
        key = re.sub(r"[^a-z0-9]+", "", text.lower())
        if key:
            keys.add(key)

    add(value)
    title, parens = _split_parenthetical_name(value)
    if title != value:
        add(title)
        _add_title_alias_keys(title, add)
        region = _first_region(parens)
        version = _first_version(parens)
        if region:
            add(f"{title} {region}")
        if region and version:
            add(f"{title} {region} {version}")

    normalized_title = _normalize_trailing_article(title)
    if normalized_title != title:
        add(normalized_title)
        _add_title_alias_keys(normalized_title, add)
        region = _first_region(parens)
        version = _first_version(parens)
        if region:
            add(f"{normalized_title} {region}")
        if region and version:
            add(f"{normalized_title} {region} {version}")

    keys.update(_scene_release_keys(value))
    return keys


def _add_title_alias_keys(title: str, add) -> None:
    aliases = [part.strip() for part in re.split(r"\s+~\s+", title) if part.strip()]
    if len(aliases) > 1:
        for alias in aliases:
            add(alias)
    for candidate in aliases or [title]:
        parts = [part.strip() for part in re.split(r"\s+-\s+", candidate) if part.strip()]
        if len(parts) > 1 and len(re.findall(r"[a-z0-9]+", parts[0].lower())) >= 3:
            add(parts[0])
        for idx in range(1, len(parts)):
            suffix = " - ".join(parts[idx:])
            if len(re.findall(r"[a-z0-9]+", suffix.lower())) >= 3:
                add(suffix)
        for idx in range(0, max(0, len(parts) - 2)):
            suffix = " - ".join(parts[idx + 2:])
            if len(re.findall(r"[a-z0-9]+", suffix.lower())) >= 3:
                add(suffix)


def _split_parenthetical_name(value: str) -> Tuple[str, List[str]]:
    title = re.split(r"\s*\(", value, maxsplit=1)[0].strip()
    parens = [match.strip() for match in re.findall(r"\(([^)]*)\)", value)]
    return title, parens


def _normalize_trailing_article(value: str) -> str:
    match = re.match(r"^(.*),\s*(the|a|an)$", value.strip())
    if not match:
        return value
    return f"{match.group(2)} {match.group(1)}".strip()


def _first_region(values: List[str]) -> Optional[str]:
    region_map = {
        "e": "europe",
        "eu": "europe",
        "europe": "europe",
        "asia": "asia",
        "k": "korea",
        "kr": "korea",
        "korea": "korea",
        "world": "world",
        "w": "world",
        "u": "usa",
        "us": "usa",
        "usa": "usa",
        "j": "japan",
        "jp": "japan",
        "japan": "japan",
    }
    for value in values:
        first = re.split(r"[,/ ]+", value.lower().strip())[0]
        region = region_map.get(first)
        if region:
            return region
    return None


def _first_version(values: List[str]) -> Optional[str]:
    for value in values:
        match = re.search(r"\bv(?:ersion)?\s*([0-9][0-9.]*)", value.lower())
        if match:
            return f"v{match.group(1)}"
    return None


def _provider_name_compatible(entry: Dict, provider_name: Optional[str]) -> bool:
    if not provider_name:
        return True
    rdb_name = entry.get("name") or entry.get("description") or entry.get("rom_name") or ""
    if not _shared_prefix_suffix_compatible(rdb_name, provider_name):
        return False
    if not _distinctive_title_tokens_compatible(rdb_name, provider_name):
        return False
    rdb_region = _region_from_name(rdb_name)
    provider_region = _region_from_name(provider_name)
    if rdb_region and provider_region and rdb_region != provider_region:
        if "world" in {rdb_region, provider_region}:
            return True
        return False

    rdb_version = _version_from_name(rdb_name)
    provider_version = _version_from_name(provider_name)
    if rdb_version and provider_version and rdb_version != provider_version:
        return False
    return True


def _shared_prefix_suffix_compatible(rdb_name: str, provider_name: str) -> bool:
    rdb_title = _normalize_title_for_match(rdb_name)
    provider_title = _normalize_title_for_match(provider_name)
    rdb_parts = [part.strip() for part in re.split(r"\s+-\s+", rdb_title) if part.strip()]
    provider_parts = [part.strip() for part in re.split(r"\s+-\s+", provider_title) if part.strip()]
    if len(rdb_parts) < 2 or len(provider_parts) < 2:
        return True

    shared_prefix = []
    for left, right in zip(rdb_parts, provider_parts):
        if _compact_name(left) != _compact_name(right):
            break
        shared_prefix.append(left)

    if not shared_prefix:
        return True
    shared_token_count = len(re.findall(r"[a-z0-9]+", " ".join(shared_prefix)))
    if shared_token_count < 2:
        return True

    rdb_suffix = _title_suffix_tokens(rdb_parts[len(shared_prefix):])
    provider_suffix = _title_suffix_tokens(provider_parts[len(shared_prefix):])
    if bool(rdb_suffix) != bool(provider_suffix):
        return False
    if not rdb_suffix and not provider_suffix:
        return True
    return rdb_suffix == provider_suffix


def _normalize_title_for_match(value: str) -> str:
    base = os.path.basename(value or "")
    while True:
        root, ext = os.path.splitext(base)
        if ext.lower() in KNOWN_ROM_NAME_EXTENSIONS:
            base = root
            continue
        break
    title, _ = _split_parenthetical_name(base.lower())
    return _normalize_trailing_article(title)


def _compact_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _title_suffix_tokens(parts: List[str]) -> Set[str]:
    stopwords = {"edition", "the", "a", "an"}
    return {
        token
        for token in re.findall(r"[a-z0-9]+", " ".join(parts).lower())
        if token not in stopwords
    }


def _distinctive_title_tokens_compatible(rdb_name: str, provider_name: str) -> bool:
    rdb_tokens = _distinctive_title_tokens(rdb_name)
    provider_tokens = _distinctive_title_tokens(provider_name)
    if not rdb_tokens or not provider_tokens:
        return True
    return bool(rdb_tokens & provider_tokens)


def _distinctive_title_tokens(value: str) -> Set[str]:
    base = os.path.basename(value or "")
    while True:
        root, ext = os.path.splitext(base)
        if ext.lower() in KNOWN_ROM_NAME_EXTENSIONS:
            base = root
            continue
        break
    title = re.split(r"\s*[\[(]", base, maxsplit=1)[0]
    title = _normalize_trailing_article(title.lower())
    stopwords = {
        "and",
        "collection",
        "edition",
        "fighting",
        "game",
        "games",
        "geo",
        "japan",
        "neo",
        "pocket",
        "series",
        "sports",
        "the",
        "usa",
        "version",
    }
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", title)
        if len(token) >= 3 and token not in stopwords
    }
    return tokens


def _region_from_name(value: str) -> Optional[str]:
    _, parens = _split_parenthetical_name(value.lower())
    region = _first_region(parens)
    if region:
        return region
    tokens = re.findall(r"[a-z0-9]+", re.sub(r"[-_]+", " ", value.lower()))
    region_map = {"e": "europe", "k": "korea", "u": "usa", "j": "japan", "asia": "asia"}
    for token in tokens:
        if token in region_map:
            return region_map[token]
    return None


def _version_from_name(value: str) -> Optional[str]:
    _, parens = _split_parenthetical_name(value.lower())
    version = _first_version(parens)
    if version:
        return re.sub(r"[^a-z0-9]+", "", version)
    match = re.search(r"(?:^|[-_ ])v([0-9][0-9.]*)", value.lower())
    if match:
        return re.sub(r"[^a-z0-9]+", "", f"v{match.group(1)}")
    return None


def _scene_release_keys(value: str) -> Set[str]:
    normalized = re.sub(r"[-_]+", " ", value.lower())
    tokens = re.findall(r"[a-z0-9]+", normalized)
    if not tokens:
        return set()

    region_map = {"e": "europe", "k": "korea", "u": "usa", "j": "japan"}
    suffix_words = {"cracked", "encrypted", "raw", "by", "wrg", "guppy", "smart", "media", "card"}
    keys: Set[str] = set()

    def compact(parts: List[str]) -> Optional[str]:
        key = re.sub(r"[^a-z0-9]+", "", " ".join(parts))
        return key or None

    for idx, token in enumerate(tokens):
        if token not in region_map:
            continue
        title = tokens[:idx]
        if not title:
            continue
        version: List[str] = []
        for later in tokens[idx + 1:]:
            if later in suffix_words or re.fullmatch(r"m[0-9]+", later):
                break
            if later.startswith("v") or version:
                version.append(later)
        for parts in (title, title + [region_map[token]], title + [region_map[token]] + version):
            key = compact(parts)
            if key:
                keys.add(key)
    return keys


def _expanded_provider_roms(catalog: Dict, *, expand_local_archives: bool = False) -> List[Dict]:
    roms = catalog.get("roms") or []
    expanded: List[Dict] = []
    for rom in roms:
        if not isinstance(rom, dict):
            continue
        expanded.append(rom)
        if not expand_local_archives:
            continue
        for inner in _local_archive_members_for_provider_rom(rom):
            enriched = dict(inner)
            enriched.setdefault("_source_bundle", rom.get("name"))
            enriched.setdefault("http_url", rom.get("http_url"))
            enriched.setdefault("torrent_url", rom.get("torrent_url") or rom.get("torrent"))
            expanded.append(enriched)
    return expanded


def _local_archive_members_for_provider_rom(rom: Dict) -> List[Dict]:
    name = rom.get("name")
    if not name:
        return []
    source = _local_provider_archive(name)
    if not source:
        return []
    if source.suffix.lower() == ".zip":
        return _zip_members(source)
    if source.suffix.lower() == ".7z":
        return _seven_zip_members(source)
    if source.suffix.lower() == ".rar":
        return _rar_members(source)
    return []


def _local_provider_archive(name: str) -> Optional[Path]:
    candidates = [
        Path("downloads"),
        Path("backend") / "downloads",
    ]
    for root in candidates:
        if not root.exists():
            continue
        for path in root.rglob(Path(name).name):
            if path.is_file():
                return path
    return None


def _zip_members(path: Path) -> List[Dict]:
    members: List[Dict] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                payload = archive.read(info)
                members.append(_member_record(info.filename, payload, info.CRC, source_archive=str(path)))
                members.extend(_nested_zip_members(info.filename, payload, source_archive=str(path)))
    except Exception:
        return []
    return members


def _nested_zip_members(name: str, payload: bytes, source_archive: str) -> List[Dict]:
    if not name.lower().endswith(".zip"):
        return []
    nested: List[Dict] = []
    try:
        import io

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                nested.append(_member_record(info.filename, archive.read(info), info.CRC, source_archive=source_archive, outer_name=name))
    except Exception:
        return []
    return nested


def _rar_members(path: Path) -> List[Dict]:
    import shutil
    import subprocess
    import tempfile

    unar = _tool_path("unar")
    if not unar:
        return []
    with tempfile.TemporaryDirectory(prefix="roms-manager-catalog-rar-") as temp_dir:
        target = Path(temp_dir)
        result = subprocess.run(
            [unar, "-force-overwrite", "-output-directory", str(target), str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=_tool_env(unar),
            check=False,
        )
        if result.returncode != 0:
            return []
        members: List[Dict] = []
        for extracted in sorted(target.rglob("*")):
            if not extracted.is_file():
                continue
            if extracted.suffix.lower() == ".zip":
                members.extend(_zip_members(extracted))
                continue
            payload = extracted.read_bytes()
            members.append(_member_record(extracted.name, payload, None, source_archive=str(path)))
        shutil.rmtree(target, ignore_errors=True)
        return members


def _seven_zip_members(path: Path) -> List[Dict]:
    import subprocess
    import tempfile

    seven_zip = _tool_path("7z")
    if not seven_zip:
        return []
    with tempfile.TemporaryDirectory(prefix="roms-manager-catalog-7z-") as temp_dir:
        target = Path(temp_dir)
        result = subprocess.run(
            [seven_zip, "x", f"-o{target}", str(path), "-y"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        members: List[Dict] = []
        for extracted in sorted(target.rglob("*")):
            if not extracted.is_file():
                continue
            if extracted.suffix.lower() == ".zip":
                members.extend(_zip_members(extracted))
                continue
            if extracted.suffix.lower() == ".7z":
                members.extend(_seven_zip_members(extracted))
                continue
            payload = extracted.read_bytes()
            members.append(_member_record(extracted.name, payload, None, source_archive=str(path)))
        return members


def _member_record(name: str, payload: bytes, crc32_value: Optional[int], source_archive: str, outer_name: Optional[str] = None) -> Dict:
    import hashlib
    import zlib

    crc = crc32_value if crc32_value is not None else zlib.crc32(payload)
    record = {
        "name": name,
        "size": len(payload),
        "md5": hashlib.md5(payload).hexdigest(),
        "sha1": hashlib.sha1(payload).hexdigest(),
        "crc32": f"{crc & 0xffffffff:08x}",
        "_archive_member": True,
        "_source_archive": source_archive,
    }
    if outer_name:
        record["_outer_name"] = outer_name
    return record


def _tool_path(name: str) -> Optional[str]:
    import shutil

    repo_local = Path(__file__).resolve().parents[1] / ".local-tools" / name / "usr" / "bin" / name
    if repo_local.exists():
        return str(repo_local)
    return shutil.which(name)


def _tool_env(tool_path: str) -> Optional[Dict[str, str]]:
    tool = Path(tool_path)
    try:
        local_root = tool.parents[1]
    except IndexError:
        return None
    if local_root.name != "usr":
        return None
    env = dict(os.environ)
    lib_paths = [
        str(local_root / "lib"),
        str(local_root / "lib" / "x86_64-linux-gnu"),
    ]
    existing = env.get("LD_LIBRARY_PATH")
    if existing:
        lib_paths.append(existing)
    env["LD_LIBRARY_PATH"] = ":".join(lib_paths)
    return env


def _merge_entries(
    entries: List[Dict],
    manufacturer: str,
    console: str,
    provider_lookup: Dict[str, Dict[str, List[Dict]]],
) -> List[Dict]:
    merged: List[Dict] = []
    matched_provider_records: Set[Tuple[str, str]] = set()
    for idx, entry in enumerate(entries):
        rom = _build_rom_entry(idx, entry, manufacturer, console, provider_lookup)
        for provider in rom.get("_providers") or []:
            provider_rom = provider.get("rom") or {}
            matched_provider_records.add((str(provider.get("provider_id") or ""), str(provider_rom.get("name") or "")))
        merged.append(rom)
    merged.extend(
        _provider_only_entries(
            len(merged),
            manufacturer,
            console,
            provider_lookup,
            matched_provider_records,
        )
    )
    return merged


def _build_rom_entry(
    index: int,
    entry: Dict,
    manufacturer: str,
    console: str,
    provider_lookup: Dict[str, Dict[str, List[Dict]]],
) -> Dict:
    name = entry.get("name") or entry.get("description") or entry.get("rom_name") or "Unknown ROM"
    size_bytes = _coerce_int(entry.get("size"))

    rom: Dict = {
        "_key": _entry_key(index, entry),
        "name": name,
        "console": console,
        "manufacturer": manufacturer,
        "region": entry.get("region") or entry.get("languages") or "—",
        "md5": entry.get("md5"),
        "sha1": entry.get("sha1"),
        "crc32": entry.get("crc") or entry.get("crc32"),
        "serial": entry.get("serial"),
        "size": size_bytes,
        "_size_bytes": size_bytes,
        "_search_blob": _build_search_blob(entry),
        "_rdb": entry,
    }

    synced_providers = entry.get("_providers")
    providers = synced_providers if isinstance(synced_providers, list) and synced_providers else _match_providers(entry, provider_lookup)
    rom["_providers"] = providers
    rom["_provider_count"] = len({p["provider_id"] for p in providers})
    rom["_provider_labels"] = sorted({p["provider_label"] for p in providers})
    primary_provider = select_preferred_provider(providers)
    if primary_provider:
        primary = primary_provider["rom"]
        rom["http_url"] = primary.get("http_url")
        rom["torrent_url"] = primary.get("torrent_url") or primary.get("torrent")
    else:
        rom["http_url"] = None
        rom["torrent_url"] = None
    return rom


def _provider_only_entries(
    start_index: int,
    manufacturer: str,
    console: str,
    provider_lookup: Dict[str, object],
    matched_provider_records: Set[Tuple[str, str]],
) -> List[Dict]:
    entries: List[Dict] = []
    seen_serials: Set[str] = set()
    seen_names: Set[Tuple[str, str]] = set()
    records = provider_lookup.get("records") or []
    if not isinstance(records, list):
        return entries
    for record in records:
        if not isinstance(record, dict):
            continue
        metadata = record.get("metadata") or {}
        if not isinstance(metadata, dict) or metadata.get("allow_provider_only") is not True:
            continue
        rom = record.get("rom") or {}
        if not isinstance(rom, dict):
            continue
        provider_id = str(record.get("provider_id") or "")
        provider_name = str(rom.get("name") or "")
        fingerprint = (provider_id, provider_name)
        if fingerprint in matched_provider_records:
            continue
        serials = sorted(_serial_keys(rom))
        serial = _display_serial(serials[0]) if serials else None
        if serial and serial in seen_serials:
            continue
        name_key = (provider_id, _compact_name(provider_name))
        if not serial and name_key in seen_names:
            continue
        if serial:
            seen_serials.add(serial)
        seen_names.add(name_key)
        public_provider = _public_provider_record(record)
        providers = [public_provider]
        primary = public_provider.get("rom") or {}
        display_name = _provider_only_display_name(provider_name)
        size_bytes = _coerce_int(rom.get("size"))
        entry = {
            "_key": f"provider-only::{provider_id}::{serial or provider_name}::{start_index + len(entries)}",
            "name": display_name,
            "console": console,
            "manufacturer": manufacturer,
            "region": _provider_region_label(provider_name) or "—",
            "md5": rom.get("md5"),
            "sha1": rom.get("sha1"),
            "crc32": rom.get("crc32") or rom.get("crc"),
            "serial": serial,
            "size": size_bytes,
            "_size_bytes": size_bytes,
            "_search_blob": " ".join(
                value.lower()
                for value in (display_name, provider_name, serial, _provider_region_label(provider_name))
                if isinstance(value, str)
            ),
            "_rdb": None,
            "_catalog_status": "provider_only",
            "provider_only": True,
            "content_type": _provider_content_type(provider_name),
            "source_status": "provider_only",
            "_providers": providers,
            "_provider_count": len({p["provider_id"] for p in providers}),
            "_provider_labels": sorted({p["provider_label"] for p in providers}),
            "http_url": primary.get("http_url"),
            "torrent_url": primary.get("torrent_url") or primary.get("torrent"),
        }
        entries.append(entry)
    return entries


def _display_serial(serial: str) -> str:
    value = re.sub(r"[^a-z0-9]", "", serial.lower()).upper()
    if len(value) == 9:
        return f"{value[:4]}-{value[4:]}"
    return value


def _provider_only_display_name(provider_name: str) -> str:
    name = os.path.basename(provider_name or "")
    while Path(name).suffix.lower() in KNOWN_ROM_NAME_EXTENSIONS:
        name = Path(name).stem
    name = re.sub(r"\s*\[[^\]]+\]", "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name or provider_name or "Provider-only entry"


def _provider_region_label(provider_name: str) -> Optional[str]:
    regions = re.findall(r"\[([A-Za-z]+)\]", provider_name or "")
    known = {"usa": "USA", "europe": "Europe", "japan": "Japan", "world": "World", "asia": "Asia"}
    for region in regions:
        label = known.get(region.lower())
        if label:
            return label
    region = _region_from_name(provider_name or "")
    return region.title() if region else None


def _provider_content_type(provider_name: str) -> str:
    normalized = provider_name.lower()
    if any(token in normalized for token in (" dlc", "[dlc]", "downloadable content")):
        return "dlc"
    if any(token in normalized for token in (" update", "[update]", "patch")):
        return "update"
    if any(token in normalized for token in (" demo", "[demo]", "trial")):
        return "demo"
    if any(token in normalized for token in ("facebook", "skype", "flickr", "foursquare", "livetweet")):
        return "app"
    return "unknown"


def _match_providers(entry: Dict, lookup: Dict[str, Dict[str, List[Dict]]]) -> List[Dict]:
    matches: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()

    md5 = (entry.get("md5") or "").lower()
    if md5 and md5 in lookup["md5"]:
        for record in lookup["md5"][md5]:
            key = (record["provider_id"], record["rom"].get("name", ""))
            if key not in seen:
                matches.append(_public_provider_record(record))
                seen.add(key)

    sha1 = (entry.get("sha1") or "").lower()
    if sha1 and sha1 in lookup["sha1"]:
        for record in lookup["sha1"][sha1]:
            key = (record["provider_id"], record["rom"].get("name", ""))
            if key not in seen:
                matches.append(_public_provider_record(record))
                seen.add(key)

    crc32 = (entry.get("crc") or entry.get("crc32") or "").lower()
    if crc32 and crc32 in lookup["crc32"]:
        for record in lookup["crc32"][crc32]:
            key = (record["provider_id"], record["rom"].get("name", ""))
            if key not in seen:
                matches.append(_public_provider_record(record))
                seen.add(key)

    for serial in _serial_keys(entry):
        for record in lookup.get("serial", {}).get(serial, []):
            fingerprint = (record["provider_id"], record["rom"].get("name", ""))
            if fingerprint not in seen:
                matches.append(_public_provider_record(record))
                seen.add(fingerprint)

    for key in _candidate_name_keys(entry):
        for record in lookup["name"].get(key, []):
            if not _provider_name_compatible(entry, record["rom"].get("name")):
                continue
            fingerprint = (record["provider_id"], record["rom"].get("name", ""))
            if fingerprint not in seen:
                matches.append(_public_provider_record(record))
                seen.add(fingerprint)
    return matches


def _public_provider_record(record: Dict) -> Dict:
    public = dict(record)
    rom = dict(public.get("rom") or {})
    for key in ("_source_archive",):
        rom.pop(key, None)
    public["rom"] = rom
    metadata = public.get("metadata") or {}
    public["metadata"] = {
        key: metadata.get(key)
        for key in (
            "archive_id",
            "provider_label",
            "preferred_cores",
            "compatible_cores",
            "runtime_playable",
            "romset_version",
            "arcade_family",
            "zip_preserve",
            "compatibility_notes",
            "downloadable",
            "download_access",
            "requires_auth",
            "availability_state",
            "availability_notes",
        )
        if key in metadata
    }
    return public


def _candidate_name_keys(entry: Dict) -> Set[str]:
    keys: Set[str] = set()
    for field in ("name", "description", "rom_name"):
        for key in _name_keys(entry.get(field)):
            keys.add(key)
    return keys


def _entry_key(index: int, entry: Dict) -> str:
    parts = [
        entry.get("name") or "",
        entry.get("serial") or "",
        entry.get("rom_name") or "",
        entry.get("md5") or "",
        str(index),
    ]
    return "::".join(parts)


def _build_search_blob(entry: Dict) -> str:
    fields = [
        entry.get("name"),
        entry.get("description"),
        entry.get("rom_name"),
        entry.get("serial"),
        entry.get("region"),
        entry.get("developer"),
        entry.get("publisher"),
    ]
    return " ".join(value.lower() for value in fields if isinstance(value, str))


def _coerce_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
