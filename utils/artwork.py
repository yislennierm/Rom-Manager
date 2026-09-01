import os
import json
import urllib.request
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote

ARTWORK_CONSOLE_MAP = {
    "Gameboy Color": "Nintendo - Game Boy Color",
    "Game Boy Color": "Nintendo - Game Boy Color",
    "Game Gear": "Sega - Game Gear",
    "Sega Game Gear": "Sega - Game Gear",
    "Dreamcast": "Sega - Dreamcast",
    "Sega Dreamcast": "Sega - Dreamcast",
}

DATA_DIR = Path(os.environ.get("ROMS_MANAGER_DATA_ROOT", Path(__file__).resolve().parents[1] / "data")).expanduser()
ARTWORK_CACHE_DIR = str(DATA_DIR / "cache" / "artwork")
INDEX_DIR = DATA_DIR / "index" / "libretro"


def normalize_rom_name(name: str) -> str:
    return name.rsplit(".", 1)[0]


def _slugify(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower()).strip("_") or "default"


def _load_module_index(module: str) -> Optional[Dict]:
    path = INDEX_DIR / f"{_slugify(module)}.json"
    if not path.exists():
        return None
    with path.open() as fh:
        return json.load(fh)


def _module_for_rom(rom: Dict) -> Optional[str]:
    manufacturer = rom.get("manufacturer") or rom.get("brand")
    console = rom.get("console")
    if manufacturer and console:
        return f"{manufacturer} - {console}"
    if console:
        return ARTWORK_CONSOLE_MAP.get(console)
    return None


def derive_artwork_url(rom: Dict, category: str = "Named_Boxarts") -> Optional[str]:
    module = _module_for_rom(rom)
    if not module:
        return None
    sanitized = normalize_rom_name(rom.get("name", ""))
    repo_base = "https://raw.githubusercontent.com/libretro-thumbnails/libretro-thumbnails/master"
    safe_chars = "()[]!-_."
    console_path = quote(module, safe=safe_chars)
    rom_path = quote(sanitized, safe=safe_chars)
    path = f"{console_path}/{category}/{rom_path}.png"
    return f"{repo_base}/{path}"


def _index_entry_for_rom(rom: Dict) -> Optional[Dict]:
    module = _module_for_rom(rom)
    if not module:
        return None
    index = _load_module_index(module)
    if not index:
        return None
    normalized = normalize_rom_name(rom.get("name", ""))
    return index.get("entries", {}).get(normalized)


def fetch_artwork(
    rom: Dict,
    cache_base: str = ARTWORK_CACHE_DIR,
    provider_id: str = "libretro",
) -> Optional[str]:
    module = _module_for_rom(rom) or "unknown"
    rom_name = normalize_rom_name(rom.get("name", ""))
    console_dir = Path(cache_base) / _slugify(module)
    os.makedirs(console_dir, exist_ok=True)
    cache_path = console_dir / f"{quote(rom_name, safe='()[]!-_. ')}.png"
    if cache_path.exists():
        return str(cache_path)

    entry = _index_entry_for_rom(rom)
    download_url = entry.get("download_url") if entry else None
    if not download_url:
        download_url = derive_artwork_url(rom)
    if not download_url:
        return None

    try:
        urllib.request.urlretrieve(download_url, cache_path)
        return str(cache_path)
    except Exception:
        if cache_path.exists():
            cache_path.unlink(missing_ok=True)
        return None
