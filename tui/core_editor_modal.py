from textual.app import ComposeResult
from textual.widgets import Header, Footer, Input, Static, DataTable
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen

from utils.cores_registry import load_registry, upsert_core
from utils.library_sync import load_modules

from .bios_editor_modal import BiosEditorModal


class CoreEditorModal(ModalScreen):
    """Create or edit a core definition."""

    CSS_PATH = "styles/core_editor.css"

    BINDINGS = [
        ("escape", "dismiss", "Cancel"),
        ("ctrl+s", "save", "Save"),
        ("b", "add_bios", "Add BIOS"),
    ]

    def __init__(self, core_id: str | None = None):
        super().__init__()
        self._core_id = core_id
        self.registry = load_registry()
        self.core_data = self.registry.get("cores", {}).get(core_id, {}) if core_id else {}
        self.selected_consoles = set(self.core_data.get("console_guids", []))
        self.selected_bios = set(self.core_data.get("bios_ids", []))
        self.modules = load_modules()

    def compose(self) -> ComposeResult:
        title = f"Edit Core" if self._core_id else "Add Core"
        yield Header(title)
        self.id_input = Input(value=self._core_id or "", placeholder="core id (snake_case)", id="core_id", disabled=bool(self._core_id))
        self.name_input = Input(value=self.core_data.get("name", ""), placeholder="Core display name", id="core_name")
        self.notes_input = Input(value=self.core_data.get("notes", ""), placeholder="Notes (optional)", id="core_notes")
        self.console_filter = Input(placeholder="Filter consoles…", id="console_filter")
        self.console_table = DataTable(id="console_table", classes="core-table")
        self.console_table.add_columns("Sel", "Manufacturer", "Console")
        self.console_table.cursor_type = "row"
        self.console_table.show_header = False
        self.console_table.zebra_stripes = True
        self.bios_filter = Input(placeholder="Filter BIOS…", id="bios_filter")
        self.bios_table = DataTable(id="bios_table", classes="core-table")
        self.bios_table.add_columns("Sel", "BIOS ID", "Filename")
        self.bios_table.cursor_type = "row"
        self.bios_table.show_header = False
        self.bios_table.zebra_stripes = True
        yield VerticalScroll(
            Vertical(
                Static("Core Identifier"), self.id_input,
                Static("Display Name"), self.name_input,
                Static("Notes"), self.notes_input,
                id="core_meta",
            ),
            Vertical(
                Static("Supported Consoles"),
                self.console_filter,
                self.console_table,
            ),
            Vertical(
                Static("BIOS Requirements"),
                self.bios_filter,
                self.bios_table,
            ),
            id="core_editor_body",
        )
        yield Static("Shortcuts: [b] Add BIOS · [Ctrl+S] Save · [Esc] Cancel", id="core_editor_hints")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_console_table()
        self._refresh_bios_table()

    def _refresh_console_table(self, focus_row: int | None = None) -> None:
        filter_value = (self.console_filter.value or "").lower().strip()
        cursor_row = getattr(self.console_table, "cursor_row", 0)
        self.console_table.clear()
        for module in self.modules:
            name = module.get("name", "Unknown")
            guid = module.get("guid")
            if not guid:
                continue
            if filter_value and filter_value not in name.lower():
                continue
            manufacturer, _, console = name.partition("-")
            manufacturer = manufacturer.strip()
            console = console.strip() if console else name
            mark = "☑" if guid in self.selected_consoles else "☐"
            self.console_table.add_row(mark, manufacturer or "—", console or name, key=guid)
        if self.console_table.row_count:
            row = focus_row if focus_row is not None else cursor_row
            row = max(0, min(row, self.console_table.row_count - 1))
            self.console_table.cursor_coordinate = (row, 0)

    def _refresh_bios_table(self, focus_row: int | None = None) -> None:
        filter_value = (self.bios_filter.value or "").lower().strip()
        cursor_row = getattr(self.bios_table, "cursor_row", 0)
        bios_map = self.registry.get("bios_files", {})
        self.bios_table.clear()
        for bios_id, entry in sorted(bios_map.items()):
            filename = entry.get("filename", "—")
            if filter_value and filter_value not in bios_id.lower() and filter_value not in filename.lower():
                continue
            mark = "☑" if bios_id in self.selected_bios else "☐"
            self.bios_table.add_row(mark, bios_id, filename, key=bios_id)
        if self.bios_table.row_count:
            row = focus_row if focus_row is not None else cursor_row
            row = max(0, min(row, self.bios_table.row_count - 1))
            self.bios_table.cursor_coordinate = (row, 0)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "console_filter":
            self._refresh_console_table()
        elif event.input.id == "bios_filter":
            self._refresh_bios_table()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table_id = event.data_table.id
        key = event.row_key
        if table_id == "console_table" and key:
            if key in self.selected_consoles:
                self.selected_consoles.remove(key)
            else:
                self.selected_consoles.add(key)
            self._refresh_console_table(focus_row=event.cursor_row)
        elif table_id == "bios_table" and key:
            if key in self.selected_bios:
                self.selected_bios.remove(key)
            else:
                self.selected_bios.add(key)
            self._refresh_bios_table(focus_row=event.cursor_row)

    def action_add_bios(self) -> None:
        self.app.push_screen(BiosEditorModal(), self._after_bios_saved)

    def _after_bios_saved(self, result):
        if result and result.get("bios_id"):
            self.registry = load_registry()
            self.selected_bios.add(result["bios_id"])
            self._refresh_bios_table()

    def action_save(self) -> None:
        self._save()

    def _save(self) -> None:
        core_id = (self._core_id or self.id_input.value or "").strip()
        if not core_id:
            self.app.notify("Core id is required", severity="error")
            return
        name = (self.name_input.value or "").strip()
        if not name:
            self.app.notify("Core name is required", severity="error")
            return
        payload = {
            "name": name,
            "notes": (self.notes_input.value or "").strip() or None,
            "bios_ids": sorted(self.selected_bios),
            "console_guids": sorted(self.selected_consoles),
        }
        upsert_core(core_id, payload)
        self.app.notify(f"Saved core {core_id}", severity="success")
        self.dismiss({"core_id": core_id})
