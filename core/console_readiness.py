import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.bios_manager import active_frontend, list_bios_requirements
from core.providers import load_providers
from data.storage.storage_config_loader import load_storage_config
from utils.catalog import build_rom_catalog
from utils.cores_registry import load_registry
from utils.library_sync import rdb_json_path


def readiness_for_module(module: Dict, include_coverage: bool = False) -> Dict[str, object]:
    name = module.get("name") or ""
    guid = module.get("guid")
    manufacturer, console = split_module_name(name)

    provider_entries = _providers_for_guid_or_name(guid, manufacturer, console)
    rdb_path = rdb_json_path(name) if name else None
    rdb_exists = bool(rdb_path and rdb_path.exists())
    core_options = _core_options_for_guid(guid)
    external_runtime = _has_external_runtime(core_options)
    frontend_key, frontend = _frontend_for_core_options(core_options)
    core_status = _core_status(core_options, frontend)
    bios_status = _external_firmware_status(core_options) or _bios_status_for_guid(guid)
    provider_coverage = (
        _provider_coverage(manufacturer, console, guid, rdb_exists, rdb_path)
        if include_coverage
        else _coverage_not_checked(provider_entries, rdb_exists)
    )
    install_status = (
        _external_install_status()
        if external_runtime
        else _install_status(manufacturer, console, frontend)
    )
    playlist_status = (
        _external_playlist_status()
        if external_runtime
        else _playlist_status(name, frontend, core_status)
    )
    install_strategy = _install_strategy_for_guid(guid)

    checks = {
        "assigned": _assigned_status(guid, frontend),
        "providers": _status_from_bool(bool(provider_entries), "ok", "missing"),
        "coverage": provider_coverage,
        "rdb": _status_from_bool(rdb_exists, "ok", "missing"),
        "core": core_status,
        "bios": bios_status,
        "install": install_status,
        "playlist": playlist_status,
    }
    score = readiness_score(checks)
    return {
        "module_name": name,
        "guid": guid,
        "manufacturer": manufacturer,
        "console": console,
        "frontend_key": frontend_key,
        "frontend_name": frontend.get("name") or frontend_key,
        "strategy": install_strategy,
        "strategy_label": _install_strategy_label(install_strategy),
        "providers": {
            "count": len(provider_entries),
            "entries": provider_entries,
        },
        "rdb_path": str(rdb_path) if rdb_path else None,
        "checks": checks,
        "score": score,
        "summary": readiness_label(score),
    }


def readiness_score(checks: Dict[str, Dict]) -> str:
    states = [str(check.get("state")) for check in checks.values() if check]
    if any(state in {"error", "invalid"} for state in states):
        return "broken"
    blocking = {"missing", "stale"}
    required_keys = ["providers", "rdb", "core", "bios", "playlist"]
    if any((checks.get(key) or {}).get("state") in blocking for key in required_keys):
        return "needs_work"
    if (checks.get("install") or {}).get("state") == "missing":
        return "catalog_ready"
    return "ready"


def readiness_label(score: str) -> str:
    return {
        "ready": "Ready",
        "catalog_ready": "Catalog ready",
        "needs_work": "Needs work",
        "broken": "Broken",
    }.get(score, "Unknown")


def split_module_name(name: str) -> Tuple[Optional[str], Optional[str]]:
    if not name:
        return None, None
    parts = [segment.strip() for segment in name.split("-", 1)]
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, name.strip()


def _assigned_status(guid: Optional[str], frontend: Dict) -> Dict[str, object]:
    if not guid:
        return {"state": "missing", "label": "No GUID"}
    supported = frontend.get("supported_guids") or []
    if guid in supported:
        return {"state": "ok", "label": "Assigned"}
    return {"state": "missing", "label": "Not active"}


def _status_from_bool(value: bool, ok: str, missing: str) -> Dict[str, object]:
    if value:
        return {"state": ok, "label": "OK"}
    return {"state": missing, "label": "Missing"}


