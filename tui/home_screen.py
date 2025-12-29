from __future__ import annotations

import os
from datetime import datetime
from typing import Dict

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Static, Tabs, Tab

from utils.backend_client import (
    load_modules_local_metadata,
    load_providers_local_metadata,
    load_roms_local_metadata,
    get_backend_base,
)
from utils.versioning import needs_branch_update, get_local_version
from .menu_screen import MenuScreen
from .download_manager_screen import DownloadManagerScreen
from .update_screen import UpdateScreen
from .global_search_screen import GlobalSearchScreen
from .console_select_screen import ConsoleSelectScreen
from .settings_screen import SettingsScreen


class HomeScreen(Screen):
    """Dashboard-style home screen with quick stats and shortcuts."""

    CSS_PATH = "styles/home_screen.css"

    BINDINGS = [
        ("1", "open_rom_explorer", "ROM Explorer"),
        ("2", "open_downloads", "Download Manager"),
        ("3", "open_settings", "Settings"),
        ("4", "open_update", "Update"),
        ("m", "open_menu", "Menu"),
        ("s", "open_search", "Search"),
        ("escape", "app.quit", "Exit"),
        ("backspace", "app.quit", "Exit"),
    ]

    def __init__(self) -> None:
        super().__init__(id="home_screen")
        self.stats: Dict[str, str] = {}
        self.update_branch = os.environ.get("ROMS_UPDATE_BRANCH", "stage")
        self.version = get_local_version()

    def compose(self) -> ComposeResult:
        self.top_info = Static(id="home_top")
        tabs = Tabs(
            Tab("Home", id="tab-home"),
            Tab("Menu", id="tab-menu"),
            Tab("Explorer", id="tab-explorer"),
            Tab("Downloads", id="tab-downloads"),
            Tab("Settings", id="tab-settings"),
            id="home_tabs",
        )
        tabs.can_focus = False
        self.summary = Static(id="home_summary")
        self.actions = Static(id="home_actions")
        yield Horizontal(self.top_info, tabs, id="home_header")
        yield Container(
            Horizontal(self.summary, self.actions, id="home_content"),
            id="home_container",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_stats()
        self.render_actions()

    def refresh_stats(self) -> None:
        backend = get_backend_base()
        modules_meta = load_modules_local_metadata() or {}
        providers_meta = load_providers_local_metadata() or {}
        roms_meta = load_roms_local_metadata() or {}

        def _fmt_ts(value):
            if not value:
                return "Never"
            try:
                dt = datetime.fromisoformat(value)
                return dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                return str(value)

        try:
            branch_update = needs_branch_update(self.update_branch)
            branch_status = (
                f"{self.update_branch} has updates" if branch_update else f"{self.update_branch} up-to-date"
            )
        except Exception:
            branch_status = f"{self.update_branch} status unknown"

        lines = [
            f"[b]Backend:[/b] {backend}",
            f"[b]Branch:[/b] {branch_status}",
            f"[b]Modules:[/b] {modules_meta.get('count') or 0} (fetched { _fmt_ts(modules_meta.get('fetched_at')) })",
            f"[b]Providers:[/b] {providers_meta.get('count') or 0} (fetched { _fmt_ts(providers_meta.get('fetched_at')) })",
            f"[b]ROM datasets:[/b] {roms_meta.get('count') or 0} (fetched { _fmt_ts(roms_meta.get('fetched_at')) })",
        ]
        self.summary.update("\n".join(lines))
        self.top_info.update(f"↪ ROMs Manager  v{self.version}")

    def render_actions(self) -> None:
        actions = [
            "[b]1.[/b] ROM Explorer",
            "[b]2.[/b] Download Manager",
            "[b]3.[/b] Settings",
            "[b]4.[/b] Update",
            "[b]s.[/b] Global Search",
            "[b]m.[/b] Main Menu",
            "[b]Esc.[/b] Exit",
        ]
        self.actions.update("\n".join(actions))

    # Actions
    def action_open_rom_explorer(self) -> None:
        # Use console selector to match existing flow
        self.app.push_screen(ConsoleSelectScreen())

    def action_open_downloads(self) -> None:
        self.app.push_screen(DownloadManagerScreen())

    def action_open_settings(self) -> None:
        self.app.push_screen(SettingsScreen())

    def action_open_update(self) -> None:
        self.app.push_screen(UpdateScreen())

    def action_open_menu(self) -> None:
        self.app.push_screen(MenuScreen("Main Menu"))

    def action_open_search(self) -> None:
        self.app.push_screen(GlobalSearchScreen())

    def on_tabs_changed(self, event: Tabs.Changed) -> None:
        tab_id = event.tab.id
        if tab_id == "tab-menu":
            self.app.push_screen(MenuScreen("Main Menu"))
        elif tab_id == "tab-explorer":
            self.app.push_screen(ConsoleSelectScreen())
        elif tab_id == "tab-downloads":
            self.app.push_screen(DownloadManagerScreen())
        elif tab_id == "tab-settings":
            self.app.push_screen(SettingsScreen())
        else:
            return
