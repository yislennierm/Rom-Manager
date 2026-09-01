import os
import shutil
from pathlib import Path
from typing import Dict, List, Set

from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, Input, DataTable
from textual.containers import Container
from textual.screen import Screen

from utils.catalog import (
    build_rom_catalog,
    provider_download_size,
    provider_download_source,
    select_preferred_provider,
)
from utils.paths import manufacturer_slug, console_slug, list_cached_consoles, provider_slug as slugify_provider
from utils.library_sync import load_modules
from data.storage.storage_config_loader import load_storage_config
from .download_manager_screen import DownloadManagerScreen
from .message_screen import MessageScreen
from .rom_detail_screen import ROMDetailScreen

MAX_BATCH_DOWNLOAD_JOBS = 50
MIN_FREE_AFTER_QUEUE_BYTES = 512 * 1024 * 1024


class GlobalSearchScreen(Screen):
    """Search across all synced library consoles."""

    CSS_PATH = "styles/update_screen.css"

    BINDINGS = [
        ("/", "focus_search", "Search"),
        ("space", "toggle_selection", "Select ROM"),
        ("enter", "show_details", "Details"),
        ("a", "queue_jobs", "Queue Download"),
        ("c", "queue_all", "Download Filter"),
        ("escape", "go_back", "Back"),
        ("backspace", "go_back", "Back"),
    ]

    def __init__(self):
        super().__init__(id="global_search_screen")
        self.roms: List[Dict] = []
        self.filtered: List[Dict] = []
        self.selected: Set[str] = set()
        self.artwork_provider = "libretro"
        self.module_lookup: Dict[str, Dict[str, str]] = {}
        self.runtime_core_ids: List[str] = []

    def compose(self) -> ComposeResult:
        self.label = Static("Global ROM Search", id="panel_status")
        yield Header()
        self.search_input = Input(placeholder="Search all consoles...", id="search")
        self.table = DataTable(id="global_rom_table")
        yield Container(self.label, self.search_input, self.table, id="panel_container")
        yield Footer()

    def on_mount(self) -> None:
        self.table = self.table
        self.search_input = self.search_input
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
        self.module_lookup = self._build_module_lookup()
        self.runtime_core_ids = self._active_runtime_core_ids()
        consoles = list_cached_consoles()
        if not consoles:
            self._notify("No synced library consoles with RDB exports. Use Updates > Sync Account.", severity="warning")
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
        current_row = getattr(self.table, "cursor_row", 0)
        self.display_roms(self.filtered, cursor_row=current_row)
        self.label.update(f"Global ROM Search — {len(self.filtered)}/{len(self.roms)} match '{query}'")

    def display_roms(self, roms: List[Dict], cursor_row: int | None = None) -> None:
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
        self._restore_cursor(cursor_row)

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
        self.display_roms(self.filtered, cursor_row=row_index)
        self._notify(f"Selected {len(self.selected)} ROM(s)", severity="debug")

    def _queue_jobs(self):
        if not self.selected:
            self.app.bell()
            self._notify("No ROMs selected.", severity="warning")
            return

        guard = self._download_batch_guard()
        if guard:
            self.app.bell()
            self.app.push_screen(MessageScreen("Download Blocked", guard))
            self._notify("Download batch blocked by size/job guard.", severity="warning")
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
            compatible_providers = self._compatible_provider_records(rom, providers)
            provider_entry = self._select_runtime_provider(rom, compatible_providers) if compatible_providers else {}
            provider_rom = provider_entry.get("rom")
            metadata = provider_entry.get("metadata") or {}
            if provider_rom:
                torrent, http_url = provider_download_source(provider_rom)
            else:
                torrent, http_url = provider_download_source(rom)
            if not torrent and not http_url:
                continue

            provider_manufacturer = metadata.get("manufacturer") or (provider_rom.get("manufacturer") if provider_rom else rom.get("manufacturer", "Unknown"))
            provider_console = metadata.get("console") or (provider_rom.get("console") if provider_rom else rom.get("console", "Unknown"))
            cache_manufacturer = provider_manufacturer
            cache_console = provider_console
            guid = metadata.get("libretro_guid") or (provider_rom or {}).get("libretro_guid") or (provider_rom or {}).get("guid")
            if guid and guid in self.module_lookup:
                canonical = self.module_lookup[guid]
                provider_manufacturer = canonical.get("manufacturer") or provider_manufacturer
                provider_console = canonical.get("console") or provider_console
            archive_id = metadata.get("archive_id")
            provider_slug_value = provider_entry.get("provider_id") or archive_id
            if provider_slug_value:
                provider_slug_value = slugify_provider(provider_slug_value)
            target_segments = [
                "downloads",
                manufacturer_slug(provider_manufacturer),
                console_slug(provider_console),
            ]
            if archive_id:
                target_segments.append(archive_id)
            destination = os.path.join(*target_segments)
            rom_filename = (provider_rom.get("name") if provider_rom else None) or rom["name"]
            if provider_rom and provider_rom.get("_archive_member") and provider_rom.get("_source_bundle"):
                rom_filename = provider_rom.get("_source_bundle")
            download_size = provider_download_size(provider_rom, rom.get("_size_bytes"))
            download_md5 = (provider_rom or {}).get("md5") or rom.get("md5")
            job = None
            if torrent:
                job = manager.add_job(
                    rom_name=rom_filename,
                    source=torrent,
                    http_url=None,
                    destination=destination,
                    console=provider_console,
                    manufacturer=provider_manufacturer,
                    size_bytes=download_size,
                    md5=download_md5,
                    provider_slug=provider_slug_value,
                    cache_manufacturer=cache_manufacturer,
                    cache_console=cache_console,
                    auto_install=True,
                    archive_member_path=(provider_rom or {}).get("_archive_member_path"),
                )
                if job.get("status") == "not_found" and http_url:
                    manager.remove_job(job["id"])
                    job = None
            if job is None and http_url:
                job = manager.add_job(
                    rom_name=rom_filename,
                    source=None,
                    http_url=http_url,
                    destination=destination,
                    console=provider_console,
                    manufacturer=provider_manufacturer,
                    size_bytes=download_size,
                    md5=download_md5,
                    provider_slug=provider_slug_value,
                    cache_manufacturer=cache_manufacturer,
                    cache_console=cache_console,
                    auto_install=True,
                    archive_member_path=(provider_rom or {}).get("_archive_member_path"),
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
        self.selected.clear()

    def _download_batch_guard(self) -> str | None:
        selected = [rom for rom in self.roms if rom["_key"] in self.selected]
        if not selected:
            return None
        if len(selected) > MAX_BATCH_DOWNLOAD_JOBS:
            return (
                f"{len(selected)} ROMs are selected. Narrow the filter or select up to "
                f"{MAX_BATCH_DOWNLOAD_JOBS} ROMs at a time before queueing downloads."
            )

        required = 0
        unknown = 0
        for rom in selected:
            providers = rom.get("_providers") or []
            compatible_providers = self._compatible_provider_records(rom, providers)
            provider_entry = self._select_runtime_provider(rom, compatible_providers) if compatible_providers else {}
            provider_rom = provider_entry.get("rom") if provider_entry else {}
            size = provider_download_size(provider_rom, rom.get("_size_bytes"))
            try:
                required += int(size)
            except (TypeError, ValueError):
                unknown += 1

        if required:
            usage = shutil.disk_usage(Path("downloads").resolve())
            if required + MIN_FREE_AFTER_QUEUE_BYTES > usage.free:
                return (
                    f"Selected downloads need about {self._format_size(required)}, but only "
                    f"{self._format_size(usage.free)} is free. Keep at least "
                    f"{self._format_size(MIN_FREE_AFTER_QUEUE_BYTES)} free after queueing."
                )
        if unknown and len(selected) > 10:
            return (
                f"{unknown} selected ROMs have unknown size. Queue 10 or fewer unknown-size "
                "downloads at a time."
            )
        return None

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

    def action_queue_all(self) -> None:
        target = self.filtered if (self.search_input.value or "").strip() else self.roms
        if not target:
            self.app.bell()
            self._notify("No ROMs available for download.", severity="warning")
            return
        self.selected = {rom["_key"] for rom in target}
        self.display_roms(self.filtered, cursor_row=0)
        self._queue_jobs()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _notify(self, message: str, severity: str = "info") -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            self.log(f"[{severity.upper()}] {message}")

    def _restore_cursor(self, requested_row: int | None) -> None:
        if not self.table.row_count:
            return
        if requested_row is None:
            requested_row = getattr(self.table, "cursor_row", 0)
        requested_row = max(0, min(requested_row or 0, self.table.row_count - 1))
        current_column = getattr(self.table, "cursor_column", 0)
        try:
            self.table.cursor_coordinate = (requested_row, current_column)
        except AttributeError:
            pass

    def _build_module_lookup(self) -> Dict[str, Dict[str, str]]:
        lookup: Dict[str, Dict[str, str]] = {}
        for module in load_modules():
            guid = module.get("guid")
            if not guid:
                continue
            manufacturer, console = self._split_module_name(module.get("name"))
            lookup[guid] = {"manufacturer": manufacturer, "console": console}
        return lookup

    def _compatible_provider_records(self, rom: Dict, providers: List[Dict]) -> List[Dict]:
        if not self._is_arcade_runtime_sensitive(rom):
            return providers
        if not self.runtime_core_ids:
            return []
        compatible = []
        for provider in providers:
            metadata = provider.get("metadata") or {}
            cores = metadata.get("compatible_cores") or metadata.get("preferred_cores") or []
            if not cores and not metadata.get("arcade_family"):
                continue
            if any(core_id in cores for core_id in self.runtime_core_ids):
                compatible.append(provider)
        return compatible

    def _select_runtime_provider(self, rom: Dict, providers: List[Dict]):
        if not providers:
            return None
        if not self._is_arcade_runtime_sensitive(rom):
            return select_preferred_provider(providers)
        ranked = sorted(providers, key=self._provider_runtime_rank)
        best_rank = self._provider_runtime_rank(ranked[0])
        best_group = [provider for provider in ranked if self._provider_runtime_rank(provider) == best_rank]
        return select_preferred_provider(best_group) or best_group[0]

    def _provider_runtime_rank(self, provider: Dict) -> int:
        family = str((provider.get("metadata") or {}).get("arcade_family") or "")
        core_id = self._best_provider_core_id(provider) or ""
        if core_id == "fbneo":
            return {"fbneo": 0}.get(family, 50)
        if core_id == "mame2003_plus":
            return {"mame_legacy": 0, "mame_current": 20}.get(family, 50)
        if core_id == "mame":
            return {"mame_current": 0, "mame_legacy": 10}.get(family, 50)
        return 50

    def _best_provider_core_id(self, provider: Dict) -> str | None:
        metadata = provider.get("metadata") or {}
        cores = metadata.get("compatible_cores") or metadata.get("preferred_cores") or []
        for core_id in self.runtime_core_ids:
            if core_id in cores:
                return core_id
        return None

    @staticmethod
    def _is_arcade_runtime_sensitive(rom: Dict) -> bool:
        return (rom.get("manufacturer"), rom.get("console")) == ("SNK", "Neo Geo")

    @staticmethod
    def _active_runtime_core_ids() -> List[str]:
        frontends = (load_storage_config() or {}).get("frontends") or {}
        frontend = next((entry for entry in frontends.values() if entry.get("active")), None)
        if frontend is None:
            frontend = next(iter(frontends.values()), {}) if frontends else {}
        cores_root = Path(frontend.get("cores_path") or "").expanduser()
        installed = []
        for core_id in ("fbneo", "mame2003_plus", "mame"):
            if (cores_root / f"{core_id}_libretro.so").exists():
                installed.append(core_id)
        return installed

    def _split_module_name(self, name: str | None) -> tuple[str, str]:
        if not name:
            return ("Unknown", "Unknown")
        parts = [segment.strip() for segment in name.split("-", 1)]
        if len(parts) == 2:
            return parts[0], parts[1]
        return (parts[0], parts[-1])
