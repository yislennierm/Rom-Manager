import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from data.storage.storage_config_loader import load_storage_config
from utils.cores_registry import load_registry


def active_frontend() -> Tuple[Optional[str], Dict]:
    frontends = (load_storage_config() or {}).get("frontends") or {}
    for key, entry in frontends.items():
        if entry.get("active"):
            return key, entry
    if frontends:
        return next(iter(frontends.items()))
    return None, {}


def bios_root_for_active_frontend() -> Path:
    _, frontend = active_frontend()
    return Path(frontend.get("bios_path", "~/BIOS")).expanduser()


def list_bios_requirements(console_guid: Optional[str] = None) -> List[Dict[str, object]]:
    registry = load_registry()
    bios_registry = registry.get("bios_files", {})
    requirements: List[Dict[str, object]] = []
    for core_id, core_meta in sorted((registry.get("cores") or {}).items()):
        guids = core_meta.get("console_guids") or []
        if console_guid and console_guid not in guids:
            continue
        bios_ids = core_meta.get("bios_ids") or []
        if not bios_ids:
            requirements.append({
                "core_id": core_id,
                "core_name": core_meta.get("name", core_id),
                "bios_id": None,
                "bios": None,
                "status": {"state": "none", "label": "No BIOS listed"},
            })
            continue
        for bios_id in bios_ids:
            bios = bios_registry.get(bios_id)
            requirements.append({
                "core_id": core_id,
                "core_name": core_meta.get("name", core_id),
                "bios_id": bios_id,
                "bios": bios,
                "status": bios_status(bios) if bios else {"state": "missing_definition", "label": "No metadata"},
            })
    return requirements


def bios_status(bios: Optional[Dict]) -> Dict[str, object]:
    if not bios:
        return {"state": "missing_definition", "label": "No metadata"}
    filename = bios.get("filename")
    if not filename:
        return {"state": "missing_definition", "label": "No filename"}
    root = bios_root_for_active_frontend()
    candidates = _candidate_paths(root, filename, bios.get("aliases") or [])
    existing = next((path for path in candidates if path.exists()), None)
    if not existing:
        return {"state": "missing", "label": "Missing", "path": str(candidates[0])}
    valid, message = validate_bios_file(existing, bios)
    return {
        "state": "ok" if valid else "invalid",
        "label": "OK" if valid else message,
        "path": str(existing),
    }


def install_bios_from_file(source: Path, bios: Dict) -> Dict[str, object]:
    source = source.expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"BIOS source file not found: {source}")
    filename = bios.get("filename")
    if not filename:
        raise ValueError("BIOS metadata has no target filename.")

    root = bios_root_for_active_frontend()
    root.mkdir(parents=True, exist_ok=True)
    target = root / filename
    target.parent.mkdir(parents=True, exist_ok=True)

    zip_contents = bios.get("zip_contents") or []
    if zip_contents:
        valid, message = validate_bios_file(source, bios)
        if valid:
            if bios.get("normalize_zip_contents"):
                _write_normalized_zip(source, target, zip_contents)
            else:
                shutil.copy2(source, target)
        elif len(zip_contents) == 1 and _matches_inner_bios(source, zip_contents[0]):
            inner = zip_contents[0]
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(source, arcname=inner["filename"])
        else:
            raise ValueError(message)
    else:
        valid, message = validate_bios_file(source, bios)
        if not valid:
            raise ValueError(message)
        shutil.copy2(source, target)
    _copy_bios_aliases(target, bios)

    valid, message = validate_bios_file(target, bios)
    if not valid:
        raise ValueError(message)
    return {"target": str(target), "status": bios_status(bios)}


