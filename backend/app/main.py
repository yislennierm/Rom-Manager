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
LIBRETRO_THUMBNAILS_DIR = _resolve_data_dir("index", "libretro")
USERS_FILE = BACKEND_DATA_DIR / "users" / "users.json"
CONSOLE_INFO_FILE = BACKEND_DATA_DIR / "cache" / "console_info.json"
CONSOLE_LOGOS_FILE = BACKEND_DATA_DIR / "console_logos.json"
ROM_ARTWORK_ALIASES_FILE = BACKEND_DATA_DIR / "rom_artwork_aliases.json"
CONSOLE_PROGRESS_FILE = BACKEND_DATA_DIR / "console_progress.json"


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


class ConsoleImageSelectionRequest(ConsoleInfoRequest):
    image_index: int


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


WIKIPEDIA_IMAGE_SKIP_PATTERNS = (
    "ambox",
    "commons-logo",
    "crystal_clear",
    "edit-clear",
    "flag_of_",
    "map_",
    "nuvola",
    "oojs_ui",
    "question_book",
    "sound-icon",
    "speaker_icon",
    "symbol_",
    "wikidata-logo",
    "wikimedia-logo",
)

WIKIPEDIA_IMAGE_HARDWARE_TERMS = (
    "attached",
    "console",
    "controller",
    "device",
    "front",
    "handheld",
    "hardware",
    "open",
    "portable",
    "set",
    "system",
    "unit",
)

CONSOLE_IMAGE_OPTIONS_VERSION = 2


def _wikipedia_api_get(params: dict[str, str]) -> dict:
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ROMs-Manager/0.1 console-info"})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _wikipedia_image_candidate(title: str | None, mime: str | None) -> bool:
    if not title:
        return False
    if mime and not mime.startswith("image/"):
        return False
    normalized = title.lower().replace(" ", "_")
    return not any(pattern in normalized for pattern in WIKIPEDIA_IMAGE_SKIP_PATTERNS)


def _canonical_image_url(url: str | None) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    return urllib.parse.urlsplit(url)._replace(query="", fragment="").geturl()


def _wikipedia_image_rank(option: dict, primary_url: str | None, order: int) -> tuple[int, int]:
    title = str(option.get("title") or "").lower()
    url = _canonical_image_url(option.get("url") or option.get("thumbnail_url"))
    primary = _canonical_image_url(primary_url)
    score = 50
    if url and primary and url == primary:
        score -= 20
    if any(term in title for term in WIKIPEDIA_IMAGE_HARDWARE_TERMS):
        score -= 35
    if "logo" in title:
        score += 35
    if any(term in title for term in ("box", "cover", "packaging")):
        score += 10
    return score, order


def _wikipedia_image_url(option: dict | None) -> str | None:
    if not isinstance(option, dict):
        return None
    url = option.get("url") or option.get("thumbnail_url")
    return url if isinstance(url, str) and url else None


def _fetch_wikipedia_image_options(page_title: str | None, primary_url: str | None = None) -> list[dict]:
    if not page_title:
        return []
    try:
        data = _wikipedia_api_get(
            {
                "action": "query",
                "titles": page_title,
                "prop": "images",
                "imlimit": "50",
                "redirects": "1",
                "format": "json",
                "formatversion": "2",
            }
        )
    except Exception:
        return []

    pages = (data.get("query") or {}).get("pages") or []
    page = pages[0] if pages else {}
    image_titles = [
        image.get("title")
        for image in page.get("images") or []
        if isinstance(image, dict) and isinstance(image.get("title"), str)
    ]
    options_by_title: dict[str, dict] = {}
    order_by_title = {title: index for index, title in enumerate(image_titles)}
    for start in range(0, len(image_titles), 50):
        titles = image_titles[start : start + 50]
        if not titles:
            continue
        try:
            image_data = _wikipedia_api_get(
                {
                    "action": "query",
                    "titles": "|".join(titles),
                    "prop": "imageinfo",
                    "iiprop": "url|mime|size",
                    "iiurlwidth": "720",
                    "format": "json",
                    "formatversion": "2",
                }
            )
        except Exception:
            continue
        for image_page in (image_data.get("query") or {}).get("pages") or []:
            if not isinstance(image_page, dict):
                continue
            title = image_page.get("title")
            image_info = (image_page.get("imageinfo") or [{}])[0]
            if not isinstance(image_info, dict):
                continue
            mime = image_info.get("mime")
            if not _wikipedia_image_candidate(title, mime):
                continue
            url = image_info.get("url")
            thumbnail_url = image_info.get("thumburl") or url
            if not isinstance(url, str) and not isinstance(thumbnail_url, str):
                continue
            options_by_title[str(title)] = {
                "title": title,
                "url": url,
                "thumbnail_url": thumbnail_url,
                "mime": mime,
                "width": image_info.get("width"),
                "height": image_info.get("height"),
                "source_url": image_info.get("descriptionurl"),
            }

    options = list(options_by_title.values())
    options.sort(
        key=lambda option: _wikipedia_image_rank(
            option,
            primary_url,
            order_by_title.get(str(option.get("title") or ""), len(order_by_title)),
        )
    )
    return options


def _fetch_wikipedia_page_primary_image(page_title: str | None) -> str | None:
    if not page_title:
        return None
    try:
        data = _wikipedia_api_get(
            {
                "action": "query",
                "titles": page_title,
                "prop": "pageimages",
                "piprop": "thumbnail|original",
                "pithumbsize": "720",
                "redirects": "1",
                "format": "json",
                "formatversion": "2",
            }
        )
    except Exception:
        return None
    pages = (data.get("query") or {}).get("pages") or []
    page = pages[0] if pages else {}
    thumbnail = page.get("thumbnail") or {}
    original = page.get("original") or {}
    return original.get("source") or thumbnail.get("source")


def _console_image_index(info: dict, options: list[dict]) -> int:
    selected_index = info.get("selected_image_index")
    if isinstance(selected_index, int) and 0 <= selected_index < len(options):
        return selected_index
    selected_title = info.get("selected_image_title")
    if isinstance(selected_title, str):
        for index, option in enumerate(options):
            if option.get("title") == selected_title:
                return index
    return 0


def _apply_console_image_selection(info: dict) -> dict:
    options = info.get("image_options")
    if not isinstance(options, list) or not options:
        return info
    index = _console_image_index(info, [option for option in options if isinstance(option, dict)])
    option = options[index]
    image_url = _wikipedia_image_url(option)
    if image_url:
        info["image_url"] = image_url
    info["image_index"] = index
    return info


