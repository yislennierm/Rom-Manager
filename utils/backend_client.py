import json
import os
from pathlib import Path
from typing import Dict, Optional

import requests

from utils.library_sync import MODULES_FILE

DEFAULT_API_BASE = "http://localhost:8000"


class BackendError(RuntimeError):
    """Raised when the backend API returns an error or invalid data."""


def _api_base() -> str:
    return os.environ.get("ROMS_MANAGER_BACKEND", DEFAULT_API_BASE).rstrip("/")


def fetch_modules_snapshot(with_stream: bool = False) -> Dict:
    """Fetch the libretro modules snapshot from the backend service."""
    url = f"{_api_base()}/update"
    try:
        response = requests.get(url, timeout=30, stream=with_stream)
    except requests.RequestException as exc:
        raise BackendError(f"Backend request failed: {exc}") from exc

    if response.status_code != 200:
        raise BackendError(f"Backend returned {response.status_code}: {response.text}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise BackendError(f"Invalid JSON payload: {exc}") from exc

    modules = payload.get("modules")
    if not isinstance(modules, list):
        raise BackendError("Snapshot payload missing 'modules' list.")

    return {
        "fetched_at": payload.get("version"),
        "modules": modules,
    }


def save_modules_snapshot(snapshot: Dict) -> Path:
    """Persist the downloaded snapshot to the local modules file."""
    if "modules" not in snapshot:
        raise BackendError("Snapshot missing modules field.")
    MODULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODULES_FILE.write_text(json.dumps(snapshot, indent=2))
    return MODULES_FILE


def load_local_metadata() -> Optional[Dict[str, object]]:
    """Return metadata about the locally stored modules snapshot."""
    if not MODULES_FILE.exists():
        return None
    try:
        payload = json.loads(MODULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    modules = payload.get("modules")
    count = len(modules) if isinstance(modules, list) else None
    return {
        "fetched_at": payload.get("fetched_at"),
        "count": count,
        "path": str(MODULES_FILE),
    }


def fetch_remote_metadata() -> Dict[str, object]:
    """Query the backend for metadata without downloading the payload."""
    url = f"{_api_base()}/update/meta"
    try:
        response = requests.get(url, timeout=15)
    except requests.RequestException as exc:
        raise BackendError(f"Backend request failed: {exc}") from exc
    if response.status_code != 200:
        raise BackendError(f"Backend returned {response.status_code}: {response.text}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise BackendError(f"Invalid metadata payload: {exc}") from exc
    return {
        "fetched_at": payload.get("version"),
        "count": payload.get("modules"),
    }
