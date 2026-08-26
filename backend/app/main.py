import json
import os
import hashlib
import re
import secrets
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

APP_DIR = Path(__file__).resolve().parents[1]
BACKEND_DATA_DIR = APP_DIR / "data"
os.environ.setdefault("ROMS_MANAGER_DATA_ROOT", str(BACKEND_DATA_DIR))

from core.services import provider_tasks
from utils.catalog import build_rom_catalog
from utils.library_sync import export_module_rdb
from utils.paths import CACHE_DIR, cache_status, console_slug, manufacturer_slug

UI_DIR = APP_DIR / "ui"
UI_BUILD_DIR = UI_DIR / "dist"
UI_INDEX = UI_BUILD_DIR / "index.html"
CACHE_PATH = Path(CACHE_DIR)

app = FastAPI(title="ROMs Manager Backend", version="0.1.0")

if UI_BUILD_DIR.exists():
    app.mount("/admin", StaticFiles(directory=UI_BUILD_DIR, html=True), name="admin-ui")
else:
    @app.get("/admin", response_class=HTMLResponse)
    async def admin_ui_placeholder() -> HTMLResponse:
        """Fallback message when the UI hasn't been built yet."""
        raise HTTPException(
            status_code=503,
            detail="Admin UI build missing. Run `npm --prefix backend/ui run build` and try again.",
        )


@app.get("/healthz")
async def health_check() -> dict:
    """Simple health endpoint for Render probes."""
    return {"status": "ok"}


@app.get("/")
async def root() -> dict:
    """Hello-world style response."""
    return {"message": "ROMs Manager backend is running"}


def _resolve_data_file(*relative: str) -> Path:
    backend_candidate = BACKEND_DATA_DIR / Path(*relative)
    if backend_candidate.exists():
        return backend_candidate
    root_candidate = ROOT_DIR / "data" / Path(*relative)
    if root_candidate.exists():
        return root_candidate
    return root_candidate


def _resolve_data_dir(*relative: str) -> Path:
    backend_candidate = BACKEND_DATA_DIR / Path(*relative)
    if backend_candidate.exists():
        return backend_candidate
    root_candidate = ROOT_DIR / "data" / Path(*relative)
    if root_candidate.exists():
        return root_candidate
    return backend_candidate


MODULES_FILE = _resolve_data_file("index", "libretro_modules.json")
PROVIDERS_FILE = BACKEND_DATA_DIR / "providers" / "providers.json"
PROVIDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
ROMS_DIR = _resolve_data_dir("roms")
USERS_FILE = BACKEND_DATA_DIR / "users" / "users.json"
CONSOLE_INFO_FILE = BACKEND_DATA_DIR / "cache" / "console_info.json"


def _cache_dir() -> Path:
    return CACHE_PATH


class ProviderEntryModel(BaseModel):
    name: str | None = None
    provider: str | None = None
    archive_id: str
    base_url: str | None = None
    files: dict[str, str] | None = None
    rom_extensions: list[str] | None = None
    size: str | None = None
    updated: str | None = None
    libretro_guid: str | None = None


class ProviderUpsertRequest(BaseModel):
    brand: str
    console: str
    entry: ProviderEntryModel
    previous_archive_id: str | None = None


class ProviderDeleteRequest(BaseModel):
    brand: str
    console: str
    archive_id: str


class ProviderTaskRequest(BaseModel):
    brand: str
    console: str
    provider_slug: str | None = None


class ProviderFetchRequest(ProviderTaskRequest):
    force: bool = False


class ProviderExportRequest(ProviderTaskRequest):
    write: bool = True


class ProviderStatusRequest(ProviderTaskRequest):
    pass


class ProviderCoverageRequest(ProviderTaskRequest):
    guid: str | None = None


class ModuleReadinessRequest(BaseModel):
    guid: str | None = None
    name: str | None = None


class ModuleExportRdbRequest(BaseModel):
    guid: str | None = None
    name: str | None = None


class ConsoleInfoRequest(BaseModel):
    brand: str
    console: str
    guid: str | None = None
    module: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    id: str
    name: str | None = None
    enabled: bool = True
    admin: bool = False
    allowed_console_guids: list[str] = []
    generate_api_key: bool = True
    api_key: str | None = None


class UserUpdateRequest(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    admin: bool | None = None
    allowed_console_guids: list[str] | None = None
    reset_api_key: bool = False
    api_key: str | None = None


def _load_users_payload() -> dict:
    if not USERS_FILE.exists():
        return {"users": []}
    try:
        payload = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid users JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="users.json must contain an object")
    users = payload.get("users")
    if not isinstance(users, list):
        raise HTTPException(status_code=500, detail="users.json must contain a users list")
    return payload


def _auth_enabled() -> bool:
    payload = _load_users_payload()
    return bool(payload.get("users"))


def _hash_api_key(api_key: str) -> str:
    return sha256(api_key.encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: str | None = None) -> str:
    salt_value = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_value),
        200_000,
    ).hex()
    return f"pbkdf2_sha256$200000${salt_value}${derived}"


def _verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        int(iterations),
    ).hex()
    return compare_digest(derived, expected)


def _generate_api_key() -> str:
    return f"rommgr_{secrets.token_urlsafe(24)}"


def _generate_session_token() -> str:
    return f"rommgr_session_{secrets.token_urlsafe(32)}"


def _preview_key_expiry() -> str:
    return (datetime.utcnow() + timedelta(minutes=30)).isoformat()


def _session_expiry() -> str:
    return (datetime.utcnow() + timedelta(hours=12)).isoformat()


