from pathlib import Path
from typing import Callable, Dict

from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, Input, Button, Checkbox
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen

from .path_browser_screen import PathBrowserScreen


class FrontendEditorScreen(ModalScreen):
    """Modal form for editing a frontend's storage paths."""

    CSS_PATH = "styles/frontend_editor.css"

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+s", "save", "Save"),
    ]

    def __init__(self, key: str, data: Dict, on_save: Callable[[str, Dict], None]):
        super().__init__()
        self.key = key
        self.data = data or {}
        self.on_save = on_save
        self._browser_target = "roms"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static(f"Editing {self.data.get('name', self.key)}", id="frontend_editor_title"),
            Input(value=self.data.get("name", self.key), placeholder="Display name", id="editor_name"),
            Checkbox(label="Active", value=bool(self.data.get("active")), id="editor_active"),
            Horizontal(
                Input(value=self.data.get("roms_path", ""), placeholder="ROMs path", id="editor_roms"),
                Button("...", id="editor_roms_browse"),
            ),
            Horizontal(
                Input(value=self.data.get("bios_path", ""), placeholder="BIOS path", id="editor_bios"),
                Button("...", id="editor_bios_browse"),
            ),
            Horizontal(
                Button("Cancel", id="editor_cancel"),
                Button("Save", id="editor_save", variant="success"),
                id="frontend_editor_buttons",
            ),
            id="frontend_editor",
        )
        yield Footer()

    # ------------------------------------------------------------------

    def action_cancel(self):
        self.dismiss()

    def action_save(self):
        self._save()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "editor_cancel":
            self.dismiss()
        elif event.button.id == "editor_save":
            self._save()
        elif event.button.id in {"editor_roms_browse", "editor_bios_browse"}:
            target = "roms" if event.button.id == "editor_roms_browse" else "bios"
            self._launch_browser(target)

    # ------------------------------------------------------------------

    def _save(self):
        payload = {
            "name": self.query_one("#editor_name", Input).value or self.key,
            "roms_path": self.query_one("#editor_roms", Input).value,
            "bios_path": self.query_one("#editor_bios", Input).value,
            "active": self.query_one("#editor_active", Checkbox).value,
        }
        self.on_save(self.key, payload)
        self.dismiss()

    def _launch_browser(self, target: str):
        self._browser_target = target
        current_value = self.query_one("#editor_roms" if target == "roms" else "#editor_bios", Input).value
        start_path = Path(current_value or "~").expanduser()
        self.app.push_screen(PathBrowserScreen(self._on_path_selected, start=start_path))

    def _on_path_selected(self, selected_path: str):
        target = getattr(self, "_browser_target", "roms")
        if target == "bios":
            self.query_one("#editor_bios", Input).value = selected_path
        else:
            self.query_one("#editor_roms", Input).value = selected_path
