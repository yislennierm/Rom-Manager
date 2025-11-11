from textual.app import ComposeResult
from textual.widgets import Header, Footer, Input, Static
from textual.containers import Vertical
from textual.screen import ModalScreen

from utils.cores_registry import load_registry, upsert_bios


class BiosEditorModal(ModalScreen):
    """Modal to create or edit a BIOS definition."""

    BINDINGS = [
        ("escape", "dismiss", "Cancel"),
        ("ctrl+s", "save", "Save"),
    ]

    def __init__(self, bios_id: str | None = None):
        super().__init__()
        self._existing_id = bios_id
        registry = load_registry()
        self._bios_map = registry.get("bios_files", {})
        self._initial = self._bios_map.get(bios_id, {}) if bios_id else {}

    def compose(self) -> ComposeResult:
        title = "Edit BIOS" if self._existing_id else "Add BIOS"
        yield Header(title)
        self.id_input = Input(value=self._existing_id or "", placeholder="bios id (snake_case)", id="bios_id", disabled=bool(self._existing_id))
        self.filename_input = Input(value=self._initial.get("filename", ""), placeholder="Filename (e.g., bios.bin)", id="bios_filename")
        self.md5_input = Input(value=self._initial.get("md5", ""), placeholder="MD5 hash", id="bios_md5")
        self.size_input = Input(value=str(self._initial.get("size", "")) if self._initial.get("size") else "", placeholder="File size in bytes (optional)", id="bios_size")
        self.url_input = Input(value=self._initial.get("url", ""), placeholder="Download URL (optional)", id="bios_url")
        self.notes_input = Input(value=self._initial.get("notes", ""), placeholder="Notes (optional)", id="bios_notes")
        yield Vertical(
            Static("BIOS Identifier"), self.id_input,
            Static("Filename"), self.filename_input,
            Static("MD5"), self.md5_input,
            Static("Size"), self.size_input,
            Static("URL"), self.url_input,
            Static("Notes"), self.notes_input,
            id="bios_form",
        )
        yield Static("Shortcuts: [Ctrl+S] Save · [Esc] Cancel", id="bios_editor_hints")
        yield Footer()

    def action_save(self) -> None:
        self._save()

    def _save(self) -> None:
        bios_id = (self._existing_id or self.id_input.value or "").strip()
        if not bios_id:
            self.app.notify("Provide a BIOS id", severity="error")
            return
        filename = (self.filename_input.value or "").strip()
        if not filename:
            self.app.notify("Provide a filename", severity="error")
            return
        md5 = (self.md5_input.value or "").strip() or None
        size_raw = (self.size_input.value or "").strip()
        try:
            size = int(size_raw) if size_raw else None
        except ValueError:
            self.app.notify("Size must be an integer", severity="error")
            return
        payload = {
            "filename": filename,
            "md5": md5,
            "size": size,
            "url": (self.url_input.value or "").strip() or None,
            "notes": (self.notes_input.value or "").strip() or None,
        }
        upsert_bios(bios_id, payload)
        self.app.notify(f"Saved BIOS {bios_id}", severity="success")
        self.dismiss({"bios_id": bios_id})
