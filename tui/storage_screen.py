import json
from pathlib import Path

from textual.app import ComposeResult
from textual.widgets import Header, Footer, DataTable, Input, Static, Button, Checkbox
from textual.containers import Container, Horizontal
from textual.screen import Screen

from .path_browser_screen import PathBrowserScreen

CONFIG_PATH = Path("data/storage/storage_config.json")
DEFAULTS_PATH = Path("data/storage/frontends.json")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if DEFAULTS_PATH.exists():
            CONFIG_PATH.write_text(DEFAULTS_PATH.read_text())
        else:
            CONFIG_PATH.write_text(json.dumps({
                "default_roms": "~/ROMs",
                "default_bios": "~/BIOS",
                "frontends": {}
            }, indent=2))
    return json.loads(CONFIG_PATH.read_text())


def save_config(data: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2))


class StorageScreen(Screen):
    """Manage frontend storage paths (ROMs/Bios)."""

    CSS_PATH = "styles/storage.css"

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("ctrl+s", "save", "Save"),
        ("enter", "open_browser", "Browse Paths"),
        ("e", "open_browser", "Browse Paths"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        self.frontend_table = DataTable(id="frontend_table")
        self.frontend_table.add_columns("Frontend", "Active")
        detail = Container(
            Static("Select a frontend to edit settings. Use '...' buttons or press Enter/E to browse.", id="storage_prompt"),
            Input(placeholder="Display name", id="name_input"),
            Checkbox(label="Active", id="active_checkbox"),
            Horizontal(
                Input(placeholder="ROMs path", id="roms_input"),
                Button("...", id="roms_browse"),
            ),
            Horizontal(
                Input(placeholder="BIOS path", id="bios_input"),
                Button("...", id="bios_browse"),
            ),
            Button("Save Changes", id="btn_save", variant="success"),
            id="storage_detail",
        )
        layout = Horizontal(
            self.frontend_table,
            detail,
            id="storage_split",
        )
        yield Container(layout, id="storage_container")
        yield Footer()

    def on_mount(self):
        self.config = load_config()
        self.selected_key = None
        self._refresh_table()

    def _refresh_table(self):
        self.frontend_table.clear()
        frontends = self.config.get("frontends", {})
        for key, entry in frontends.items():
            self.frontend_table.add_row(
                entry.get("name", key),
                "✅" if entry.get("active") else "—",
                key=key,
            )
        if frontends:
            self.frontend_table.cursor_type = "row"
            self.frontend_table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        key = event.row_key.value
        self.selected_key = key
        entry = self.config["frontends"].get(key, {})
        self.query_one("#name_input", Input).value = entry.get("name", key)
        self.query_one("#roms_input", Input).value = entry.get("roms_path", "")
        self.query_one("#bios_input", Input).value = entry.get("bios_path", "")
        self.query_one("#active_checkbox", Checkbox).value = bool(entry.get("active"))
        self.query_one("#storage_prompt", Static).update(f"Editing {entry.get('name', key)}")

    def action_go_back(self):
        self.app.pop_screen()

    def action_save(self):
        self._save_current()

    def action_open_browser(self):
        if not self.selected_key:
            return
        focus_target = "roms"
        focused = self.app.focused
        focused_id = getattr(focused, "id", "") if focused else ""
        if focused_id in {"bios_input", "bios_browse"}:
            focus_target = "bios"
        elif focused_id in {"roms_input", "roms_browse"}:
            focus_target = "roms"
        self._launch_browser(focus_target)

    def _on_browser_selected(self, selected_path: str):
        target = getattr(self, "_browser_target", "roms")
        if target == "bios":
            self.query_one("#bios_input", Input).value = selected_path
        else:
            self.query_one("#roms_input", Input).value = selected_path

    def _launch_browser(self, target: str):
        if not self.selected_key:
            return
        self._browser_target = target
        current_value = self.query_one("#roms_input" if target == "roms" else "#bios_input", Input).value
        start_path = Path(current_value or "~").expanduser()
        self.app.push_screen(PathBrowserScreen(self._on_browser_selected, start=start_path))

    def on_input_submitted(self, event: Input.Submitted):
        self._save_current()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id in {"roms_browse", "bios_browse"}:
            if not self.selected_key:
                return
            target = "roms" if event.button.id == "roms_browse" else "bios"
            self._launch_browser(target)
        elif event.button.id == "btn_save":
            self._save_current()

    def _save_current(self, update_prompt: bool = True):
        if not self.selected_key:
            return
        entry = self.config["frontends"].setdefault(self.selected_key, {})
        entry["name"] = self.query_one("#name_input", Input).value or self.selected_key
        entry["roms_path"] = self.query_one("#roms_input", Input).value
        entry["bios_path"] = self.query_one("#bios_input", Input).value
        entry["active"] = self.query_one("#active_checkbox", Checkbox).value
        save_config(self.config)
        if update_prompt:
            self.query_one("#storage_prompt", Static).update(
                f"Saved {entry.get('name', self.selected_key)}"
            )
        self._refresh_table()
