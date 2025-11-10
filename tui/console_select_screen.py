from textual.app import ComposeResult
from textual.widgets import Header, Footer, DataTable, Static
from textual.containers import Container
from textual.screen import Screen
from textual import events

from utils.paths import list_cached_consoles
from .rom_explorer_screen import ROMExplorerScreen


class ConsoleSelectScreen(Screen):
    """Screen for listing cached consoles and selecting one for browsing."""

    CSS_PATH = "styles/rom_explorer.css"

    def __init__(self):
        super().__init__(id="console_select_screen")

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("enter", " action_select_console", "Select"),
        ("r", "refresh", "Refresh"),
    ]
    

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("[b]Cached Consoles[/b]\n(Press [Enter] to select, [R] to refresh, [Esc] to return)", id="label"),
            DataTable(id="console_table"),
        )
        yield Footer()

    def on_mount(self):
        self._ready_for_selection = False
        self.table = self.query_one("#console_table", DataTable)
        self.table.add_columns("Manufacturer", "Console", "ROMs")
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.table.focus()
        self.populate()

    def populate(self):
        self.table.clear()
        self.consoles = list_cached_consoles()
        if not self.consoles:
            self.table.add_row("—", "No activated consoles with RDB exports. Use Database screen (space + i).", "")
            self.table.cursor_row = 0
            self._notify(
                "No activated consoles found. Toggle a console in Database and press [i] to export its RDB.",
                severity="warning",
            )
            return

        for entry in self.consoles:
            roms_count = entry.get("rom_count")
            roms_display = f"{roms_count}" if roms_count not in (None, 0) else "—"
            self.table.add_row(entry["manufacturer"], entry["console"], roms_display)
        self._notify(f"Loaded {len(self.consoles)} cached console(s).", severity="debug")
        self._ready_for_selection = True

    def action_refresh(self):
        self.populate()

    def action_go_back(self):
        self.app.pop_screen()

    def action_select_console(self):
        if not self._ready_for_selection:
            return
        if not getattr(self, "consoles", None):
            self.app.bell()
            return
        row_index = getattr(self.table, "cursor_row", 0)
        row_index = max(0, min(row_index, len(self.consoles) - 1))
        entry = self.consoles[row_index]

        self.app.current_manufacturer = entry["manufacturer"]
        self.app.current_console = entry["console"]
        self.app.current_roms_path = entry["roms_path"]
        self.app.current_manufacturer_slug = entry["manufacturer_slug"]
        self.app.current_console_slug = entry["console_slug"]
        self.app.current_module_guid = entry.get("guid")

        self._notify(
            f"Switching to {entry['manufacturer']} / {entry['console']} (ROMs: {entry.get('rom_count', 'unknown')})",
            severity="info",
        )

        self.app.push_screen(
            ROMExplorerScreen(
                manufacturer=entry["manufacturer"],
                console=entry["console"],
                roms_path=entry["roms_path"],
                module_guid=entry.get("guid"),
            )
        )

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            self.action_select_console()
            event.stop()
        elif event.key in ("escape", "backspace"):
            self.action_go_back()
            event.stop()
        elif event.key == "r":
            self.action_refresh()
            event.stop()

    def _notify(self, message: str, severity: str = "info") -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            print(f"[{severity.upper()}] {message}")
