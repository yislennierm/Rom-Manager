from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, Input, DataTable
from textual.containers import Container
from textual.screen import Screen

import json
import os

from core.providers import load_cached_roms
from utils.paths import manufacturer_slug, console_slug
from .download_manager_screen import DownloadManagerScreen
from .message_screen import MessageScreen
from .rom_detail_screen import ROMDetailScreen


class GlobalSearchScreen(Screen):
    """Search across all cached ROMs."""

    BINDINGS = [
        ("/", "focus_search", "Search"),
        ("space", "toggle_selection", "Select ROM"),
        ("enter", "show_details", "Details"),
        ("a", "queue_jobs", "Queue Download"),
        ("escape", "go_back", "Back"),
        ("backspace", "go_back", "Back"),
    ]

    def __init__(self):
        super().__init__()
        self.roms = []
        self.filtered = []
        self.selected = set()
        self.artwork_provider = "libretro"

    def compose(self) -> ComposeResult:
        self.label = Static("Global ROM Search", id="label")
        yield Header()
        yield Container(
            self.label,
            Input(placeholder="Search all consoles...", id="search"),
            DataTable(id="rom_table"),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.table = self.query_one("#rom_table", DataTable)
        self.search_input = self.query_one("#search", Input)
        self.table.add_columns("Selected", "Manufacturer", "Console", "Name", "Size", "MD5", "Protocol", "Local")
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.table.focus()

        self.load_roms()
        self.apply_filter()

    def load_roms(self) -> None:
        self.roms = load_cached_roms()
        for rom in self.roms:
            manufacturer = rom.get("manufacturer", "Unknown")
            console = rom.get("console", "Unknown")
            local_path = os.path.join(
                "downloads",
                manufacturer_slug(manufacturer),
                console_slug(console),
                rom["name"],
            )
            rom["_local_path"] = local_path
            rom["_is_local"] = os.path.exists(local_path)
        count = len(self.roms)
        self._notify(f"Loaded {count} ROM entries from cache.", severity="debug")

    def apply_filter(self) -> None:
        query = (self.search_input.value or "").lower()
        if not query:
            self.filtered = self.roms
        else:
            self.filtered = [
                rom for rom in self.roms
                if query in rom["name"].lower()
                   or query in rom.get("console", "").lower()
                   or query in rom.get("manufacturer", "").lower()
            ]
        self.display_roms(self.filtered)
        self.label.update(f"Global ROM Search — {len(self.filtered)}/{len(self.roms)} match '{query}'")

    def display_roms(self, roms):
        self.table.clear()
        for rom in roms:
            selected = "[*]" if rom["name"] in self.selected else "[ ]"
            size = rom.get("size") or "?"
            if isinstance(size, str) and size.isdigit():
                size = int(size)
            if isinstance(size, (int, float)):
                size_display = self._format_size(int(size))
            else:
                size_display = str(size)
            protocol = "torrent" if rom.get("torrent_url") else ("http" if rom.get("http_url") else "—")
            self.table.add_row(
                selected,
                rom.get("manufacturer", "Unknown"),
                rom.get("console", "Unknown"),
                rom.get("name", "Unknown"),
                size_display,
                rom.get("md5") or "—",
                protocol,
                "✅" if rom.get("_is_local") else "—",
            )

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes <= 0:
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

    def _toggle_selection(self):
        if not self.table.row_count:
            return
        row_index = getattr(self.table, "cursor_row", 0)
        rom = self.filtered[row_index]
        name = rom["name"]
        if name in self.selected:
            self.selected.remove(name)
        else:
            self.selected.add(name)
        self.apply_filter()
        self._notify(f"Selected {len(self.selected)} ROM(s)", severity="debug")

    def _queue_jobs(self):
        if not self.selected:
            self.app.bell()
            self._notify("No ROMs selected.", severity="warning")
            return

        manager = getattr(self.app, "download_manager", None)
        if manager is None:
            self.app.push_screen(MessageScreen("Error", "Download manager unavailable."))
            return

        jobs_created = 0
        existing_count = 0
        for rom in self.roms:
            if rom["name"] not in self.selected:
                continue
            torrent = rom.get("torrent_url")
            http_url = rom.get("http_url")
            if not torrent and not http_url:
                continue
            manufacturer = rom.get("manufacturer", "Unknown")
            console = rom.get("console", "Unknown")
            destination = os.path.join(
                "downloads",
                manufacturer_slug(manufacturer),
                console_slug(console),
            )
            job = manager.add_job(
                rom_name=rom["name"],
                source=torrent,
                http_url=http_url,
                destination=destination,
                console=console,
                manufacturer=manufacturer,
                size_bytes=rom.get("size"),
                md5=rom.get("md5"),
            )
            if job.get("protocol") == "local" and job.get("status") == "completed":
                existing_count += 1
            else:
                jobs_created += 1

        if jobs_created:
            self._notify(f"Queued {jobs_created} job(s).", severity="success")
            self.app.push_screen(DownloadManagerScreen())
        elif existing_count:
            message = f"{existing_count} ROM(s) already in library."
            self._notify(message, severity="info")
            self.app.push_screen(MessageScreen("Already Downloaded", message))
        else:
            self.app.bell()
            self._notify("No valid download source for selected ROMs.", severity="warning")
            self.app.push_screen(MessageScreen("Info", "No download source for selected ROMs."))

    def on_input_changed(self, event: Input.Changed) -> None:
        self.apply_filter()

    def _current_rom(self):
        if not self.table.row_count or not self.filtered:
            return None
        row_index = getattr(self.table, "cursor_row", 0)
        return self.filtered[row_index] if row_index < len(self.filtered) else None

    def _show_details(self):
        rom = self._current_rom()
        if not rom:
            self.app.bell()
            return
        self.app.push_screen(ROMDetailScreen(rom, artwork_provider=self.artwork_provider))

    # ------------------------------------------------------------------
    # Actions / bindings
    # ------------------------------------------------------------------

    def action_focus_search(self) -> None:
        if hasattr(self, "search_input"):
            self.set_focus(self.search_input)

    def action_toggle_selection(self) -> None:
        self._toggle_selection()

    def action_show_details(self) -> None:
        self._show_details()

    def action_queue_jobs(self) -> None:
        self._queue_jobs()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _notify(self, message: str, severity: str = "info") -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            print(f"[{severity.upper()}] {message}")
