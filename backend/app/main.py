import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse


app = FastAPI(title="ROMs Manager Backend", version="0.1.0")


@app.get("/healthz")
async def health_check() -> dict:
    """Simple health endpoint for Render probes."""
    return {"status": "ok"}


@app.get("/")
async def root() -> dict:
    """Hello-world style response."""
    return {"message": "ROMs Manager backend is running"}


APP_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = Path(__file__).resolve().parents[2]


def _resolve_data_file(*relative: str) -> Path:
    root_candidate = ROOT_DIR / "data" / Path(*relative)
    if root_candidate.exists():
        return root_candidate
    backend_candidate = APP_DIR / "data" / Path(*relative)
    if backend_candidate.exists():
        return backend_candidate
    return root_candidate


MODULES_FILE = _resolve_data_file("index", "libretro_modules.json")
PROVIDERS_FILE = _resolve_data_file("providers", "providers.json")


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
        raise HTTPException(status_code=404, detail="providers.json not found on server")
    try:
        providers = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid providers JSON: {exc}") from exc
    return providers


def _file_timestamp(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except Exception:
        return None


def _resolve_target(target: str) -> tuple[str, dict, Path]:
    normalized = target.lower()
    if normalized == "modules":
        return "modules", _load_modules_payload(), MODULES_FILE
    if normalized == "providers":
        return "providers", _load_providers_payload(), PROVIDERS_FILE
    raise HTTPException(status_code=400, detail=f"Unknown target '{target}'")


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
