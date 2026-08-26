import hashlib
import os
import zipfile
from pathlib import Path

from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, DataTable
from textual.containers import Vertical
from textual.screen import ModalScreen

import json
from core.bios_manager import install_bios_from_file, install_bios_from_source, list_bios_requirements
from core.console_readiness import readiness_for_module
from .path_browser_screen import PathBrowserScreen
from utils.library_sync import rdb_json_path


def compute_md5(path: Path) -> str:
    hash_md5 = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


DATA_DIR = Path(os.environ.get("ROMS_MANAGER_DATA_ROOT", Path(__file__).resolve().parents[1] / "data")).expanduser()
CONFIG_PATH = DATA_DIR / "storage" / "storage_config.json"
CORE_PATH = DATA_DIR / "emulators" / "cores.json"


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
        ("d", "download_bios", "Download BIOS"),
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
        self._bios_rows = []
        name = module.get("name", "")
        parts = [segment.strip() for segment in name.split("-", 1)]
        self.manufacturer = parts[0] if parts else "Unknown"
        self.console = parts[1] if len(parts) == 2 else name
        self.rdb_info = self._load_rdb_info()
        self.readiness = readiness_for_module(module, include_coverage=True)

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
        yield Static(self._readiness_summary(), id="console_detail_readiness")
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
        yield Static(
            "Select a BIOS row and press [i] to install from a local file or [d] from a configured source.",
            id="console_detail_actions",
        )
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
        self._missing_bios = []
        self._bios_rows = []
        requirements = list_bios_requirements(self.guid)
        if not requirements:
            self.bios_table.add_row("—", "No cores reference this console.", "—", "—")
            return

        for requirement in requirements:
            core_name = requirement.get("core_name") or requirement.get("core_id") or "—"
            bios_entry = requirement.get("bios")
            status = requirement.get("status") or {}
            if not bios_entry:
                self.bios_table.add_row(str(core_name), "—", "—", str(status.get("label", "No metadata")))
                self._bios_rows.append(None)
                continue
            filename = bios_entry.get("filename")
            md5_display = bios_entry.get("md5") or "—"
            state = status.get("state")
            label = status.get("label") or "Unknown"
            display = "✅ OK" if state == "ok" else f"⚠ {label}"
            row_payload = {**requirement, "bios": bios_entry}
            self._bios_rows.append(row_payload)
            if state != "ok":
                self._missing_bios.append(row_payload)
            self.bios_table.add_row(str(core_name), filename or "—", md5_display, display)

    @staticmethod
    def _check_zip_contents(path: Path, bios_entry: dict) -> str | None:
        contents = bios_entry.get("zip_contents") or []
        if not contents:
            return None
        if not zipfile.is_zipfile(path):
            return "⚠ Not a zip"
        with zipfile.ZipFile(path) as archive:
            names = {name.lower(): name for name in archive.namelist()}
            for expected in contents:
                filename = (expected.get("filename") or "").lower()
                md5_expected = (expected.get("md5") or "").lower()
                archive_name = names.get(filename)
                if not archive_name:
                    return f"⚠ Missing {expected.get('filename')}"
                if md5_expected:
                    md5_actual = hashlib.md5(archive.read(archive_name)).hexdigest()
                    if md5_actual.lower() != md5_expected:
                        return f"⚠ Hash mismatch: {expected.get('filename')}"
        return "✅ OK"

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

    def _readiness_summary(self) -> str:
        checks = self.readiness.get("checks") or {}
        parts = [
            f"[b]Readiness:[/b] {self.readiness.get('summary', 'Unknown')}",
            f"Strategy: {self.readiness.get('strategy_label', 'standard_libretro')}",
        ]
        for key in ("coverage", "core", "bios", "install", "playlist"):
            check = checks.get(key) or {}
            parts.append(f"{key.title()}: {check.get('label', '—')}")
        return " | ".join(parts)

    def action_install_bios(self):
        self._install_bios()

    def action_download_bios(self):
        row_payload = self._selected_bios_payload()
        if not row_payload:
            return
        bios_entry = row_payload.get("bios")
        if not (bios_entry.get("sources") or []):
            self._notify("No configured source for this BIOS.", severity="warning")
            return
        try:
            result = install_bios_from_source(bios_entry)
            source = result.get("source") or {}
            self._notify(f"Installed BIOS from {source.get('name', 'configured source')}", severity="success")
            self._load_bios_status()
        except Exception as exc:
            self._notify(f"Failed to download BIOS: {exc}", severity="error")

    def _install_bios(self):
        row_payload = self._selected_bios_payload()
        if not row_payload:
            return
        bios_entry = row_payload.get("bios")
        self._pending_bios_entry = bios_entry
        start = Path.home() / "Downloads"
        self.app.push_screen(PathBrowserScreen(self._install_selected_bios_file, start=start, select_files=True))

    def _selected_bios_payload(self):
        row = getattr(self.bios_table, "cursor_row", None)
        if row is None or row < 0 or row >= len(self.bios_table.rows):
            self.app.bell()
            return None
        core_name, filename, md5_expected, status = self.bios_table.get_row_at(row)
        row_payload = self._bios_rows[row] if row < len(self._bios_rows) else None
        if not row_payload or not status.startswith("⚠") or not filename.strip():
            self._notify("This BIOS is already satisfied.", severity="info")
            return None
        bios_entry = row_payload.get("bios")
        if not bios_entry:
            self._notify("No metadata available for this BIOS.", severity="warning")
            return None
        return row_payload

    def _install_selected_bios_file(self, selected_path: str):
        bios_entry = getattr(self, "_pending_bios_entry", None)
        if not bios_entry:
            self._notify("No BIOS row selected.", severity="warning")
            return
        try:
            result = install_bios_from_file(Path(selected_path), bios_entry)
            self._notify(f"Installed BIOS to {result.get('target')}", severity="success")
            self._load_bios_status()
        except Exception as exc:
            self._notify(f"Failed to install BIOS: {exc}", severity="error")

    def _notify(self, message: str, severity: str = "info"):
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            self.log(f"[{severity.upper()}] {message}")
