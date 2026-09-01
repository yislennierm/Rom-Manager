import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional


def detect_frontends() -> Dict[str, Dict[str, object]]:
    detected: Dict[str, Dict[str, object]] = {}
    steam = _detect_steam_retroarch()
    if steam:
        detected["retroarch_steam"] = steam
    flatpak = _detect_flatpak_retroarch()
    if flatpak:
        detected["retroarch_flatpak"] = flatpak
    vita3k = _detect_vita3k()
    if vita3k:
        detected["vita3k"] = vita3k
    standalone = _detect_native_retroarch()
    if standalone:
        detected["retroarch_standalone"] = standalone
    return detected


def merge_detected_frontends(config: dict) -> tuple[dict, int]:
    frontends = dict(config.get("frontends") or {})
    detected = detect_frontends()
    added = 0
    has_active = any(bool(entry.get("active")) for entry in frontends.values())

    for key, entry in detected.items():
        existing = dict(frontends.get(key) or {})
        if not existing:
            added += 1
        merged = {**entry, **existing}
        for field in ("roms_path", "bios_path", "playlists_path", "cores_path", "launcher"):
            if entry.get(field) and not existing.get(field):
                merged[field] = entry[field]
        merged.setdefault("active", not has_active and not any(f.get("active") for f in frontends.values()))
        frontends[key] = merged

    config["frontends"] = frontends
    return config, added


def _portable_path(path: Path) -> str:
    try:
        return f"~/{path.resolve().relative_to(Path.home().resolve())}"
    except ValueError:
        return str(path)


def _detect_steam_retroarch() -> Optional[Dict[str, object]]:
    candidates = [
        Path.home() / ".steam/steam/steamapps/common/RetroArch",
        Path.home() / ".local/share/Steam/steamapps/common/RetroArch",
    ]
    root = next((path for path in candidates if (path / "retroarch").exists()), None)
    if not root:
        return None
    return {
        "name": "RetroArch (Steam)",
        "kind": "retroarch",
        "install_type": "steam",
        "launcher": _portable_path(root / "retroarch"),
        "roms_path": _portable_path(root / "downloads"),
        "bios_path": _portable_path(root / "system"),
        "playlists_path": _portable_path(root / "playlists"),
        "cores_path": _portable_path(root / "cores"),
        "platform": "linux",
        "detected": True,
        "active": False,
    }


def _detect_flatpak_retroarch() -> Optional[Dict[str, object]]:
    if not shutil.which("flatpak"):
        return None
    if not _flatpak_app_installed("org.libretro.RetroArch"):
        return None
    root = Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch"
    for child in ("downloads", "system", "playlists", "cores"):
        (root / child).mkdir(parents=True, exist_ok=True)
    return {
        "name": "RetroArch (Standalone Flatpak)",
        "kind": "retroarch",
        "install_type": "flatpak",
        "launcher": "flatpak run org.libretro.RetroArch",
        "roms_path": _portable_path(root / "downloads"),
        "bios_path": _portable_path(root / "system"),
        "playlists_path": _portable_path(root / "playlists"),
        "cores_path": _portable_path(root / "cores"),
        "platform": "linux",
        "detected": True,
        "active": False,
    }


def _detect_native_retroarch() -> Optional[Dict[str, object]]:
    launcher = shutil.which("retroarch")
    if not launcher:
        return None
    config_root = Path.home() / ".config/retroarch"
    return {
        "name": "RetroArch (Standalone)",
        "kind": "retroarch",
        "install_type": "native",
        "launcher": launcher,
        "roms_path": _portable_path(config_root / "downloads"),
        "bios_path": _portable_path(config_root / "system"),
        "playlists_path": _portable_path(config_root / "playlists"),
        "cores_path": _portable_path(config_root / "cores"),
        "platform": "linux",
        "detected": True,
        "active": False,
    }


def _detect_vita3k() -> Optional[Dict[str, object]]:
    launcher = shutil.which("vita3k")
    if not launcher:
        appimage = Path.home() / ".local/opt/vita3k/Vita3K-x86_64.AppImage"
        if appimage.exists():
            launcher = _portable_path(appimage)
    if not launcher:
        return None
    if launcher.startswith(str(Path.home())):
        launcher = _portable_path(Path(launcher))
    root = Path.home() / ".local/share/Vita3K/Vita3K"
    package_root = root / "ux0" / "app"
    package_root.mkdir(parents=True, exist_ok=True)
    return {
        "name": "Vita3K",
        "kind": "external_emulator",
        "install_type": "vita3k",
        "launcher": launcher,
        "roms_path": _portable_path(package_root),
        "bios_path": _portable_path(root),
        "platform": "linux",
        "detected": True,
        "active": False,
        "supported_guids": [
            "219b39f7-8c82-5053-8efa-b74c7c654aa7",
        ],
    }


def _flatpak_app_installed(app_id: str) -> bool:
    try:
        result = subprocess.run(
            ["flatpak", "info", "--user", app_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            return True
        result = subprocess.run(
            ["flatpak", "info", "--system", app_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False