def _ensure_console_info_gallery(info: dict) -> dict:
    if info.get("status") != "ok" or not info.get("title"):
        return info
    options = info.get("image_options")
    needs_refresh = info.get("image_options_version") != CONSOLE_IMAGE_OPTIONS_VERSION
    if needs_refresh or not isinstance(options, list) or not options:
        primary_url = info.get("page_image_url") or _fetch_wikipedia_page_primary_image(info.get("title"))
        if primary_url:
            info["page_image_url"] = primary_url
        image_options = _fetch_wikipedia_image_options(info.get("title"), primary_url)
        if image_options:
            info["image_options"] = image_options
            info["image_options_version"] = CONSOLE_IMAGE_OPTIONS_VERSION
    return _apply_console_image_selection(info)


def _load_console_logos() -> dict:
    if not CONSOLE_LOGOS_FILE.exists():
        return {}
    try:
        payload = json.loads(CONSOLE_LOGOS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _console_logo_payload(brand: str, console: str, guid: str | None, module_name: str | None) -> dict | None:
    payload = _load_console_logos()
    entries = payload.get("logos")
    if not isinstance(entries, list):
        return None
    keys = {
        guid or "",
        module_name or "",
        f"{brand} - {console}",
        f"{manufacturer_slug(brand)}:{console_slug(console)}",
    }
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_keys = {
            entry.get("guid") or "",
            entry.get("module") or "",
            entry.get("key") or "",
        }
        if keys & entry_keys:
            return {
                "logo_url": entry.get("url"),
                "logo_source_url": entry.get("source_url"),
                "logo_license": entry.get("license"),
                "logo_credit": entry.get("credit"),
            }
    return None


def _console_info_payload(brand: str, console: str, guid: str | None, module_name: str | None) -> dict:
    cache_key = guid or f"{manufacturer_slug(brand)}:{console_slug(console)}"
    query = _wikipedia_query_for_console(brand, console, module_name)
    logo = _console_logo_payload(brand, console, guid, module_name)
    cache = _load_console_info_cache()
    cached = cache.get(cache_key)
    if isinstance(cached, dict) and cached.get("query") == query:
        cached = dict(cached)
        if logo:
            cached = {**cached, **logo}
        before = json.dumps(cached, sort_keys=True)
        cached = _ensure_console_info_gallery(cached)
        if cached.get("status") != "error" and json.dumps(cached, sort_keys=True) != before:
            cache[cache_key] = cached
            _save_console_info_cache(cache)
        return cached

    info = _fetch_wikipedia_console_info(query)
    payload = {
        "brand": brand,
        "console": console,
        "guid": guid,
        "query": query,
        **info,
    }
    if logo:
        payload.update(logo)
    payload = _ensure_console_info_gallery(payload)
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
        "Nintendo - Nintendo 3DS": "Nintendo 3DS",
        "Nintendo - Nintendo 64": "Nintendo 64",
        "Nintendo - Nintendo 64DD": "64DD",
        "Nintendo - Nintendo DS": "Nintendo DS",
        "Nintendo - Nintendo DSi": "Nintendo DSi",
        "Nintendo - Nintendo Entertainment System": "Nintendo Entertainment System",
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
    try:
        data = _wikipedia_api_get(params)
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
    primary_url = original.get("source") or thumbnail.get("source")
    image_options = _fetch_wikipedia_image_options(page.get("title"), primary_url) if page else []
    payload = {
        "source": "wikipedia",
        "status": "ok" if page else "not_found",
        "page_id": page.get("pageid"),
        "title": page.get("title"),
        "summary": page.get("extract"),
        "page_url": page.get("fullurl"),
        "page_image_url": primary_url,
        "image_url": primary_url,
        "image_options": image_options,
        "image_options_version": CONSOLE_IMAGE_OPTIONS_VERSION,
    }
    return _apply_console_image_selection(payload)


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
        module = payload.get("module")
        module_name = module.get("name") if isinstance(module, dict) else module
        brand, console = _split_module_label(module_name)
        entries = payload.get("entries")
        roms = payload.get("roms")
        entry_count = payload.get("entry_count")
        if entry_count is None and isinstance(entries, list):
            entry_count = len(entries)
        if entry_count is None and isinstance(roms, list):
            entry_count = len(roms)
        rdb_entry_count = payload.get("rdb_entry_count")
        if rdb_entry_count is None:
            rdb_entry_count = payload.get("entry_count") if isinstance(entries, list) else None
        provider_only_count = _safe_int(payload.get("provider_only_count"))
        coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
        catalog_total = _safe_int(coverage.get("catalog_total"), _safe_int(entry_count))
        if not catalog_total:
            catalog_total = _safe_int(entry_count)
        guid = payload.get("guid") or (module.get("guid") if isinstance(module, dict) else None)
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
                "rdb_entry_count": _safe_int(rdb_entry_count, _safe_int(entry_count)),
                "provider_only_count": provider_only_count,
                "catalog_total": catalog_total,
                "coverage": coverage,
                "fetched_at": payload.get("fetched_at"),
            }
        )
    return rom_sets


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_console_progress_payload() -> dict:
    if not CONSOLE_PROGRESS_FILE.exists():
        return {"version": 1, "consoles": {}}
    try:
        payload = json.loads(CONSOLE_PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "consoles": {}}
    return payload if isinstance(payload, dict) else {"version": 1, "consoles": {}}


def _dashboard_provider_index(payload: dict) -> dict:
    root = payload.get("console_root") or {}
    by_guid: dict[str, list[dict]] = {}
    by_label: dict[str, list[dict]] = {}
    brand_count = 0
    console_count = 0
    provider_count = 0
    providers_with_cache = 0
    providers_missing_cache = 0

    if isinstance(root, dict):
        brand_count = len(root)
        for brand, consoles in root.items():
            if not isinstance(consoles, dict):
                continue
            for console, entry in consoles.items():
                entries = entry if isinstance(entry, list) else [entry]
                entries = [candidate for candidate in entries if isinstance(candidate, dict)]
                if not entries:
                    continue
                console_count += 1
                label_key = f"{brand} - {console}".lower()
                for candidate in entries:
                    provider_count += 1
                    enriched = {"brand": brand, "console": console, **candidate}
                    guid = candidate.get("libretro_guid") or candidate.get("guid")
                    if guid:
                        by_guid.setdefault(str(guid).lower(), []).append(enriched)
                    by_label.setdefault(label_key, []).append(enriched)
                    provider_slug = (
                        candidate.get("provider_slug")
                        or candidate.get("id")
                        or candidate.get("archive_id")
                    )
                    status = _coverage_provider_status(str(brand), str(console), str(provider_slug) if provider_slug else None)
                    if any(status.get(key) for key in ("metadata", "listings", "torrent", "rom_json")):
                        providers_with_cache += 1
                    else:
                        providers_missing_cache += 1

    return {
        "brands": brand_count,
        "consoles": console_count,
        "total": provider_count,
        "with_cache": providers_with_cache,
        "missing_cache": providers_missing_cache,
        "by_guid": by_guid,
        "by_label": by_label,
    }


