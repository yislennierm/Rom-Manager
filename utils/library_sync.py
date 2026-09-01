import json
import os
import re
import io
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import UUID, uuid5
from urllib.parse import quote

import msgpack  # type: ignore
import requests

REPO_OWNER = "libretro-thumbnails"
REPO_NAME = "libretro-thumbnails"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/master"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RDB_BASE = "https://raw.githubusercontent.com/libretro/libretro-database/master/rdb"
ALLOWED_CATEGORIES = ["Named_Boxarts", "Named_Snaps", "Named_Titles"]
GUID_NAMESPACE = UUID("b9ae55f5-9f8f-4a5c-9a1d-8c7f2006100b")

RDB_SOURCE_OVERRIDES = {
    "Amstrad - GX4000": [
        {"name": "Amstrad - GX4000"},
        {
            "name": "Amstrad - CPC",
            "extensions": [".cpr"],
            "note": "Caprice32 documents the CPC database for CPC/GX4000 cartridge content.",
        },
    ],
}


def _data_dir() -> Path:
    return Path(os.environ.get("ROMS_MANAGER_DATA_ROOT", PROJECT_ROOT / "data")).expanduser()


def _modules_file() -> Path:
    return _data_dir() / "index" / "libretro_modules.json"


def _index_dir() -> Path:
    return _data_dir() / "index" / "libretro"


def _rdb_dir() -> Path:
    return _data_dir() / "index" / "rdb"


def rdb_dir() -> Path:
    path = _rdb_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def _module_repo(module: Dict[str, str]) -> Tuple[str, str]:
    return _parse_repo(module.get("url"))


