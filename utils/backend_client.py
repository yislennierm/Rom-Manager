import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import requests

import shutil
import tempfile

from utils.library_sync import MODULES_FILE, RDB_DIR, rdb_json_path
from utils.paths import PROVIDER_FILE, CACHE_DIR

RDB_PATH = RDB_DIR
CACHE_PATH = Path(CACHE_DIR)

DEFAULT_API_BASE = "http://localhost:8000"
PROVIDERS_PATH = Path(PROVIDER_FILE)
_BACKEND_OVERRIDE: Optional[str] = None


def set_backend_base(url: Optional[str]) -> None:
  """Override the backend base URL for this process (takes precedence over env)."""
  global _BACKEND_OVERRIDE
  if url:
    _BACKEND_OVERRIDE = url.rstrip("/")
  else:
    _BACKEND_OVERRIDE = None


def get_backend_base() -> str:
  if _BACKEND_OVERRIDE:
    return _BACKEND_OVERRIDE
  return os.environ.get("ROMS_MANAGER_BACKEND", DEFAULT_API_BASE).rstrip("/")


class BackendError(RuntimeError):
    """Raised when the backend API returns an error or invalid data."""


def _api_base() -> str:
    return get_backend_base()


def test_backend() -> Dict:
    """Ping the backend health endpoint and return payload."""
    url = f"{_api_base()}/healthz"
    try:
        response = requests.get(url, timeout=8)
    except requests.RequestException as exc:
        raise BackendError(f"Backend request failed: {exc}") from exc
    if response.status_code != 200:
        raise BackendError(f"Backend returned {response.status_code}: {response.text}")
    try:
        return response.json()
    except ValueError as exc:
        raise BackendError(f"Invalid JSON payload from healthz: {exc}") from exc