def _providers_for_guid_or_name(guid: Optional[str], manufacturer: Optional[str], console: Optional[str]) -> List[Dict]:
    try:
        root = load_providers().get("console_root") or {}
    except Exception:
        return []
    matches: List[Dict] = []
    for maker, consoles in root.items():
        if not isinstance(consoles, dict):
            continue
        for console_name, entry in consoles.items():
            if manufacturer and console and maker == manufacturer and console_name == console:
                matches.extend(_entry_list(entry))
                continue
            if not guid:
                continue
            for candidate in _entry_list(entry):
                candidate_guid = candidate.get("libretro_guid") or candidate.get("guid")
                if candidate_guid == guid:
                    matches.append(candidate)
    return matches


def _entry_list(entry) -> List[Dict]:
    if isinstance(entry, list):
        return [item for item in entry if isinstance(item, dict)]
    if isinstance(entry, dict):
        return [entry]
    return []


def _provider_coverage(
    manufacturer: Optional[str],
    console: Optional[str],
    guid: Optional[str],
    rdb_exists: bool,
    rdb_path: Optional[Path],
) -> Dict[str, object]:
    if not manufacturer or not console:
        return {"state": "missing", "label": "No module name"}
    if not rdb_exists or not rdb_path:
        return {"state": "missing", "label": "RDB missing"}
    try:
        catalog = build_rom_catalog(manufacturer, console, module_guid=guid, rdb_path=rdb_path)
    except Exception as exc:
        return {"state": "missing", "label": str(exc)}
    roms = catalog.get("roms") or []
    rdb_roms = [rom for rom in roms if not rom.get("provider_only")]
    provider_only = [rom for rom in roms if rom.get("provider_only")]
    total = len(rdb_roms)
    matched = sum(1 for rom in rdb_roms if int(rom.get("_provider_count") or 0) > 0)
    provider_only_downloadable = sum(1 for rom in provider_only if rom.get("http_url") or rom.get("torrent_url"))
    provider_total = int(catalog.get("provider_total") or 0)
    percent = round((matched / total) * 100, 1) if total else 0
    state = "ok" if matched else "missing"
    if matched and total and matched < total:
        state = "partial"
    label = f"{matched}/{total} ({percent}%)"
    if provider_only:
        label += f" + {len(provider_only)} provider-only"
    return {
        "state": state,
        "label": label,
        "matched": matched,
        "total": total,
        "percent": percent,
        "provider_total": provider_total,
        "provider_only": len(provider_only),
        "provider_only_downloadable": provider_only_downloadable,
        "catalog_total": len(roms),
    }


def _coverage_not_checked(provider_entries: List[Dict], rdb_exists: bool) -> Dict[str, object]:
    if not provider_entries:
        return {"state": "missing", "label": "No providers"}
    if not rdb_exists:
        return {"state": "missing", "label": "RDB missing"}
    return {"state": "unknown", "label": "Not checked"}


def _core_options_for_guid(guid: Optional[str]) -> List[Tuple[str, Dict]]:
    if not guid:
        return []
    registry = load_registry()
    cores = registry.get("cores") or {}
    return [
        (core_id, meta)
        for core_id, meta in sorted(cores.items())
        if guid in (meta.get("console_guids") or [])
    ]


def _core_status(core_options: List[Tuple[str, Dict]], frontend: Dict) -> Dict[str, object]:
    if not core_options:
        return {"state": "missing", "label": "No core mapped"}
    external = _external_runtime_status(core_options)
    if external:
        return external
    cores_root = Path(frontend.get("cores_path") or "").expanduser()
    installed = []
    mapped = []
    for core_id, meta in core_options:
        mapped.append(core_id)
        core_file = meta.get("core_file") or core_id
        core_path = cores_root / f"{core_file}_libretro.so"
        if core_path.exists():
            installed.append({
                "id": core_id,
                "name": _core_display_name(cores_root, core_file) or meta.get("name") or core_id,
                "path": str(core_path),
            })
    if installed:
        return {"state": "ok", "label": installed[0]["name"], "installed": installed, "mapped": mapped}
    return {"state": "missing", "label": f"Missing core ({', '.join(mapped)})", "installed": [], "mapped": mapped}


