import hashlib
from pathlib import Path

from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, DataTable
from textual.containers import Vertical
from textual.screen import ModalScreen

import json
from utils.paths import manufacturer_slug, console_slug


def compute_md5(path: Path) -> str:
    hash_md5 = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


CONFIG_PATH = Path("data/storage/storage_config.json")
CORE_PATH = Path("data/emulators/cores.json")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


class ConsoleDetailModal(ModalScreen):
    """Detailed view for a console: providers, core requirements, BIOS status."""

    CSS_PATH = "styles/console_detail.css"

    def __init__(self, manufacturer: str, console: str, module: dict, provider_entry: dict | None):
        super().__init__()
        self.manufacturer = manufacturer
        self.console = console
        self.module = module
        self.provider_entry = provider_entry or {}
        self.storage_config = _load_json(CONFIG_PATH)
        self.cores_config = _load_json(CORE_PATH)

    def compose(self) -> ComposeResult:
        title = f"{self.manufacturer} / {self.console}"
        yield Header(show_clock=False)
        yield Static(f"[b]{title}[/b]\nModules fetched: {self.module.get('name', '—')}", id="console_detail_title")
        self.provider_table = DataTable(id="console_provider_table")
        self.provider_table.add_columns("Name", "Base URL", "Active")
        self.bios_table = DataTable(id="console_bios_table")
        self.bios_table.add_columns("Core", "BIOS File", "MD5", "Status")
        yield Vertical(
            Static("[b]Providers[/b]", id="console_detail_section_providers"),
            self.provider_table,
            Static("[b]Cores / BIOS Requirements[/b]", id="console_detail_section_cores"),
            self.bios_table,
            id="console_detail_body",
        )
        yield Footer()

    def on_mount(self):
        self._load_providers()
        self._load_bios_status()

    def _load_providers(self):
        self.provider_table.clear()
        providers = self.provider_entry
        if not providers:
            self.provider_table.add_row("⚠ No provider registered", "—", "—")
            return
        if isinstance(providers, dict):
            providers = [providers]
        for entry in providers:
            self.provider_table.add_row(
                entry.get("name", "Unnamed"),
                entry.get("base_url", "—"),
                "Yes" if entry else "—",
            )

    def _load_bios_status(self):
        self.bios_table.clear()
        frontend = self.storage_config.get("frontends", {}).get("retroarch_standalone") or {}
        bios_path = Path(frontend.get("bios_path", Path.home())).expanduser()
        platform_slug = console_slug(self.console)

        cores_for_console = self.cores_config.get("retroarch", {}).get(platform_slug, {})
        if not cores_for_console:
            self.bios_table.add_row("—", "No cores defined", "—", "—")
            return
        for core_key, core_info in cores_for_console.items():
            bios_entries = core_info.get("bios", [])
            if not bios_entries:
                self.bios_table.add_row(core_info.get("name", core_key), "No BIOS listed", "—", "—")
                continue
            for entry in bios_entries:
                filename = entry.get("filename")
                md5_expected = entry.get("md5", "—")
                status = "⚠ Missing"
                md5_actual = "—"
                bios_file = bios_path / filename if filename else None
                if bios_file and bios_file.exists():
                    try:
                        md5_actual = compute_md5(bios_file)
                        status = "✅ OK" if md5_actual.lower() == md5_expected.lower() else "⚠ Hash mismatch"
                    except Exception as exc:
                        status = f"⚠ Error: {exc}"
                self.bios_table.add_row(
                    core_info.get("name", core_key),
                    filename or "—",
                    md5_expected,
                    status,
                )