def _fetch_snapshot(target: str) -> Dict:
    url = f"{_api_base()}/update"
    try:
        response = requests.get(url, timeout=30, params={"target": target})
    except requests.RequestException as exc:
        raise BackendError(f"Backend request failed: {exc}") from exc
    if response.status_code != 200:
        raise BackendError(f"Backend returned {response.status_code}: {response.text}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise BackendError(f"Invalid JSON payload: {exc}") from exc
    return payload


def _fetch_metadata(target: str) -> Dict:
    url = f"{_api_base()}/update/meta"
    try:
        response = requests.get(url, timeout=15, params={"target": target})
    except requests.RequestException as exc:
        raise BackendError(f"Backend request failed: {exc}") from exc
    if response.status_code != 200:
        raise BackendError(f"Backend returned {response.status_code}: {response.text}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise BackendError(f"Invalid metadata payload: {exc}") from exc
    return payload


def fetch_modules_snapshot() -> Dict:
    payload = _fetch_snapshot("modules")
    modules = payload.get("modules")
    if not isinstance(modules, list):
        raise BackendError("Modules payload missing 'modules' list.")
    return {
        "fetched_at": payload.get("version"),
        "modules": modules,
    }


def save_modules_snapshot(snapshot: Dict) -> Path:
    if "modules" not in snapshot:
        raise BackendError("Snapshot missing modules field.")
    MODULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODULES_FILE.write_text(json.dumps(snapshot, indent=2))
    return MODULES_FILE


def load_modules_local_metadata() -> Optional[Dict[str, object]]:
    if not MODULES_FILE.exists():
        return None
    try:
        payload = json.loads(MODULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    modules = payload.get("modules")
    count = len(modules) if isinstance(modules, list) else None
    return {
        "fetched_at": payload.get("fetched_at"),
        "count": count,
        "path": str(MODULES_FILE),
    }


def fetch_modules_remote_metadata() -> Dict[str, object]:
    payload = _fetch_metadata("modules")
    return {
        "fetched_at": payload.get("version"),
        "count": payload.get("count"),
    }


def fetch_providers_snapshot() -> Dict:
    payload = _fetch_snapshot("providers")
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        raise BackendError("Providers payload missing 'providers' object.")
    if "fetched_at" not in providers and payload.get("version"):
        providers["fetched_at"] = payload["version"]
    return providers


def save_providers_snapshot(snapshot: Dict) -> Path:
    PROVIDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROVIDERS_PATH.write_text(json.dumps(snapshot, indent=2))
    return PROVIDERS_PATH


def _rom_dataset_path(dataset: Dict) -> Path:
    slug = dataset.get("slug")
    if slug:
        path = RDB_PATH / f"{slug}.json"
    else:
        module_name = dataset.get("module")
        if not module_name:
            raise BackendError("ROM dataset missing slug and module name")
        path = Path(rdb_json_path(module_name))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def save_rom_dataset(dataset: Dict) -> Path:
    path = _rom_dataset_path(dataset)
    path.write_text(json.dumps(dataset, indent=2))
    return path


def load_providers_local_metadata() -> Optional[Dict[str, object]]:
    if not PROVIDERS_PATH.exists():
        return None
    try:
        payload = json.loads(PROVIDERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    console_root = payload.get("console_root")
    if isinstance(console_root, dict):
        count = sum(
            len(systems) if isinstance(systems, dict) else 0
            for systems in console_root.values()
        )
    else:
        count = None
    return {
        "fetched_at": payload.get("fetched_at") or _file_timestamp(PROVIDERS_PATH),
        "count": count,
        "path": str(PROVIDERS_PATH),
    }


def fetch_providers_remote_metadata() -> Dict[str, object]:
    payload = _fetch_metadata("providers")
    return {
        "fetched_at": payload.get("version"),
        "count": payload.get("count"),
    }


def fetch_rom_catalog_metadata() -> Dict:
    url = f"{_api_base()}/roms"
    try:
        response = requests.get(url, timeout=30)
    except requests.RequestException as exc:
        raise BackendError(f"Backend request failed: {exc}") from exc
    if response.status_code != 200:
        raise BackendError(f"Backend returned {response.status_code}: {response.text}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise BackendError(f"Invalid ROM catalog payload: {exc}") from exc
    return payload


def download_rom_dataset(identifier: str) -> Dict:
    url = f"{_api_base()}/roms/{identifier}"
    try:
        response = requests.get(url, timeout=60)
    except requests.RequestException as exc:
        raise BackendError(f"Backend request failed: {exc}") from exc
    if response.status_code != 200:
        raise BackendError(f"Backend returned {response.status_code}: {response.text}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise BackendError(f"Invalid ROM dataset payload: {exc}") from exc
    return payload


def load_roms_local_metadata() -> Optional[Dict[str, object]]:
    if not RDB_PATH.exists():
        return None
    datasets = list(RDB_PATH.glob("*.json"))
    if not datasets:
        return None
    latest_ts = None
    for path in datasets:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        fetched_at = payload.get("fetched_at")
        if fetched_at and (latest_ts is None or fetched_at > latest_ts):
            latest_ts = fetched_at
    return {
        "fetched_at": latest_ts,
        "count": len(datasets),
        "path": str(RDB_PATH),
    }


def fetch_roms_remote_metadata() -> Dict[str, object]:
    payload = fetch_rom_catalog_metadata()
    roms = payload.get("roms")
    latest = None
    if isinstance(roms, list):
        for entry in roms:
            ts = entry.get("fetched_at") if isinstance(entry, dict) else None
            if ts and (latest is None or ts > latest):
                latest = ts
        count = len(roms)
    else:
        count = payload.get("count")
    return {
        "fetched_at": latest,
        "count": count,
    }


def _file_timestamp(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except Exception:
        return None


def load_cache_local_metadata() -> Optional[Dict[str, object]]:
    if not CACHE_PATH.exists():
        return None
    file_count = 0
    total_size = 0
    latest_ts = None
    for path in CACHE_PATH.rglob("*"):
        if not path.is_file():
            continue
        file_count += 1
        stats = path.stat()
        total_size += stats.st_size
        if latest_ts is None or stats.st_mtime > latest_ts:
            latest_ts = stats.st_mtime
    fetched_at = datetime.fromtimestamp(latest_ts).isoformat() if latest_ts else None
    return {
        "fetched_at": fetched_at,
        "count": file_count,
        "size": total_size,
        "path": str(CACHE_PATH),
    }


def fetch_cache_remote_metadata() -> Dict[str, object]:
    url = f"{_api_base()}/cache/meta"
    try:
        response = requests.get(url, timeout=20)
    except requests.RequestException as exc:
        raise BackendError(f"Backend request failed: {exc}") from exc
    if response.status_code != 200:
        raise BackendError(f"Backend returned {response.status_code}: {response.text}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise BackendError(f"Invalid cache metadata payload: {exc}") from exc
    return {
        "fetched_at": payload.get("updated"),
        "count": payload.get("file_count"),
        "size": payload.get("size"),
    }


def download_cache_archive() -> Dict[str, object]:
    url = f"{_api_base()}/cache/archive"
    try:
        response = requests.get(url, timeout=300, stream=True)
    except requests.RequestException as exc:
        raise BackendError(f"Backend request failed: {exc}") from exc
    if response.status_code != 200:
        raise BackendError(f"Backend returned {response.status_code}: {response.text}")
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = Path(tmp_file.name)
    try:
        with tmp_file:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    tmp_file.write(chunk)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise BackendError(f"Failed to download cache archive: {exc}") from exc

    extract_dir = Path(tempfile.mkdtemp(prefix="cache_sync_"))
    try:
        shutil.unpack_archive(str(tmp_path), str(extract_dir))
        if CACHE_PATH.exists():
            shutil.rmtree(CACHE_PATH)
        CACHE_PATH.mkdir(parents=True, exist_ok=True)
        for item in extract_dir.iterdir():
            destination = CACHE_PATH / item.name
            if destination.exists():
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            shutil.move(str(item), destination)
    finally:
        tmp_path.unlink(missing_ok=True)
        shutil.rmtree(extract_dir, ignore_errors=True)

    meta = load_cache_local_metadata() or {}
    return {
        "fetched_at": meta.get("fetched_at"),
        "count": meta.get("count"),
        "path": meta.get("path") or str(CACHE_PATH),
    }
