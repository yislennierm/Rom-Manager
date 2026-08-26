import json
import shutil
from pathlib import Path
from typing import Dict, Iterable, List

from data.storage.storage_config_loader import load_storage_config
from utils.paths import DATA_DIR, slugify


SYNC_STATE_PATH = Path(DATA_DIR) / "client" / "sync_state.json"


def load_sync_state() -> Dict:
    if not SYNC_STATE_PATH.exists():
        return {"assigned": {}, "revoked": {}}
    try:
        payload = json.loads(SYNC_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"assigned": {}, "revoked": {}}
    if not isinstance(payload, dict):
        return {"assigned": {}, "revoked": {}}
    payload.setdefault("assigned", {})
    payload.setdefault("revoked", {})
    return payload


def save_sync_state(state: Dict) -> None:
    SYNC_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def update_assignments_from_manifest(manifest: Dict) -> List[Dict]:
    """Record current backend assignments and return newly revoked consoles."""
    current = _assignment_map(manifest)
    state = load_sync_state()
    previous = state.get("assigned") or {}
    revoked = state.get("revoked") or {}

    for guid, entry in previous.items():
        if guid not in current:
            revoked[guid] = {
                **entry,
                **_local_paths(entry),
                "cleanup_status": revoked.get(guid, {}).get("cleanup_status", "pending"),
            }

    for guid in current:
        revoked.pop(guid, None)

    assigned_modules = {
        entry.get("module")
        for entry in current.values()
        if entry.get("module")
    }
    for guid, entry in list(revoked.items()):
        if entry.get("module") in assigned_modules:
            revoked.pop(guid, None)
    for local_entry in _local_unassigned_playlists(assigned_modules):
        guid = local_entry["guid"]
        revoked.setdefault(guid, local_entry)

    state["assigned"] = current
    state["revoked"] = revoked
    save_sync_state(state)
    return list(revoked.values())


def list_revoked_consoles() -> List[Dict]:
    state = load_sync_state()
    return list((state.get("revoked") or {}).values())


def cleanup_revoked_console(guid: str, action: str) -> Dict[str, object]:
    state = load_sync_state()
    revoked = state.get("revoked") or {}
    entry = revoked.get(guid)
    if not entry:
        raise KeyError(f"Revoked console '{guid}' not found.")

    result: Dict[str, object] = {
        "guid": guid,
        "action": action,
        "roms_removed": False,
        "playlist_removed": False,
        "playlist_disabled": False,
        "errors": [],
    }

    roms_path = Path(entry.get("roms_path") or "").expanduser()
    playlist_path = Path(entry.get("playlist_path") or "").expanduser()

    if action == "keep":
        entry["cleanup_status"] = "kept"
    elif action == "disable_playlist":
        if playlist_path.exists():
            disabled = playlist_path.with_suffix(playlist_path.suffix + ".disabled")
            try:
                playlist_path.rename(disabled)
                entry["playlist_path"] = str(disabled)
                result["playlist_disabled"] = True
            except Exception as exc:
                result["errors"].append(str(exc))
        entry["cleanup_status"] = "playlist_disabled"
    elif action == "delete_local":
        if playlist_path.exists():
            try:
                playlist_path.unlink()
                result["playlist_removed"] = True
            except Exception as exc:
                result["errors"].append(str(exc))
        if roms_path.exists() and roms_path.is_dir():
            try:
                shutil.rmtree(roms_path)
                result["roms_removed"] = True
            except Exception as exc:
                result["errors"].append(str(exc))
        entry["cleanup_status"] = "deleted"
    else:
        raise ValueError(f"Unknown cleanup action '{action}'.")

    if not result["errors"]:
        revoked.pop(guid, None)
    else:
        revoked[guid] = entry
    state["revoked"] = revoked
    save_sync_state(state)
    return result


def _assignment_map(manifest: Dict) -> Dict[str, Dict]:
    datasets = manifest.get("datasets") or {}
    modules = datasets.get("modules_list") or manifest.get("modules") or []
    if not modules:
        modules = _modules_from_manifest_countless(manifest)
    assignments: Dict[str, Dict] = {}
    for module in modules:
        guid = module.get("guid")
        if not guid:
            continue
        manufacturer, console = _split_module_name(module.get("name"))
        assignments[guid] = {
            "guid": guid,
            "module": module.get("name"),
            "manufacturer": manufacturer,
            "console": console,
        }
    return assignments


def _modules_from_manifest_countless(manifest: Dict) -> Iterable[Dict]:
    # Backward-compatible fallback; newer TUI calls inject modules into the manifest.
    return []


def _split_module_name(name: str | None) -> tuple[str, str]:
    if not name:
        return "Unknown", "Unknown"
    if " - " in name:
        manufacturer, console = name.split(" - ", 1)
        return manufacturer.strip(), console.strip()
    return "Unknown", name.strip()


def _local_paths(entry: Dict) -> Dict[str, str]:
    manufacturer = entry.get("manufacturer") or "Unknown"
    console = entry.get("console") or "Unknown"
    console_name = f"{manufacturer} - {console}"
    frontend = _active_frontend()
    roms_root = Path(frontend.get("roms_path", "~/ROMs")).expanduser()
    retroarch_root = _retroarch_root(roms_root)
    playlists_root = Path(frontend.get("playlists_path") or retroarch_root / "playlists").expanduser()
    return {
        "roms_path": str(roms_root / console_name),
        "playlist_path": str(playlists_root / f"{console_name}.lpl"),
    }


def _local_unassigned_playlists(assigned_modules: set[str]) -> List[Dict]:
    frontend = _active_frontend()
    roms_root = Path(frontend.get("roms_path", "~/ROMs")).expanduser()
    retroarch_root = _retroarch_root(roms_root)
    playlists_root = Path(frontend.get("playlists_path") or retroarch_root / "playlists").expanduser()
    if not playlists_root.exists():
        return []

    revoked: List[Dict] = []
    for playlist in sorted(playlists_root.glob("*.lpl")):
        module_name = playlist.stem
        if module_name in assigned_modules:
            continue
        manufacturer, console = _split_module_name(module_name)
        revoked.append(
            {
                "guid": f"local:{slugify(module_name)}",
                "module": module_name,
                "manufacturer": manufacturer,
                "console": console,
                "roms_path": str(roms_root / module_name),
                "playlist_path": str(playlist),
                "cleanup_status": "pending",
                "source": "local_playlist",
            }
        )
    return revoked


def _active_frontend() -> Dict:
    config = load_storage_config()
    frontends = config.get("frontends") or {}
    for entry in frontends.values():
        if entry.get("active"):
            return entry
    if frontends:
        return next(iter(frontends.values()))
    return {}


def _retroarch_root(roms_root: Path) -> Path:
    expanded = roms_root.expanduser()
    if expanded.name == "downloads":
        return expanded.parent
    return expanded.parent
