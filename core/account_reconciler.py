import json
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

from data.storage.frontend_detector import detect_frontends
from data.storage.storage_config_loader import CONFIG_PATH
from core.frontend_installer import install_completed_jobs
from utils.cores_registry import load_registry
from utils.paths import CACHE_DIR, console_slug, manufacturer_slug, provider_slug

SUPPORTED_AUTO_COLLECTION_SUFFIXES = {".zip"}


def reconcile_assigned_consoles(download_manager, install_ready: bool = True) -> Dict[str, object]:
    """Queue safe provider collection archives from the synced account cache.

    The synced backend cache is already filtered by the client's API key. This
    helper intentionally queues only collection-level exports, represented as a
    provider export with one downloadable archive. It does not automatically
    queue every per-ROM source for large systems.
    """
    report: Dict[str, object] = {
        "collection_sources_seen": 0,
        "jobs_created": 0,
        "jobs_existing": 0,
        "jobs_completed": 0,
        "install_report": None,
        "skipped": [],
        "errors": [],
    }
    queued_keys: Set[Tuple[str, str, str]] = set()
    queued_consoles: Set[Tuple[str, str]] = set()

    for catalog in _iter_provider_exports():
        roms = catalog.get("roms")
        if not isinstance(roms, list) or len(roms) != 1:
            continue
        rom = roms[0]
        http_url = rom.get("http_url")
        torrent = rom.get("torrent_url") or rom.get("torrent")
        if not http_url and not torrent:
            report["skipped"].append(f"{catalog.get('archive_id') or catalog.get('console')}: no download URL")
            continue
        rom_name = rom.get("name") or catalog.get("archive_id") or "provider_collection.zip"
        suffix = Path(rom_name).suffix.lower()
        if suffix and suffix not in SUPPORTED_AUTO_COLLECTION_SUFFIXES:
            report["skipped"].append(f"{rom_name}: unsupported auto-install archive")
            continue

        manufacturer = catalog.get("manufacturer") or rom.get("manufacturer") or "Unknown"
        console = catalog.get("console") or rom.get("console") or "Unknown"
        console_key = (manufacturer, console)
        if console_key in queued_consoles:
            report["skipped"].append(f"{manufacturer}/{console}: duplicate collection archive")
            continue
        archive_id = catalog.get("archive_id") or rom.get("archive_id") or rom.get("name")
        queue_key = (manufacturer, console, archive_id or rom.get("name") or "")
        if queue_key in queued_keys:
            continue
        queued_keys.add(queue_key)
        queued_consoles.add(console_key)
        report["collection_sources_seen"] += 1

        target_segments = [
            "downloads",
            manufacturer_slug(manufacturer),
            console_slug(console),
        ]
        if archive_id:
            target_segments.append(str(archive_id))
        target_dir = str(Path(*target_segments))

        try:
            job = download_manager.add_job(
                rom_name=rom_name,
                source=None if http_url else torrent,
                http_url=http_url,
                destination=target_dir,
                console=console,
                manufacturer=manufacturer,
                size_bytes=rom.get("size"),
                md5=rom.get("md5"),
                provider_slug=provider_slug(archive_id),
                auto_install=True,
            )
        except Exception as exc:
            report["errors"].append(str(exc))
            continue

        if job.get("status") == "completed":
            report["jobs_completed"] += 1
        elif job.get("protocol") == "local":
            report["jobs_existing"] += 1
        else:
            report["jobs_created"] += 1

    if install_ready:
        completed_jobs = [
            job
            for job in download_manager.list_jobs()
            if job.get("auto_install") and job.get("status") == "completed"
        ]
        if completed_jobs:
            try:
                report["install_report"] = install_completed_jobs(completed_jobs)
            except Exception as exc:
                report["errors"].append(str(exc))

    return report