def _has_external_runtime(core_options: List[Tuple[str, Dict]]) -> bool:
    return any(meta.get("kind") == "external_emulator" for _core_id, meta in core_options)


def _external_runtime_status(core_options: List[Tuple[str, Dict]]) -> Optional[Dict[str, object]]:
    installed = []
    mapped = []
    for core_id, meta in core_options:
        if meta.get("kind") != "external_emulator":
            continue
        mapped.append(core_id)
        launcher = meta.get("launcher") or meta.get("command") or core_id
        executable = str(launcher).split()[0]
        resolved = shutil.which(executable)
        if not resolved and executable.startswith("~/"):
            candidate = Path(executable).expanduser()
            if candidate.exists():
                resolved = str(candidate)
        if resolved:
            installed.append({
                "id": core_id,
                "name": meta.get("name") or core_id,
                "launcher": executable,
            })
    if not mapped:
        return None
    if installed:
        return {"state": "ok", "label": installed[0]["name"], "installed": installed, "mapped": mapped}
    return {"state": "missing", "label": f"Missing external runtime ({', '.join(mapped)})", "installed": [], "mapped": mapped}


def _frontend_for_core_options(core_options: List[Tuple[str, Dict]]) -> Tuple[Optional[str], Dict]:
    external_install_types = {
        str(meta.get("install_type") or core_id).lower()
        for core_id, meta in core_options
        if meta.get("kind") == "external_emulator"
    }
    if not external_install_types:
        return active_frontend()
    frontends = (load_storage_config() or {}).get("frontends") or {}
    for key, frontend in frontends.items():
        if not isinstance(frontend, dict):
            continue
        if frontend.get("kind") != "external_emulator":
            continue
        install_type = str(frontend.get("install_type") or key).lower()
        if install_type in external_install_types:
            return key, frontend
    return active_frontend()


def _external_install_status() -> Dict[str, object]:
    return {
        "state": "missing",
        "label": "External package staging only",
    }


def _external_playlist_status() -> Dict[str, object]:
    return {
        "state": "ok",
        "label": "No RetroArch playlist required",
    }


def _external_firmware_status(core_options: List[Tuple[str, Dict]]) -> Optional[Dict[str, object]]:
    for core_id, meta in core_options:
        if meta.get("kind") != "external_emulator":
            continue
        firmware = meta.get("firmware")
        if not isinstance(firmware, dict):
            continue
        install_type = str(meta.get("install_type") or core_id).lower()
        if install_type == "vita3k":
            return _vita3k_firmware_status(firmware)
    return None


def _vita3k_firmware_status(firmware: Dict[str, object]) -> Dict[str, object]:
    markers = firmware.get("installed_markers")
    if not isinstance(markers, list):
        markers = []
    present = []
    for marker in markers:
        if not isinstance(marker, str) or not marker:
            continue
        path = Path(marker).expanduser()
        if path.exists():
            present.append(path.name)
    minimum = int(firmware.get("minimum_markers") or 1)
    if len(present) >= minimum:
        return {
            "state": "ok",
            "label": firmware.get("ready_label") or "Vita3K firmware installed",
            "requirements": [],
        }
    return {
        "state": "missing",
        "label": firmware.get("missing_label") or "Install Vita3K firmware",
        "requirements": [],
    }


