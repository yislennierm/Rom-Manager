import json
from pathlib import Path
from typing import Dict, List

CORE_PATH = Path("data/emulators/cores.json")


def _empty_registry() -> Dict:
    return {"bios_files": {}, "cores": {}}


def load_registry() -> Dict:
    if not CORE_PATH.exists():
        return _empty_registry()
    try:
        data = json.loads(CORE_PATH.read_text())
    except Exception:
        return _empty_registry()
    if not isinstance(data, dict):
        return _empty_registry()
    data.setdefault("bios_files", {})
    data.setdefault("cores", {})
    return data


def save_registry(registry: Dict) -> None:
    registry = registry or _empty_registry()
    registry.setdefault("bios_files", {})
    registry.setdefault("cores", {})
    CORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORE_PATH.write_text(json.dumps(registry, indent=2))


def list_cores() -> List[tuple[str, Dict]]:
    registry = load_registry()
    cores = registry.get("cores", {})
    return sorted(cores.items(), key=lambda item: item[0])


def list_bios() -> List[tuple[str, Dict]]:
    registry = load_registry()
    bios = registry.get("bios_files", {})
    return sorted(bios.items(), key=lambda item: item[0])


def upsert_core(core_id: str, payload: Dict) -> None:
    registry = load_registry()
    cores = registry.setdefault("cores", {})
    cores[core_id] = payload
    save_registry(registry)


def delete_core(core_id: str) -> None:
    registry = load_registry()
    cores = registry.setdefault("cores", {})
    if core_id in cores:
        del cores[core_id]
        save_registry(registry)


def upsert_bios(bios_id: str, payload: Dict) -> None:
    registry = load_registry()
    bios = registry.setdefault("bios_files", {})
    bios[bios_id] = payload
    save_registry(registry)


def delete_bios(bios_id: str) -> None:
    registry = load_registry()
    bios = registry.setdefault("bios_files", {})
    if bios_id in bios:
        del bios[bios_id]
        save_registry(registry)