def _public_user(user: dict) -> dict:
    return {
        "id": user.get("id"),
        "name": user.get("name") or user.get("id"),
        "enabled": bool(user.get("enabled", True)),
        "admin": bool(user.get("admin", False)),
        "allowed_console_guids": user.get("allowed_console_guids") or [],
        "has_api_key": bool(user.get("api_key_hash")),
    }


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def current_user(authorization: str | None = Header(default=None)) -> dict:
    payload = _load_users_payload()
    users = payload.get("users") or []
    if not users:
        return {
            "id": "anonymous",
            "name": "Anonymous",
            "enabled": True,
            "allowed_console_guids": ["*"],
        }

    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing API key")
    token_hash = _hash_api_key(token)
    for user in users:
        stored = user.get("api_key_hash") or ""
        if stored and compare_digest(stored, token_hash):
            if not user.get("enabled", True):
                raise HTTPException(status_code=403, detail="User is disabled")
            return user
        sessions = user.get("sessions") or []
        active_sessions = []
        matched_session = False
        for session in sessions:
            expires_at = session.get("expires_at")
            try:
                is_active = bool(expires_at and datetime.fromisoformat(expires_at) > datetime.utcnow())
            except ValueError:
                is_active = False
            if not is_active:
                continue
            active_sessions.append(session)
            session_hash = session.get("token_hash") or ""
            if session_hash and compare_digest(session_hash, token_hash):
                matched_session = True
        if len(active_sessions) != len(sessions):
            user["sessions"] = active_sessions
            _save_users_payload(payload)
        if matched_session:
            if not user.get("enabled", True):
                raise HTTPException(status_code=403, detail="User is disabled")
            return user
        preview_keys = user.get("preview_keys") or []
        active_preview_keys = []
        matched_preview = False
        for preview in preview_keys:
            expires_at = preview.get("expires_at")
            try:
                is_active = bool(expires_at and datetime.fromisoformat(expires_at) > datetime.utcnow())
            except ValueError:
                is_active = False
            if not is_active:
                continue
            active_preview_keys.append(preview)
            preview_hash = preview.get("api_key_hash") or ""
            if preview_hash and compare_digest(preview_hash, token_hash):
                matched_preview = True
        if len(active_preview_keys) != len(preview_keys):
            user["preview_keys"] = active_preview_keys
            _save_users_payload(payload)
        if matched_preview:
            if not user.get("enabled", True):
                raise HTTPException(status_code=403, detail="User is disabled")
            preview_user = dict(user)
            preview_user["admin"] = False
            return preview_user
    raise HTTPException(status_code=401, detail="Invalid API key")


def _allowed_guids(user: dict) -> set[str] | None:
    if user.get("admin"):
        return None
    values = user.get("allowed_console_guids") or []
    if "*" in values:
        return None
    return {str(value).lower() for value in values if value}


def _guid_allowed(guid: str | None, user: dict) -> bool:
    allowed = _allowed_guids(user)
    if allowed is None:
        return True
    return bool(guid and guid.lower() in allowed)


def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user.get("admin"):
        raise HTTPException(status_code=403, detail="Admin account required")
    return user


def _save_users_payload(payload: dict) -> dict:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, sort_keys=False)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=USERS_FILE.parent,
        prefix=f".{USERS_FILE.name}.",
        suffix=".tmp",
        delete=False,
    ) as fh:
        temp_path = Path(fh.name)
        fh.write(serialized)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp_path, USERS_FILE)
    return payload


def _find_user(payload: dict, user_id: str) -> dict | None:
    for user in payload.get("users") or []:
        if user.get("id") == user_id:
            return user
    return None


@app.post("/auth/login")
async def login(request: LoginRequest) -> dict:
    payload = _load_users_payload()
    user = _find_user(payload, request.username.strip())
    if not user or not _verify_password(request.password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.get("enabled", True):
        raise HTTPException(status_code=403, detail="User is disabled")
    if not user.get("admin"):
        raise HTTPException(status_code=403, detail="Admin account required")
    token = _generate_session_token()
    expires_at = _session_expiry()
    sessions = user.setdefault("sessions", [])
    sessions.append(
        {
            "token_hash": _hash_api_key(token),
            "expires_at": expires_at,
        }
    )
    user["sessions"] = sessions[-10:]
    _save_users_payload(payload)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "user": _public_user(user),
    }


def _filter_modules_payload(payload: dict, user: dict) -> dict:
    allowed = _allowed_guids(user)
    if allowed is None:
        return payload
    filtered = dict(payload)
    modules = payload.get("modules")
    if isinstance(modules, list):
        filtered["modules"] = [
            module
            for module in modules
            if isinstance(module, dict) and (module.get("guid") or "").lower() in allowed
        ]
    return filtered


