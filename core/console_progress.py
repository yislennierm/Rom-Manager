import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from utils.cores_registry import load_registry
from utils.library_sync import rdb_json_path
from utils.paths import slugify


BACKEND_DATA_ROOT = Path("backend/data")
PROGRESS_PATH = BACKEND_DATA_ROOT / "console_progress.json"
MODULES_PATH = BACKEND_DATA_ROOT / "index" / "libretro_modules.json"
BACKEND_ROMS_DIR = BACKEND_DATA_ROOT / "roms"
BACKEND_PROVIDERS_PATH = BACKEND_DATA_ROOT / "providers" / "providers.json"

NON_CONSOLE_PREFIXES = (
    "Commodore -",
    "Microsoft - MSX",
    "Microsoft - Xbox",
    "DOS",
    "ScummVM",
    "Sinclair -",
    "Amstrad -",
    "Thomson -",
    "Sharp -",
    "Atari - ST",
    "NEC - PC-98",
)
NON_CONSOLE_NAMES = {
    "Atari - 8-bit",
    "Dinothawr",
    "DOOM",
    "Cave Story",
    "Bomberman Game Clone",
    "Lutro",
    "Quake1",
    "Quake II",
    "Quake III",
    "Tomb Raider",
    "ChaiLove",
    "Rick Dangerous",
    "RPG Maker",
    "MAME",
    "Flashback",
    "Cannonball",
    "TIC-80",
    "LowRes NX",
    "WASM-4",
    "Wolfenstein 3D",
    "Handheld Electronic Game",
    "FBNeo - Arcade Games",
    "Atomiswave",
    "Jump 'n Bump",
    "NEC - PC-8001 - PC-8801",
    "Sega - Naomi",
    "Sega - Naomi 2",
    "Spectravideo - SVI-318 - SVI-328",
    "Vircon32",
}

CONSOLE_NAME_ALLOWLIST = {
    "Amstrad - GX4000",
    "Commodore - CD32",
    "Commodore - CDTV",
    "Microsoft - Xbox",
    "Microsoft - Xbox 360",
}


def load_progress() -> Dict:
    if not PROGRESS_PATH.exists():
        return {"version": 1, "consoles": {}}
    try:
        payload = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "consoles": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "consoles": {}}
    payload.setdefault("version", 1)
    payload.setdefault("consoles", {})
    return payload


