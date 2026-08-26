import json
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

from data.storage.storage_config_loader import CONFIG_PATH, load_storage_config
from core.frontend_installer import install_completed_jobs
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
    """Add backend-assigned console GUIDs to the active local frontend config.

    This only mutates the client's local storage config. It never writes to the
    backend and is meant to make RetroArch integration follow account access.
    """
    config = load_storage_config() or {}
    frontends = config.get("frontends") or {}
    frontend_key = None
    frontend = None
    for key, entry in frontends.items():
        if entry.get("active"):
            frontend_key = key
            frontend = entry
            break
    if frontend is None and frontends:
        frontend_key, frontend = next(iter(frontends.items()))
    if not frontend_key or frontend is None:
        return {"frontend": None, "assigned": 0, "added": 0, "changed": False}

    assigned_guids = _assigned_guids(manifest)
    revoked_guids = _revoked_guids()
    supported = list(frontend.get("supported_guids") or [])
    filtered_supported = [guid for guid in supported if guid not in revoked_guids]
    removed = len(supported) - len(filtered_supported)
    supported = filtered_supported
    seen = set(supported)
    added = 0
    for guid in assigned_guids:
        if guid and guid not in seen:
            supported.append(guid)
            seen.add(guid)
            added += 1

    changed = added > 0 or removed > 0
    if changed:
        frontend["supported_guids"] = supported
        frontends[frontend_key] = frontend
        config["frontends"] = frontends
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")

    return {
        "frontend": frontend.get("name") or frontend_key,
        "assigned": len(assigned_guids),
        "added": added,
        "removed": removed,
        "changed": changed,
    }


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
