import json
import os
from pathlib import Path

from textual.app import ComposeResult
from textual.widgets import Header, Footer, DataTable, Static, Input
from textual.containers import Container
from textual.screen import Screen
from textual import events

from core.providers import load_providers
from core.console_readiness import readiness_for_module
from utils.library_sync import (
    load_modules,
    build_module_index,
    index_exists,
    export_module_rdb,
    rdb_json_path,
)
from utils.paths import manufacturer_slug, console_slug
from .rom_explorer_screen import ROMExplorerScreen
from .bios_manager_screen import BiosManagerScreen

DATA_DIR = Path(os.environ.get("ROMS_MANAGER_DATA_ROOT", Path(__file__).resolve().parents[1] / "data")).expanduser()
STORAGE_CONFIG_PATH = DATA_DIR / "storage" / "storage_config.json"

class DatabaseScreen(Screen):
    """Advanced local libretro/frontend database tooling."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "refresh", "Refresh"),
        ("space", "toggle_activation", "Toggle Activation"),
        ("a", "build_artwork", "Build Artwork"),
        ("b", "bios_manager", "BIOS"),
        ("i", "export_rdb", "Export RDB"),
        ("enter", "open_explorer", "Open Explorer"),
        ("d", "detail", "Details"),
        ("/", "focus_search", "Search"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        self.search_input = Input(placeholder="Search consoles…", id="db_search")
        self.table = DataTable(id="db_table")
        yield Container(
            Static(
                "Advanced local frontend tools. Backend account sync normally manages assigned consoles."
                "\nPress '/' to search, [Enter] to open Explorer, [space] to toggle local frontend activation, [i] for RDB, [d] for details."
                "\nPress [b] for BIOS readiness. Destination shows where ROMs will be stored when the console is active in the selected frontend.",
                id="db_info",
            ),
            self.search_input,
            self.table,
        )
        yield Footer()

    def on_mount(self) -> None:
        self.search_value = ""
        self.table.add_columns("Active", "Ready", "Device", "Providers", "RDB", "Core", "BIOS", "Playlist", "Path")
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.table.focus()
        self.storage_config = self._load_storage_config()
        self.refresh_modules()

    def refresh_modules(self):
        self.modules = load_modules()
        self.provider_map = self._build_provider_lookup()
        self.storage_config = self._load_storage_config()
        self._apply_filter()

    def action_refresh(self):
        self.refresh_modules()

    def action_go_back(self):
        self.app.pop_screen()

    def action_build_artwork(self):
        module = self._current_module()
        if not module:
            self.app.bell()
            return
        self._build_artwork_index(module, force=True)

    def action_export_rdb(self):
        module = self._current_module()
        if not module:
            self.app.bell()
            return
        self._export_rdb(module)

    def action_bios_manager(self):
        self.app.push_screen(BiosManagerScreen())

    def action_open_explorer(self):
        module = self._current_module()
        if not module:
            self.app.bell()
            return
        guid = module.get("guid")
        if not guid:
            self._notify("Module GUID missing; run database fetch again.", severity="warning")
            return
        manufacturer, console = self._split_module_name(module.get("name") or "")
        if not manufacturer or not console:
            self._notify("Module name cannot be mapped to a console.", severity="warning")
            return

        key, frontend = self._active_frontend_entry()
        if frontend and guid not in (frontend.get("supported_guids") or []):
            supported = list(frontend.get("supported_guids") or [])
            supported.append(guid)
            frontend["supported_guids"] = supported
            self.storage_config.setdefault("frontends", {})[key] = frontend
            self._save_storage_config(self.storage_config)
            self.storage_config = self._load_storage_config()
            self._notify(f"Activated {module.get('name', 'module')} for {frontend.get('name', 'frontend')}.", severity="success")

        rdb_path = rdb_json_path(module.get("name") or "")
        if not rdb_path.exists():
            self._notify(f"Exporting RDB for {module.get('name')} before opening Explorer…", severity="information")
            try:
                exported_path = export_module_rdb(module)
            except Exception as exc:
                self._notify(f"RDB export failed: {exc}", severity="warning")
                return
            rdb_path = Path(exported_path)
            self._notify(f"Exported RDB to {exported_path}", severity="success")

        app = getattr(self, "app", None)
        if app is not None:
            app.current_manufacturer = manufacturer
            app.current_console = console
            app.current_roms_path = str(rdb_path)
            app.current_manufacturer_slug = manufacturer_slug(manufacturer)
            app.current_console_slug = console_slug(console)
            app.current_module_guid = guid

        self.app.push_screen(
            ROMExplorerScreen(
                manufacturer=manufacturer,
                console=console,
                roms_path=str(rdb_path),
                module_guid=guid,
            )
        )

    def action_toggle_activation(self):
        module = self._current_module()
        if not module:
            self.app.bell()
            return
        guid = module.get("guid")
        if not guid:
            self._notify("Module GUID missing; run database fetch again.", severity="warning")
            return
        key, frontend = self._active_frontend_entry()
        if not frontend:
            self._notify("No frontend configured. Use Storage settings first.", severity="warning")
            return
        supported = frontend.get("supported_guids") or []
        if guid in supported:
            supported = [value for value in supported if value != guid]
            message = f"Deactivated {module.get('name', 'module')} for {frontend.get('name', 'frontend')}."
        else:
            supported = supported + [guid]
            message = f"Activated {module.get('name', 'module')} for {frontend.get('name', 'frontend')}."
        frontend["supported_guids"] = supported
        self.storage_config.setdefault("frontends", {})[key] = frontend
        self._save_storage_config(self.storage_config)
        self.storage_config = self._load_storage_config()
        self._apply_filter()
        self._notify(message, severity="success")

    def action_detail(self):
        module = self._current_module()
        if not module:
            self.app.bell()
            return
        guid = module.get("guid")
        if not guid:
            self._notify("Module GUID missing; run database fetch again.", severity="warning")
            return
        from .console_detail_modal import ConsoleDetailModal

        modal = ConsoleDetailModal(module, guid, self._provider_entry_by_guid(guid))
        self.app.push_screen(modal)

    def _build_artwork_index(self, module: dict, force: bool = False) -> None:
        if not getattr(self, "modules", None):
            self.app.bell()
            return
        name = module.get("name") or ""
        if not name:
            self._notify("Module has no name; cannot build index.", severity="error")
            return
        if not force and index_exists(name):
            self._notify(f"{name} already has an artwork index. Press [a] again to rebuild.", severity="warning")
            return
        try:
            build_module_index(name)
            self._notify(f"Indexed {name}.", severity="success")
        except Exception as exc:
            self._notify(f"Index failed: {exc}", severity="error")
            return
        self.refresh_modules()

    def _export_rdb(self, module: dict) -> None:
        if not module:
            return
        try:
            path = export_module_rdb(module)
            self._notify(f"Exported RDB to {path}", severity="info")
        except Exception as exc:
            self._notify(f"RDB export failed: {exc}", severity="warning")
        self.refresh_modules()

    def action_focus_search(self):
        if hasattr(self, "search_input"):
            self.search_input.focus()
            self.search_input.cursor_position = len(self.search_input.value or "")

    def _notify(self, message: str, severity: str = "info") -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            self.log(f"[{severity.upper()}] {message}")

    def _current_module(self):
        if not getattr(self, "filtered_modules", None):
            return None
        row_index = getattr(self.table, "cursor_row", 0)
        if row_index < 0 or row_index >= len(self.filtered_modules):
            return None
        return self.filtered_modules[row_index]

    def _load_storage_config(self) -> dict:
        if not STORAGE_CONFIG_PATH.exists():
            return {}
        try:
            return json.loads(STORAGE_CONFIG_PATH.read_text())
        except Exception:
            return {}

    def _save_storage_config(self, payload: dict) -> None:
        STORAGE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        STORAGE_CONFIG_PATH.write_text(json.dumps(payload, indent=2))

    def _active_frontend_entry(self):
        config = getattr(self, "storage_config", {}) or {}
        frontends = config.get("frontends", {})
        for key, entry in frontends.items():
            if entry.get("active"):
                return key, entry
        if frontends:
            return next(iter(frontends.items()))
        return None, None

    def _is_guid_active(self, guid: str | None) -> bool:
        if not guid:
            return False
        _, frontend = self._active_frontend_entry()
        if not frontend:
            return False
        return guid in (frontend.get("supported_guids") or [])

    def _apply_filter(self):
        self.filtered_modules = []
        self.readiness_by_guid = {}
        self.table.clear()
        if not getattr(self, "modules", None):
            self.table.add_row("—", "No modules synced", "—", "—", "—", "—", "—", "—", "—")
            return
        query = getattr(self, "search_value", "").lower().strip()
        for module in self.modules:
            name = module.get("name") or ""
            if query and query not in name.lower():
                continue
            checkbox = "☑" if self._is_guid_active(module.get("guid")) else "☐"
            readiness = readiness_for_module(module)
            if module.get("guid"):
                self.readiness_by_guid[module.get("guid")] = readiness
            checks = readiness.get("checks") or {}
            provider_cell = str((readiness.get("providers") or {}).get("count") or 0)
            destination = self._rdb_destination_for(name)
            self.table.add_row(
                checkbox,
                _readiness_marker(str(readiness.get("score") or "")),
                name or "—",
                provider_cell,
                _check_marker(checks.get("rdb")),
                _check_marker(checks.get("core")),
                _check_marker(checks.get("bios")),
                _check_marker(checks.get("playlist")),
                destination,
            )
            self.filtered_modules.append(module)
        if not self.filtered_modules:
            self.table.add_row("—", "No matches", "—", "—", "—", "—", "—", "—", "—")

    def _rdb_destination_for(self, module_name: str | None) -> str:
        if not module_name:
            return str(rdb_json_path("module"))
        try:
            return str(rdb_json_path(module_name))
        except Exception:
            slug = "".join(ch if ch.isalnum() else "_" for ch in module_name.lower()).strip("_") or "default"
            return os.path.join("data", "index", "rdb", f"{slug}.json")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "db_search":
            return
        self.search_value = event.value or ""
        self._apply_filter()

    def on_key(self, event: events.Key) -> None:
        if event.key == "/":
            self.action_focus_search()
        elif event.key in ("escape", "backspace"):
            self.action_go_back()

    def _build_provider_lookup(self):
        lookup: dict[str, list[dict]] = {}
        try:
            providers = load_providers().get("console_root", {})
        except Exception:
            providers = {}
        for manufacturer, consoles in providers.items():
            if not isinstance(consoles, dict):
                continue
            for console_name, entry in consoles.items():
                entries = entry if isinstance(entry, list) else [entry]
                for variant in entries:
                    if not isinstance(variant, dict):
                        continue
                    guid = variant.get("libretro_guid") or variant.get("guid")
                    if guid:
                        lookup.setdefault(guid, []).append(variant)
        return lookup

    def _provider_count_cell(self, guid: str | None) -> str:
        if not guid:
            return "0"
        entries = self.provider_map.get(guid) or []
        return str(len(entries))

    def _provider_entry_by_guid(self, guid: str | None):
        if not guid:
            return None
        return getattr(self, "provider_map", {}).get(guid)

    @staticmethod
    def _split_module_name(name: str):
        if not name:
            return None, None
        parts = [segment.strip() for segment in name.split("-", 1)]
        if len(parts) == 2:
            return parts[0], parts[1]
        return None, name.strip()

    @staticmethod
    def _normalize_label(value: str) -> str:
        if not value:
            return ""
        return "".join(ch for ch in value.lower() if ch.isalnum())


def _readiness_marker(score: str) -> str:
    return {
        "ready": "✅ Ready",
        "catalog_ready": "◐ Catalog",
        "needs_work": "⚠ Work",
        "broken": "✖ Broken",
    }.get(score, "—")


def _check_marker(check: dict | None) -> str:
    if not check:
        return "—"
    state = check.get("state")
    label = str(check.get("label") or state or "—")
    if state == "ok":
        return "✅"
    if state == "partial":
        return f"◐ {label}"
    if state == "unknown":
        return "?"
    if state in {"invalid", "error", "stale"}:
        return f"✖ {label}"
    return f"⚠ {label}"
