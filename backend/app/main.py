import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

APP_DIR = Path(__file__).resolve().parents[1]
os.environ.setdefault("ROMS_MANAGER_DATA_ROOT", str(APP_DIR / "data"))

from core.services import provider_tasks
from utils.paths import CACHE_DIR

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
    root_candidate = ROOT_DIR / "data" / Path(*relative)
    if root_candidate.exists():
        return root_candidate
    backend_candidate = APP_DIR / "data" / Path(*relative)
    if backend_candidate.exists():
        return backend_candidate
    return root_candidate


def _resolve_data_dir(*relative: str) -> Path:
    root_candidate = ROOT_DIR / "data" / Path(*relative)
    if root_candidate.exists():
        return root_candidate
    backend_candidate = APP_DIR / "data" / Path(*relative)
    return backend_candidate


MODULES_FILE = _resolve_data_file("index", "libretro_modules.json")
PROVIDERS_FILE = APP_DIR / "data" / "providers" / "providers.json"
PROVIDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
ROMS_DIR = _resolve_data_dir("roms")


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


def _iter_rom_files() -> list[Path]:
    if not ROMS_DIR.exists():
        return []
    return sorted(ROMS_DIR.glob("*.json"))


def _collect_rom_metadata() -> list[dict]:
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
        rom_sets.append(
            {
                "slug": rom_file.stem,
                "module": module_name,
                "brand": brand or payload.get("brand"),
                "console": console or payload.get("console"),
                "guid": payload.get("guid"),
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


@app.get("/update")
async def fetch_seed_payload(target: str = Query("modules")) -> JSONResponse:
    """
    Deliver the base dataset required by a fresh ROMs Manager install.

    Supported targets: modules, providers.
    """
    normalized, payload, path = _resolve_target(target)
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
async def fetch_seed_metadata(target: str = Query("modules")) -> dict:
    """Return metadata for a given dataset without downloading the entire payload."""
    normalized, payload, path = _resolve_target(target)

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
async def list_rom_datasets() -> dict:
    """Return metadata for every exported ROM dataset available on the server."""
    metadata = _collect_rom_metadata()
    return {"roms": metadata, "count": len(metadata)}


@app.get("/roms/{identifier}")
async def fetch_rom_dataset(identifier: str) -> dict:
    """Return the ROM dataset payload for a given slug or GUID."""
    dataset = _load_rom_dataset(identifier)
    if not dataset:
        raise HTTPException(status_code=404, detail=f"ROM dataset '{identifier}' not found")
    return dataset


@app.post("/providers")
async def upsert_provider(request: ProviderUpsertRequest) -> dict:
    payload = _load_providers_payload()
    payload = _upsert_provider_entry(payload, request)
    _save_providers_payload(payload)
    return {
        "target": "providers",
        "providers": payload,
    }


def _collect_cache_metadata() -> dict:
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
async def delete_provider(request: ProviderDeleteRequest) -> dict:
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
async def fetch_provider_assets(request: ProviderFetchRequest) -> dict:
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
        "summary": summary,
    }


@app.post("/providers/tasks/export")
async def export_provider_roms(request: ProviderExportRequest) -> dict:
    brand, console, provider_slug = _normalize_task_fields(request)
    try:
        roms, json_path = provider_tasks.export_console_roms(
            console=console,
            manufacturer=brand,
            provider_slug=provider_slug,
            write=request.write,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "brand": brand,
        "console": console,
        "provider": provider_slug,
        "count": len(roms),
        "path": json_path,
    }


@app.post("/providers/tasks/validate")
async def validate_providers_dataset() -> dict:
    ok, issues = provider_tasks.validate_providers()
    return {
        "valid": ok,
        "issues": issues,
    }


@app.get("/cache/meta")
async def cache_metadata() -> dict:
    meta = _collect_cache_metadata()
    return meta


@app.get("/cache/archive")
async def download_cache_archive() -> FileResponse:
    cache_dir = _cache_dir()
    if not cache_dir.is_dir():
        raise HTTPException(status_code=404, detail="Cache directory is empty.")
    temp_dir = Path(tempfile.mkdtemp(prefix="cache_export_"))
    zip_name = f"cache-export-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
    zip_path = temp_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root, _, files in os.walk(cache_dir):
            for filename in files:
                abs_path = Path(root) / filename
                arcname = abs_path.relative_to(cache_dir)
                archive.write(abs_path, arcname.as_posix())
    response = FileResponse(
        zip_path,
        filename=zip_name,
        media_type="application/zip",
    )
    response.background = BackgroundTask(_cleanup_temp_file, zip_path)
    return response
