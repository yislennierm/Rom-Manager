from pathlib import Path

from textual.app import ComposeResult
from textual.widgets import Header, Footer, Static, DataTable
from textual.screen import Screen

from core.bios_manager import active_frontend, install_bios_from_file, install_bios_from_source, list_bios_requirements
from .path_browser_screen import PathBrowserScreen


class BiosManagerScreen(Screen):
    """Manage BIOS readiness for the active frontend."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "refresh", "Refresh"),
        ("i", "install_bios", "Install"),
        ("d", "download_bios", "Download"),
    ]

    def compose(self) -> ComposeResult:
        yield Header("Database BIOS")
        self.summary = Static("", id="bios_manager_summary")
        self.table = DataTable(id="bios_manager_table")
        self.table.add_columns("Status", "Core", "BIOS", "Expected", "Path / Notes")
        yield self.summary
        yield self.table
        yield Static(
            "Database BIOS readiness. Select a missing/invalid BIOS and press [i] for local file or [d] for configured source.",
            id="bios_manager_actions",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self._pending_bios = None
        self.refresh_table()

    def refresh_table(self) -> None:
        frontend_key, frontend = active_frontend()
        self.summary.update(
            f"[b]Active frontend:[/b] {frontend.get('name', frontend_key or 'None')}\n"
            f"[b]BIOS path:[/b] {frontend.get('bios_path', '—')}"
        )
        self.table.clear()
        self._row_payloads = []
        requirements = [req for req in list_bios_requirements() if req.get("bios")]
        if not requirements:
            self.table.add_row("—", "—", "No BIOS requirements configured", "—", "—")
            self._row_payloads.append(None)
            return
        for requirement in requirements:
            bios = requirement.get("bios") or {}
            status = requirement.get("status") or {}
            state = status.get("state")
            label = "OK" if state == "ok" else status.get("label", "Missing")
            marker = "✅" if state == "ok" else "⚠"
            expected = bios.get("md5") or _zip_expected_display(bios) or "—"
            source_label = _source_display(bios)
            notes = status.get("path") or source_label or bios.get("notes") or "—"
            self.table.add_row(
                f"{marker} {label}",
                str(requirement.get("core_name") or requirement.get("core_id") or "—"),
                bios.get("filename") or "—",
                expected,
                str(notes),
            )
            self._row_payloads.append(requirement)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.refresh_table()

    def action_install_bios(self) -> None:
        payload = self._selected_requirement()
        if not payload:
            return
        self._pending_bios = payload.get("bios")
        self.app.push_screen(
            PathBrowserScreen(self._install_selected_file, start=Path.home() / "Downloads", select_files=True)
        )

    def action_download_bios(self) -> None:
        payload = self._selected_requirement()
        if not payload:
            return
        bios = payload.get("bios")
        if not (bios.get("sources") or []):
            self.app.notify("No configured source for this BIOS.", severity="warning")
            return
        try:
            result = install_bios_from_source(bios)
        except Exception as exc:
            self.app.notify(f"BIOS download failed: {exc}", severity="error")
            return
        source = result.get("source") or {}
        self.app.notify(f"Installed BIOS from {source.get('name', 'configured source')}", severity="success")
        self.refresh_table()

    def _install_selected_file(self, selected_path: str) -> None:
        if not self._pending_bios:
            self.app.notify("No BIOS selected.", severity="warning")
            return
        try:
            result = install_bios_from_file(Path(selected_path), self._pending_bios)
        except Exception as exc:
            self.app.notify(f"BIOS install failed: {exc}", severity="error")
            return
        self.app.notify(f"Installed BIOS to {result.get('target')}", severity="success")
        self.refresh_table()

    def _selected_requirement(self):
        row = getattr(self.table, "cursor_row", None)
        if row is None or row < 0 or row >= len(getattr(self, "_row_payloads", [])):
            self.app.bell()
            return None
        payload = self._row_payloads[row]
        if not payload or not payload.get("bios"):
            self.app.notify("No BIOS metadata for this row.", severity="warning")
            return None
        status = payload.get("status") or {}
        if status.get("state") == "ok":
            self.app.notify("This BIOS is already satisfied.", severity="information")
            return None
        return payload


def _zip_expected_display(bios: dict) -> str | None:
    contents = bios.get("zip_contents") or []
    if not contents:
        return None
    first = contents[0]
    if len(contents) == 1:
        return f"{first.get('filename')}:{first.get('md5', 'md5?')}"
    return f"{len(contents)} files"


def _source_display(bios: dict) -> str | None:
    sources = bios.get("sources") or []
    if not sources:
        return None
    if len(sources) == 1:
        return f"Source: {sources[0].get('name', sources[0].get('id', 'configured'))}"
    return f"{len(sources)} configured sources"
