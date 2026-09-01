import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse

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
        bios_ids = list(core_meta.get("bios_ids") or [])
        console_bios = core_meta.get("console_bios_ids") or {}
        if console_guid and isinstance(console_bios, dict):
            bios_ids.extend(console_bios.get(console_guid) or [])
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

    if bios.get("type") == "directory":
        valid, message = _validate_directory_bios_source(source, bios)
        if not valid:
            raise ValueError(message)
        target.mkdir(parents=True, exist_ok=True)
        target_file = target / source.name
        shutil.copy2(source, target_file)
        valid, message = validate_bios_file(target, bios)
        if not valid:
            target_file.unlink(missing_ok=True)
            raise ValueError(message)
        return {"target": str(target_file), "status": bios_status(bios)}

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

    url_path = unquote(urlparse(str(url)).path)
    filename = Path(url_path).name or "download.bin"
    suffix = Path(filename).suffix or ".bin"
    with tempfile.TemporaryDirectory(prefix="roms-manager-bios-") as temp_dir:
        download_path = Path(temp_dir) / filename
        if not download_path.suffix:
            download_path = download_path.with_suffix(suffix)
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
    for source in sources:
        if _bios_source_is_installable(source):
            return source
    return next((source for source in sources if source.get("url")), None)


def _bios_source_is_installable(source: Dict) -> bool:
    if not source.get("url"):
        return False
    source_type = str(source.get("type") or "").lower()
    policy = str(source.get("policy") or "").lower()
    if source_type in {"documentation", "local_transform"}:
        return False
    if policy in {"checksum_reference", "user_provided_firmware"}:
        return False
    return True


def validate_bios_file(path: Path, bios: Dict) -> Tuple[bool, str]:
    if bios.get("type") == "directory":
        return _validate_bios_directory(path, bios)

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


def _validate_bios_directory(path: Path, bios: Dict) -> Tuple[bool, str]:
    if not path.is_dir():
        return False, "Missing directory"

    minimum_files = int(bios.get("minimum_files") or 1)
    required_extensions = {
        str(extension).lower()
        for extension in (bios.get("required_extensions") or [])
        if extension
    }
    allowed_extensions = {
        str(extension).lower()
        for extension in (bios.get("allowed_extensions") or [])
        if extension
    }

    files = [candidate for candidate in path.iterdir() if candidate.is_file()]
    if required_extensions:
        files = [
            candidate
            for candidate in files
            if candidate.suffix.lower() in required_extensions
        ]
        if len(files) < minimum_files:
            extensions = ", ".join(sorted(required_extensions))
            return False, f"Missing BIOS file ({extensions})"
    elif allowed_extensions:
        files = [
            candidate
            for candidate in files
            if candidate.suffix.lower() in allowed_extensions
        ]
        if len(files) < minimum_files:
            return False, "Missing BIOS file"
    elif len(files) < minimum_files:
        return False, "Missing BIOS file"

    minimum_file_size = int(bios.get("minimum_file_size") or 0)
    if minimum_file_size and not any(candidate.stat().st_size >= minimum_file_size for candidate in files):
        return False, "BIOS file too small"

    known_md5s = {
        str(value).lower()
        for value in (bios.get("known_md5s") or [])
        if value
    }
    if known_md5s:
        for candidate in files:
            if compute_md5(candidate).lower() in known_md5s:
                return True, "OK"
        return True, "Present, checksum not in known list"

    return True, "OK"


def _validate_directory_bios_source(source: Path, bios: Dict) -> Tuple[bool, str]:
    required_extensions = {
        str(extension).lower()
        for extension in (bios.get("required_extensions") or [])
        if extension
    }
    allowed_extensions = {
        str(extension).lower()
        for extension in (bios.get("allowed_extensions") or [])
        if extension
    }
    extension = source.suffix.lower()
    if required_extensions and extension not in required_extensions:
        return False, f"Expected BIOS file extension: {', '.join(sorted(required_extensions))}"
    if allowed_extensions and extension not in allowed_extensions:
        return False, f"Unsupported BIOS file extension: {extension or '(none)'}"

    minimum_file_size = int(bios.get("minimum_file_size") or 0)
    if minimum_file_size and source.stat().st_size < minimum_file_size:
        return False, f"BIOS file too small; expected at least {_format_bytes(minimum_file_size)}"
    return True, "OK"


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024


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
        melonds_candidate = root / "melonDS DS" / candidate_name
        if melonds_candidate not in seen:
            paths.append(melonds_candidate)
            seen.add(melonds_candidate)
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
