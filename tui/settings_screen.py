from textual.app import ComposeResult
from textual.widgets import Header, Footer, DataTable, Static
from textual.containers import Container
from textual.screen import Screen
from textual import events

from core.providers import (
    export_roms_to_json,
    list_providers_with_status,
    remove_provider,
    validate_providers_schema,
)
from utils.fetch_metadata import fetch_console_metadata
from .message_screen import MessageScreen
from .provider_form_screen import ProviderFormScreen


class SettingsScreen(Screen):
    """Provider management panel."""

    CSS_PATH = "styles/update_screen.css"
    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "refresh", "Refresh"),
        ("f", "fetch_assets", "Fetch Assets"),
        ("e", "export_roms", "Export ROM JSON"),
        ("v", "validate_providers", "Validate Providers"),
        ("a", "add_provider", "Add Provider"),
        ("d", "remove_provider", "Remove Provider"),
    ]

    def compose(self) -> ComposeResult:
        self.label = Static(
            "[b]Providers[/b]\nUse ↑/↓ to select, [f] fetch assets, [e] export ROM list, [v] validate schema.",
            id="panel_status",
        )
        yield Header()
        self.table = DataTable(id="provider_table")
        yield Container(self.label, self.table, id="panel_container")
        yield Footer()

    def on_mount(self) -> None:
        self.table.add_columns(
            "Manufacturer",
            "Console",
            "Extensions",
            "Metadata",
            "Listings",
            "ROM JSON",
            "Torrent",
        )
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.table.focus()
        self.refresh_providers()

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def refresh_providers(self) -> None:
        self.providers = list_providers_with_status()
        self.table.clear()
        if not self.providers:
            self.table.add_row("—", "No providers found in providers.json", "", "", "", "", "")
            self.table.cursor_row = 0
            self._notify("No providers defined in providers.json", severity="warning")
            return

        for provider in self.providers:
            status = provider["status"]
            extensions = ", ".join(provider["rom_extensions"]) or "—"
            self.table.add_row(
                provider["manufacturer"],
                provider["console"],
                extensions,
                self._status_icon(status.get("metadata")),
                self._status_icon(status.get("listings")),
                self._status_icon(status.get("rom_json")),
                self._status_icon(status.get("torrent")),
            )
        self._notify(f"Loaded {len(self.providers)} provider(s).", severity="debug")

    def _status_icon(self, value: bool) -> str:
        return "✅" if value else "—"

    def _get_selected_provider(self):
        if not getattr(self, "providers", None):
            return None
        row = getattr(self.table, "cursor_row", 0)
        row = max(0, min(row, len(self.providers) - 1))
        return self.providers[row]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh(self) -> None:
        self.refresh_providers()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_fetch_assets(self) -> None:
        provider = self._get_selected_provider()
        if not provider:
            self.app.bell()
            return
        manufacturer = provider["manufacturer"]
        console = provider["console"]
        try:
            summary = fetch_console_metadata(console=console, manufacturer=manufacturer)
            self._notify(
                f"Fetched assets for {manufacturer}/{console}: {', '.join(summary.keys())}",
                severity="success",
            )
        except Exception as exc:
            self._notify(f"Fetch failed: {exc}", severity="error")
            self.app.push_screen(MessageScreen("Fetch Error", str(exc)))
            return
        self.refresh_providers()

    def action_export_roms(self) -> None:
        provider = self._get_selected_provider()
        if not provider:
            self.app.bell()
            return
        manufacturer = provider["manufacturer"]
        console = provider["console"]
        entry = provider["entry"]
        try:
            roms, json_path = export_roms_to_json(manufacturer, console, entry)
            self._notify(
                f"Exported {len(roms)} ROM entries to {json_path}",
                severity="success",
            )
        except Exception as exc:
            self._notify(f"Export failed: {exc}", severity="error")
            self.app.push_screen(MessageScreen("Export Error", str(exc)))
            return
        self.refresh_providers()

    def action_validate_providers(self) -> None:
        ok, issues = validate_providers_schema()
        if ok:
            self._notify("providers.json passes schema validation.", severity="success")
            return
        messages = [
            f"{'/'.join(str(p) for p in issue['path']) or '<root>'}: {issue['message']}"
            for issue in issues
        ]
        detail = "\n".join(messages[:10])
        if len(messages) > 10:
            detail += f"\n… {len(messages) - 10} more issue(s)"
        self._notify("providers.json has validation errors.", severity="error")
        self.app.push_screen(MessageScreen("Validation Errors", detail))

    def action_add_provider(self) -> None:
        def _done():
            self.refresh_providers()

        self.app.push_screen(ProviderFormScreen(on_save=_done))

    def action_remove_provider(self) -> None:
        provider = self._get_selected_provider()
        if not provider:
            self.app.bell()
            return
        manufacturer = provider["manufacturer"]
        console = provider["console"]
        try:
            remove_provider(manufacturer, console, remove_cache=True)
            self._notify(f"Removed provider {manufacturer}/{console} and purged cache.", severity="warning")
        except Exception as exc:
            self._notify(f"Remove failed: {exc}", severity="error")
            self.app.push_screen(MessageScreen("Remove Provider Failed", str(exc)))
            return
        self.refresh_providers()

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            self.action_fetch_assets()
            event.stop()

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _notify(self, message: str, severity: str = "info") -> None:
        app = getattr(self, "app", None)
        if app and hasattr(app, "notify"):
            app.notify(message, severity=severity)
        else:
            print(f"[{severity.upper()}] {message}")