def activate_assigned_frontend_consoles(manifest: Dict | None = None) -> Dict[str, object]:
    """Add backend-assigned console GUIDs to compatible local frontends.

    This only mutates the client's local storage config. It never writes to the
    backend and is meant to make local frontend integration follow account access.
    The write is based on the raw config file so detected local launcher paths
    are not persisted into the tracked project config as a side effect.
    """
    config = _load_raw_storage_config()
    config, _added = _merge_detected_frontends(config)
    frontends = config.get("frontends") or {}
    detected_keys = set(detect_frontends().keys())
    local_frontends = [
        (key, entry)
        for key, entry in frontends.items()
        if isinstance(entry, dict) and _is_local_frontend(key, entry, detected_keys)
    ]
    if not local_frontends:
        return {
            "frontend": None,
            "frontends": [],
            "assigned": 0,
            "added": 0,
            "removed": 0,
            "changed": False,
        }

    assigned_guids = _assigned_guids(manifest)
    revoked_guids = _revoked_guids()
    reports: List[Dict[str, object]] = []
    total_added = 0
    total_removed = 0
    changed = False

    for frontend_key, frontend in local_frontends:
        supported = list(frontend.get("supported_guids") or [])
        filtered_supported = [guid for guid in supported if guid not in revoked_guids]
        removed = len(supported) - len(filtered_supported)
        supported = filtered_supported
        seen = set(supported)
        added = 0
        for guid in assigned_guids:
            if not _frontend_can_support_guid(frontend, guid):
                continue
            if guid and guid not in seen:
                supported.append(guid)
                seen.add(guid)
                added += 1

        if added > 0 or removed > 0:
            frontend["supported_guids"] = supported
            frontends[frontend_key] = frontend
            changed = True

        total_added += added
        total_removed += removed
        reports.append(
            {
                "key": frontend_key,
                "frontend": frontend.get("name") or frontend_key,
                "assigned": len(assigned_guids),
                "added": added,
                "removed": removed,
                "supported": len(supported),
            }
        )

    if changed:
        config["frontends"] = frontends
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")

    frontend_names = ", ".join(str(report["frontend"]) for report in reports)
    return {
        "frontend": frontend_names,
        "frontends": reports,
        "assigned": len(assigned_guids),
        "added": total_added,
        "removed": total_removed,
        "changed": changed,
    }


def _load_raw_storage_config() -> Dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _merge_detected_frontends(config: Dict) -> Tuple[Dict, int]:
    from data.storage.frontend_detector import merge_detected_frontends

    return merge_detected_frontends(config)


def _is_local_frontend(key: str, entry: Dict[str, object], detected_keys: Set[str]) -> bool:
    return _is_retroarch_frontend(key, entry, detected_keys) or _is_external_frontend(key, entry, detected_keys)


def _is_retroarch_frontend(key: str, entry: Dict[str, object], detected_keys: Set[str]) -> bool:
    if key in detected_keys:
        detected = detect_frontends().get(key) or {}
        if detected.get("kind") == "external_emulator":
            return False
        return True
    if entry.get("kind") == "retroarch":
        return True
    return str(entry.get("install_type") or "").lower() in {"steam", "flatpak", "native"}


def _is_external_frontend(key: str, entry: Dict[str, object], detected_keys: Set[str]) -> bool:
    if key in detected_keys and entry.get("kind") == "external_emulator":
        return True
    return entry.get("kind") == "external_emulator"


def _frontend_can_support_guid(frontend: Dict[str, object], guid: str) -> bool:
    if not guid:
        return False
    explicit = set(frontend.get("supported_guids") or [])
    if guid in explicit:
        return True
    registry = load_registry()
    kind = frontend.get("kind")
    install_type = str(frontend.get("install_type") or "").lower()
    for meta in (registry.get("cores") or {}).values():
        if guid not in (meta.get("console_guids") or []):
            continue
        if kind == "external_emulator" or install_type in {"vita3k"}:
            return meta.get("kind") == "external_emulator" and meta.get("install_type") == install_type
        if meta.get("kind") == "external_emulator":
            continue
        return True
    return False


def _assigned_guids(manifest: Dict | None = None) -> List[str]:
    if manifest:
        datasets = manifest.get("datasets") or {}
        modules = datasets.get("modules_list") or manifest.get("modules") or []
        guids = [module.get("guid") for module in modules if module.get("guid")]
        if guids:
            return guids

    state_path = Path(CACHE_DIR).parent / "client" / "sync_state.json"
    if not state_path.exists():
        return []
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    assigned = state.get("assigned") if isinstance(state, dict) else {}
    if not isinstance(assigned, dict):
        return []
    return [guid for guid in assigned.keys() if guid]


def _revoked_guids() -> Set[str]:
    state_path = Path(CACHE_DIR).parent / "client" / "sync_state.json"
    if not state_path.exists():
        return set()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    revoked = state.get("revoked") if isinstance(state, dict) else {}
    if not isinstance(revoked, dict):
        return set()
    return {guid for guid in revoked.keys() if guid}


def _iter_provider_exports() -> Iterable[Dict]:
    cache_root = Path(CACHE_DIR)
    if not cache_root.exists():
        return []
    catalogs: List[Dict] = []
    for path in sorted(cache_root.glob("*/*/*/exports/*_roms*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            catalogs.append(payload)
    return catalogs