def _raw_file_url(owner: str, repo: str, branch: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{quote(path, safe='/()[]!-_.,')}"


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
    existing_modules = load_modules() if names else []
    if names:
        requested = [name.strip() for name in names if name and name.strip()]
        if requested:
            requested_set = set(requested)
            modules = [m for m in modules if m.get("name") in requested_set]
    for module in modules:
        name = module.get("name") or module.get("path") or ""
        module["guid"] = existing_guids.get(name) or _generate_guid(module)
    snapshot_modules = modules
    if names and existing_modules:
        requested_names = {module.get("name") for module in modules}
        refreshed_by_name = {module.get("name"): module for module in modules}
        snapshot_modules = [
            refreshed_by_name.get(module.get("name"), module)
            for module in existing_modules
            if module.get("name") not in requested_names or module.get("name") in refreshed_by_name
        ]
        existing_names = {module.get("name") for module in snapshot_modules}
        snapshot_modules.extend(
            module for module in modules if module.get("name") not in existing_names
        )
    snapshot = {"fetched_at": datetime.utcnow().isoformat(), "modules": snapshot_modules}
    modules_file = _modules_file()
    modules_file.parent.mkdir(parents=True, exist_ok=True)
    modules_file.write_text(json.dumps(snapshot, indent=2))
    return modules


def load_modules() -> List[Dict]:
    modules_file = _modules_file()
    if not modules_file.exists():
        return []
    data = json.loads(modules_file.read_text())
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
    owner, repo = _module_repo(module)

    entries: Dict[str, Dict] = {}
    tree_res = requests.get(
        f"{module_api}/git/trees/{quote(branch, safe='')}?recursive=1",
        headers=_headers(token),
        timeout=120,
    )
    tree = None
    if tree_res.ok:
        tree_payload = tree_res.json()
        tree = tree_payload.get("tree") if isinstance(tree_payload, dict) else None
    elif tree_res.status_code == 404:
        tree_res.raise_for_status()
    allowed_categories = set(ALLOWED_CATEGORIES)
    if isinstance(tree, list):
        for file in tree:
            if not isinstance(file, dict):
                continue
            path = file.get("path") or ""
            if file.get("type") != "blob" or not path.lower().endswith(".png"):
                continue
            category_name, _, filename = path.partition("/")
            if allowed_categories and category_name not in allowed_categories:
                continue
            if not filename:
                continue
            rom_name = filename.rsplit(".", 1)[0]
            entries.setdefault(rom_name, {})[category_name] = {
                "category": category_name,
                "path": path,
                "download_url": _raw_file_url(owner, repo, branch, path),
                "sha": file.get("sha"),
            }
    else:
        for category_name, category_url in _iter_categories(module_api, branch, token):
            files_res = requests.get(category_url, headers=_headers(token), timeout=120)
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
                entries.setdefault(rom_name, {})[category_name] = {
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
    target = _index_dir() / f"{slug}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2))
    return str(target)


def index_exists(name: str) -> bool:
    slug = _slugify(name)
    return (_index_dir() / f"{slug}.json").exists()


def _generate_guid(module: Dict[str, str]) -> str:
    payload = f"{module.get('name','')}::{module.get('url') or module.get('path') or ''}"
    return str(uuid5(GUID_NAMESPACE, payload))


def _load_existing_guids() -> Dict[str, str]:
    modules_file = _modules_file()
    if not modules_file.exists():
        return {}
    try:
        data = json.loads(modules_file.read_text())
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
    rdb_dir = _rdb_dir()
    rdb_dir.mkdir(parents=True, exist_ok=True)
    return rdb_dir / f"{slug}.json"


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


def _rdb_source_url(name: str) -> str:
    return f"{RDB_BASE}/{requests.utils.quote(name)}.rdb"


def _download_rdb_entries(name: str) -> Tuple[str, List[Dict]]:
    url = _rdb_source_url(name)
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
    return url, entries


def _rdb_entry_matches_source(entry: Dict, source: Dict[str, object]) -> bool:
    extensions = source.get("extensions")
    if not extensions:
        return True
    allowed = {str(ext).lower() for ext in extensions if ext}
    for field in ("rom_name", "name", "description"):
        value = entry.get(field)
        if not isinstance(value, str):
            continue
        if Path(value).suffix.lower() in allowed:
            return True
    return False


def _rdb_entry_key(entry: Dict) -> Tuple[str, str]:
    for field in ("sha1", "md5", "crc", "crc32"):
        value = entry.get(field)
        if value:
            return field, str(value).lower()
    for field in ("rom_name", "name", "description"):
        value = entry.get(field)
        if value:
            return field, str(value).lower()
    return "object", json.dumps(entry, sort_keys=True)


def _rdb_sources_for_module(name: str) -> List[Dict[str, object]]:
    return RDB_SOURCE_OVERRIDES.get(name) or [{"name": name}]


def export_module_rdb(module: Dict[str, str]) -> str:
    name = module.get("name")
    if not name:
        raise ValueError("Module missing name; cannot fetch RDB")
    sources = _rdb_sources_for_module(name)
    urls = []
    notes = []
    entries = []
    seen = set()
    for source in sources:
        source_name = str(source.get("name") or name)
        url, source_entries = _download_rdb_entries(source_name)
        urls.append(url)
        if source.get("note"):
            notes.append(str(source["note"]))
        for entry in source_entries:
            if not _rdb_entry_matches_source(entry, source):
                continue
            enriched = dict(entry)
            if len(sources) > 1:
                enriched["_source_rdb"] = source_name
            key = _rdb_entry_key(enriched)
            if key in seen:
                continue
            seen.add(key)
            entries.append(enriched)
    target = rdb_json_path(name)
    payload = {
        "module": name,
        "guid": module.get("guid"),
        "source_url": urls[0] if urls else None,
        "entry_count": len(entries),
        "fetched_at": datetime.utcnow().isoformat(),
        "entries": entries,
    }
    if len(urls) > 1:
        payload["source_urls"] = urls
    if notes:
        payload["source_notes"] = notes
    target.write_text(json.dumps(payload, indent=2))
    return str(target)
