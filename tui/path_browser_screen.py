from pathlib import Path
from typing import Callable

from textual.app import ComposeResult
from textual.widgets import Header, Footer, DataTable, Static
from textual.containers import Container
from textual.screen import ModalScreen


class PathBrowserScreen(ModalScreen):
    """Simple filesystem browser for selecting directories."""

    BINDINGS = [
        ("escape", "dismiss", "Cancel"),
        ("enter", "open", "Open Folder"),
        ("s", "select", "Select Folder"),
    ]

    def __init__(self, callback: Callable[[str], None], start: Path | None = None):
        super().__init__()
        self.callback = callback
        candidate = Path(start or Path.home()).expanduser()
        if not candidate.exists():
            candidate = candidate.parent if candidate.parent.exists() else Path.home()
        self.current = candidate

    def compose(self) -> ComposeResult:
        yield Header()
        self.table = DataTable(id="path_browser")
        self.table.add_columns("Name", "Type")
        yield Container(self.table, id="path_browser_container")
        yield Static("Enter: open folder • S: select current • Esc: cancel", id="path_browser_status")
        yield Footer()

    def on_mount(self):
        self._refresh()
        self.table.cursor_type = "row"
        self.table.focus()

    def _refresh(self):
        self.table.clear()
        self.query_one("#path_browser_status", Static).update(str(self.current))
        if self.current.parent != self.current:
            self.table.add_row("..", "Parent", key="..")
        try:
            entries = sorted(self.current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            entries = []
        for entry in entries:
            entry_path = entry.resolve()
            self.table.add_row(entry.name, "Dir" if entry.is_dir() else "File", key=str(entry_path))

    def action_dismiss(self):
        self.dismiss()

    def action_open(self):
        key = self.table.cursor_row_key
        if not key:
            return
        value = key.value
        if value == "..":
            self.current = self.current.parent
            self._refresh()
            return
        path = Path(value)
        if path.is_dir():
            self.current = path
            self._refresh()

    def action_select(self):
        self.callback(str(self.current))
        self.dismiss()
