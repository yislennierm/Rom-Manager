import os
from typing import Dict, List, Set

from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, Input, DataTable
from textual.containers import Container
from textual.screen import Screen

from utils.catalog import build_rom_catalog
from utils.paths import manufacturer_slug, console_slug, list_cached_consoles
from .download_manager_screen import DownloadManagerScreen
from .message_screen import MessageScreen
from .rom_detail_screen import ROMDetailScreen


class GlobalSearchScreen(Screen):
    """Search across all activated consoles."""

    CSS_PATH = "styles/rom_explorer.css"

    BINDINGS = [
        ("/", "focus_search", "Search"),
        ("space", "toggle_selection", "Select ROM"),
        ("enter", "show_details", "Details"),
        ("a", "queue_jobs", "Queue Download"),
        ("escape", "go_back", "Back"),
        ("backspace", "go_back", "Back"),
    ]

    def __init__(self):
        super().__init__(id="global_search_screen")
        self.roms: List[Dict] = []
        self.filtered: List[Dict] = []
        self.selected: Set[str] = set()
        self.artwork_provider = "libretro"

    def compose(self) -> ComposeResult:
        self.label = Static("Global ROM Search", id="label")
        yield Header()
        yield Container(
            self.label,
            Input(placeholder="Search all consoles...", id="search"),
            DataTable(id="global_rom_table"),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.table = self.query_one("#global_rom_table", DataTable)
        self.search_input = self.query_one("#search", Input)
        self.table.add_column("Sel.", width=4)
        self.table.add_column("Brand", width=10)
        self.table.add_column("Console", width=10)
        self.table.add_column("Name", width=60)
        self.table.add_column("Size", width=8)
        self.table.add_column("MD5", width=36)
        self.table.add_column("Protocol", width=10)
        self.table.add_column("Local", width=6)
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.table.focus()

        self.load_roms()
        self.apply_filter()

    def load_roms(self) -> None:
        self.roms = []
        consoles = list_cached_consoles()
        if not consoles:
            self._notify("No activated consoles with RDB exports. Use Database screen first.", severity="warning")
            return
        for entry in consoles:
            manufacturer = entry["manufacturer"]
            console = entry["console"]
            try:
                catalog = build_rom_catalog(
                    manufacturer,
                    console,
                    module_guid=entry.get("guid"),
                    rdb_path=entry.get("roms_path"),
                )
            except Exception as exc:
                self._notify(f"Skipping {manufacturer}/{console}: {exc}", severity="warning")
                continue
            for rom in catalog["roms"]:
                record = dict(rom)
                record["_providers"] = rom.get("_providers", [])
                record["_provider_count"] = rom.get("_provider_count", 0)
                record["_provider_labels"] = rom.get("_provider_labels", [])
                local_path = os.path.join(
                    "downloads",
                    manufacturer_slug(manufacturer),
                    console_slug(console),
                    record["name"],
                )
                record["_local_path"] = local_path
                record["_is_local"] = os.path.exists(local_path)
                self.roms.append(record)
        self._notify(f"Loaded {len(self.roms)} ROM entries from active catalogs.", severity="debug")

    def apply_filter(self) -> None:
        query = (self.search_input.value or "").lower().strip()
        if not query:
            self.filtered = self.roms
        else:
            tokens = query.split()
            self.filtered = [
                rom
                for rom in self.roms
                if all(
                    token in (rom.get("_search_blob") or "")
                    or token in (rom.get("manufacturer", "").lower())
                    or token in (rom.get("console", "").lower())
                    for token in tokens
                )
            ]
        self.display_roms(self.filtered)
        self.label.update(f"Global ROM Search — {len(self.filtered)}/{len(self.roms)} match '{query}'")

    def display_roms(self, roms: List[Dict]) -> None:
        self.table.clear()
        for rom in roms:
            selected = "[*]" if rom["_key"] in self.selected else "[ ]"
            size_display = self._format_size(rom.get("_size_bytes"))
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
    def _format_size(size_bytes) -> str:
        if not size_bytes or size_bytes <= 0:
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
        if not self.table.row_count or not self.filtered:
            return
        row_index = getattr(self.table, "cursor_row", 0)
        rom = self.filtered[row_index]
        key = rom["_key"]
        if key in self.selected:
            self.selected.remove(key)
        else:
            self.selected.add(key)
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
            if rom["_key"] not in self.selected:
                continue
            providers = rom.get("_providers") or []
            provider_rom = providers[0]["rom"] if providers else None
            torrent = None
            http_url = None
            if provider_rom:
                torrent = provider_rom.get("torrent_url") or provider_rom.get("torrent")
                http_url = provider_rom.get("http_url")
            else:
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
                size_bytes=rom.get("_size_bytes"),
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
