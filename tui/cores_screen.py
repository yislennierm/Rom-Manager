from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, DataTable
from textual.screen import Screen

from utils.cores_registry import load_registry, delete_core
from .core_editor_modal import CoreEditorModal


class CoresScreen(Screen):
    """Screen to manage libretro cores and BIOS mappings."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("n", "new_core", "New"),
        ("e", "edit_core", "Edit"),
        ("enter", "edit_core", "Edit"),
        ("delete", "delete_core", "Delete"),
        ("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header("Cores")
        yield Static(
            "Manage RetroArch core definitions, assign consoles, and configure BIOS requirements.\n"
            "Shortcuts: [n]ew, [e]dit, [delete], [r]efresh, [Esc] back.",
            id="cores_info",
        )
        self.table = DataTable(id="cores_table")
        self.table.add_columns("Core", "Name", "Consoles", "BIOS Files")
        yield self.table
        yield Footer()

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.refresh_table()

    def refresh_table(self) -> None:
        registry = load_registry()
        cores = registry.get("cores", {})
        self.table.clear()
        self._row_keys: list[str | None] = []
        if not cores:
            self.table.add_row("—", "No cores defined", "—", "—")
            self._row_keys.append(None)
            return
        for core_id, payload in sorted(cores.items()):
            name = payload.get("name", core_id)
            consoles = len(payload.get("console_guids", []) or [])
            bios = len(payload.get("bios_ids", []) or [])
            self.table.add_row(core_id, name, str(consoles), str(bios), key=core_id)
            self._row_keys.append(core_id)

    def _current_core_id(self) -> str | None:
        if not getattr(self, "_row_keys", None):
            return None
        row = getattr(self.table, "cursor_row", 0)
        if not self._row_keys:
            return None
        row = max(0, min(row, len(self._row_keys) - 1))
        return self._row_keys[row]

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.refresh_table()

    def action_new_core(self) -> None:
        self.app.push_screen(CoreEditorModal(), self._after_edit)

    def action_edit_core(self) -> None:
        core_id = self._current_core_id()
        if not core_id:
            self.app.bell()
            return
        self.app.push_screen(CoreEditorModal(core_id), self._after_edit)

    def action_delete_core(self) -> None:
        core_id = self._current_core_id()
        if not core_id:
            self.app.bell()
            return
        delete_core(core_id)
        self.app.notify(f"Deleted core {core_id}", severity="warning")
        self.refresh_table()

    def _after_edit(self, result) -> None:
        if result and result.get("core_id"):
            self.refresh_table()
