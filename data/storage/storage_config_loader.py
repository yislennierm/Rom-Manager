import json
import os
from pathlib import Path

from data.storage.frontend_detector import merge_detected_frontends

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("ROMS_MANAGER_DATA_ROOT", PROJECT_ROOT / "data")).expanduser()
CONFIG_PATH = DATA_DIR / "storage" / "storage_config.json"
FALLBACK_CONFIG_PATH = PROJECT_ROOT / "data" / "storage" / "storage_config.json"


def load_storage_config() -> dict:
    config_path = CONFIG_PATH
    if not config_path.exists() and CONFIG_PATH != FALLBACK_CONFIG_PATH:
        config_path = FALLBACK_CONFIG_PATH
    if not config_path.exists():
        return {}
    config = json.loads(config_path.read_text())
    config, _added = merge_detected_frontends(config)
    return config
