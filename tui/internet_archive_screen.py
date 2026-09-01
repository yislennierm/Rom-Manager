from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from utils.internet_archive_auth import ia_auth_status


class InternetArchiveScreen(Screen):
    """Local Internet Archive credential status."""

    CSS_PATH = "styles/update_screen.css"
    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("escape", "go_back", "Back"),
        ("backspace", "go_back", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        self.status = Static("", id="panel_status")
        yield Container(self.status, id="panel_container")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()

    def action_refresh(self) -> None:
        self._refresh()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _refresh(self) -> None:
        status = ia_auth_status()
        configured = "Configured" if status.get("configured") else "Not configured"
        config_state = "found" if status.get("config_found") else "not found"
        message = (
            "[b]Internet Archive[/b]\n"
            f"Status: {configured}\n"
            f"Official IA config: {config_state}\n\n"
            "Use the official Internet Archive CLI so ROMs Manager can reuse your local login cookies:\n\n"
            "  .venv/bin/python -m pip install internetarchive\n"
            "  .venv/bin/ia configure\n\n"
            "ROMs Manager reads the local IA cookie config for archive.org downloads only. "
            "It does not store your Internet Archive password, and it does not send IA credentials to the backend."
        )
        self.status.update(message)
