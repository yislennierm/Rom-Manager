from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from core.revoked_access import cleanup_revoked_console, list_revoked_consoles
from .message_screen import MessageScreen


class RevokedAccessScreen(Screen):
    """Review consoles no longer assigned by the backend."""

    CSS_PATH = "styles/update_screen.css"

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "refresh", "Refresh"),
        ("k", "keep", "Keep Files"),
        ("p", "disable_playlist", "Disable Playlist"),
        ("D", "delete_local", "Delete Local"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static(
                "[b]Revoked Console Cleanup[/b]\n"
                "Backend access was removed for these consoles. "
                "Choose what to do with local RetroArch files.",
                id="label",
            ),
            DataTable(id="revoked_table"),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.table = self.query_one("#revoked_table", DataTable)
        self.table.add_column("Console", width=32)
        self.table.add_column("ROM Folder", width=58)
        self.table.add_column("Playlist", width=58)
        self.table.add_column("Disk", width=10)
        self.table.add_column("Status", width=14)
        self.table.cursor_type = "row"
        self.refresh_table()

    def refresh_table(self) -> None:
        self.revoked = list_revoked_consoles()
        self.table.clear()
        if not self.revoked:
            self.table.add_row("No revoked consoles", "", "", "", "")
            return
        for entry in self.revoked:
            roms_path = entry.get("roms_path") or ""
            playlist_path = entry.get("playlist_path") or ""
            self.table.add_row(
                entry.get("module") or f"{entry.get('manufacturer')} - {entry.get('console')}",
                roms_path,
                playlist_path,
                self._format_bytes(self._dir_size(Path(roms_path).expanduser())),
                entry.get("cleanup_status", "pending"),
            )

    def action_refresh(self) -> None:
        self.refresh_table()

    def action_keep(self) -> None:
        self._cleanup("keep")

    def action_disable_playlist(self) -> None:
        self._cleanup("disable_playlist")

    def action_delete_local(self) -> None:
        self._cleanup("delete_local")

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _selected_entry(self):
        if not getattr(self, "revoked", None):
            return None
        row = getattr(self.table, "cursor_row", 0)
        if row >= len(self.revoked):
            return None
        return self.revoked[row]

    def _cleanup(self, action: str) -> None:
        entry = self._selected_entry()
        if not entry:
            self.app.bell()
            return
        try:
            result = cleanup_revoked_console(entry["guid"], action)
        except Exception as exc:
            self.app.push_screen(MessageScreen("Cleanup Failed", str(exc)))
            return
        self.refresh_table()
        self.app.push_screen(MessageScreen("Cleanup Complete", self._format_result(result)))

    @staticmethod
    def _format_result(result) -> str:
        lines = [
            f"Action: {result.get('action')}",
            f"ROM folder removed: {result.get('roms_removed')}",
            f"Playlist removed: {result.get('playlist_removed')}",
            f"Playlist disabled: {result.get('playlist_disabled')}",
        ]
        errors = result.get("errors") or []
        if errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(str(error) for error in errors)
        return "\n".join(lines)

    @staticmethod
    def _dir_size(path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        total = 0
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
        return total

    @staticmethod
    def _format_bytes(value: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        amount = float(value)
        for unit in units:
            if amount < 1024 or unit == units[-1]:
                return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
            amount /= 1024
        return "0 B"