def _dashboard_core_index() -> dict:
    core_path = _resolve_data_file("emulators", "cores.json")
    if not core_path.exists():
        return {
            "cores": 0,
            "bios_files": 0,
            "bios_with_sources": 0,
            "bios_without_sources": 0,
            "by_guid": {},
        }
    try:
        payload = json.loads(core_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}

    bios_registry = payload.get("bios_files") or {}
    by_guid: dict[str, list[dict]] = {}
    for core_id, meta in (payload.get("cores") or {}).items():
        if not isinstance(meta, dict):
            continue
        entry = {"id": core_id, **meta}
        for guid in meta.get("console_guids") or []:
            if guid:
                by_guid.setdefault(str(guid).lower(), []).append(entry)

    bios_with_sources = 0
    bios_without_sources = 0
    for bios in bios_registry.values():
        if not isinstance(bios, dict):
            continue
        if bios.get("url") or bios.get("sources"):
            bios_with_sources += 1
        else:
            bios_without_sources += 1

    return {
        "cores": len(payload.get("cores") or {}),
        "bios_files": len(bios_registry),
        "bios_with_sources": bios_with_sources,
        "bios_without_sources": bios_without_sources,
        "by_guid": by_guid,
    }


def _rom_entries_from_payload(payload: dict) -> list:
    entries = payload.get("entries")
    if isinstance(entries, list):
        return entries
    roms = payload.get("roms")
    return roms if isinstance(roms, list) else []


def _entry_has_provider(entry: dict) -> bool:
    providers = entry.get("_providers")
    if isinstance(providers, list) and providers:
        return True
    return bool(entry.get("http_url") or entry.get("torrent_url"))


def _entry_has_artwork(entry: dict) -> bool:
    artwork = entry.get("artwork")
    return bool(entry.get("thumbnail_url") or (isinstance(artwork, dict) and artwork))


def _thumbnail_index_summary(module_name: str | None) -> dict:
    if not module_name:
        return {"indexed_titles": 0, "image_count": 0, "categories": []}
    index_path = LIBRETRO_THUMBNAILS_DIR / f"{_slugify_module(module_name)}.json"
    if not index_path.exists():
        return {"indexed_titles": 0, "image_count": 0, "categories": []}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {"indexed_titles": 0, "image_count": 0, "categories": []}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {"indexed_titles": 0, "image_count": 0, "categories": []}
    categories: set[str] = set()
    image_count = 0
    for artwork in entries.values():
        if not isinstance(artwork, dict):
            continue
        if "download_url" in artwork:
            image_count += 1
            categories.add(str(artwork.get("category") or "Named_Boxarts"))
            continue
        for category, item in artwork.items():
            if isinstance(item, dict) and item.get("download_url"):
                image_count += 1
                categories.add(str(category))
    return {
        "indexed_titles": len(entries),
        "image_count": image_count,
        "categories": sorted(categories),
    }