def _provider_entry_allowed(entry: Any, allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    if not isinstance(entry, dict):
        return False
    guid = entry.get("libretro_guid") or entry.get("guid")
    return bool(guid and guid.lower() in allowed)


def _filter_providers_payload(payload: dict, user: dict) -> dict:
    allowed = _allowed_guids(user)
    if allowed is None:
        return payload
    filtered = {
        key: value
        for key, value in payload.items()
        if key != "console_root"
    }
    console_root = payload.get("console_root") or {}
    filtered_root: dict[str, dict] = {}
    if isinstance(console_root, dict):
        for brand, consoles in console_root.items():
            if not isinstance(consoles, dict):
                continue
            brand_block: dict[str, Any] = {}
            for console, entry in consoles.items():
                if isinstance(entry, list):
                    entries = [item for item in entry if _provider_entry_allowed(item, allowed)]
                    if entries:
                        brand_block[console] = entries[0] if len(entries) == 1 else entries
                elif _provider_entry_allowed(entry, allowed):
                    brand_block[console] = entry
            if brand_block:
                filtered_root[brand] = brand_block
    filtered["console_root"] = filtered_root
    return filtered


def _filter_payload(target: str, payload: dict, user: dict) -> dict:
    if target == "modules":
        return _filter_modules_payload(payload, user)
    if target == "providers":
        return _filter_providers_payload(payload, user)
    return payload


def _allowed_cache_prefixes(user: dict) -> set[Path] | None:
    allowed = _allowed_guids(user)
    if allowed is None:
        return None
    providers = _filter_providers_payload(_load_providers_payload(), user)
    console_root = providers.get("console_root") or {}
    prefixes: set[Path] = set()
    if isinstance(console_root, dict):
        for brand, consoles in console_root.items():
            if not isinstance(consoles, dict):
                continue
            for console in consoles.keys():
                prefixes.add(Path(manufacturer_slug(brand)) / console_slug(console))
    return prefixes


def _cache_file_allowed(path: Path, cache_dir: Path, user: dict) -> bool:
    prefixes = _allowed_cache_prefixes(user)
    if prefixes is None:
        return True
    try:
        relative = path.relative_to(cache_dir)
    except ValueError:
        return False
    return any(relative == prefix or prefix in relative.parents for prefix in prefixes)


def _load_modules_payload() -> dict:
    if not MODULES_FILE.exists():
        raise HTTPException(status_code=404, detail="libretro_modules.json not found on server")
    try:
        modules = json.loads(MODULES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid modules JSON: {exc}") from exc
    return modules


def _load_providers_payload() -> dict:
    if not PROVIDERS_FILE.exists():
        fallback = _resolve_data_file("providers", "providers.json")
        if fallback != PROVIDERS_FILE and fallback.exists():
            try:
                payload = json.loads(fallback.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=500, detail=f"Invalid providers JSON: {exc}") from exc
            _save_providers_payload(payload)
            return payload
        raise HTTPException(status_code=404, detail="providers.json not found on server")
    try:
        providers = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid providers JSON: {exc}") from exc
    return providers


def _save_providers_payload(payload: dict) -> dict:
    PROVIDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROVIDERS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    return payload


def _file_timestamp(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except Exception:
        return None


def _split_module_label(label: str | None) -> tuple[str | None, str | None]:
    if not label:
        return None, None
    parts = [segment.strip() for segment in label.split("-", 1)]
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, label.strip()


def _module_by_guid(guid: str | None) -> dict | None:
    if not guid:
        return None
    target = guid.lower()
    modules = _load_modules_payload().get("modules") or []
    if not isinstance(modules, list):
        return None
    for module in modules:
        if isinstance(module, dict) and (module.get("guid") or "").lower() == target:
            return module
    return None


def _module_by_name(name: str | None) -> dict | None:
    if not name:
        return None
    modules = _load_modules_payload().get("modules") or []
    if not isinstance(modules, list):
        return None
    for module in modules:
        if isinstance(module, dict) and module.get("name") == name:
            return module
    return None


def _load_console_info_cache() -> dict:
    if not CONSOLE_INFO_FILE.exists():
        return {}
    try:
        payload = json.loads(CONSOLE_INFO_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_console_info_cache(payload: dict) -> None:
    CONSOLE_INFO_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_path = CONSOLE_INFO_FILE.with_suffix(CONSOLE_INFO_FILE.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(CONSOLE_INFO_FILE)


def _console_info_payload(brand: str, console: str, guid: str | None, module_name: str | None) -> dict:
    cache_key = guid or f"{manufacturer_slug(brand)}:{console_slug(console)}"
    cache = _load_console_info_cache()
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    query = _wikipedia_query_for_console(brand, console, module_name)
    info = _fetch_wikipedia_console_info(query)
    payload = {
        "brand": brand,
        "console": console,
        "guid": guid,
        "query": query,
        **info,
    }
    if payload.get("status") != "error":
        cache[cache_key] = payload
        _save_console_info_cache(cache)
    return payload


def _wikipedia_query_for_console(brand: str, console: str, module_name: str | None = None) -> str:
    label = module_name or f"{brand} {console}"
    replacements = {
        "Atari - 2600": "Atari 2600",
        "Atari - 5200": "Atari 5200",
        "Atari - 7800": "Atari 7800",
        "Atari - Jaguar": "Atari Jaguar",
        "Atari - Lynx": "Atari Lynx",
        "Coleco - ColecoVision": "ColecoVision",
        "Emerson - Arcadia 2001": "Arcadia 2001",
        "Entex - Adventure Vision": "Adventure Vision",
        "Epoch - Super Cassette Vision": "Super Cassette Vision",
        "Fairchild - Channel F": "Fairchild Channel F",
        "Funtech - Super Acan": "Super A'Can",
        "GamePark - GP32": "GP32",
        "GCE - Vectrex": "Vectrex",
        "Hartung - Game Master": "Hartung Game Master",
        "NEC - PC Engine - TurboGrafx 16": "TurboGrafx-16",
        "NEC - PC Engine CD - TurboGrafx-CD": "TurboGrafx-CD",
        "Sega - 32X": "Sega 32X",
        "Sega - Game Gear": "Game Gear",
        "Sega - Master System - Mark III": "Master System",
        "Sega - Mega Drive - Genesis": "Sega Genesis",
        "Sega - Mega-CD - Sega CD": "Sega CD",
        "Sega - Saturn": "Sega Saturn",
        "Sega - SG-1000": "SG-1000",
    }
    return replacements.get(label, label.replace(" - ", " "))


def _fetch_wikipedia_console_info(query: str) -> dict:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": "1",
        "prop": "extracts|pageimages|info",
        "exintro": "1",
        "explaintext": "1",
        "piprop": "thumbnail|original",
        "pithumbsize": "720",
        "inprop": "url",
        "format": "json",
        "formatversion": "2",
    }
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ROMs-Manager/0.1 console-info"})
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {
            "source": "wikipedia",
            "status": "error",
            "message": str(exc),
            "title": None,
            "summary": None,
            "page_url": None,
            "image_url": None,
        }

    pages = (data.get("query") or {}).get("pages") or []
    page = pages[0] if pages else {}
    thumbnail = page.get("thumbnail") or {}
    original = page.get("original") or {}
    return {
        "source": "wikipedia",
        "status": "ok" if page else "not_found",
        "title": page.get("title"),
        "summary": page.get("extract"),
        "page_url": page.get("fullurl"),
        "image_url": original.get("source") or thumbnail.get("source"),
    }


def _coverage_provider_status(brand: str, console: str, provider_id: str | None) -> dict:
    try:
        return _public_cache_status(cache_status(brand, console, provider_id))
    except Exception:
        return {
            "metadata": False,
            "metadata_count": 0,
            "listings": False,
            "listings_count": 0,
            "torrent": False,
            "torrent_count": 0,
            "rom_json": False,
            "rom_json_count": 0,
        }


def _public_cache_status(status: dict) -> dict:
    public = {}
    for key in ("metadata", "listings", "torrent", "rom_json"):
        public[key] = bool(status.get(key))
        files = status.get(f"{key}_files")
        public[f"{key}_count"] = len(files) if isinstance(files, list) else 0
    return public


def _provider_coverage_payload(brand: str, console: str, guid: str | None) -> dict:
    module = _module_by_guid(guid)
    module_brand, module_console = _split_module_label(module.get("name") if module else None)
    catalog_brand = module_brand or brand
    catalog_console = module_console or console
    module_guid = guid or (module.get("guid") if module else None)

    try:
        catalog = build_rom_catalog(catalog_brand, catalog_console, module_guid=module_guid)
    except FileNotFoundError as exc:
        return {
            "brand": brand,
            "console": console,
            "guid": module_guid,
            "module": module,
            "ready": False,
            "missing": "rdb",
            "message": str(exc),
            "summary": {
                "rdb_entries": 0,
                "provider_count": 0,
                "matched_entries": 0,
                "unmatched_entries": 0,
                "multi_provider_entries": 0,
                "coverage_percent": 0,
            },
            "providers": [],
            "unmatched_samples": [],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    roms = catalog.get("roms") or []
    provider_catalogs = catalog.get("provider_catalogs") or []
    provider_stats: dict[str, dict] = {}
    for provider in provider_catalogs:
        provider_id = provider.get("id") or "default"
        metadata = provider.get("metadata") or {}
        provider_console = metadata.get("console") or console
        provider_brand = metadata.get("manufacturer") or brand
        provider_stats[provider_id] = {
            "id": provider_id,
            "label": provider.get("label") or provider_id,
            "archive_id": metadata.get("archive_id"),
            "provider": metadata.get("provider_label") or metadata.get("provider"),
            "source_roms": len(provider.get("roms") or []),
            "matched_entries": 0,
            "matched_unique_files": 0,
            "status": _coverage_provider_status(provider_brand, provider_console, provider_id),
        }

    matched_entries = 0
    multi_provider_entries = 0
    unmatched_samples = []
    matched_files: dict[str, set[str]] = {provider_id: set() for provider_id in provider_stats}
    for rom in roms:
        providers = rom.get("_providers") or []
        provider_ids = {provider.get("provider_id") for provider in providers if provider.get("provider_id")}
        if provider_ids:
            matched_entries += 1
        if len(provider_ids) > 1:
            multi_provider_entries += 1
        if not provider_ids and len(unmatched_samples) < 25:
            unmatched_samples.append(
                {
                    "name": rom.get("name"),
                    "region": rom.get("region"),
                    "md5": rom.get("md5"),
                    "crc32": rom.get("crc32"),
                }
            )
        for provider in providers:
            provider_id = provider.get("provider_id")
            if not provider_id or provider_id not in provider_stats:
                continue
            provider_stats[provider_id]["matched_entries"] += 1
            provider_rom = provider.get("rom") or {}
            matched_files[provider_id].add(
                provider_rom.get("md5")
                or provider_rom.get("sha1")
                or provider_rom.get("name")
                or rom.get("_key")
            )

    for provider_id, files in matched_files.items():
        provider_stats[provider_id]["matched_unique_files"] = len(files)

    total = int(catalog.get("entry_count") or len(roms))
    coverage_percent = round((matched_entries / total) * 100, 2) if total else 0
    return {
        "brand": brand,
        "console": console,
        "guid": module_guid,
        "module": module,
        "ready": True,
        "rdb_path": _public_data_path(catalog.get("rdb_path")),
        "summary": {
            "rdb_entries": total,
            "provider_count": len(provider_catalogs),
            "matched_entries": matched_entries,
            "unmatched_entries": max(total - matched_entries, 0),
            "multi_provider_entries": multi_provider_entries,
            "coverage_percent": coverage_percent,
        },
        "providers": sorted(provider_stats.values(), key=lambda item: item.get("label") or item.get("id")),
        "unmatched_samples": unmatched_samples,
    }


def _module_readiness_payload(module: dict) -> dict:
    guid = module.get("guid")
    name = module.get("name")
    brand, console = _split_module_label(name)
    provider_entries = _provider_entries_for_module(guid, brand, console)
    rdb_path = _rdb_json_path_for_module_name(name)
    rdb_exists = bool(rdb_path and rdb_path.exists())
    core_entries = _core_entries_for_guid(guid)
    bios_entries = _bios_entries_for_cores(core_entries)
    coverage = (
        _provider_coverage_payload(brand or "", console or "", guid)
        if brand and console and provider_entries and rdb_exists
        else None
    )

    checks = {
        "providers": _readiness_check(bool(provider_entries), f"{len(provider_entries)} provider(s)", "No providers"),
        "rdb": _readiness_check(rdb_exists, "RDB exported", "RDB missing"),
        "core_metadata": _readiness_check(bool(core_entries), f"{len(core_entries)} core mapping(s)", "No core metadata"),
        "bios_metadata": _bios_metadata_check(bios_entries),
        "coverage": _coverage_check(coverage, bool(provider_entries), rdb_exists),
    }
    score = _server_readiness_score(checks)
    return {
        "module": module,
        "guid": guid,
        "name": name,
        "brand": brand,
        "console": console,
        "score": score,
        "summary": {
            "ready": score == "ready",
            "label": {
                "ready": "Ready for assignment",
                "partial": "Usable with gaps",
                "needs_work": "Needs setup",
            }.get(score, "Needs setup"),
        },
        "checks": checks,
        "providers": provider_entries,
        "core_metadata": core_entries,
        "bios_metadata": bios_entries,
        "coverage": coverage,
    }


def _readiness_check(ok: bool, ok_label: str, missing_label: str) -> dict:
    return {"state": "ok" if ok else "missing", "label": ok_label if ok else missing_label}


def _coverage_check(coverage: dict | None, has_providers: bool, rdb_exists: bool) -> dict:
    if not has_providers:
        return {"state": "missing", "label": "No providers"}
    if not rdb_exists:
        return {"state": "missing", "label": "RDB missing"}
    if not coverage or not coverage.get("ready"):
        return {"state": "missing", "label": "Coverage unavailable"}
    summary = coverage.get("summary") or {}
    percent = float(summary.get("coverage_percent") or 0)
    state = "ok" if percent >= 100 else "partial" if percent > 0 else "missing"
    return {
        "state": state,
        "label": f"{summary.get('matched_entries', 0)}/{summary.get('rdb_entries', 0)} ({percent:.2f}%)",
        "coverage_percent": percent,
    }


def _server_readiness_score(checks: dict) -> str:
    states = {key: value.get("state") for key, value in checks.items() if isinstance(value, dict)}
    if any(states.get(key) == "missing" for key in ("providers", "rdb", "core_metadata")):
        return "needs_work"
    if states.get("coverage") == "missing":
        return "needs_work"
    if any(state == "partial" for state in states.values()):
        return "partial"
    return "ready"


def _rdb_json_path_for_module_name(name: str | None) -> Path | None:
    if not name:
        return None
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "default"
    return _resolve_data_dir("index", "rdb") / f"{slug}.json"


def _public_data_path(path_value: str | Path | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    for root, prefix in ((BACKEND_DATA_DIR, "backend-data"), (ROOT_DIR / "data", "data")):
        try:
            relative = path.resolve().relative_to(root.resolve())
        except Exception:
            continue
        return f"{prefix}/{relative.as_posix()}"
    return Path(path_value).name


def _public_path_payload(payload):
    if isinstance(payload, dict):
        return {key: _public_path_payload(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_public_path_payload(value) for value in payload]
    if isinstance(payload, str):
        public = _public_data_path(payload)
        return public or payload
    return payload


def _provider_entries_for_module(guid: str | None, brand: str | None, console: str | None) -> list[dict]:
    providers = _load_providers_payload()
    root = providers.get("console_root") or {}
    entries: list[dict] = []
    if not isinstance(root, dict):
        return entries
    for maker, consoles in root.items():
        if not isinstance(consoles, dict):
            continue
        for console_name, entry in consoles.items():
            candidates = entry if isinstance(entry, list) else [entry]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                candidate_guid = candidate.get("libretro_guid") or candidate.get("guid")
                if guid and candidate_guid == guid:
                    entries.append({"brand": maker, "console": console_name, **candidate})
                elif brand and console and maker == brand and console_name == console:
                    entries.append({"brand": maker, "console": console_name, **candidate})
    return entries


def _core_entries_for_guid(guid: str | None) -> list[dict]:
    if not guid:
        return []
    core_path = _resolve_data_file("emulators", "cores.json")
    if not core_path.exists():
        return []
    try:
        payload = json.loads(core_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    core_entries = []
    for core_id, meta in (payload.get("cores") or {}).items():
        if guid in (meta.get("console_guids") or []):
            core_entries.append({"id": core_id, **meta})
    return core_entries


def _bios_entries_for_cores(core_entries: list[dict]) -> list[dict]:
    core_path = _resolve_data_file("emulators", "cores.json")
    if not core_path.exists():
        return []
    try:
        payload = json.loads(core_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    bios_registry = payload.get("bios_files") or {}
    bios_entries = []
    seen = set()
    for core in core_entries:
        for bios_id in core.get("bios_ids") or []:
            if bios_id in seen:
                continue
            seen.add(bios_id)
            bios_entries.append({"id": bios_id, **(bios_registry.get(bios_id) or {})})
    return bios_entries


def _bios_metadata_check(bios_entries: list[dict]) -> dict:
    if not bios_entries:
        return {"state": "ok", "label": "No BIOS metadata required"}
    with_sources = sum(1 for entry in bios_entries if entry.get("url") or entry.get("sources"))
    return {
        "state": "ok" if with_sources == len(bios_entries) else "partial",
        "label": f"{len(bios_entries)} BIOS item(s), {with_sources} with source metadata",
    }


def _iter_rom_files() -> list[Path]:
    if not ROMS_DIR.exists():
        return []
    return sorted(ROMS_DIR.glob("*.json"))


def _collect_rom_metadata(user: dict | None = None) -> list[dict]:
    rom_sets: list[dict] = []
    for rom_file in _iter_rom_files():
        try:
            payload = json.loads(rom_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        module_name = payload.get("module")
        brand, console = _split_module_label(module_name)
        entries = payload.get("entries")
        entry_count = payload.get("entry_count")
        if entry_count is None and isinstance(entries, list):
            entry_count = len(entries)
        guid = payload.get("guid")
        if user is not None and not _guid_allowed(guid, user):
            continue
        rom_sets.append(
            {
                "slug": rom_file.stem,
                "module": module_name,
                "brand": brand or payload.get("brand"),
                "console": console or payload.get("console"),
                "guid": guid,
                "dataset_role": payload.get("dataset_role") or "master_rom_list",
                "source_kind": payload.get("source_kind") or "libretro_rdb",
                "source_label": payload.get("source_label") or "Libretro database RDB",
                "source_url": payload.get("source_url"),
                "entry_count": entry_count,
                "fetched_at": payload.get("fetched_at"),
            }
        )
    return rom_sets


def _load_rom_dataset(identifier: str) -> dict | None:
    for rom_file in _iter_rom_files():
        slug = rom_file.stem
        try:
            payload = json.loads(rom_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        guid = payload.get("guid")
        if identifier == slug or (guid and identifier.lower() == guid.lower()):
            if "entry_count" not in payload and isinstance(payload.get("entries"), list):
                payload["entry_count"] = len(payload["entries"])
            payload.setdefault("slug", slug)
            return payload
    return None


def _rom_dataset_path_for_module(module: dict) -> Path | None:
    name = module.get("name")
    if not name:
        return None
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "default"
    return ROMS_DIR / f"{slug}.json"


def _sync_rom_dataset_from_rdb(module: dict) -> dict | None:
    rdb_path = _rdb_json_path_for_module_name(module.get("name"))
    target = _rom_dataset_path_for_module(module)
    if not rdb_path or not target or not rdb_path.exists():
        return None
    try:
        payload = json.loads(rdb_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid RDB JSON: {exc}") from exc

    entries = payload.get("entries")
    if not isinstance(entries, list):
        entries = []
    entries = [entry for entry in entries if _is_rom_entry(entry)]
    dataset = {
        "module": module.get("name") or payload.get("module"),
        "guid": module.get("guid") or payload.get("guid"),
        "dataset_role": "master_rom_list",
        "source_kind": "libretro_rdb",
        "source_label": "Libretro database RDB",
        "source_url": payload.get("source_url"),
        "entry_count": len(entries),
        "fetched_at": payload.get("fetched_at") or datetime.utcnow().isoformat(),
        "entries": entries,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dataset, indent=2), encoding="utf-8")
    return {
        "slug": target.stem,
        "module": dataset["module"],
        "guid": dataset["guid"],
        "dataset_role": dataset["dataset_role"],
        "source_kind": dataset["source_kind"],
        "source_label": dataset["source_label"],
        "entry_count": dataset["entry_count"],
        "path": _public_data_path(target),
    }


def _is_rom_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    return any(entry.get(field) for field in ("name", "description", "rom_name", "md5", "sha1", "crc", "crc32"))


def _module_for_provider(brand: str, console: str, provider_slug: str | None) -> dict | None:
    providers = _load_providers_payload().get("console_root") or {}
    entry = (providers.get(brand) or {}).get(console) if isinstance(providers, dict) else None
    if not entry:
        return None
    entries = entry if isinstance(entry, list) else [entry]
    target_slug = _provider_slug({"archive_id": provider_slug}) if provider_slug else None
    for candidate in entries:
        if not isinstance(candidate, dict):
            continue
        candidate_slug = _provider_slug(candidate)
        if target_slug and candidate_slug != target_slug:
            continue
        guid = candidate.get("libretro_guid") or candidate.get("guid")
        return _module_by_guid(guid) if guid else _module_by_name(f"{brand} - {console}")
    return None


def _provider_slug(entry: dict) -> str:
    raw = entry.get("archive_id") or entry.get("provider") or entry.get("name") or "default"
    return re.sub(r"[^a-z0-9]+", "_", str(raw).lower()).strip("_") or "default"


def _normalize_brand_console_entries(console_root: dict, brand: str, console: str) -> tuple[dict, list[dict]]:
    brand_block = console_root.setdefault(brand, {})
    current = brand_block.get(console)
    if current is None:
        entries: list[dict] = []
    elif isinstance(current, list):
        entries = list(current)
    else:
        entries = [current]
    return brand_block, entries


def _set_brand_console_entries(console_root: dict, brand: str, console: str, entries: list[dict]) -> None:
    brand_block = console_root.setdefault(brand, {})
    if not entries:
        brand_block.pop(console, None)
    elif len(entries) == 1:
        brand_block[console] = entries[0]
    else:
        brand_block[console] = entries
    if not brand_block:
        console_root.pop(brand, None)


def _resolve_target(target: str) -> tuple[str, dict, Path]:
    normalized = target.lower()
    if normalized == "modules":
        return "modules", _load_modules_payload(), MODULES_FILE
    if normalized == "providers":
        return "providers", _load_providers_payload(), PROVIDERS_FILE
    raise HTTPException(status_code=400, detail=f"Unknown target '{target}'")


def _upsert_provider_entry(payload: dict, request: ProviderUpsertRequest) -> dict:
    console_root = payload.setdefault("console_root", {})
    brand = request.brand.strip()
    console = request.console.strip()
    if not brand or not console:
        raise HTTPException(status_code=400, detail="Brand and console names are required")
    entry = request.entry.dict(exclude_unset=True)
    archive_id = entry.get("archive_id")
    if not archive_id:
        raise HTTPException(status_code=400, detail="archive_id is required")
    entry_name = entry.get("name") or entry.get("provider") or archive_id
    entry["name"] = entry_name
    brand_block, entries = _normalize_brand_console_entries(console_root, brand, console)
    target_archive = request.previous_archive_id or archive_id
    replaced = False
    new_entries: list[dict] = []
    for current in entries:
        current_id = current.get("archive_id")
        if current_id == target_archive:
            new_entries.append(entry)
            replaced = True
        else:
            new_entries.append(current)
    if not replaced:
        new_entries.append(entry)
    _set_brand_console_entries(console_root, brand, console, new_entries)
    return payload


def _delete_provider_entry(payload: dict, request: ProviderDeleteRequest) -> dict:
    console_root = payload.get("console_root")
    if not console_root:
        raise HTTPException(status_code=404, detail="Providers dataset is empty")
    brand = request.brand.strip()
    console = request.console.strip()
    archive_id = request.archive_id.strip()
    if not brand or not console or not archive_id:
        raise HTTPException(status_code=400, detail="Brand, console, and archive_id are required")
    brand_block = console_root.get(brand)
    if not brand_block or console not in brand_block:
        raise HTTPException(status_code=404, detail="Provider entry not found")
    _, entries = _normalize_brand_console_entries(console_root, brand, console)
    filtered = [entry for entry in entries if entry.get("archive_id") != archive_id]
    if len(filtered) == len(entries):
        raise HTTPException(status_code=404, detail="Provider entry not found")
    _set_brand_console_entries(console_root, brand, console, filtered)
    return payload


@app.get("/me")
async def me(user: dict = Depends(current_user)) -> dict:
    return {"user": _public_user(user)}


@app.get("/client/sync")
async def client_sync_manifest(user: dict = Depends(current_user)) -> dict:
    modules_payload = _filter_modules_payload(_load_modules_payload(), user)
    providers_payload = _filter_providers_payload(_load_providers_payload(), user)
    roms = _collect_rom_metadata(user)
    cache_meta = _collect_cache_metadata(user)
    modules = modules_payload.get("modules")
    providers_root = providers_payload.get("console_root")
    provider_count = (
        sum(len(systems) if isinstance(systems, dict) else 0 for systems in providers_root.values())
        if isinstance(providers_root, dict)
        else 0
    )
    return {
        "user": _public_user(user),
        "datasets": {
            "modules": {
                "version": modules_payload.get("fetched_at") or _file_timestamp(MODULES_FILE),
                "count": len(modules) if isinstance(modules, list) else 0,
            },
            "providers": {
                "version": providers_payload.get("fetched_at") or _file_timestamp(PROVIDERS_FILE),
                "count": provider_count,
            },
            "roms": {
                "version": max((entry.get("fetched_at") for entry in roms if entry.get("fetched_at")), default=None),
                "count": len(roms),
            },
            "cache": {
                "version": cache_meta.get("updated"),
                "count": cache_meta.get("file_count", 0),
                "size": cache_meta.get("size", 0),
            },
        },
        "modules": modules if isinstance(modules, list) else [],
    }


@app.get("/access/users")
async def list_users(_: dict = Depends(require_admin)) -> dict:
    payload = _load_users_payload()
    users = [_public_user(user) for user in payload.get("users") or []]
    return {"users": users, "count": len(users)}


@app.post("/access/users")
async def create_user(request: UserCreateRequest, _: dict = Depends(require_admin)) -> dict:
    payload = _load_users_payload()
    user_id = request.id.strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="User id is required")
    if _find_user(payload, user_id):
        raise HTTPException(status_code=409, detail=f"User '{user_id}' already exists")
    api_key = request.api_key.strip() if request.api_key else None
    if request.generate_api_key and not api_key:
        api_key = _generate_api_key()
    user = {
        "id": user_id,
        "name": request.name or user_id,
        "enabled": request.enabled,
        "admin": request.admin,
        "allowed_console_guids": ["*"] if request.admin else request.allowed_console_guids,
    }
    if api_key:
        user["api_key_hash"] = _hash_api_key(api_key)
    payload.setdefault("users", []).append(user)
    _save_users_payload(payload)
    response = {"user": _public_user(user)}
    if api_key:
        response["api_key"] = api_key
    return response


@app.patch("/access/users/{user_id}")
async def update_user(
    user_id: str,
    request: UserUpdateRequest,
    _: dict = Depends(require_admin),
) -> dict:
    payload = _load_users_payload()
    user = _find_user(payload, user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
    updates = request.dict(exclude_unset=True)
    if "name" in updates:
        user["name"] = request.name or user_id
    if request.enabled is not None:
        user["enabled"] = request.enabled
    if request.admin is not None:
        user["admin"] = request.admin
        if request.admin:
            user["allowed_console_guids"] = ["*"]
    if request.allowed_console_guids is not None and not user.get("admin"):
        user["allowed_console_guids"] = request.allowed_console_guids
    api_key = request.api_key.strip() if request.api_key else None
    if request.reset_api_key and not api_key:
        api_key = _generate_api_key()
    if api_key:
        user["api_key_hash"] = _hash_api_key(api_key)
    _save_users_payload(payload)
    response = {"user": _public_user(user)}
    if api_key:
        response["api_key"] = api_key
    return response


@app.post("/access/users/{user_id}/preview-link")
async def create_user_preview_link(user_id: str, _: dict = Depends(require_admin)) -> dict:
    payload = _load_users_payload()
    user = _find_user(payload, user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")
    if not user.get("enabled", True):
        raise HTTPException(status_code=403, detail="User is disabled")
    api_key = _generate_api_key()
    expires_at = _preview_key_expiry()
    preview_keys = user.setdefault("preview_keys", [])
    preview_keys.append(
        {
            "api_key_hash": _hash_api_key(api_key),
            "expires_at": expires_at,
        }
    )
    user["preview_keys"] = preview_keys[-5:]
    _save_users_payload(payload)
    return {
        "user": _public_user(user),
        "api_key": api_key,
        "expires_at": expires_at,
        "url": f"/admin/?api_key={api_key}&preview_user={user_id}",
    }


@app.get("/update")
async def fetch_seed_payload(
    target: str = Query("modules"),
    user: dict = Depends(current_user),
) -> JSONResponse:
    """
    Deliver the base dataset required by a fresh ROMs Manager install.

    Supported targets: modules, providers.
    """
    normalized, payload, path = _resolve_target(target)
    payload = _filter_payload(normalized, payload, user)
    version = payload.get("fetched_at") or _file_timestamp(path)
    if normalized == "modules":
        data = payload.get("modules", [])
        key = "modules"
    else:
        data = payload
        key = "providers"

    return JSONResponse(
        content={
            "target": normalized,
            "version": version,
            key: data,
        }
    )


@app.get("/update/meta")
async def fetch_seed_metadata(
    target: str = Query("modules"),
    user: dict = Depends(current_user),
) -> dict:
    """Return metadata for a given dataset without downloading the entire payload."""
    normalized, payload, path = _resolve_target(target)
    payload = _filter_payload(normalized, payload, user)

    if normalized == "modules":
        entries = payload.get("modules")
        count = len(entries) if isinstance(entries, list) else None
    else:
        consoles = payload.get("console_root")
        if isinstance(consoles, dict):
            count = sum(
                len(systems) if isinstance(systems, dict) else 0
                for systems in consoles.values()
            )
        else:
            count = None

    return {
        "target": normalized,
        "version": payload.get("fetched_at") or _file_timestamp(path),
        "count": count,
    }


@app.get("/roms")
async def list_rom_datasets(user: dict = Depends(current_user)) -> dict:
    """Return metadata for every exported ROM dataset available on the server."""
    metadata = _collect_rom_metadata(user)
    return {"roms": metadata, "count": len(metadata)}


@app.get("/roms/{identifier}")
async def fetch_rom_dataset(identifier: str, user: dict = Depends(current_user)) -> dict:
    """Return the ROM dataset payload for a given slug or GUID."""
    dataset = _load_rom_dataset(identifier)
    if not dataset:
        raise HTTPException(status_code=404, detail=f"ROM dataset '{identifier}' not found")
    if not _guid_allowed(dataset.get("guid"), user):
        raise HTTPException(status_code=403, detail="Console is not assigned to this API key")
    return dataset


@app.post("/modules/tasks/export-rdb")
async def export_module_rdb_task(request: ModuleExportRdbRequest, _: dict = Depends(require_admin)) -> dict:
    module = _module_by_guid(request.guid) or _module_by_name(request.name)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    try:
        path = export_module_rdb(module)
        rom_dataset = _sync_rom_dataset_from_rdb(module)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "module": module,
        "guid": module.get("guid"),
        "path": _public_data_path(path),
        "rom_dataset": rom_dataset,
    }


@app.post("/modules/readiness")
async def module_readiness(request: ModuleReadinessRequest, _: dict = Depends(require_admin)) -> dict:
    module = _module_by_guid(request.guid) or _module_by_name(request.name)
    if not module:
        raise HTTPException(status_code=404, detail="Module not found")
    return _module_readiness_payload(module)


@app.post("/consoles/info")
async def console_info(request: ConsoleInfoRequest, user: dict = Depends(current_user)) -> dict:
    if request.guid and not _guid_allowed(request.guid, user):
        raise HTTPException(status_code=403, detail="Console is not assigned to this API key")
    brand = request.brand.strip()
    console = request.console.strip()
    if not brand or not console:
        raise HTTPException(status_code=400, detail="Brand and console are required")
    return _console_info_payload(brand, console, request.guid, request.module)


@app.post("/providers")
async def upsert_provider(request: ProviderUpsertRequest, _: dict = Depends(require_admin)) -> dict:
    payload = _load_providers_payload()
    payload = _upsert_provider_entry(payload, request)
    _save_providers_payload(payload)
    return {
        "target": "providers",
        "providers": payload,
    }


def _collect_cache_metadata(user: dict | None = None) -> dict:
    cache_dir = _cache_dir()
    if not cache_dir.is_dir():
        return {
            "updated": None,
            "file_count": 0,
            "size": 0,
        }
    file_count = 0
    total_size = 0
    latest_mtime = None
    for path in cache_dir.rglob("*"):
        if path.is_file():
            if user is not None and not _cache_file_allowed(path, cache_dir, user):
                continue
            file_count += 1
            stats = path.stat()
            total_size += stats.st_size
            if latest_mtime is None or stats.st_mtime > latest_mtime:
                latest_mtime = stats.st_mtime
    updated = datetime.fromtimestamp(latest_mtime).isoformat() if latest_mtime else None
    return {
        "updated": updated,
        "file_count": file_count,
        "size": total_size,
    }


def _cleanup_temp_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    finally:
        try:
            path.parent.rmdir()
        except Exception:
            pass


@app.delete("/providers")
async def delete_provider(request: ProviderDeleteRequest, _: dict = Depends(require_admin)) -> dict:
    payload = _load_providers_payload()
    payload = _delete_provider_entry(payload, request)
    _save_providers_payload(payload)
    return {
        "target": "providers",
        "providers": payload,
    }


def _normalize_task_fields(request: ProviderTaskRequest) -> tuple[str, str, str | None]:
    brand = request.brand.strip()
    console = request.console.strip()
    provider_slug = request.provider_slug.strip() if request.provider_slug else None
    if not brand or not console:
        raise HTTPException(status_code=400, detail="Brand and console are required")
    return brand, console, provider_slug


@app.post("/providers/tasks/fetch")
async def fetch_provider_assets(request: ProviderFetchRequest, _: dict = Depends(require_admin)) -> dict:
    brand, console, provider_slug = _normalize_task_fields(request)
    try:
        summary = provider_tasks.fetch_console_metadata(
            console=console,
            manufacturer=brand,
            provider_slug=provider_slug,
            force=request.force,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "brand": brand,
        "console": console,
        "provider": provider_slug,
        "summary": _public_path_payload(summary),
    }


@app.post("/providers/tasks/export")
async def export_provider_roms(request: ProviderExportRequest, _: dict = Depends(require_admin)) -> dict:
    brand, console, provider_slug = _normalize_task_fields(request)
    try:
        roms, json_path = provider_tasks.export_console_roms(
            console=console,
            manufacturer=brand,
            provider_slug=provider_slug,
            write=request.write,
        )
        module = _module_for_provider(brand, console, provider_slug)
        rom_dataset = _sync_rom_dataset_from_rdb(module) if module else None
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "brand": brand,
        "console": console,
        "provider": provider_slug,
        "count": len(roms),
        "path": _public_data_path(json_path),
        "rom_dataset": rom_dataset,
    }


@app.post("/providers/tasks/validate")
async def validate_providers_dataset(_: dict = Depends(require_admin)) -> dict:
    ok, issues = provider_tasks.validate_providers()
    return {
        "valid": ok,
        "issues": issues,
    }


@app.post("/providers/status")
async def provider_status(request: ProviderStatusRequest, _: dict = Depends(require_admin)) -> dict:
    brand, console, provider_slug = _normalize_task_fields(request)
    try:
        status = _public_cache_status(cache_status(brand, console, provider_slug))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "brand": brand,
        "console": console,
        "provider": provider_slug,
        "status": status,
    }


@app.post("/providers/coverage")
async def provider_coverage(request: ProviderCoverageRequest, _: dict = Depends(require_admin)) -> dict:
    brand, console, _ = _normalize_task_fields(request)
    return _provider_coverage_payload(brand, console, request.guid)


@app.get("/cache/meta")
async def cache_metadata(user: dict = Depends(current_user)) -> dict:
    meta = _collect_cache_metadata(user)
    return meta


@app.get("/cache/archive")
async def download_cache_archive(user: dict = Depends(current_user)) -> FileResponse:
    cache_dir = _cache_dir()
    if not cache_dir.is_dir():
        raise HTTPException(status_code=404, detail="Cache directory is empty.")
    temp_dir = Path(tempfile.mkdtemp(prefix="cache_export_"))
    zip_name = f"cache-export-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
    zip_path = temp_dir / zip_name
    included = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root, _, files in os.walk(cache_dir):
            for filename in files:
                abs_path = Path(root) / filename
                if not _cache_file_allowed(abs_path, cache_dir, user):
                    continue
                arcname = abs_path.relative_to(cache_dir)
                archive.write(abs_path, arcname.as_posix())
                included += 1
    if included == 0:
        _cleanup_temp_file(zip_path)
        raise HTTPException(status_code=404, detail="No cache files available for this API key.")
    response = FileResponse(
        zip_path,
        filename=zip_name,
        media_type="application/zip",
    )
    response.background = BackgroundTask(_cleanup_temp_file, zip_path)
    return response