def install_bios_from_source(bios: Dict, source_id: Optional[str] = None) -> Dict[str, object]:
    source = select_bios_source(bios, source_id)
    if not source:
        raise ValueError("No configured BIOS source is available.")
    url = source.get("url")
    if not url:
        raise ValueError("Configured BIOS source has no URL.")

    suffix = Path(str(url).split("?", 1)[0]).suffix or ".bin"
    with tempfile.TemporaryDirectory(prefix="roms-manager-bios-") as temp_dir:
        download_path = Path(temp_dir) / f"download{suffix}"
        request = urllib.request.Request(
            str(url),
            headers={"User-Agent": "Rom-Manager/1.0"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            with download_path.open("wb") as fh:
                shutil.copyfileobj(response, fh)
        result = install_bios_from_file(download_path, bios)
    result["source"] = source
    return result


def select_bios_source(bios: Dict, source_id: Optional[str] = None) -> Optional[Dict]:
    sources = bios.get("sources") or []
    if not sources:
        return None
    if source_id:
        return next((source for source in sources if source.get("id") == source_id), None)
    return sources[0]


def validate_bios_file(path: Path, bios: Dict) -> Tuple[bool, str]:
    zip_contents = bios.get("zip_contents") or []
    if zip_contents:
        if not zipfile.is_zipfile(path):
            return False, "Not a zip"
        with zipfile.ZipFile(path) as archive:
            names = {name.lower(): name for name in archive.namelist()}
            for expected in zip_contents:
                filename = (expected.get("filename") or "").lower()
                archive_name = names.get(filename)
                if not archive_name:
                    for alias in expected.get("aliases") or []:
                        archive_name = names.get(str(alias).lower())
                        if archive_name:
                            break
                if not archive_name:
                    return False, f"Missing {expected.get('filename')}"
                expected_md5 = (expected.get("md5") or "").lower()
                if expected_md5:
                    digest = hashlib.md5(archive.read(archive_name)).hexdigest()
                    if digest.lower() != expected_md5:
                        return False, f"Hash mismatch: {expected.get('filename')}"
        return True, "OK"

    expected_md5 = (bios.get("md5") or "").lower()
    if expected_md5:
        digest = compute_md5(path)
        if digest.lower() != expected_md5:
            return False, f"Hash mismatch: {digest}"
    expected_size = bios.get("size")
    if expected_size and path.stat().st_size != int(expected_size):
        return False, f"Size mismatch: {path.stat().st_size}"
    return True, "OK"


def compute_md5(path: Path) -> str:
    hash_md5 = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def _copy_bios_aliases(target: Path, bios: Dict) -> None:
    for alias in bios.get("aliases") or []:
        if not alias:
            continue
        alias_target = target.parent / alias
        if alias_target == target:
            continue
        alias_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, alias_target)


def _candidate_paths(root: Path, filename: str, aliases: Iterable[str] = ()) -> List[Path]:
    paths: List[Path] = []
    seen: set[Path] = set()
    for candidate_name in [filename, *aliases]:
        if not candidate_name:
            continue
        candidate = root / candidate_name
        if candidate not in seen:
            paths.append(candidate)
            seen.add(candidate)
        if candidate_name.endswith(".zip"):
            mame_candidate = root / "mame" / "roms" / candidate_name
            if mame_candidate not in seen:
                paths.append(mame_candidate)
                seen.add(mame_candidate)
    return paths


def _matches_inner_bios(source: Path, inner: Dict) -> bool:
    expected_name = inner.get("filename")
    if expected_name and source.name.lower() != expected_name.lower():
        return False
    expected_md5 = inner.get("md5")
    if expected_md5 and compute_md5(source).lower() != expected_md5.lower():
        return False
    return True


def _write_normalized_zip(source: Path, target: Path, zip_contents: List[Dict]) -> None:
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        names = {name.lower(): name for name in src.namelist()}
        for expected in zip_contents:
            filename = expected.get("filename")
            if not filename:
                continue
            member = names.get(str(filename).lower())
            if not member:
                for alias in expected.get("aliases") or []:
                    member = names.get(str(alias).lower())
                    if member:
                        break
            if member:
                dst.writestr(str(filename), src.read(member))