def _bios_status_for_guid(guid: Optional[str]) -> Dict[str, object]:
    requirements = [req for req in list_bios_requirements(guid) if req.get("bios")]
    if not requirements:
        return {"state": "ok", "label": "No BIOS required", "requirements": []}
    states = [(req.get("status") or {}).get("state") for req in requirements]
    if all(state == "ok" for state in states):
        label = "OK" if len(requirements) == 1 else f"{len(requirements)} OK"
        return {"state": "ok", "label": label, "requirements": requirements}
    missing = [req for req in requirements if (req.get("status") or {}).get("state") != "ok"]
    label = ", ".join((req.get("bios") or {}).get("filename") or "BIOS" for req in missing[:2])
    if len(missing) > 2:
        label += f" +{len(missing) - 2}"
    return {"state": "missing", "label": label or "Missing BIOS", "requirements": requirements}


def _install_status(manufacturer: Optional[str], console: Optional[str], frontend: Dict) -> Dict[str, object]:
    if not manufacturer or not console:
        return {"state": "missing", "label": "No console"}
    roms_root = Path(frontend.get("roms_path") or "").expanduser()
    console_dir = roms_root / f"{manufacturer} - {console}"
    if not console_dir.exists():
        return {"state": "missing", "label": "No local ROMs", "path": str(console_dir)}
    files = [path for path in console_dir.rglob("*") if path.is_file() and ".mame_cmd" not in path.parts]
    if not files:
        return {"state": "missing", "label": "No local ROMs", "path": str(console_dir)}
    return {"state": "ok", "label": f"{len(files)} files", "path": str(console_dir), "files": len(files)}


def _playlist_status(name: str, frontend: Dict, core_status: Dict[str, object]) -> Dict[str, object]:
    playlists_root = Path(frontend.get("playlists_path") or "").expanduser()
    playlist_path = playlists_root / f"{name}.lpl"
    if not playlist_path.exists():
        return {"state": "missing", "label": "No playlist", "path": str(playlist_path)}
    try:
        payload = json.loads(playlist_path.read_text())
    except Exception:
        return {"state": "invalid", "label": "Invalid playlist", "path": str(playlist_path)}
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return {"state": "missing", "label": "Empty playlist", "path": str(playlist_path)}
    missing_paths = 0
    missing_cores = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        item_path = item.get("path")
        core_path = item.get("core_path")
        if item_path and not Path(item_path).expanduser().exists():
            missing_paths += 1
        if core_path and core_path != "DETECT" and not Path(core_path).expanduser().exists():
            missing_cores += 1
    if missing_paths or missing_cores:
        return {
            "state": "stale",
            "label": f"Stale ({missing_paths} paths, {missing_cores} cores)",
            "path": str(playlist_path),
            "items": len(items),
        }
    if core_status.get("state") == "ok":
        detect = sum(1 for item in items if isinstance(item, dict) and item.get("core_path") == "DETECT")
        if detect:
            return {"state": "stale", "label": f"{detect} DETECT items", "path": str(playlist_path), "items": len(items)}
    return {"state": "ok", "label": f"{len(items)} items", "path": str(playlist_path), "items": len(items)}


def _install_strategy_for_guid(guid: Optional[str]) -> str:
    if not guid:
        return "standard_libretro"
    registry = load_registry()
    for meta in (registry.get("cores") or {}).values():
        if guid in (meta.get("console_guids") or []):
            strategy = meta.get("install_strategy")
            if isinstance(strategy, dict) and strategy.get("console_guid") and strategy.get("console_guid") != guid:
                continue
            return strategy or "standard_libretro"
    return "standard_libretro"


def _install_strategy_label(strategy) -> str:
    if isinstance(strategy, dict):
        return strategy.get("type") or "custom"
    return str(strategy or "standard_libretro")


def _core_display_name(cores_root: Path, core_id: str) -> Optional[str]:
    info = cores_root / f"{core_id}_libretro.info"
    if not info.exists():
        return None
    for line in info.read_text(errors="ignore").splitlines():
        if line.startswith("display_name"):
            return line.split("=", 1)[1].strip().strip('"')
    return None
