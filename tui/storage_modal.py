import json
import os
from pathlib import Path

from textual.app import ComposeResult
from textual.widgets import Header, Footer, DataTable, Static
from textual.containers import Vertical
from textual.screen import ModalScreen

from .frontend_editor_screen import FrontendEditorScreen
from data.storage.frontend_detector import merge_detected_frontends

DATA_DIR = Path(os.environ.get("ROMS_MANAGER_DATA_ROOT", Path(__file__).resolve().parents[1] / "data")).expanduser()
CONFIG_PATH = DATA_DIR / "storage" / "storage_config.json"
DEFAULTS_PATH = DATA_DIR / "storage" / "frontends.json"


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


class StorageModal(ModalScreen):
    """Modal view for managing frontend storage paths (ROMs/Bios)."""

    CSS_PATH = "styles/storage.css"

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("enter", "edit_frontend", "Edit"),
        ("e", "edit_frontend", "Edit"),
        ("r", "refresh_detected", "Detect"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        self.frontend_table = DataTable(id="frontend_table")
        self.frontend_table.add_columns("Frontend", "Type", "ROMs Path", "BIOS Path", "Active")
        self.status = Static("Select a frontend and press Enter to edit. Press r to detect RetroArch installs.", id="storage_prompt")
        yield Vertical(self.frontend_table, self.status, id="storage_container")
        yield Footer()

    def on_mount(self):
        self.config = load_config()
        self.selected_key = None
        self._refresh_table()

    # ------------------------------------------------------------------

    def _refresh_table(self):
        self.frontend_table.clear()
        frontends = self.config.get("frontends", {})
        self._row_keys = list(frontends.keys())
        for key, entry in frontends.items():
            self.frontend_table.add_row(
                entry.get("name", key),
                entry.get("install_type", entry.get("kind", "manual")),
                entry.get("roms_path", "—"),
                entry.get("bios_path", "—"),
                "✅" if entry.get("active") else "—",
                key=key,
            )
        if frontends:
            self.frontend_table.cursor_type = "row"
            self.frontend_table.focus()
            if self.selected_key not in frontends:
                self.selected_key = self._row_keys[0]
            row_index = self._row_keys.index(self.selected_key)
            try:
                self.frontend_table.move_cursor(row=row_index, column=0)
            except Exception:
                pass
            entry = frontends[self.selected_key]
            self.status.update(f"{entry.get('name', self.selected_key)} ready to edit")
        else:
            self.selected_key = None
            self.status.update("No frontends configured.")

    def _current_key(self) -> str | None:
        row_index = getattr(self.frontend_table, "cursor_row", None)
        if row_index is not None and hasattr(self, "_row_keys"):
            if 0 <= row_index < len(self._row_keys):
                return self._row_keys[row_index]
        return self.selected_key

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        self.selected_key = event.row_key.value
        entry = self.config["frontends"].get(self.selected_key, {})
        self.status.update(f"{entry.get('name', self.selected_key)} — {'Active' if entry.get('active') else 'Inactive'}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_edit_frontend(self):
        key = self._current_key()
        if not key:
            self.status.update("Select a frontend first.")
            return
        entry = self.config["frontends"].get(key, {})
        modal = FrontendEditorScreen(key, entry, self._persist_frontend)
        self.app.push_screen(modal)

    def action_refresh_detected(self):
        self.config, added = merge_detected_frontends(self.config)
        save_config(self.config)
        self.status.update(f"Detected frontends refreshed. Added {added}.")
        self._refresh_table()

    def _persist_frontend(self, key: str, payload: dict):
        self.config["frontends"][key] = payload
        save_config(self.config)
        self.status.update(f"Saved {payload.get('name', key)}")
        self.selected_key = key
        self._refresh_table()
