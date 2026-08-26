from typing import Dict, List

from core.revoked_access import update_assignments_from_manifest
from utils.backend_client import (
    BackendError,
    download_cache_archive,
    download_rom_dataset,
    fetch_client_sync_manifest,
    fetch_modules_snapshot,
    fetch_providers_snapshot,
    fetch_rom_catalog_metadata,
    save_modules_snapshot,
    save_providers_snapshot,
    save_rom_dataset,
)


def sync_client_metadata(include_cache: bool = True) -> Dict[str, object]:
    """Sync backend-assigned client metadata for the current API key."""
    manifest = fetch_client_sync_manifest()
    revoked = update_assignments_from_manifest(manifest)

    modules_snapshot = fetch_modules_snapshot()
    modules_path = save_modules_snapshot(modules_snapshot)

    providers_snapshot = fetch_providers_snapshot()
    providers_path = save_providers_snapshot(providers_snapshot)

    rom_catalog = fetch_rom_catalog_metadata()
    saved_rom_paths: List[str] = []
    for entry in rom_catalog.get("roms") or []:
        identifier = entry.get("slug") or entry.get("guid")
        if not identifier:
            continue
        dataset = download_rom_dataset(identifier)
        saved_rom_paths.append(str(save_rom_dataset(dataset)))

    cache_result = None
    if include_cache:
        try:
            cache_result = download_cache_archive()
        except BackendError as exc:
            cache_result = {"error": str(exc)}

    return {
        "manifest": manifest,
        "revoked": revoked,
        "modules_path": str(modules_path),
        "providers_path": str(providers_path),
        "rom_paths": saved_rom_paths,
        "cache": cache_result,
    }


def assignment_signature(manifest: Dict) -> str:
    modules = manifest.get("modules") or []
    guids = sorted(module.get("guid") for module in modules if module.get("guid"))
    datasets = manifest.get("datasets") or {}
    parts = [
        ",".join(guids),
        str(datasets.get("modules", {}).get("version")),
        str(datasets.get("providers", {}).get("version")),
        str(datasets.get("roms", {}).get("version")),
        str(datasets.get("cache", {}).get("version")),
    ]
    return "|".join(parts)
