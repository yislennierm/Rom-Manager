from pathlib import Path
from typing import Callable

from textual.app import ComposeResult
from textual.widgets import Header, Footer, DataTable, Static
from textual.containers import Container
from textual.screen import ModalScreen


class PathBrowserScreen(ModalScreen):
    """Simple filesystem browser for selecting directories or files."""

    BINDINGS = [
        ("escape", "dismiss", "Cancel"),
        ("enter", "open", "Open"),
        ("s", "select", "Select"),
    ]

    def __init__(self, callback: Callable[[str], None], start: Path | None = None, select_files: bool = False):
        super().__init__()
        self.callback = callback
        self.select_files = select_files
        candidate = Path(start or Path.home()).expanduser()
        if not candidate.exists():
            candidate = candidate.parent if candidate.parent.exists() else Path.home()
        if candidate.is_file():
            candidate = candidate.parent
        self.current = candidate

    def compose(self) -> ComposeResult:
        yield Header()
        self.table = DataTable(id="path_browser")
        self.table.add_columns("Name", "Type")
        yield Container(self.table, id="path_browser_container")
        action = "Enter: open/select file" if self.select_files else "Enter: open folder"
        yield Static(f"{action} • S: select current • Esc: cancel", id="path_browser_status")
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
        elif self.select_files and path.is_file():
            self.callback(str(path))
            self.dismiss()

    def action_select(self):
        if self.select_files:
            key = self.table.cursor_row_key
            if key and key.value != "..":
                path = Path(key.value)
                if path.is_file():
                    self.callback(str(path))
                    self.dismiss()
                    return
        self.callback(str(self.current))
        self.dismiss()
