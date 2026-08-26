import os
from pathlib import Path
from typing import Dict, List, Set

from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, Input, DataTable
from textual.containers import Container
from textual.screen import Screen

from utils.catalog import build_rom_catalog, resolve_module, select_preferred_provider
from utils.library_sync import load_modules
from utils.paths import manufacturer_slug, console_slug, provider_slug as slugify_provider
from data.storage.storage_config_loader import load_storage_config

from .message_screen import MessageScreen
from .download_manager_screen import DownloadManagerScreen
from .rom_detail_screen import ROMDetailScreen


DEFAULT_MANUFACTURER = "Sega"
DEFAULT_CONSOLE = "Dreamcast"


class ROMExplorerScreen(Screen):
    """Browse ROMs for the currently selected console (RDB-first)."""

    CSS_PATH = "styles/update_screen.css"

    BINDINGS = [
        ("/", "focus_search", "Search"),
        ("space", "toggle_selection", "Select ROM"),
        ("A", "select_all", "Select All"),
        ("N", "select_none", "Select None"),
        ("enter", "show_details", "Details"),
        ("a", "queue_jobs", "Queue Download"),
        ("c", "queue_all", "Download Filter"),
        ("escape", "go_back", "Back"),
        ("backspace", "go_back", "Back"),
    ]

    def __init__(self, manufacturer=None, console=None, roms_path=None, module_guid=None):
        super().__init__(id="rom_explorer_screen")
        self._initial_manufacturer = manufacturer
        self._initial_console = console
        self._explicit_roms_path = roms_path
        self._explicit_guid = module_guid
        self.roms: List[Dict] = []
        self.filtered: List[Dict] = []
        self.selected_keys: Set[str] = set()
        self.artwork_provider = "libretro"
        self._provider_total = 0
        self.rdb_entry_count = 0
        self.rdb_path: str | None = None
        self.module_guid: str | None = None
        self.module_lookup: Dict[str, Dict[str, str]] = {}
        self.provider_catalogs: List[Dict] = []
        self.runtime_core_id: str | None = None

    def compose(self) -> ComposeResult:
        self.label = Static("", id="panel_status")
        yield Header()
        self.search_input = Input(placeholder="Type to search...", id="search")
        self.table = DataTable(id="rom_table")
        yield Container(self.label, self.search_input, self.table, id="panel_container")
        yield Footer()

    def on_mount(self) -> None:
        app = getattr(self, "app", None)

        manufacturer = self._initial_manufacturer or getattr(app, "current_manufacturer", DEFAULT_MANUFACTURER)
        console = self._initial_console or getattr(app, "current_console", DEFAULT_CONSOLE)
        module_guid = self._explicit_guid or getattr(app, "current_module_guid", None)
        rdb_path = self._explicit_roms_path or getattr(app, "current_roms_path", None)

        module = resolve_module(manufacturer, console, module_guid)
        if module:
            module_guid = module.get("guid")

        self.module_guid = module_guid

        table = self.table
        self.manager = getattr(app, "download_manager", None)

        table.clear()
        table.add_column("Sel.", width=4)
        table.add_column("Name", width=60)
        table.add_column("Region", width=8)
        table.add_column("Size", width=8)
        table.add_column("Play", width=18)
        table.add_column("Providers", width=23)
        table.add_column("MD5", width=36)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.focus()

        if self.manager is None:
            self._notify("Download manager instance unavailable.", severity="error")
            self.app.push_screen(MessageScreen("Error", "Download manager is not available."))
            return
        self.module_lookup = self._build_module_lookup()

        try:
            catalog = build_rom_catalog(
                manufacturer,
                console,
                module_guid=module_guid,
                rdb_path=rdb_path,
            )
        except FileNotFoundError:
            message = (
                f"No RDB export found for {manufacturer}/{console}.\n"
                "Use Updates > Sync Account to download backend ROM catalogs. Database export is an advanced local fallback."
            )
            self._notify(message, severity="warning")
            self.app.push_screen(MessageScreen("Missing RDB", message))
            return
        except Exception as exc:
            self._notify(f"Failed to build catalog: {exc}", severity="error")
            self.app.push_screen(MessageScreen("Error", f"Unable to load catalog: {exc}"))
            return

        self.rdb_path = catalog["rdb_path"]
        self.rdb_entry_count = catalog["entry_count"]
        self._provider_total = catalog["provider_total"]
        self.provider_catalogs = catalog.get("provider_catalogs") or []
        self.roms = catalog["roms"]
        self.filtered = self.roms

        self.manufacturer = manufacturer
        self.console = console
        self.runtime_core_id = self._active_runtime_core_id()

        if app is not None:
            app.current_manufacturer = manufacturer
            app.current_console = console
            app.current_roms_path = self.rdb_path
            app.current_manufacturer_slug = manufacturer_slug(manufacturer)
            app.current_console_slug = console_slug(console)
            app.current_module_guid = module_guid

        provider_info = (
            f"{self._provider_total} provider cache(s)"
            if self._provider_total
            else "no provider caches"
        )
        self.label.update(
            f"RDB — {manufacturer} / {console} · {self.rdb_entry_count} entries · {provider_info}"
            f"{self._runtime_suffix()}"
        )
        self.apply_filter(announce=False)
        self._notify(
            f"Explorer ready for {manufacturer} / {console} ({self.rdb_entry_count} entries, {provider_info}).",
            severity="info",
        )

    # ------------------------------------------------------------------
    # Table helpers
    # ------------------------------------------------------------------

    def apply_filter(self, announce: bool = True) -> None:
        query = (self.search_input.value or "").lower().strip()
        if not query:
            filtered = self.roms
        else:
            tokens = query.split()
            filtered = [
                rom for rom in self.roms
                if all(token in rom["_search_blob"] for token in tokens)
            ]
        self.filtered = filtered
        current_row = getattr(self.table, "cursor_row", 0)
        self.display_roms(filtered, cursor_row=current_row)
        if announce:
            self._notify(f"Filter applied — {len(filtered)}/{len(self.roms)} match '{query}'", severity="debug")

    def display_roms(self, roms: List[Dict], cursor_row: int | None = None) -> None:
        self.table.clear()
        for rom in roms:
            mark = "[*]" if rom["_key"] in self.selected_keys else "[ ]"
            providers_cell = self._format_provider_cell(rom)
            self.table.add_row(
                mark,
                rom["name"],
                rom.get("region", "—"),
                self._format_size(rom.get("_size_bytes")),
                self._format_compat_cell(rom),
                providers_cell,
                rom.get("md5") or "—",
            )
        self._restore_cursor(cursor_row)

    def _format_provider_cell(self, rom: Dict) -> str:
        total = self._provider_total
        count = rom.get("_provider_count", 0)
        if total:
            labels = ", ".join(rom["_provider_labels"][:2])
            if len(rom["_provider_labels"]) > 2:
                labels += ", …"
            suffix = f" ({labels})" if labels else ""
            return f"{count}/{total}{suffix}"
        if count:
            labels = ", ".join(rom["_provider_labels"])
            return f"{count}{(' (' + labels + ')') if labels else ''}"
        return "0"

    def _format_compat_cell(self, rom: Dict) -> str:
        providers = rom.get("_providers") or []
        if not self._is_arcade_runtime_sensitive():
            return "Ready" if select_preferred_provider(providers) else "No source"
        compatible = self._compatible_provider_records(providers)
        if compatible:
            families = sorted({
                str((provider.get("metadata") or {}).get("arcade_family") or "arcade")
                for provider in compatible
            })
            label = self.runtime_core_id or "core"
            return f"{label}: {len(compatible)}"
        if providers:
            return "Core mismatch"
        return "No source"

    def _runtime_suffix(self) -> str:
        if not self._is_arcade_runtime_sensitive():
            return ""
        core = self.runtime_core_id or "none"
        return f" · arcade profile: {core}"

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

    # ------------------------------------------------------------------
    # Selection & jobs
    # ------------------------------------------------------------------

    def _toggle_selection(self) -> None:
        if not self.table.row_count or not self.filtered:
            return
        row_index = getattr(self.table, "cursor_row", 0)
        row_index = max(0, min(row_index, len(self.filtered) - 1))
        rom = self.filtered[row_index]
        key = rom["_key"]
        if key in self.selected_keys:
            self.selected_keys.remove(key)
        else:
            self.selected_keys.add(key)
        self.display_roms(self.filtered, cursor_row=row_index)
        self._notify(f"Selected {len(self.selected_keys)} ROM(s)", severity="debug")

    def _select_all_filtered(self) -> None:
        if not self.filtered:
            self.app.bell()
            self._notify("No ROMs in the current filter.", severity="warning")
            return
        self.selected_keys.update(rom["_key"] for rom in self.filtered)
        row_index = getattr(self.table, "cursor_row", 0)
        self.display_roms(self.filtered, cursor_row=row_index)
        self._notify(f"Selected {len(self.selected_keys)} ROM(s).", severity="info")

    def _select_none(self) -> None:
        if not self.selected_keys:
            self._notify("No ROMs selected.", severity="debug")
            return
        self.selected_keys.clear()
        row_index = getattr(self.table, "cursor_row", 0)
        self.display_roms(self.filtered, cursor_row=row_index)
        self._notify("Cleared ROM selection.", severity="info")

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

    def _create_jobs(self) -> None:
        jobs_created = 0
        missing_sources = 0
        incompatible_sources = 0
        existing_count = 0
        for rom in self.roms:
            if rom["_key"] not in self.selected_keys:
                continue
            providers = rom.get("_providers") or []
            if not providers:
                missing_sources += 1
                continue
            compatible_providers = self._compatible_provider_records(providers)
            if providers and not compatible_providers:
                incompatible_sources += 1
                continue
            provider_entry = self._select_runtime_provider(compatible_providers)
            if not provider_entry:
                missing_sources += 1
                continue
            preferred = provider_entry["rom"]
            metadata = provider_entry.get("metadata") or {}
            torrent = preferred.get("torrent_url") or preferred.get("torrent")
            http_url = preferred.get("http_url")
            if not torrent and not http_url:
                missing_sources += 1
                continue

            provider_manufacturer = metadata.get("manufacturer") or preferred.get("manufacturer") or rom.get("manufacturer") or self.manufacturer
            provider_console = metadata.get("console") or preferred.get("console") or rom.get("console") or self.console
            cache_manufacturer = provider_manufacturer
            cache_console = provider_console
            guid = metadata.get("libretro_guid") or preferred.get("libretro_guid") or preferred.get("guid")
            if guid:
                canonical = self.module_lookup.get(guid)
                if canonical:
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
            target_dir = os.path.join(*target_segments)

            rom_filename = preferred.get("name") or rom["name"]
            if preferred.get("_archive_member") and preferred.get("_source_bundle"):
                rom_filename = preferred.get("_source_bundle")

            job = None
            if torrent:
                job = self.manager.add_job(
                    rom_name=rom_filename,
                    source=torrent,
                    http_url=None,
                    destination=target_dir,
                    console=provider_console,
                    manufacturer=provider_manufacturer,
                    size_bytes=rom.get("_size_bytes"),
                    md5=rom.get("md5"),
                    provider_slug=provider_slug_value,
                    cache_manufacturer=cache_manufacturer,
                    cache_console=cache_console,
                    auto_install=True,
                    archive_member_path=preferred.get("_archive_member_path"),
                )
                if job.get("status") == "not_found" and http_url:
                    self.manager.remove_job(job["id"])
                    job = None
            if job is None and http_url:
                job = self.manager.add_job(
                    rom_name=rom_filename,
                    source=None,
                    http_url=http_url,
                    destination=target_dir,
                    console=provider_console,
                    manufacturer=provider_manufacturer,
                    size_bytes=rom.get("_size_bytes"),
                    md5=rom.get("md5"),
                    provider_slug=provider_slug_value,
                    cache_manufacturer=cache_manufacturer,
                    cache_console=cache_console,
                    auto_install=True,
                    archive_member_path=preferred.get("_archive_member_path"),
                )
            if job.get("protocol") == "local" and job.get("status") == "completed":
                existing_count += 1
            else:
                jobs_created += 1

        if not jobs_created and not existing_count and missing_sources and not self._is_arcade_runtime_sensitive():
            fallback = self._queue_provider_collection()
            if fallback:
                jobs_created += 1
                missing_sources = 0

        if jobs_created:
            self.app.push_screen(DownloadManagerScreen())
            self._notify(f"Created {jobs_created} download job(s) for {self.console}", severity="info")
        elif existing_count:
            message = f"{existing_count} ROM(s) already present in your library."
            self._notify(message, severity="info")
            self.app.push_screen(MessageScreen("Already Downloaded", message))
        else:
            note = "No available download source for the selected ROMs."
            if missing_sources:
                note += f" ({missing_sources} selection(s) lack provider data.)"
            if incompatible_sources:
                core = self.runtime_core_id or "active core"
                note += f" ({incompatible_sources} selection(s) have providers, but not for {core}.)"
            self.app.bell()
            self.app.push_screen(MessageScreen("Info", note))
            self._notify("No download source found for selected ROMs", severity="warning")
        self.selected_keys.clear()

    def _compatible_provider_records(self, providers: List[Dict]) -> List[Dict]:
        if not self._is_arcade_runtime_sensitive():
            return providers
        core_id = self.runtime_core_id
        if not core_id:
            return []
        compatible = []
        for provider in providers:
            metadata = provider.get("metadata") or {}
            cores = metadata.get("compatible_cores") or metadata.get("preferred_cores") or []
            if not cores and not metadata.get("arcade_family"):
                continue
            if core_id in cores:
                compatible.append(provider)
        return compatible

    def _select_runtime_provider(self, providers: List[Dict]):
        if not providers:
            return None
        if not self._is_arcade_runtime_sensitive():
            return select_preferred_provider(providers)
        ranked = sorted(providers, key=self._provider_runtime_rank)
        best_rank = self._provider_runtime_rank(ranked[0])
        best_group = [provider for provider in ranked if self._provider_runtime_rank(provider) == best_rank]
        return select_preferred_provider(best_group) or best_group[0]

    def _provider_runtime_rank(self, provider: Dict) -> int:
        family = str((provider.get("metadata") or {}).get("arcade_family") or "")
        core_id = self.runtime_core_id or ""
        if core_id == "mame":
            return {"mame_current": 0, "mame_legacy": 10}.get(family, 50)
        if core_id == "mame2003_plus":
            return {"mame_legacy": 0, "mame_current": 20}.get(family, 50)
        if core_id == "fbneo":
            return {"fbneo": 0}.get(family, 50)
        return 50

    def _is_arcade_runtime_sensitive(self) -> bool:
        return (self.manufacturer, self.console) == ("SNK", "Neo Geo")

    def _active_runtime_core_id(self) -> str | None:
        if (self.manufacturer, self.console) != ("SNK", "Neo Geo"):
            return None
        frontend = self._active_frontend()
        cores_root = Path(frontend.get("cores_path") or "").expanduser()
        for core_id in ("fbneo", "mame", "mame2003_plus"):
            if (cores_root / f"{core_id}_libretro.so").exists():
                return core_id
        return None

    @staticmethod
    def _active_frontend() -> Dict:
        frontends = (load_storage_config() or {}).get("frontends") or {}
        for entry in frontends.values():
            if entry.get("active"):
                return entry
        return next(iter(frontends.values()), {}) if frontends else {}

    def _queue_provider_collection(self) -> bool:
        """Queue an archive-level provider export when per-ROM matching is unavailable."""
        for catalog in self.provider_catalogs:
            metadata = catalog.get("metadata") or {}
            provider_id = catalog.get("id") or metadata.get("archive_id")
            for provider_rom in catalog.get("roms") or []:
                http_url = provider_rom.get("http_url")
                torrent = provider_rom.get("torrent_url") or provider_rom.get("torrent")
                if not http_url and not torrent:
                    continue

                provider_manufacturer = metadata.get("manufacturer") or provider_rom.get("manufacturer") or self.manufacturer
                provider_console = metadata.get("console") or provider_rom.get("console") or self.console
                cache_manufacturer = provider_manufacturer
                cache_console = provider_console
                guid = metadata.get("libretro_guid") or provider_rom.get("libretro_guid") or provider_rom.get("guid")
                if guid:
                    canonical = self.module_lookup.get(guid)
                    if canonical:
                        provider_manufacturer = canonical.get("manufacturer") or provider_manufacturer
                        provider_console = canonical.get("console") or provider_console
                archive_id = metadata.get("archive_id")
                provider_slug_value = slugify_provider(provider_id or archive_id)
                target_segments = [
                    "downloads",
                    manufacturer_slug(provider_manufacturer),
                    console_slug(provider_console),
                ]
                if archive_id:
                    target_segments.append(archive_id)
                target_dir = os.path.join(*target_segments)

                job = self.manager.add_job(
                    rom_name=provider_rom.get("name") or archive_id or "provider_collection.zip",
                    source=None if http_url else torrent,
                    http_url=http_url,
                    destination=target_dir,
                    console=provider_console,
                    manufacturer=provider_manufacturer,
                    size_bytes=provider_rom.get("size"),
                    md5=provider_rom.get("md5"),
                    provider_slug=provider_slug_value,
                    cache_manufacturer=cache_manufacturer,
                    cache_console=cache_console,
                    auto_install=True,
                )
                self._notify(
                    f"Queued provider collection archive: {job.get('rom_name')}",
                    severity="info",
                )
                return True
        return False

    # ------------------------------------------------------------------
    # Event handlers & actions
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input is self.search_input:
            self.apply_filter()

    def action_focus_search(self) -> None:
        if hasattr(self, "search_input"):
            self.set_focus(self.search_input)

    def action_toggle_selection(self) -> None:
        self._toggle_selection()

    def action_select_all(self) -> None:
        self._select_all_filtered()

    def action_select_none(self) -> None:
        self._select_none()

    def action_show_details(self) -> None:
        self._show_details()

    def action_queue_jobs(self) -> None:
        if not self.selected_keys and self.filtered:
            self._toggle_selection()
        self._create_jobs()

    def action_queue_all(self) -> None:
        target = self.filtered if (self.search_input.value or "").strip() else self.roms
        if not target:
            self.app.bell()
            self._notify("No ROMs available for download.", severity="warning")
            return
        self.selected_keys = {rom["_key"] for rom in target}
        self.display_roms(self.filtered, cursor_row=0)
        self._create_jobs()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # ------------------------------------------------------------------
    # Notifications & details
    # ------------------------------------------------------------------

    def _notify(self, message: str, severity: str = "info") -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            self.log(f"[{severity.upper()}] {message}")

    def _build_module_lookup(self) -> Dict[str, Dict[str, str]]:
        modules = load_modules()
        lookup: Dict[str, Dict[str, str]] = {}
        for module in modules:
            guid = module.get("guid")
            if not guid:
                continue
            manufacturer, console = self._split_module_name(module.get("name"))
            lookup[guid] = {"manufacturer": manufacturer, "console": console}
        return lookup

    def _split_module_name(self, name: str | None) -> tuple[str, str]:
        if not name:
            return ("Unknown", "Unknown")
        parts = [segment.strip() for segment in name.split("-", 1)]
        if len(parts) == 2:
            return parts[0], parts[1]
        return (parts[0], parts[-1])

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