def save_progress(payload: Dict) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = datetime.utcnow().isoformat()
    PROGRESS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def seed_known_progress() -> Dict:
    payload = load_progress()
    consoles = payload.setdefault("consoles", {})
    known = {
        "5656b00c-8050-57bd-93ed-0ba9bb176580": ("Atari - Lynx", "runtime_validated", "User previously confirmed Lynx flow worked."),
        "3a5c3ea5-f514-59fa-9039-75d98e1ca63c": ("Hartung - Game Master", "runtime_validated", "Validated through MAME softlist after BIOS/hash troubleshooting."),
        "a69e967b-f947-5962-8578-03ca389c6231": ("GamePark - GP32", "runtime_validated", "Validated through MAME softlist with gp32 BIOS/hash."),
        "0acbb3bd-c595-56ea-893f-60d6d25aa2a4": ("GCE - Vectrex", "runtime_validated", "User confirmed Vectrex worked."),
        "7b81f262-ec70-5234-928d-207322439020": ("Funtech - Super Acan", "runtime_validated", "Validated after Super Acan BIOS/hash install."),
        "fa71743b-728a-59a1-abe8-ce3ec78be77e": ("Fairchild - Channel F", "runtime_validated", "User confirmed runtime worked."),
        "947cc03e-2c72-5cd0-92d9-8f69f946d59f": ("Epoch - Super Cassette Vision", "runtime_validated", "User confirmed runtime worked."),
        "2de2782e-37a2-5e1c-9f4e-ffbf96872531": ("Entex - Adventure Vision", "runtime_validated", "User confirmed Adventure Vision worked after disk-space check."),
        "b7e90431-7e83-585a-90ed-eb636b009779": ("Emerson - Arcadia 2001", "runtime_validated", "User confirmed runtime worked."),
        "72c1d004-1fce-5e54-9936-f41a1bcfe733": ("Coleco - ColecoVision", "runtime_validated", "Validated after coleco.rom BIOS install."),
        "71b2e78e-d776-59a6-8bfa-5f84f90e7eca": ("Casio - PV-1000", "runtime_validated", "User confirmed runtime worked."),
        "632b56c7-fdb2-5eff-81fc-0d9a5be9e208": ("Casio - Loopy", "runtime_validated", "User confirmed runtime worked."),
        "595c8d22-7e81-5e3b-a7f3-52ae0cef34cd": ("Bandai - WonderSwan", "runtime_validated", "Playlist generation issue was fixed and user confirmed."),
        "8f06706f-2b37-5d8a-94cc-028c7beef756": ("Sega - PICO", "runtime_validated", "User confirmed runtime worked."),
        "63aab22a-8415-5a15-8d82-f70c218298f6": ("Sega - Mega-CD - Sega CD", "runtime_validated", "User confirmed runtime worked after BIOS/source validation."),
        "126fc9a7-f783-5a73-9c17-b1e497cc8edd": ("Sega - Mega Drive - Genesis", "runtime_validated", "User confirmed runtime worked."),
        "acc73d91-7629-55cb-aeca-7b90814d8604": ("Sega - Master System - Mark III", "runtime_validated", "User confirmed runtime worked."),
        "fdb51c61-0560-593b-937f-cccae600d469": ("Sega - Game Gear", "runtime_validated", "User confirmed runtime worked."),
        "2530a9bf-3926-52a9-b3fd-401ed1655bcc": ("Sega - Dreamcast", "runtime_validated", "User confirmed runtime worked after BIOS check."),
        "2bf97080-34ce-529a-9e1c-67a5cdab9070": ("Sega - 32X", "runtime_validated", "User confirmed runtime worked."),
        "e9d5a0d5-42af-5654-848c-a29ea154ba0f": ("SNK - Neo Geo", "runtime_validated", "Validated with caveat: arcade set/core compatibility needs later refinement."),
        "d1c9402f-aa02-5f19-a846-449517ecd7c0": ("SNK - Neo Geo Pocket", "runtime_validated", "User confirmed runtime worked after playlist fix."),
        "ef04915a-086b-574d-aa82-e99c2892c331": ("SNK - Neo Geo Pocket Color", "runtime_validated", "User confirmed runtime worked."),
        "f86b3c1b-7bf6-5538-b5f3-712173dce0b0": ("SNK - Neo Geo CD", "runtime_validated", "User confirmed runtime worked."),
        "713057d9-8ae9-5b0a-9d1b-cf121b9357ae": ("RCA - Studio II", "runtime_validated", "Runs with caveats: MAME softlist, game focus, F3/keypad startup."),
        "96d28972-81ec-55c8-a748-b621d6612d1c": ("Bandai - WonderSwan Color", "runtime_validated", "User confirmed WonderSwan Color worked."),
        "d4874312-2442-5d55-b0a1-8fbd6b5bb68e": ("Atari - 2600", "runtime_validated", "User considered Atari 2600 already happy; smoke test passed."),
        "f47fa56b-1c6e-5364-a344-9fafc6ba4bf0": ("Atari - 5200", "runtime_validated", "A5200 smoke test passed; BIOS present."),
        "123de432-6905-5c13-b785-0c643bf5cb09": ("Atari - 7800", "runtime_validated", "ProSystem smoke test passed with Food Fight."),
        "a7d91fb9-7cd4-51ff-9dfd-44a90ad355de": ("Nintendo - Game Boy", "runtime_validated", "Gambatte smoke test passed with Dr. Mario Rev 0 from the TOSEC provider archive."),
        "6014b17f-d1fe-5067-b80b-f3e582e492b2": ("Nintendo - Game Boy Advance", "runtime_validated", "mGBA smoke test passed with They See Me Rollin' from the TOSEC provider archive. Provider coverage is limited until large No-Intro bundles can be indexed on a larger disk."),
        "bc81aad5-81d4-537e-9c47-ee9d18a95ee8": ("Nintendo - Game Boy Color", "runtime_validated", "Gambatte smoke test passed with Resident Evil prototype from the No-Intro provider archive."),
        "58d85a09-26d6-5da5-a102-c6b9257853da": ("Nintendo - GameCube", "runtime_validated", "Dolphin smoke test passed with Soulcalibur II demo RVZ. Large library providers are registered, but broad installs need substantially more disk space."),
    }
    for guid, (module, status, note) in known.items():
        entry = consoles.setdefault(guid, {})
        entry.setdefault("module", module)
        entry["status"] = status
        entry.setdefault("notes", note)
        entry.setdefault("validated_at", datetime.utcnow().date().isoformat())
    save_progress(payload)
    return payload


