import json
import os
import re
import io
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import UUID, uuid5

import msgpack  # type: ignore
import requests

REPO_OWNER = "libretro-thumbnails"
REPO_NAME = "libretro-thumbnails"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/master"
MODULES_FILE = Path("data") / "index" / "libretro_modules.json"
INDEX_DIR = Path("data") / "index" / "libretro"
RDB_DIR = Path("data") / "index" / "rdb"
RDB_BASE = "https://raw.githubusercontent.com/libretro/libretro-database/master/rdb"
ALLOWED_CATEGORIES = ["Named_Boxarts"]
GUID_NAMESPACE = UUID("b9ae55f5-9f8f-4a5c-9a1d-8c7f2006100b")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower())
    slug = slug.strip("_")
    return slug or "default"


def _headers(token: Optional[str]) -> Dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_repo(url: Optional[str]) -> Tuple[str, str]:
    if not url:
        raise ValueError("Module URL missing")
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$", url)
    if not match:
        raise ValueError(f"Unsupported module URL: {url}")
    return match.group("owner"), match.group("repo")


def _module_api(module: Dict[str, str]) -> str:
    owner, repo = _parse_repo(module.get("url"))
    return f"https://api.github.com/repos/{owner}/{repo}"


def _list_categories(module_api: str, branch: str, token: Optional[str]) -> List[str]:
    res = requests.get(f"{module_api}/contents?ref={branch}", headers=_headers(token))
    res.raise_for_status()
    payload = res.json()
    if not isinstance(payload, list):
        return []
    categories = [
        entry.get("name")
        for entry in payload
        if isinstance(entry, dict) and entry.get("type") == "dir"
    ]
    return [c for c in categories if c]


def fetch_gitmodules() -> List[Dict]:
    url = f"{RAW_BASE}/.gitmodules"
    res = requests.get(url)
    if res.status_code != 200:
        raise RuntimeError("Unable to fetch .gitmodules from libretro repo")
    content = res.text

    submodules = []
    current: Dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("[submodule"):
            if current:
                submodules.append(current)
            current = {"name": line.split('"')[1]}
        elif "=" in line:
            key, value = [x.strip() for x in line.split("=", 1)]
            current[key] = value
    if current:
        submodules.append(current)
    return submodules


def sync_modules(token: Optional[str] = None, names: Optional[Sequence[str]] = None) -> List[Dict]:
    modules = fetch_gitmodules()
    existing_guids = _load_existing_guids()
    if names:
        requested = [name.strip() for name in names if name and name.strip()]
        if requested:
            requested_set = set(requested)
            modules = [m for m in modules if m.get("name") in requested_set]
    for module in modules:
        name = module.get("name") or module.get("path") or ""
        module["guid"] = existing_guids.get(name) or _generate_guid(module)
    snapshot = {"fetched_at": datetime.utcnow().isoformat(), "modules": modules}
    MODULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODULES_FILE.write_text(json.dumps(snapshot, indent=2))
    return modules


def load_modules() -> List[Dict]:
    if not MODULES_FILE.exists():
        return []
    data = json.loads(MODULES_FILE.read_text())
    return data.get("modules", [])


def _module_by_name(name: str) -> Optional[Dict]:
    for module in load_modules():
        if module.get("name") == name:
            return module
    return None


def _iter_categories(module_api: str, branch: str, token: Optional[str]) -> Sequence[Tuple[str, str]]:
    if ALLOWED_CATEGORIES:
        for name in ALLOWED_CATEGORIES:
            yield name, f"{module_api}/contents/{requests.utils.quote(name)}?ref={branch}"
        return

    categories = _list_categories(module_api, branch, token)
    for name in categories:
        yield name, f"{module_api}/contents/{requests.utils.quote(name)}?ref={branch}"


def build_module_index(name: str, token: Optional[str] = None) -> str:
    module = _module_by_name(name)
    if not module:
        raise ValueError(f"Module {name} not found. Run database fetch first.")

    branch = module.get("branch", "master")
    module_api = _module_api(module)

    entries: Dict[str, Dict] = {}
    for category_name, category_url in _iter_categories(module_api, branch, token):
        files_res = requests.get(category_url, headers=_headers(token))
        if files_res.status_code == 404:
            continue
        files_res.raise_for_status()
        payload = files_res.json()
        if not isinstance(payload, list):
            continue
        for file in payload:
            if not isinstance(file, dict):
                continue
            if file.get("type") != "file" or not file.get("name", "").lower().endswith(".png"):
                continue
            rom_name = file["name"].rsplit(".", 1)[0]
            entries[rom_name] = {
                "category": category_name,
                "path": file.get("path"),
                "download_url": file.get("download_url"),
                "sha": file.get("sha"),
            }

    payload = {
        "module": name,
        "path": module.get("path"),
        "repo_url": module.get("url"),
        "branch": branch,
        "entries": entries,
        "fetched_at": datetime.utcnow().isoformat(),
    }
    slug = _slugify(name)
    target = INDEX_DIR / f"{slug}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2))
    return str(target)


def index_exists(name: str) -> bool:
    slug = _slugify(name)
    return (INDEX_DIR / f"{slug}.json").exists()


def _generate_guid(module: Dict[str, str]) -> str:
    payload = f"{module.get('name','')}::{module.get('url') or module.get('path') or ''}"
    return str(uuid5(GUID_NAMESPACE, payload))


def _load_existing_guids() -> Dict[str, str]:
    if not MODULES_FILE.exists():
        return {}
    try:
        data = json.loads(MODULES_FILE.read_text())
    except Exception:
        return {}
    existing = {}
    for module in data.get("modules", []):
        name = module.get("name")
        guid = module.get("guid")
        if name and guid:
            existing[name] = guid
    return existing


def rdb_json_path(name: str) -> Path:
    slug = _slugify(name)
    RDB_DIR.mkdir(parents=True, exist_ok=True)
    return RDB_DIR / f"{slug}.json"


def _detect_msgpack_offset(blob: bytes, max_search: int = 4096) -> int:
    for offset in range(0, min(max_search, len(blob))):
        view = memoryview(blob)[offset:]
        unpacker = msgpack.Unpacker(io.BytesIO(view), raw=False)
        try:
            first = next(unpacker)
        except Exception:
            continue
        if isinstance(first, dict) and any(key in first for key in ("name", "title", "serial")):
            return offset
    return 0


def _jsonify(value):
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify(v) for v in value]
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except Exception:
            return value.hex()
    return value


def export_module_rdb(module: Dict[str, str]) -> str:
    name = module.get("name")
    if not name:
        raise ValueError("Module missing name; cannot fetch RDB")
    url = f"{RDB_BASE}/{requests.utils.quote(name)}.rdb"
    response = requests.get(url, timeout=120)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to download RDB: {response.status_code}")
    blob = response.content
    offset = _detect_msgpack_offset(blob)
    unpacker = msgpack.Unpacker(io.BytesIO(blob[offset:]), raw=False)
    entries = []
    for obj in unpacker:
        if isinstance(obj, dict):
            entries.append(_jsonify(obj))
    target = rdb_json_path(name)
    payload = {
        "module": name,
        "guid": module.get("guid"),
        "source_url": url,
        "entry_count": len(entries),
        "fetched_at": datetime.utcnow().isoformat(),
        "entries": entries,
    }
    target.write_text(json.dumps(payload, indent=2))
    return str(target)
