from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Header, Footer, Static, DataTable

from utils.backend_client import (
    BackendError,
    fetch_modules_snapshot,
    save_modules_snapshot,
    fetch_modules_remote_metadata,
    load_modules_local_metadata,
    fetch_providers_snapshot,
    save_providers_snapshot,
    fetch_providers_remote_metadata,
    load_providers_local_metadata,
)

TaskHandler = Callable[[], Dict[str, object]]


class UpdateScreen(Screen):
    """Backend update manager with download-manager style UI."""

    CSS_PATH = "styles/update_screen.css"

    BINDINGS = [
        ("u", "update_selected", "Update Selected"),
        ("ctrl+u", "update_all", "Update All"),
        ("escape", "go_back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.tasks: Dict[str, Dict[str, object]] = {
            "modules": {
                "label": "Libretro modules",
                "update_handler": self._update_modules_task,
                "local_loader": load_modules_local_metadata,
                "remote_loader": fetch_modules_remote_metadata,
                "local_ts": None,
                "remote_ts": None,
                "path": None,
            },
            "providers": {
                "label": "Providers registry",
                "update_handler": self._update_providers_task,
                "local_loader": load_providers_local_metadata,
                "remote_loader": fetch_providers_remote_metadata,
                "local_ts": None,
                "remote_ts": None,
                "path": None,
            },
        }
        self.row_lookup: Dict[str, int] = {}
        self.row_reverse: Dict[int, str] = {}
        self.status_message = (
            "[b]ROMs Manager Update[/b]\n"
            "Press [u] to update the highlighted task or [Ctrl+U] to update everything.\n"
            "Data downloads from the backend and is written to your local data folder."
        )

    def compose(self) -> ComposeResult:
        yield Header()
        self.status = Static(self.status_message, id="update_status")
        self.table = DataTable(id="update_table")
        self.table.add_column("Data", width=30)
        self.table.add_column("Status", width=10)
        self.table.add_column("Progress", width=12)
        self.table.add_column("Local", width=20)
        self.table.add_column("Remote", width=20)
        self.table.add_column("Path", width=60)
        self.table.add_column("Info", width=10)
        for row_index, (task_id, task) in enumerate(self.tasks.items()):
            self.table.add_row(
                task["label"],
                "Waiting",
                self._progress_bar(0),
                "—",
                "—",
                "—",
                "—",
                key=task_id,
            )
            self.row_lookup[task_id] = row_index
            self.row_reverse[row_index] = task_id
        self.table.cursor_type = "row"
        yield Container(self.status, self.table, id="update_container")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_metadata()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_update_selected(self) -> None:
        task_id = self._selected_task_id()
        if not task_id:
            self.app.bell()
            return
        self._run_task(task_id)

    def action_update_all(self) -> None:
        for task_id in self.tasks.keys():
            self._run_task(task_id)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _run_task(self, task_id: str) -> None:
        task = self.tasks[task_id]
        label = task["label"]
        self._set_status(f"⏳ Updating {label} …")
        self._set_row(task_id, status="Contacting", progress=5, notes="Connecting…")
        handler: TaskHandler = task["update_handler"]
        try:
            result = handler()
        except BackendError as exc:
            self._set_row(task_id, status="Error", progress=0, notes=str(exc))
            self._set_status(f"❌ {label} update failed: {exc}")
            self.app.notify(str(exc), severity="error")
            return
        except Exception as exc:
            self._set_row(task_id, status="Error", progress=0, notes=str(exc))
            self._set_status(f"❌ Unexpected error while updating {label}: {exc}")
            self.app.notify(str(exc), severity="error")
            return

        fetched_at = result.get("fetched_at")
        saved_path = result.get("path")
        modules_count = result.get("count")

        self.tasks[task_id]["local_ts"] = fetched_at
        self.tasks[task_id]["path"] = saved_path
        row = self.row_lookup[task_id]
        self.table.update_cell_at((row, 3), self._format_timestamp(fetched_at))
        if saved_path:
            self.table.update_cell_at((row, 5), saved_path)

        count_display = "?" if modules_count is None else modules_count
        self._set_row(
            task_id,
            status="Completed",
            progress=100,
            notes=f"{count_display} entries · fetched {self._format_timestamp(fetched_at)}",
        )
        self._refresh_metadata(task_id=task_id, remote_only=True)
        self._set_status(f"✅ {label} updated and saved to {saved_path}")
        self.app.notify(f"{label} updated.", severity="success")

    def _update_modules_task(self) -> Dict[str, object]:
        snapshot = fetch_modules_snapshot()
        self._set_row("modules", status="Downloading", progress=70, notes="Writing to disk…")
        path = save_modules_snapshot(snapshot)
        return {
            "fetched_at": snapshot.get("fetched_at"),
            "path": str(path),
            "count": len(snapshot.get("modules") or []),
        }

    def _update_providers_task(self) -> Dict[str, object]:
        snapshot = fetch_providers_snapshot()
        self._set_row("providers", status="Downloading", progress=70, notes="Writing to disk…")
        path = save_providers_snapshot(snapshot)
        meta = load_providers_local_metadata() or {}
        fetched_at = meta.get("fetched_at") or datetime.now().isoformat()
        return {
            "fetched_at": fetched_at,
            "path": str(path),
            "count": meta.get("count"),
        }

    # ------------------------------------------------------------------
    # Metadata / UI helpers
    # ------------------------------------------------------------------

    def _refresh_metadata(self, task_id: Optional[str] = None, remote_only: bool = False) -> None:
        task_ids = [task_id] if task_id else list(self.tasks.keys())
        for tid in task_ids:
            task = self.tasks[tid]
            row = self.row_lookup[tid]
            if not remote_only:
                local_meta = task["local_loader"]()
                if local_meta:
                    task["local_ts"] = local_meta.get("fetched_at")
                    task["path"] = local_meta.get("path")
                    self.table.update_cell_at((row, 3), self._format_timestamp(task["local_ts"]))
                    self.table.update_cell_at((row, 5), local_meta.get("path", "—"))
                else:
                    task["local_ts"] = None
                    self.table.update_cell_at((row, 3), "—")
                    self.table.update_cell_at((row, 5), "—")
            try:
                remote_meta = task["remote_loader"]()
                task["remote_ts"] = remote_meta.get("fetched_at")
                self.table.update_cell_at((row, 4), self._format_timestamp(task["remote_ts"]))
                self.table.update_cell_at((row, 6), self._build_note(tid))
            except BackendError as exc:
                task["remote_ts"] = None
                self.table.update_cell_at((row, 4), f"Error: {exc}")
                note = "Remote check failed" if not task.get("local_ts") else "Offline"
                self.table.update_cell_at((row, 6), note)

    def _build_note(self, task_id: str) -> str:
        task = self.tasks[task_id]
        local_ts = task.get("local_ts")
        remote_ts = task.get("remote_ts")
        if local_ts and remote_ts:
            if self._format_timestamp(local_ts) == self._format_timestamp(remote_ts):
                return "Up to date"
            return "Update available"
        if remote_ts and not local_ts:
            return "Not installed"
        if local_ts and not remote_ts:
            return "Offline"
        return "—"

    def _selected_task_id(self) -> Optional[str]:
        if not getattr(self, "table", None) or self.table.row_count == 0:
            return None
        row = self.table.cursor_row
        return self.row_reverse.get(row)

    def _set_status(self, message: str) -> None:
        self.status.update(message)

    def _set_row(self, task_id: str, status: str, progress: float, notes: str) -> None:
        row = self.row_lookup[task_id]
        self.table.update_cell_at((row, 1), status)
        self.table.update_cell_at((row, 2), self._progress_bar(progress))
        self.table.update_cell_at((row, 6), notes)

    @staticmethod
    def _progress_bar(percent_value) -> str:
        try:
            value = float(percent_value or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        value = max(0.0, min(100.0, value))
        filled = int((value / 100.0) * 20)
        empty = 20 - filled
        bar = "█" * filled + "░" * empty
        return f"[cyan]{bar}[/] {value:5.1f}%"

    @staticmethod
    def _format_timestamp(timestamp: Optional[str]) -> str:
        if not timestamp:
            return "—"
        try:
            dt = datetime.fromisoformat(timestamp)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(timestamp)
