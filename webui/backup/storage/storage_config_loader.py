import json
from pathlib import Path

CONFIG_PATH = Path("data/storage/storage_config.json")


def load_storage_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())
