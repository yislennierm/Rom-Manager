from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, Input, DataTable
from textual.containers import Container
from textual.screen import Screen
from textual import events

import json
import os

from .message_screen import MessageScreen
from .download_manager_screen import DownloadManagerScreen
from utils.paths import roms_json_path, manufacturer_slug, console_slug


DEFAULT_MANUFACTURER = "Sega"
DEFAULT_CONSOLE = "Dreamcast"


class ROMExplorerScreen(Screen):
    """Browse ROMs for the currently selected console."""

    def __init__(self, manufacturer=None, console=None, roms_path=None):
        super().__init__()
        self._initial_manufacturer = manufacturer
        self._initial_console = console
        self._explicit_roms_path = roms_path
        self.roms = []
        self.selected_names: set[str] = set()
        self.torrent_url = None

    def compose(self) -> ComposeResult:
        self.label = Static("", id="label")
        yield Header()
        yield Container(
            self.label,
            Input(placeholder="Type to search...", id="search"),
            DataTable(id="rom_table"),
        )
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        app = getattr(self, "app", None)

        manufacturer = self._initial_manufacturer or getattr(app, "current_manufacturer", DEFAULT_MANUFACTURER)
        console = self._initial_console or getattr(app, "current_console", DEFAULT_CONSOLE)
        roms_path = self._explicit_roms_path or getattr(app, "current_roms_path", None)

        if not roms_path:
            roms_path = roms_json_path(manufacturer, console)
        elif not os.path.exists(roms_path):
            self._notify(
                f"Provided ROM list not found at {roms_path}; falling back to default cache.",
                severity="warning",
            )
            roms_path = roms_json_path(manufacturer, console)

        table = self.query_one("#rom_table", DataTable)
        self.table = table
        self.search_input = self.query_one("#search", Input)
        self.manager = getattr(app, "download_manager", None)

        table.clear()
        table.add_columns("Selected", "Console", "Name", "Size", "MD5")
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.focus()

        if self.manager is None:
            self._notify("Download manager instance unavailable.", severity="error")
            self.app.push_screen(MessageScreen("Error", "Download manager is not available."))
            return

        if not os.path.exists(roms_path):
            self._notify(f"ROM list missing: {roms_path}", severity="error")
            self.app.push_screen(MessageScreen("Error", f"Missing ROM list: {os.path.basename(roms_path)}"))
            return

        with open(roms_path) as fh:
            self.roms = json.load(fh)
        self._notify(
            f"Loaded {len(self.roms)} ROM entries from {os.path.basename(roms_path)}",
            severity="debug",
        )

        # Normalise metadata for display and downloads.
        for rom in self.roms:
            rom.setdefault("manufacturer", manufacturer)
            rom.setdefault("console", console)
            if rom.get("torrent_url"):
                self.torrent_url = rom["torrent_url"]
            size_value = rom.get("size")
            try:
                rom["_size_bytes"] = int(size_value)
            except (TypeError, ValueError):
                rom["_size_bytes"] = None

        self.manufacturer = manufacturer
        self.console = console
        self.roms_path = roms_path

        # Update shared state for other screens.
        if app is not None:
            app.current_manufacturer = manufacturer
            app.current_console = console
            app.current_roms_path = roms_path
            app.current_manufacturer_slug = manufacturer_slug(manufacturer)
            app.current_console_slug = console_slug(console)

        self.label.update(f"Search ROMs — {manufacturer} / {console} (press / to focus, SPACE to select)")
        self.apply_filter()
        self._notify(f"Explorer ready for {manufacturer} / {console}", severity="info")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def apply_filter(self) -> None:
        query = (self.search_input.value or "").lower()
        filtered = [rom for rom in self.roms if query in rom["name"].lower()]
        self.display_roms(filtered)
        self._notify(f"Filter applied — {len(filtered)}/{len(self.roms)} ROMs match '{query}'", severity="debug")

    def display_roms(self, roms: list[dict]) -> None:
        self.table.clear()
        for rom in roms:
            mark = "[*]" if rom["name"] in self.selected_names else "[ ]"
            formatted_size = self._format_size(rom.get("_size_bytes"))
            md5 = rom.get("md5") or "—"
            self.table.add_row(mark, rom.get("console", "Unknown"), rom["name"], formatted_size, md5)

    @staticmethod
    def _format_size(size_bytes):
        if size_bytes in (None, 0):
            return "?"
        if size_bytes < 0:
            return "?"
        thresholds = [
            (1 << 40, "TB"),
            (1 << 30, "GB"),
            (1 << 20, "MB"),
            (1 << 10, "KB"),
        ]
        for factor, unit in thresholds:
            if size_bytes >= factor:
                return f"{size_bytes / factor:.1f} {unit}"
        return f"{size_bytes} B"

    def _toggle_selection(self) -> None:
        if not self.table.row_count:
            return
        row_index = getattr(self.table, "cursor_row", 0)
        row = self.table.get_row_at(row_index)
        rom_name = row[2]
        if rom_name in self.selected_names:
            self.selected_names.remove(rom_name)
        else:
            self.selected_names.add(rom_name)
        self.apply_filter()
        self._notify(f"Selected {len(self.selected_names)} ROM(s)", severity="debug")

    def _create_jobs(self) -> None:
        jobs_created = 0
        target_dir = os.path.join(
            "downloads",
            manufacturer_slug(self.manufacturer),
            console_slug(self.console),
        )

        existing_count = 0
        for rom in self.roms:
            if rom["name"] not in self.selected_names:
                continue
            torrent = rom.get("torrent_url") or self.torrent_url
            http_url = rom.get("http_url")
            if not torrent and not http_url:
                continue
            job = self.manager.add_job(
                rom_name=rom["name"],
                source=torrent,
                http_url=http_url,
                destination=target_dir,
                console=rom.get("console", self.console),
                manufacturer=rom.get("manufacturer", self.manufacturer),
                size_bytes=rom.get("_size_bytes"),
                md5=rom.get("md5"),
            )
            if job.get("protocol") == "local" and job.get("status") == "completed":
                existing_count += 1
            else:
                jobs_created += 1

        if jobs_created:
            self.app.push_screen(DownloadManagerScreen())
            self._notify(f"Created {jobs_created} download job(s) for {self.console}", severity="info")
        elif existing_count:
            message = f"{existing_count} ROM(s) already present in your library."
            self._notify(message, severity="info")
            self.app.push_screen(MessageScreen("Already Downloaded", message))
        else:
            self.app.bell()
            self.app.push_screen(MessageScreen("Info", "No available download source for the selected ROMs."))
            self._notify("No download source found for selected ROMs", severity="warning")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        self.apply_filter()

    def on_key(self, event: events.Key) -> None:
        if event.key == "/":
            self.set_focus(self.search_input)
        elif event.key == "space":
            self._toggle_selection()
        elif event.key == "enter":
            if not self.selected_names:
                self.app.bell()
            else:
                self._create_jobs()
        elif event.key in ("escape", "backspace"):
            self.app.pop_screen()

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _notify(self, message: str, severity: str = "info") -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            # Fallback for environments without notify support.
            print(f"[{severity.upper()}] {message}")
