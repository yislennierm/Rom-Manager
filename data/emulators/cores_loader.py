import json
from pathlib import Path

CORE_PATH = Path("data/emulators/cores.json")


def load_cores_config() -> dict:
    if not CORE_PATH.exists():
        return {}
    return json.loads(CORE_PATH.read_text())
