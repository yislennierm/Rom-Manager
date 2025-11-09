import os

from textual.app import ComposeResult
from textual.widgets import Header, Footer, DataTable, Static, Input
from textual.containers import Container
from textual.screen import Screen
from textual import events

from core.providers import load_providers
from utils.library_sync import load_modules, build_module_index, index_exists
from utils.paths import manufacturer_slug, console_slug


class DatabaseScreen(Screen):
    """Manage synced consoles from libretro."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "refresh", "Refresh"),
        ("space", "toggle_activation", "Toggle Activation"),
        ("i", "index", "Rebuild Index"),
        ("enter", "detail", "Details"),
        ("d", "detail", "Details"),
        ("/", "focus_search", "Search"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        self.search_input = Input(placeholder="Search consoles…", id="db_search")
        self.table = DataTable(id="db_table")
        yield Container(
            Static(
                "Module list is loaded from data/index/libretro_modules.json."
                "\nUse `python3 roms_manager.py database fetch` to update it."
                "\nPress '/' to search, [space] to activate, [i] to rebuild."
                "\nDestination shows where ROMs will be stored once active.",
                id="db_info",
            ),
            self.search_input,
            self.table,
        )
        yield Footer()

    def on_mount(self) -> None:
        self.search_value = ""
        self.table.add_columns("Active", "Module", "Provider", "Destination")
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.table.focus()
        self.refresh_modules()

    def refresh_modules(self):
        self.modules = load_modules()
        self.provider_map = self._build_provider_lookup()
        self._apply_filter()

    def action_refresh(self):
        self.refresh_modules()

    def action_go_back(self):
        self.app.pop_screen()

    def action_index(self):
        module = self._current_module()
        if not module:
            self.app.bell()
            return
        name = module.get("name")
        self._activate_module(name, force=True)

    def action_toggle_activation(self):
        module = self._current_module()
        if not module:
            self.app.bell()
            return
        name = module.get("name")
        if index_exists(name):
            self._notify(f"{name} is already active. Press [i] to rebuild.", severity="warning")
            self.app.bell()
            return
        self._activate_module(name)

    def action_detail(self):
        module = self._current_module()
        if not module:
            self.app.bell()
            return
        manufacturer, console = self._split_module_name(module.get("name") or "")
        if not manufacturer or not console:
            self._notify("Module name does not map to a console.", severity="warning")
            return
        from .console_detail_modal import ConsoleDetailModal

        modal = ConsoleDetailModal(manufacturer.strip(), console.strip(), module, self._provider_entry(module.get("name")))
        self.app.push_screen(modal)

    def _activate_module(self, name: str, force: bool = False) -> None:
        if not getattr(self, "modules", None):
            self.app.bell()
            return
        if not force and index_exists(name):
            return
        try:
            build_module_index(name)
            self._notify(f"Indexed {name}.", severity="success")
        except Exception as exc:
            self._notify(f"Index failed: {exc}", severity="error")
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
            print(f"[{severity.upper()}] {message}")

    def _current_module(self):
        if not getattr(self, "filtered_modules", None):
            return None
        row_index = getattr(self.table, "cursor_row", 0)
        if row_index < 0 or row_index >= len(self.filtered_modules):
            return None
        return self.filtered_modules[row_index]

    def _apply_filter(self):
        self.filtered_modules = []
        self.table.clear()
        if not getattr(self, "modules", None):
            self.table.add_row("—", "No modules synced", "—", "—")
            return
        query = getattr(self, "search_value", "").lower().strip()
        for module in self.modules:
            name = module.get("name") or ""
            if query and query not in name.lower():
                continue
            checkbox = "☑" if index_exists(name) else "☐"
            provider_cell = self._provider_cell(name)
            destination = self._destination_for(name)
            self.table.add_row(checkbox, name or "—", provider_cell, destination)
            self.filtered_modules.append(module)
        if not self.filtered_modules:
            self.table.add_row("—", "No matches", "—", "—")

    def _destination_for(self, module_name: str) -> str:
        manufacturer, console = self._split_module_name(module_name)
        if manufacturer and console:
            return os.path.join(
                "downloads",
                manufacturer_slug(manufacturer),
                console_slug(console),
            )
        slug = "".join(ch if ch.isalnum() else "_" for ch in (module_name or "").lower()).strip("_") or "default"
        return f"downloads/libretro/{slug}"

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
        lookup = {}
        try:
            providers = load_providers().get("console_root", {})
        except Exception:
            providers = {}
        for manufacturer, consoles in providers.items():
            if not isinstance(consoles, dict):
                continue
            for console_name, entry in consoles.items():
                key = (self._normalize_label(manufacturer), self._normalize_label(console_name))
                lookup[key] = entry
        return lookup

    def _provider_cell(self, module_name: str) -> str:
        entry = self._provider_entry(module_name)
        if not entry:
            return "⚠ Missing"
        provider_name = entry.get("provider") or entry.get("name") or "Available"
        return f"✅ {provider_name}"

    def _provider_entry(self, module_name: str):
        manufacturer, console = self._split_module_name(module_name)
        if not manufacturer or not console:
            return None
        key = (self._normalize_label(manufacturer), self._normalize_label(console))
        return getattr(self, "provider_map", {}).get(key)

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
