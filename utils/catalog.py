import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from utils.library_sync import load_modules, rdb_json_path
from utils.paths import console_dirs, path_prefix, PROVIDER_FILE


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
    catalogs = _load_provider_catalogs(manufacturer, console)
    lookup = _build_provider_lookup(catalogs)
    roms = _merge_entries(entries, manufacturer, console, lookup)

    return {
        "roms": roms,
        "entry_count": entry_count,
        "provider_total": len(catalogs),
        "rdb_path": str(rdb_file),
        "module": module,
    }


def _load_rdb_entries(path: Path) -> Tuple[List[Dict], int]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("RDB payload missing entries array.")
    entry_count = payload.get("entry_count") or len(entries)
    return entries, entry_count


def _load_provider_catalogs(manufacturer: str, console: str) -> List[Dict]:
    dirs = console_dirs(manufacturer, console, ensure=False)
    exports_dir = Path(dirs["exports"])
    if not exports_dir.is_dir():
        return []

    labels = _load_provider_labels(manufacturer, console)
    prefix = f"{path_prefix(manufacturer, console)}_roms"
    catalogs: List[Dict] = []

    for json_file in sorted(exports_dir.glob(f"{prefix}*.json")):
        provider_id = _provider_id_from_stem(json_file.stem, prefix)
        label = labels.get(provider_id) or labels.get("default") or _humanize(provider_id)
        try:
            with json_file.open("r", encoding="utf-8") as fh:
                entries = json.load(fh)
        except Exception:
            continue
        if not isinstance(entries, list):
            continue
        catalogs.append({
            "id": provider_id,
            "label": label,
            "roms": entries,
        })
    return catalogs


def _load_provider_labels(manufacturer: str, console: str) -> Dict[str, str]:
    try:
        with open(PROVIDER_FILE, "r", encoding="utf-8") as fh:
            providers = json.load(fh)
    except Exception:
        return {}
    entry = (
        providers.get("console_root", {})
        .get(manufacturer, {})
        .get(console)
    )
    labels: Dict[str, str] = {}
    if isinstance(entry, list):
        for item in entry:
            label = item.get("provider") or item.get("name")
            labels[_slug_identifier(label)] = label or "Provider"
    elif isinstance(entry, dict):
        label = entry.get("provider") or entry.get("name") or "Provider"
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


def _build_provider_lookup(catalogs: List[Dict]) -> Dict[str, Dict[str, List[Dict]]]:
    by_md5: Dict[str, List[Dict]] = {}
    by_name: Dict[str, List[Dict]] = {}

    for catalog in catalogs:
        provider_id = catalog["id"]
        label = catalog["label"]
        for rom in catalog["roms"]:
            record = {"provider_id": provider_id, "provider_label": label, "rom": rom}
            md5 = (rom.get("md5") or "").lower()
            if md5:
                by_md5.setdefault(md5, []).append(record)
            for key in _name_keys(rom.get("name")):
                by_name.setdefault(key, []).append(record)
    return {"md5": by_md5, "name": by_name}


def _name_keys(value: Optional[str]) -> Set[str]:
    if not value:
        return set()
    base = os.path.splitext(value)[0]
    key = re.sub(r"[^a-z0-9]+", "", base.lower())
    return {key} if key else set()


def _merge_entries(
    entries: List[Dict],
    manufacturer: str,
    console: str,
    provider_lookup: Dict[str, Dict[str, List[Dict]]],
) -> List[Dict]:
    merged: List[Dict] = []
    for idx, entry in enumerate(entries):
        merged.append(
            _build_rom_entry(idx, entry, manufacturer, console, provider_lookup)
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

    providers = _match_providers(entry, provider_lookup)
    rom["_providers"] = providers
    rom["_provider_count"] = len({p["provider_id"] for p in providers})
    rom["_provider_labels"] = sorted({p["provider_label"] for p in providers})
    if providers:
        primary = providers[0]["rom"]
        rom["http_url"] = primary.get("http_url")
        rom["torrent_url"] = primary.get("torrent_url") or primary.get("torrent")
    else:
        rom["http_url"] = None
        rom["torrent_url"] = None
    return rom


def _match_providers(entry: Dict, lookup: Dict[str, Dict[str, List[Dict]]]) -> List[Dict]:
    matches: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()

    md5 = (entry.get("md5") or "").lower()
    if md5 and md5 in lookup["md5"]:
        for record in lookup["md5"][md5]:
            key = (record["provider_id"], record["rom"].get("name", ""))
            if key not in seen:
                matches.append(record)
                seen.add(key)

    for key in _candidate_name_keys(entry):
        for record in lookup["name"].get(key, []):
            fingerprint = (record["provider_id"], record["rom"].get("name", ""))
            if fingerprint not in seen:
                matches.append(record)
                seen.add(fingerprint)
    return matches


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
