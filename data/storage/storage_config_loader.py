import json
import os
from pathlib import Path

from data.storage.frontend_detector import merge_detected_frontends

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("ROMS_MANAGER_DATA_ROOT", PROJECT_ROOT / "data")).expanduser()
CONFIG_PATH = DATA_DIR / "storage" / "storage_config.json"


def load_storage_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    config = json.loads(CONFIG_PATH.read_text())
    config, _added = merge_detected_frontends(config)
    return config