def _collect_dashboard_rom_stats(user: dict | None = None) -> dict:
    by_guid: dict[str, dict] = {}
    by_module: dict[str, dict] = {}
    datasets = []
    totals = {
        "datasets": 0,
        "entries": 0,
        "provider_linked_entries": 0,
        "downloadable_entries": 0,
        "inline_artwork_entries": 0,
        "thumbnail_indexed_titles": 0,
        "thumbnail_images": 0,
        "thumbnail_indexes": 0,
        "known_size": 0,
    }

    for rom_file in _iter_rom_files():
        try:
            payload = json.loads(rom_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        module = payload.get("module")
        module_name = module.get("name") if isinstance(module, dict) else module
        guid = payload.get("guid") or (module.get("guid") if isinstance(module, dict) else None)
        if user is not None and not _guid_allowed(guid, user):
            continue

        entries = _rom_entries_from_payload(payload)
        entry_count = payload.get("entry_count")
        if entry_count is None:
            entry_count = len(entries)
        entry_count = _safe_int(entry_count)
        rdb_entry_count = _safe_int(payload.get("rdb_entry_count"), entry_count)
        provider_only_count = _safe_int(payload.get("provider_only_count"))

        provider_linked = 0
        downloadable = 0
        inline_artwork = 0
        known_size = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if _entry_has_provider(entry):
                provider_linked += 1
            if entry.get("http_url") or entry.get("torrent_url"):
                downloadable += 1
            if _entry_has_artwork(entry):
                inline_artwork += 1
            known_size += _safe_int(entry.get("_size_bytes") or entry.get("size"))

        thumbnail_stats = _thumbnail_index_summary(module_name)
        brand, console = _split_module_label(module_name)
        stats = {
            "slug": rom_file.stem,
            "module": module_name,
            "brand": brand or payload.get("brand"),
            "console": console or payload.get("console"),
            "guid": guid,
            "entry_count": entry_count,
            "rdb_entry_count": rdb_entry_count,
            "provider_only_count": provider_only_count,
            "catalog_total": entry_count,
            "provider_linked_entries": provider_linked,
            "downloadable_entries": downloadable,
            "inline_artwork_entries": inline_artwork,
            "coverage_percent": round((provider_linked / entry_count) * 100, 2) if entry_count else 0,
            "known_size": known_size,
            "thumbnail_indexed_titles": thumbnail_stats["indexed_titles"],
            "thumbnail_images": thumbnail_stats["image_count"],
            "thumbnail_categories": thumbnail_stats["categories"],
            "has_thumbnail_index": thumbnail_stats["indexed_titles"] > 0,
        }

        datasets.append(stats)
        totals["datasets"] += 1
        totals["entries"] += entry_count
        totals["provider_linked_entries"] += provider_linked
        totals["downloadable_entries"] += downloadable
        totals["inline_artwork_entries"] += inline_artwork
        totals["thumbnail_indexed_titles"] += thumbnail_stats["indexed_titles"]
        totals["thumbnail_images"] += thumbnail_stats["image_count"]
        totals["known_size"] += known_size
        if thumbnail_stats["indexed_titles"] > 0:
            totals["thumbnail_indexes"] += 1

        if guid:
            by_guid[str(guid).lower()] = stats
        if module_name:
            by_module[str(module_name).lower()] = stats

    totals["coverage_percent"] = (
        round((totals["provider_linked_entries"] / totals["entries"]) * 100, 2)
        if totals["entries"]
        else 0
    )
    totals["thumbnail_index_percent"] = (
        round((totals["thumbnail_indexes"] / totals["datasets"]) * 100, 2)
        if totals["datasets"]
        else 0
    )
    return {
        "totals": totals,
        "datasets": sorted(datasets, key=lambda item: item["entry_count"], reverse=True),
        "by_guid": by_guid,
        "by_module": by_module,
    }


def _dashboard_required_bios(core_entries: list[dict]) -> tuple[set[str], int]:
    required: set[str] = set()
    with_sources = 0
    for core in core_entries:
        for bios_id in core.get("bios_ids") or []:
            if bios_id:
                required.add(str(bios_id))
    if required:
        bios_entries = _bios_entries_for_cores(core_entries)
        with_sources = sum(1 for entry in bios_entries if entry.get("url") or entry.get("sources"))
    return required, with_sources


def _dashboard_completion_score(status: str, rom_stats: dict | None, provider_count: int, core_count: int) -> int:
    total = _safe_int((rom_stats or {}).get("entry_count"))
    matched = _safe_int((rom_stats or {}).get("provider_linked_entries"))
    has_catalog_shell = bool(rom_stats or provider_count)

    if status == "runtime_validated":
        if total and matched >= total:
            return 100
        if matched > 0:
            return 90
        return 75
    if status == "backend_ready":
        return 70 if matched > 0 else 35 if has_catalog_shell else 10
    if status == "needs_backend_work":
        return 35 if has_catalog_shell else 10
    if rom_stats:
        return 35
    if provider_count:
        return 35
    return 10


COMPUTER_MODULE_NAMES = {
    "Amstrad - CPC",
    "Atari - 8-bit",
    "Atari - ST",
    "Commodore - 64",
    "Commodore - Amiga",
    "Commodore - PET",
    "Commodore - Plus-4",
    "Commodore - VIC-20",
    "DOS",
    "Microsoft - MSX",
    "Microsoft - MSX2",
    "NEC - PC-8001 - PC-8801",
    "NEC - PC-98",
    "Sharp - X1",
    "Sharp - X68000",
    "Sinclair - ZX 81",
    "Sinclair - ZX Spectrum",
    "Spectravideo - SVI-318 - SVI-328",
    "Thomson - MOTO",
}

ARCADE_MODULE_NAMES = {
    "Atomiswave",
    "FBNeo - Arcade Games",
    "MAME",
    "Sega - Naomi",
    "Sega - Naomi 2",
}


def _dashboard_module_category(name: str) -> str:
    if name in COMPUTER_MODULE_NAMES:
        return "computer"
    if name in ARCADE_MODULE_NAMES:
        return "arcade"
    if " - " not in name:
        return "engine"
    return "console"


def _dashboard_gaps(status: str, rom_stats: dict | None, provider_count: int, core_entries: list[dict]) -> list[str]:
    gaps = []
    total = _safe_int((rom_stats or {}).get("entry_count"))
    matched = _safe_int((rom_stats or {}).get("provider_linked_entries"))
    if not rom_stats:
        gaps.append("rom_dataset")
    if provider_count == 0:
        gaps.append("providers")
    if rom_stats and total and matched == 0:
        gaps.append("coverage")
    elif rom_stats and total and matched < total:
        gaps.append("partial_coverage")
    if not core_entries:
        gaps.append("core_metadata")
    required_bios, bios_with_sources = _dashboard_required_bios(core_entries)
    if required_bios and bios_with_sources < len(required_bios):
        gaps.append("bios_sources")
    if status != "runtime_validated":
        gaps.append("runtime_test")
    return gaps


def _dashboard_next_action(gaps: list[str], completion: int) -> str:
    if "core_metadata" in gaps:
        return "Confirm RetroArch core availability"
    if "rom_dataset" in gaps:
        return "Export or import master ROM list"
    if "providers" in gaps:
        return "Add provider candidates"
    if "coverage" in gaps:
        return "Rebuild provider coverage"
    if "bios_sources" in gaps:
        return "Document BIOS source metadata"
    if "runtime_test" in gaps:
        return "Assign, install, and smoke test"
    if "partial_coverage" in gaps:
        return "Improve provider coverage"
    if completion >= 100:
        return "Ready for assignment"
    return "Review console metadata"


def _dashboard_console_payload(
    module: dict,
    progress: dict,
    provider_index: dict,
    core_index: dict,
    rom_index: dict,
) -> dict:
    name = module.get("name") or "Unknown module"
    guid = module.get("guid")
    brand, console = _split_module_label(name)
    guid_key = str(guid).lower() if guid else ""
    provider_entries = provider_index["by_guid"].get(guid_key) or provider_index["by_label"].get(name.lower()) or []
    core_entries = core_index["by_guid"].get(guid_key, [])
    rom_stats = rom_index["by_guid"].get(guid_key) or rom_index["by_module"].get(name.lower())
    progress_entry = progress.get(guid_key) or progress.get(guid) or {}
    status = str(progress_entry.get("status") or "unknown")
    category = _dashboard_module_category(name)
    completion = _dashboard_completion_score(status, rom_stats, len(provider_entries), len(core_entries))
    gaps = _dashboard_gaps(status, rom_stats, len(provider_entries), core_entries)
    required_bios, bios_with_sources = _dashboard_required_bios(core_entries)
    strategy_types = sorted(
        {
            str(strategy.get("type"))
            for core in core_entries
            for strategy in [core.get("install_strategy")]
            if isinstance(strategy, dict) and strategy.get("type")
        }
    )

    entry_count = _safe_int((rom_stats or {}).get("entry_count"))
    matched_entries = _safe_int((rom_stats or {}).get("provider_linked_entries"))
    return {
        "guid": guid,
        "module": name,
        "brand": brand,
        "console": console,
        "category": category,
        "status": status,
        "completion": completion,
        "coverage_percent": round((matched_entries / entry_count) * 100, 2) if entry_count else 0,
        "entry_count": entry_count,
        "provider_linked_entries": matched_entries,
        "provider_count": len(provider_entries),
        "core_count": len(core_entries),
        "required_bios_count": len(required_bios),
        "bios_with_sources": bios_with_sources,
        "strategy_types": strategy_types,
        "thumbnail_indexed_titles": _safe_int((rom_stats or {}).get("thumbnail_indexed_titles")),
        "gaps": gaps,
        "next_action": _dashboard_next_action(gaps, completion),
        "validated_at": progress_entry.get("validated_at"),
    }


def _dashboard_status_counts(consoles: list[dict]) -> dict:
    counts = {
        "runtime_validated": 0,
        "backend_ready": 0,
        "needs_backend_work": 0,
        "unknown": 0,
    }
    for console in consoles:
        status = console.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _dashboard_bucket_counts(consoles: list[dict]) -> dict:
    buckets = {"100": 0, "90": 0, "75": 0, "70": 0, "35": 0, "10": 0}
    for console in consoles:
        key = str(console.get("completion") or 10)
        buckets[key] = buckets.get(key, 0) + 1
    return buckets


def _dashboard_category_counts(consoles: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for console in consoles:
        category = str(console.get("category") or "console")
        counts[category] = counts.get(category, 0) + 1
    return counts


def _dashboard_users_summary(user: dict, consoles_by_guid: dict[str, dict]) -> dict:
    if not user.get("admin"):
        allowed = user.get("allowed_console_guids") or []
        return {
            "visible": False,
            "assigned_console_count": len(consoles_by_guid) if "*" in allowed else len(allowed),
        }

    payload = _load_users_payload()
    users = payload.get("users") or []
    enabled = 0
    admins = 0
    clients = 0
    zero_access = 0
    assigned_total = 0
    assigned_ready = 0
    assigned_at_risk = 0
    zero_access_users = []
    risky_users = []

    for entry in users:
        if not isinstance(entry, dict):
            continue
        public = _public_user(entry)
        if public["enabled"]:
            enabled += 1
        if public["admin"]:
            admins += 1
            continue
        clients += 1
        allowed = public.get("allowed_console_guids") or []
        if "*" in allowed:
            assigned = list(consoles_by_guid)
        else:
            assigned = [str(guid).lower() for guid in allowed if guid]
        if public["enabled"] and not assigned:
            zero_access += 1
            zero_access_users.append(public["id"])
        user_risk = 0
        for guid in assigned:
            console = consoles_by_guid.get(guid)
            if not console:
                continue
            assigned_total += 1
            if console.get("status") == "runtime_validated" and _safe_int(console.get("completion")) >= 75:
                assigned_ready += 1
            else:
                assigned_at_risk += 1
                user_risk += 1
        if user_risk:
            risky_users.append({"id": public["id"], "at_risk": user_risk})

    return {
        "visible": True,
        "total": len(users),
        "enabled": enabled,
        "admins": admins,
        "clients": clients,
        "zero_access": zero_access,
        "zero_access_users": zero_access_users[:8],
        "assigned_total": assigned_total,
        "assigned_ready": assigned_ready,
        "assigned_at_risk": assigned_at_risk,
        "risky_users": risky_users[:8],
    }


def _dashboard_alerts(
    consoles: list[dict],
    users: dict,
    provider_index: dict,
    rom_totals: dict,
    core_index: dict,
) -> list[dict]:
    alerts = []
    unstarted = [console for console in consoles if _safe_int(console.get("completion")) <= 10]
    missing_core = [console for console in consoles if "core_metadata" in (console.get("gaps") or [])]
    no_providers = [console for console in consoles if "providers" in (console.get("gaps") or [])]
    large_low_coverage = [
        console
        for console in consoles
        if _safe_int(console.get("entry_count")) >= 1000
        and 0 < float(console.get("coverage_percent") or 0) < 20
    ]

    if unstarted:
        alerts.append(
            {
                "severity": "warning",
                "title": f"{len(unstarted)} modules are unstarted",
                "message": "These modules still need a ROM dataset, providers, coverage, and runtime validation.",
                "action": "Open the next work queue",
            }
        )
    if no_providers:
        alerts.append(
            {
                "severity": "warning",
                "title": f"{len(no_providers)} modules have no provider coverage",
                "message": "Users can see the catalog, but the TUI will not find downloadable sources.",
                "action": "Add provider candidates",
            }
        )
    if missing_core:
        alerts.append(
            {
                "severity": "warning",
                "title": f"{len(missing_core)} modules lack core metadata",
                "message": "Fresh installations will not know which RetroArch core to use.",
                "action": "Update core registry",
            }
        )
    if users.get("visible") and users.get("assigned_at_risk"):
        alerts.append(
            {
                "severity": "critical",
                "title": f"{users['assigned_at_risk']} assigned consoles need attention",
                "message": "At least one enabled client can be assigned to consoles that are not fully ready.",
                "action": "Review user access",
            }
        )
    if users.get("visible") and users.get("zero_access"):
        alerts.append(
            {
                "severity": "info",
                "title": f"{users['zero_access']} enabled users have no consoles",
                "message": "These accounts can sync, but will not receive playable content.",
                "action": "Assign consoles",
            }
        )
    if large_low_coverage:
        alerts.append(
            {
                "severity": "info",
                "title": f"{len(large_low_coverage)} large catalogs have low provider coverage",
                "message": "Large systems like Wii, DS, PlayStation, or NES need targeted providers and disk-aware installs.",
                "action": "Prioritize high-value providers",
            }
        )
    if provider_index.get("total") and provider_index.get("missing_cache") == provider_index.get("total"):
        alerts.append(
            {
                "severity": "info",
                "title": "Provider cache is empty",
                "message": "Provider entries exist, but metadata/listing cache has not been fetched yet.",
                "action": "Fetch provider assets",
            }
        )
    if rom_totals.get("datasets") and not rom_totals.get("thumbnail_indexes"):
        alerts.append(
            {
                "severity": "info",
                "title": "No thumbnail indexes are available",
                "message": "ROM cards will fall back to the generic artwork cover.",
                "action": "Build thumbnail indexes",
            }
        )
    if core_index.get("bios_without_sources"):
        alerts.append(
            {
                "severity": "info",
                "title": f"{core_index['bios_without_sources']} BIOS records need source metadata",
                "message": "The TUI can identify required files, but fresh setup guidance is incomplete.",
                "action": "Document BIOS sources",
            }
        )
    return alerts[:8]


def _dashboard_payload(user: dict) -> dict:
    modules_payload = _filter_modules_payload(_load_modules_payload(), user)
    providers_payload = _filter_providers_payload(_load_providers_payload(), user)
    modules = [module for module in (modules_payload.get("modules") or []) if isinstance(module, dict)]
    progress_payload = _load_console_progress_payload()
    progress = {
        str(guid).lower(): value
        for guid, value in (progress_payload.get("consoles") or {}).items()
        if isinstance(value, dict)
    }
    provider_index = _dashboard_provider_index(providers_payload)
    core_index = _dashboard_core_index()
    rom_index = _collect_dashboard_rom_stats(user)

    modules_status = [
        _dashboard_console_payload(module, progress, provider_index, core_index, rom_index)
        for module in modules
    ]
    modules_status.sort(key=lambda item: (item["completion"], item["module"]))
    consoles = [module for module in modules_status if module.get("category") == "console"]
    consoles_by_guid = {
        str(console.get("guid")).lower(): console
        for console in consoles
        if console.get("guid")
    }
    users = _dashboard_users_summary(user, consoles_by_guid)
    rom_totals = rom_index["totals"]
    cache_meta = _collect_cache_metadata(user)
    completion_average = round(
        sum(_safe_int(console.get("completion")) for console in consoles) / len(consoles),
        1,
    ) if consoles else 0

    ready_console_candidates = [
        console
        for console in sorted(consoles, key=lambda item: (-_safe_int(item.get("completion")), item["module"]))
        if console.get("status") == "runtime_validated"
    ]
    ready_consoles = ready_console_candidates[:8]
    console_work_queue = [
        console
        for console in consoles
        if console.get("category") == "console" and _safe_int(console.get("completion")) < 100
    ][:14]
    other_work_queue = [
        module
        for module in modules_status
        if module.get("category") != "console" and _safe_int(module.get("completion")) < 100
    ][:10]
    special_strategy_consoles = [
        {
            "module": console["module"],
            "guid": console.get("guid"),
            "strategy_types": console.get("strategy_types") or [],
        }
        for console in consoles
        if console.get("strategy_types")
    ][:10]

    provider_public = {
        key: value
        for key, value in provider_index.items()
        if key not in {"by_guid", "by_label"}
    }
    provider_public["without_providers"] = sum(1 for console in consoles if console.get("provider_count") == 0)
    provider_public["with_providers"] = len(consoles) - provider_public["without_providers"]

    runtime = {
        "cores": core_index["cores"],
        "bios_files": core_index["bios_files"],
        "bios_with_sources": core_index["bios_with_sources"],
        "bios_without_sources": core_index["bios_without_sources"],
        "mapped_consoles": len(core_index["by_guid"]),
        "missing_core_metadata": sum(1 for console in consoles if console.get("core_count") == 0),
        "special_strategy_consoles": special_strategy_consoles,
    }
    users_summary = users
    alerts = _dashboard_alerts(consoles, users_summary, provider_public, rom_totals, runtime)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "scope": "admin" if user.get("admin") else "client",
        "datasets": {
            "modules": {
                "version": modules_payload.get("fetched_at") or _file_timestamp(MODULES_FILE),
                "count": len(modules),
            },
            "providers": {
                "version": providers_payload.get("fetched_at") or _file_timestamp(PROVIDERS_FILE),
                "count": provider_public["total"],
            },
            "roms": {
                "version": max(
                    (
                        dataset.get("fetched_at")
                        for dataset in _collect_rom_metadata(user)
                        if dataset.get("fetched_at")
                    ),
                    default=None,
                ),
                "count": rom_totals["datasets"],
            },
            "cache": {
                "version": cache_meta.get("updated"),
                "count": cache_meta.get("file_count", 0),
                "size": cache_meta.get("size", 0),
            },
        },
        "readiness": {
            "total": len(consoles),
            "average_completion": completion_average,
            "buckets": _dashboard_bucket_counts(consoles),
            "categories": _dashboard_category_counts(modules_status),
            "statuses": _dashboard_status_counts(consoles),
            "ready_for_assignment": len(ready_console_candidates),
        },
        "providers": provider_public,
        "roms": {
            **rom_totals,
            "largest_datasets": rom_index["datasets"][:8],
            "missing_thumbnail_indexes": [
                dataset
                for dataset in rom_index["datasets"]
                if not dataset.get("has_thumbnail_index")
            ][:8],
        },
        "users": users_summary,
        "runtime": runtime,
        "alerts": alerts,
        "work_queue": console_work_queue,
        "other_work_queue": other_work_queue,
        "ready_consoles": ready_consoles,
    }


def _load_rom_dataset(identifier: str) -> dict | None:
    for rom_file in _iter_rom_files():
        slug = rom_file.stem
        try:
            payload = json.loads(rom_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        module = payload.get("module")
        guid = payload.get("guid") or (module.get("guid") if isinstance(module, dict) else None)
        if identifier == slug or (guid and identifier.lower() == guid.lower()):
            if "entry_count" not in payload and isinstance(payload.get("entries"), list):
                payload["entry_count"] = len(payload["entries"])
            if "entry_count" not in payload and isinstance(payload.get("roms"), list):
                payload["entry_count"] = len(payload["roms"])
            if isinstance(module, dict):
                payload.setdefault("guid", module.get("guid"))
                payload.setdefault("module_name", module.get("name"))
            payload.setdefault("slug", slug)
            return payload
    return None


def _slugify_module(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower())
    return slug.strip("_") or "default"


def _rom_display_name(entry: dict) -> str:
    raw_name = entry.get("name") or entry.get("title") or entry.get("rom_name") or ""
    name = str(raw_name).strip()
    if "." in name:
        extension = name.rsplit(".", 1)[1].lower()
        if extension in {"zip", "7z", "bin", "cue", "iso", "chd", "gba", "gb", "gbc", "nds"}:
            return name.rsplit(".", 1)[0]
    return name


def _rom_name_tags(name: str) -> list[str]:
    return [tag.strip() for tag in re.findall(r"\(([^()]*)\)", name) if tag.strip()]


def _rom_game_title(entry: dict) -> str:
    name = _rom_display_name(entry)
    title = re.sub(r"\s*\([^()]*\)", "", name).strip()
    return re.sub(r"\s+", " ", title) or name


def _rom_artwork_key(value: str) -> str:
    value = re.sub(r"\s*\((?:19|20)\d{2}(?:[-_]\d{2}){0,2}\)", "", value)
    value = re.sub(r"\s*\[[^\]]+\]", "", value)
    value = re.sub(r"\bbrothers\b", "bros", value, flags=re.I)
    value = re.sub(r"\bbros\.\b", "bros", value, flags=re.I)
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _compact_artwork_key(value: str) -> str:
    return _rom_artwork_key(value).replace(" ", "")


def _rom_variant_tags(entry: dict) -> list[str]:
    name = _rom_display_name(entry)
    region = _rom_entry_region(entry).lower()
    tags = []
    for tag in _rom_name_tags(name):
        if tag.lower() == region:
            continue
        tags.append(tag)
    serial = str(entry.get("serial") or "").strip()
    if serial and serial not in tags:
        tags.append(serial)
    return tags


def _rom_identity_key(entry: dict) -> str:
    return _rom_game_title(entry).lower()


def _rom_unique_key(entry: dict) -> str:
    for field in ("sha1", "md5", "crc32", "crc", "_key"):
        value = str(entry.get(field) or "").strip()
        if value:
            return value.lower()
    return _rom_display_name(entry).lower()


def _rom_entry_format(entry: dict) -> str | None:
    source = str(entry.get("rom_name") or entry.get("name") or "").strip()
    if "." not in source:
        return None
    extension = source.rsplit(".", 1)[1].lower()
    return extension or None


def _rom_entry_availability(entry: dict) -> str:
    if entry.get("http_url"):
        return "downloadable"
    if entry.get("torrent_url"):
        return "torrent"
    return "catalog"


def _rom_entry_region(entry: dict) -> str:
    region = str(entry.get("region") or "").strip()
    if region and region != "—":
        return region
    name = str(entry.get("name") or entry.get("rom_name") or "")
    match = re.search(r"\((USA|Europe|Japan|World|Germany|France|Spain|Italy|Brazil|Korea|Asia|Canada|Australia)\)", name, re.I)
    return match.group(1) if match else "Unknown"


def _rom_entry_search_blob(entry: dict) -> str:
    fields = (
        "name",
        "rom_name",
        "description",
        "region",
        "serial",
        "publisher",
        "developer",
        "genre",
        "crc",
        "crc32",
        "md5",
        "sha1",
    )
    return " ".join(str(entry.get(field) or "") for field in fields).lower()


def _filter_rom_entries(
    entries: list,
    query: str | None,
    availability: str | None,
    region: str | None,
    file_format: str | None,
) -> list:
    normalized_query = query.strip().lower() if query else ""
    normalized_availability = availability.strip().lower() if availability else ""
    normalized_region = region.strip().lower() if region else ""
    normalized_format = file_format.strip().lower().lstrip(".") if file_format else ""
    filtered = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if normalized_query and normalized_query not in _rom_entry_search_blob(entry):
            continue
        if normalized_availability and normalized_availability != "all":
            if _rom_entry_availability(entry) != normalized_availability:
                continue
        if normalized_region and normalized_region != "all":
            if _rom_entry_region(entry).lower() != normalized_region:
                continue
        if normalized_format and normalized_format != "all":
            if (_rom_entry_format(entry) or "").lower() != normalized_format:
                continue
        filtered.append(entry)
    return filtered


def _sort_rom_entries(entries: list, sort: str | None) -> list:
    normalized_sort = (sort or "name").strip().lower()
    if normalized_sort == "size":
        return sorted(entries, key=lambda entry: (entry.get("size") in (None, 0), entry.get("size") or 0))
    if normalized_sort == "availability":
        rank = {"downloadable": 0, "torrent": 1, "catalog": 2}
        return sorted(entries, key=lambda entry: (rank.get(_rom_entry_availability(entry), 3), _rom_display_name(entry).lower()))
    if normalized_sort == "region":
        return sorted(entries, key=lambda entry: (_rom_entry_region(entry).lower(), _rom_display_name(entry).lower()))
    return sorted(entries, key=lambda entry: _rom_display_name(entry).lower())


def _load_thumbnail_index(module_name: str | None) -> dict:
    if not module_name:
        return {}
    index_path = LIBRETRO_THUMBNAILS_DIR / f"{_slugify_module(module_name)}.json"
    if not index_path.exists():
        return {}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    entries = payload.get("entries")
    return entries if isinstance(entries, dict) else {}


def _load_rom_artwork_aliases() -> dict:
    if not ROM_ARTWORK_ALIASES_FILE.exists():
        return {}
    try:
        payload = json.loads(ROM_ARTWORK_ALIASES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _rom_artwork_alias(module_name: str | None, entry: dict) -> str | None:
    if not module_name:
        return None
    payload = _load_rom_artwork_aliases()
    aliases = payload.get("aliases")
    if not isinstance(aliases, list):
        return None
    candidates = {
        _rom_display_name(entry),
        str(entry.get("name") or "").strip(),
        str(entry.get("rom_name") or "").strip(),
    }
    for alias in aliases:
        if not isinstance(alias, dict):
            continue
        if alias.get("module") != module_name:
            continue
        source = str(alias.get("source") or "").strip()
        target = str(alias.get("target") or "").strip()
        if source and target and source in candidates:
            return target
    return None


def _thumbnail_lookup(thumbnail_index: dict) -> dict:
    lookup = {}
    for title, thumbnail in thumbnail_index.items():
        if not isinstance(title, str) or not isinstance(thumbnail, dict):
            continue
        if "download_url" in thumbnail:
            thumbnail = {thumbnail.get("category") or "Named_Boxarts": thumbnail}
        lookup.setdefault(title, thumbnail)
        lookup.setdefault(_rom_artwork_key(title), thumbnail)
        lookup.setdefault(_compact_artwork_key(title), thumbnail)
        base_title = re.sub(r"\s*\([^()]*\)", "", title).strip()
        if base_title:
            lookup.setdefault(_rom_artwork_key(base_title), thumbnail)
            lookup.setdefault(_compact_artwork_key(base_title), thumbnail)
    return lookup


def _rom_artwork_payload(thumbnail: dict | None) -> dict:
    if not isinstance(thumbnail, dict):
        return {}
    if "download_url" in thumbnail:
        thumbnail = {thumbnail.get("category") or "Named_Boxarts": thumbnail}
    artwork = {}
    for category, label in (
        ("Named_Boxarts", "boxart"),
        ("Named_Snaps", "snap"),
        ("Named_Titles", "title"),
    ):
        item = thumbnail.get(category)
        if isinstance(item, dict) and item.get("download_url"):
            artwork[label] = {
                "category": category,
                "url": item.get("download_url"),
                "path": item.get("path"),
                "sha": item.get("sha"),
            }
    return artwork


def _enrich_rom_entries(
    entries: list,
    module_name: str | None,
    variant_counts: dict[str, int] | None = None,
    variant_indexes: dict[str, int] | None = None,
) -> list:
    thumbnail_index = _load_thumbnail_index(module_name)
    thumbnail_lookup = _thumbnail_lookup(thumbnail_index) if thumbnail_index else {}
    enriched = []
    for entry in entries:
        if not isinstance(entry, dict):
            enriched.append(entry)
            continue
        entry = dict(entry)
        display_name = _rom_display_name(entry)
        game_title = _rom_game_title(entry)
        variant_tags = _rom_variant_tags(entry)
        entry["game_title"] = game_title
        entry["variant_tags"] = variant_tags
        entry["variant_label"] = " / ".join(variant_tags)
        entry["variant_count"] = (variant_counts or {}).get(_rom_identity_key(entry), 1)
        entry["variant_index"] = (variant_indexes or {}).get(_rom_unique_key(entry), 1)
        alias_name = _rom_artwork_alias(module_name, entry)
        thumbnail = thumbnail_index.get(alias_name) if alias_name and thumbnail_index else None
        if not thumbnail:
            thumbnail = thumbnail_index.get(display_name) if thumbnail_index else None
        if not thumbnail and entry.get("rom_name"):
            thumbnail = thumbnail_index.get(_rom_display_name({"name": entry.get("rom_name")})) if thumbnail_index else None
        if not thumbnail:
            for key in (
                _rom_artwork_key(display_name),
                _compact_artwork_key(display_name),
                _rom_artwork_key(str(entry.get("rom_name") or "")),
                _compact_artwork_key(str(entry.get("rom_name") or "")),
                _rom_artwork_key(game_title),
                _compact_artwork_key(game_title),
            ):
                if key and key in thumbnail_lookup:
                    thumbnail = thumbnail_lookup[key]
                    break
        artwork = _rom_artwork_payload(thumbnail)
        if artwork:
            entry["artwork"] = artwork
            preferred = artwork.get("boxart") or artwork.get("snap") or artwork.get("title")
            if preferred:
                entry["thumbnail_url"] = preferred.get("url")
                entry["thumbnail_category"] = preferred.get("category")
        enriched.append(entry)
    return enriched


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
    module_name = module.get("name") or payload.get("module")
    guid = module.get("guid") or payload.get("guid")
    brand, console = _split_module_label(module_name)
    dataset = None
    if brand and console:
        try:
            dataset = build_rom_catalog(brand, console, module_guid=guid, rdb_path=rdb_path)
        except Exception:
            dataset = None
    if dataset:
        dataset = {
            **dataset,
            "guid": guid,
            "dataset_role": "master_rom_list",
            "source_kind": "libretro_rdb_with_provider_coverage",
            "source_label": "Libretro database RDB + provider coverage",
            "source_url": payload.get("source_url"),
            "source_urls": payload.get("source_urls"),
            "source_notes": payload.get("source_notes"),
            "fetched_at": payload.get("fetched_at") or datetime.utcnow().isoformat(),
            "slug": target.stem,
        }
    else:
        dataset = {
            "module": module_name,
            "guid": guid,
            "dataset_role": "master_rom_list",
            "source_kind": "libretro_rdb",
            "source_label": "Libretro database RDB",
            "source_url": payload.get("source_url"),
            "source_urls": payload.get("source_urls"),
            "source_notes": payload.get("source_notes"),
            "entry_count": len(entries),
            "fetched_at": payload.get("fetched_at") or datetime.utcnow().isoformat(),
            "entries": entries,
        }
    if dataset.get("source_urls") is None:
        dataset.pop("source_urls", None)
    if dataset.get("source_notes") is None:
        dataset.pop("source_notes", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dataset, indent=2), encoding="utf-8")
    return {
        "slug": target.stem,
        "module": module_name,
        "guid": guid,
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


@app.get("/dashboard")
async def dashboard(user: dict = Depends(current_user)) -> dict:
    """Return the admin home-page health summary, scoped to the current account."""
    return _dashboard_payload(user)


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
async def fetch_rom_dataset(
    identifier: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None),
    availability: str | None = Query(None),
    region: str | None = Query(None),
    format: str | None = Query(None),
    sort: str | None = Query("name"),
    user: dict = Depends(current_user),
) -> dict:
    """Return the ROM dataset payload for a given slug or GUID."""
    dataset = _load_rom_dataset(identifier)
    if not dataset:
        raise HTTPException(status_code=404, detail=f"ROM dataset '{identifier}' not found")
    if not _guid_allowed(dataset.get("guid"), user):
        raise HTTPException(status_code=403, detail="Console is not assigned to this API key")
    module = dataset.get("module")
    module_name = module.get("name") if isinstance(module, dict) else module
    if not module_name:
        module_name = dataset.get("module_name")
    entries = dataset.get("entries")
    if not isinstance(entries, list):
        entries = dataset.get("roms")
    if isinstance(entries, list):
        catalog_total = len(entries)
        filtered_entries = _sort_rom_entries(
            _filter_rom_entries(entries, q, availability, region, format),
            sort,
        )
        variant_counts: dict[str, int] = {}
        variant_indexes: dict[str, int] = {}
        for entry in filtered_entries:
            if isinstance(entry, dict):
                key = _rom_identity_key(entry)
                variant_counts[key] = variant_counts.get(key, 0) + 1
                variant_indexes[_rom_unique_key(entry)] = variant_counts[key]
        total = len(filtered_entries)
        paged_entries = filtered_entries[offset : offset + limit]
        dataset = dict(dataset)
        dataset["entries"] = _enrich_rom_entries(paged_entries, module_name, variant_counts, variant_indexes)
        dataset["catalog_total"] = catalog_total
        dataset["total"] = total
        dataset["limit"] = limit
        dataset["offset"] = offset
        dataset["has_more"] = offset + limit < total
        dataset["filters"] = {
            "q": q,
            "availability": availability,
            "region": region,
            "format": format,
            "sort": sort,
        }
        dataset.pop("roms", None)
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
    payload = dict(_console_info_payload(brand, console, request.guid, request.module))
    payload["can_select_image"] = bool(user.get("admin"))
    return payload


@app.post("/consoles/info/image")
async def select_console_info_image(
    request: ConsoleImageSelectionRequest,
    _: dict = Depends(require_admin),
) -> dict:
    brand = request.brand.strip()
    console = request.console.strip()
    if not brand or not console:
        raise HTTPException(status_code=400, detail="Brand and console are required")
    payload = dict(_console_info_payload(brand, console, request.guid, request.module))
    options = payload.get("image_options")
    if not isinstance(options, list) or not options:
        raise HTTPException(status_code=404, detail="No selectable images are available for this console")
    if request.image_index < 0 or request.image_index >= len(options):
        raise HTTPException(status_code=400, detail="Image index is out of range")
    option = options[request.image_index]
    if not isinstance(option, dict):
        raise HTTPException(status_code=400, detail="Image option is invalid")
    image_url = _wikipedia_image_url(option)
    if not image_url:
        raise HTTPException(status_code=400, detail="Image option has no usable URL")

    payload["selected_image_index"] = request.image_index
    payload["selected_image_title"] = option.get("title")
    payload["selected_image_url"] = image_url
    payload["image_url"] = image_url
    payload["image_index"] = request.image_index

    cache_key = request.guid or f"{manufacturer_slug(brand)}:{console_slug(console)}"
    cache = _load_console_info_cache()
    cache[cache_key] = payload
    _save_console_info_cache(cache)
    payload["can_select_image"] = True
    return payload


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
