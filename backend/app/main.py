import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
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


MODULES_FILE = Path(__file__).resolve().parents[2] / "data" / "index" / "libretro_modules.json"


def _load_modules_payload() -> dict:
    if not MODULES_FILE.exists():
        raise HTTPException(status_code=404, detail="libretro_modules.json not found on server")
    try:
        modules = json.loads(MODULES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid modules JSON: {exc}") from exc
    return modules


@app.get("/update")
async def fetch_seed_payload() -> JSONResponse:
    """
    Deliver the base dataset required by a fresh ROMs Manager install.

    For now this is only the libretro modules snapshot; future revisions can
    extend the payload to include providers, BIOS metadata, etc.
    """
    modules = _load_modules_payload()
    return JSONResponse(
        content={
            "version": modules.get("fetched_at"),
            "modules": modules.get("modules", []),
        }
    )


@app.get("/update/meta")
async def fetch_seed_metadata() -> dict:
    """Return only metadata about the seed snapshot."""
    modules = _load_modules_payload()
    entries = modules.get("modules")
    count = len(entries) if isinstance(entries, list) else None
    return {
        "version": modules.get("fetched_at"),
        "modules": count,
    }