def build_backend_matrix() -> List[Dict]:
    modules = _load_modules()
    providers = _load_backend_providers()
    registry = load_registry()
    progress = load_progress().get("consoles") or {}
    core_by_guid = _core_map(registry)
    bios_by_guid = _bios_map(registry)
    rows: List[Dict] = []
    for index, module in enumerate(modules, 1):
        name = module.get("name") or ""
        guid = module.get("guid")
        if not _is_console_module(name):
            continue
        manufacturer, console = _split_module_name(name)
        provider_count = _provider_count(providers, manufacturer, console, guid)
        rdb_exists = Path(rdb_json_path(name)).exists() or (BACKEND_DATA_ROOT / "index" / "rdb" / f"{slugify(name)}.json").exists()
        roms_exists = (BACKEND_ROMS_DIR / f"{slugify(name)}.json").exists()
        mapped_cores = core_by_guid.get(guid, [])
        bios_requirements = bios_by_guid.get(guid, [])
        missing = []
        if not provider_count:
            missing.append("providers")
        if not rdb_exists:
            missing.append("rdb")
        if not roms_exists:
            missing.append("rom_dataset")
        if not mapped_cores:
            missing.append("core_mapping")
        if any(not req.get("filename") for req in bios_requirements):
            missing.append("bios_metadata")
        derived_status = "backend_ready" if not missing else "needs_backend_work"
        manual = progress.get(guid, {})
        status = manual.get("status") or derived_status
        rows.append({
            "index": index,
            "module": name,
            "guid": guid,
            "provider_count": provider_count,
            "rdb": rdb_exists,
            "rom_dataset": roms_exists,
            "core_mapped": bool(mapped_cores),
            "bios_metadata": "ok" if not bios_requirements else "tracked",
            "missing": missing,
            "derived_status": derived_status,
            "status": status,
            "notes": manual.get("notes"),
        })
    return rows


def next_incomplete(rows: Optional[List[Dict]] = None) -> Optional[Dict]:
    rows = rows or build_backend_matrix()
    for status in ("needs_backend_work", "backend_ready"):
        for row in rows:
            if row.get("status") == status:
                return row
    return None


def _load_modules() -> List[Dict]:
    if not MODULES_PATH.exists():
        return []
    payload = json.loads(MODULES_PATH.read_text(encoding="utf-8"))
    return payload.get("modules") or []


def _load_backend_providers() -> Dict:
    if not BACKEND_PROVIDERS_PATH.exists():
        return {"console_root": {}}
    try:
        payload = json.loads(BACKEND_PROVIDERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"console_root": {}}
    if not isinstance(payload, dict):
        return {"console_root": {}}
    payload.setdefault("console_root", {})
    return payload


def _is_console_module(name: str) -> bool:
    if not name:
        return False
    if name in CONSOLE_NAME_ALLOWLIST:
        return True
    if name in NON_CONSOLE_NAMES:
        return False
    return not name.startswith(NON_CONSOLE_PREFIXES)


def _split_module_name(name: str) -> Tuple[Optional[str], Optional[str]]:
    if " - " not in name:
        return None, name
    manufacturer, console = name.split(" - ", 1)
    return manufacturer, console


def _provider_count(providers: Dict, manufacturer: Optional[str], console: Optional[str], guid: Optional[str]) -> int:
    count = 0
    for maker, systems in (providers.get("console_root") or {}).items():
        if not isinstance(systems, dict):
            continue
        for console_name, entry in systems.items():
            entries = entry if isinstance(entry, list) else [entry]
            entries = [item for item in entries if isinstance(item, dict)]
            if manufacturer and console and maker == manufacturer and console_name == console:
                count += len(entries)
                continue
            if guid:
                count += sum(1 for item in entries if item.get("libretro_guid") == guid or item.get("guid") == guid)
    return count


def _core_map(registry: Dict) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = defaultdict(list)
    for core_id, meta in (registry.get("cores") or {}).items():
        for guid in meta.get("console_guids") or []:
            result[guid].append(core_id)
    return dict(result)


def _bios_map(registry: Dict) -> Dict[str, List[Dict]]:
    bios_files = registry.get("bios_files") or {}
    result: Dict[str, List[Dict]] = defaultdict(list)
    for _core_id, meta in (registry.get("cores") or {}).items():
        for guid in meta.get("console_guids") or []:
            for bios_id in meta.get("bios_ids") or []:
                result[guid].append(bios_files.get(bios_id, {}))
            console_bios = meta.get("console_bios_ids") or {}
            if isinstance(console_bios, dict):
                for bios_id in console_bios.get(guid) or []:
                    result[guid].append(bios_files.get(bios_id, {}))
    return dict(result)


def print_summary(rows: Optional[List[Dict]] = None) -> None:
    rows = rows or build_backend_matrix()
    print("idx\tstatus\tmodule\tproviders\trdb\troms\tcore\tmissing")
    for row in rows:
        missing = ",".join(row.get("missing") or [])
        print(
            f"{row['index']}\t{row['status']}\t{row['module']}\t{row['provider_count']}\t"
            f"{int(bool(row['rdb']))}\t{int(bool(row['rom_dataset']))}\t{int(bool(row['core_mapped']))}\t{missing}"
        )


if __name__ == "__main__":
    seed_known_progress()
    matrix = build_backend_matrix()
    print_summary(matrix)
    target = next_incomplete(matrix)
    if target:
        print(f"\nNEXT\t{target['index']}\t{target['module']}\t{target['status']}\t{','.join(target.get('missing') or [])}")
