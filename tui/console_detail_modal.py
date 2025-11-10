import hashlib
from pathlib import Path

from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, DataTable
from textual.containers import Vertical
from textual.screen import ModalScreen

import json
from utils.library_sync import rdb_json_path
from utils.paths import console_slug


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

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("i", "install_bios", "Install BIOS"),
    ]

    def __init__(self, module: dict, guid: str, provider_entry: dict | None):
        super().__init__()
        self.module = module
        self.guid = guid
        self.provider_entry = provider_entry or {}
        self.storage_config = _load_json(CONFIG_PATH)
        self.cores_config = _load_json(CORE_PATH)
        self._active_frontend = self._resolve_active_frontend()
        self._roms_path = Path(self._active_frontend.get("roms_path", Path.home())).expanduser()
        self._bios_path = Path(self._active_frontend.get("bios_path", Path.home())).expanduser()
        self._missing_bios = []
        name = module.get("name", "")
        parts = [segment.strip() for segment in name.split("-", 1)]
        self.manufacturer = parts[0] if parts else "Unknown"
        self.console = parts[1] if len(parts) == 2 else name
        self.rdb_info = self._load_rdb_info()

    def compose(self) -> ComposeResult:
        title = f"{self.manufacturer} / {self.console}"
        yield Header(show_clock=False)
        yield Static(f"[b]{title}[/b]\nModules fetched: {self.module.get('name', '—')}", id="console_detail_title")
        frontend_label = self._active_frontend.get("name", "Unknown frontend")
        yield Static(
            f"[b]Frontend:[/b] {frontend_label}\n"
            f"[b]ROMs path:[/b] {self._roms_path}\n"
            f"[b]BIOS path:[/b] {self._bios_path}\n"
            f"[b]RDB export:[/b] {self._describe_rdb()}",
            id="console_detail_paths",
        )
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
        yield Static("Select a BIOS row and press [i] to install missing files.", id="console_detail_actions")
        yield Footer()

    def on_mount(self):
        self._load_providers()
        self._load_bios_status()
        self.bios_table.cursor_type = "row"
        self.bios_table.focus()

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
        bios_path = self._bios_path
        missing = []
        cores_for_console = self.cores_config.get("retroarch", {})
        # Prefer entries matching our GUID
        cores = cores_for_console.values()
        if self.guid:
            cores = [
                core
                for console_map in cores_for_console.values()
                for core in console_map.values()
                if core.get("libretro_guid") == self.guid
            ]
        else:
            cores = [
                core
                for console_map in cores_for_console.values()
                for core in console_map.values()
            ]
        if not cores_for_console:
            self.bios_table.add_row("—", "No cores defined", "—", "—")
            return
        for core_key, core_info in cores_for_console.get(console_slug(self.console), {}).items():
            bios_entries = core_info.get("bios", [])
            if not bios_entries:
                self.bios_table.add_row(core_info.get("name", core_key), "No BIOS listed", "—", "—")
                continue
            for entry in bios_entries:
                filename = entry.get("filename")
                md5_expected = entry.get("md5", "—")
                status = "⚠ Missing"
                bios_file = bios_path / filename if filename else None
                if bios_file and bios_file.exists():
                    try:
                        md5_actual = compute_md5(bios_file)
                        status = "✅ OK" if md5_actual.lower() == md5_expected.lower() else "⚠ Hash mismatch"
                    except Exception as exc:
                        status = f"⚠ Error: {exc}"
                else:
                    missing.append(entry)
                self.bios_table.add_row(
                    core_info.get("name", core_key),
                    filename or "—",
                    md5_expected,
                    status,
                )
        self._missing_bios = missing

    def _resolve_active_frontend(self) -> dict:
        frontends = self.storage_config.get("frontends", {})
        for entry in frontends.values():
            if entry.get("active"):
                return entry
        return next(iter(frontends.values()), {})

    def _load_rdb_info(self) -> dict:
        name = self.module.get("name") if isinstance(self.module, dict) else None
        if not name:
            return {"path": None, "exists": False}
        try:
            path = rdb_json_path(name)
        except Exception:
            return {"path": None, "exists": False}
        info = {
            "path": str(path),
            "exists": path.exists(),
        }
        if path.exists():
            try:
                payload = json.loads(path.read_text())
                info["entry_count"] = payload.get("entry_count") or len(payload.get("entries", []))
                info["fetched_at"] = payload.get("fetched_at")
            except Exception:
                info["entry_count"] = None
                info["fetched_at"] = None
        return info

    def _describe_rdb(self) -> str:
        path = self.rdb_info.get("path")
        if not path:
            return "Not available"
        if not self.rdb_info.get("exists"):
            return f"{path} (not exported)"
        entry_count = self.rdb_info.get("entry_count")
        fetched_at = self.rdb_info.get("fetched_at")
        details = []
        if entry_count:
            details.append(f"{entry_count} entries")
        if fetched_at:
            details.append(fetched_at)
        extra = f" ({', '.join(details)})" if details else ""
        return f"{path}{extra}"

    def action_install_bios(self):
        self._install_bios()

    def _install_bios(self):
        row = getattr(self.bios_table, "cursor_row", None)
        if row is None or row < 0 or row >= len(self.bios_table.rows):
            self.app.bell()
            return
        core_name, filename, md5_expected, status = self.bios_table.get_row_at(row)
        if not status.startswith("⚠") or not filename.strip():
            self._notify("This BIOS is already satisfied.", severity="info")
            return
        bios_entry = next((entry for entry in self._missing_bios if entry.get("filename") == filename), None)
        if not bios_entry:
            self._notify("No metadata available for this BIOS.", severity="warning")
            return
        source_url = bios_entry.get("url")
        if not source_url:
            self._notify("No download URL configured for this BIOS.", severity="warning")
            return
        target_path = self._bios_path / filename
        try:
            import urllib.request

            target_path.parent.mkdir(parents=True, exist_ok=True)
            self._notify(f"Downloading {filename}…", severity="info")
            urllib.request.urlretrieve(source_url, target_path)
            md5 = compute_md5(target_path)
            if md5.lower() == md5_expected.lower():
                self._notify(f"Installed {filename} successfully.", severity="success")
            else:
                self._notify(f"Hash mismatch after install (expected {md5_expected}, got {md5}).", severity="warning")
            self._load_bios_status()
        except Exception as exc:
            self._notify(f"Failed to install BIOS: {exc}", severity="error")
        self._load_bios_status()

    def _notify(self, message: str, severity: str = "info"):
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            print(f"[{severity.upper()}] {message}")
