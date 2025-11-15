import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import requests

from utils.library_sync import MODULES_FILE
from utils.paths import PROVIDER_FILE

DEFAULT_API_BASE = "http://localhost:8000"
PROVIDERS_PATH = Path(PROVIDER_FILE)


class BackendError(RuntimeError):
    """Raised when the backend API returns an error or invalid data."""


def _api_base() -> str:
    return os.environ.get("ROMS_MANAGER_BACKEND", DEFAULT_API_BASE).rstrip("/")


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


def _file_timestamp(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except Exception:
        return None
