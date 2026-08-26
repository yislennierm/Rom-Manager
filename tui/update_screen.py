from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Header, Footer, Static, DataTable, Input

from core.account_reconciler import activate_assigned_frontend_consoles, reconcile_assigned_consoles
from core.revoked_access import update_assignments_from_manifest
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
    fetch_rom_catalog_metadata,
    fetch_roms_remote_metadata,
    load_roms_local_metadata,
    download_rom_dataset,
    save_rom_dataset,
    load_cache_local_metadata,
    fetch_cache_remote_metadata,
    download_cache_archive,
    test_backend,
    fetch_client_sync_manifest,
    get_backend_base,
    set_backend_base,
    get_api_key,
    set_api_key,
)
from utils.library_sync import RDB_DIR
from .revoked_access_screen import RevokedAccessScreen

TaskHandler = Callable[[], Dict[str, object]]


class UpdateScreen(Screen):
    """Backend update manager with download-manager style UI."""

    CSS_PATH = "styles/update_screen.css"

    BINDINGS = [
        ("u", "update_selected", "Update Selected"),
        ("ctrl+u", "update_all", "Update All"),
        ("s", "sync_account", "Sync Account"),
        ("x", "cleanup_revoked", "Cleanup Revoked"),
        ("t", "test_backend", "Test Backend"),
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
            "roms": {
                "label": "ROM catalogs",
                "update_handler": self._update_roms_task,
                "local_loader": load_roms_local_metadata,
                "remote_loader": fetch_roms_remote_metadata,
                "local_ts": None,
                "remote_ts": None,
                "path": None,
            },
            "cache": {
                "label": "Cache assets",
                "update_handler": self._update_cache_task,
                "local_loader": load_cache_local_metadata,
                "remote_loader": fetch_cache_remote_metadata,
                "local_ts": None,
                "remote_ts": None,
                "path": None,
            },
        }
        self.row_lookup: Dict[str, int] = {}
        self.row_reverse: Dict[int, str] = {}
        self.status_message = (
            "[b]ROMs Manager Update[/b]\n"
            "Enter the backend URL and client API key once, then press [s] to sync the assigned account.\n"
            "The sync downloads metadata, providers, ROM catalogs, and backend cache only for consoles assigned to this key."
        )

    def compose(self) -> ComposeResult:
        yield Header()
        self.status = Static(self.status_message, id="update_status")
        self.backend_input = Input(value=get_backend_base(), placeholder="Backend URL", id="backend_input")
        self.api_key_input = Input(value=get_api_key() or "", placeholder="API key", id="api_key_input", password=True)
        self.backend_label = Static("Backend", id="backend_label")
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
        yield Container(self.backend_label, self.backend_input, self.api_key_input, self.status, self.table, id="update_container")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_account_status()
        self._refresh_metadata()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "backend_input":
            new_base = event.value.strip()
            set_backend_base(new_base or None)
            self.app.notify(f"Backend set to {get_backend_base()}", severity="information")
        elif event.input.id == "api_key_input":
            set_api_key(event.value.strip() or None)
            self.app.notify("API key saved for this TUI.", severity="information")
        else:
            return
        self._refresh_account_status()
        self._refresh_metadata()

    def action_test_backend(self) -> None:
        base = get_backend_base()
        self._set_status(f"Testing backend at {base} ...")
        try:
            payload = test_backend()
        except BackendError as exc:
            self._set_status(f"Backend unreachable: {exc}")
            self.app.notify(str(exc), severity="error")
            return
        except Exception as exc:
            self._set_status(f"Unexpected error: {exc}")
            self.app.notify(str(exc), severity="error")
            return
        self._set_status(f"Backend OK at {base}: {payload}")
        self.app.notify(f"Backend OK: {base}", severity="information")

    def action_sync_account(self) -> None:
        try:
            manifest = fetch_client_sync_manifest()
        except BackendError as exc:
            self._set_status(f"Account sync unavailable: {exc}")
            self.app.notify(str(exc), severity="error")
            return
        user = manifest.get("user") or {}
        datasets = manifest.get("datasets") or {}
        label = user.get("name") or user.get("id") or "client"
        self._set_status(
            f"Syncing {label}: "
            f"{datasets.get('modules', {}).get('count', 0)} console(s), "
            f"{datasets.get('providers', {}).get('count', 0)} provider console(s), "
            f"{datasets.get('roms', {}).get('count', 0)} ROM catalog(s)."
        )
        for task_id in self.tasks.keys():
            self._run_task(task_id)
        manager = getattr(self.app, "download_manager", None)
        if manager is None:
            self.app.notify("Download manager is not available; skipped RetroArch reconciliation.", severity="warning")
            return
        revoked = update_assignments_from_manifest(manifest)
        frontend_report = activate_assigned_frontend_consoles(manifest)
        report = reconcile_assigned_consoles(manager, install_ready=True)
        self._set_status(self._format_reconcile_report(report))
        frontend_added = frontend_report.get("added", 0)
        if frontend_added:
            self.app.notify(
                f"Activated {frontend_added} assigned console(s) in {frontend_report.get('frontend')}.",
                severity="information",
            )
        frontend_removed = frontend_report.get("removed", 0)
        if frontend_removed:
            self.app.notify(f"Deactivated {frontend_removed} revoked console(s) locally.", severity="warning")
        queued = report.get("jobs_created", 0)
        completed = report.get("jobs_completed", 0)
        if queued:
            self.app.notify(f"Queued {queued} provider collection download(s).", severity="information")
        elif completed:
            self.app.notify("Assigned provider collections are downloaded; RetroArch install checked.", severity="success")
        else:
            self.app.notify("No collection-level provider archive to queue.", severity="information")
        if revoked:
            self.app.notify(f"{len(revoked)} console(s) no longer assigned. Press [x] to review cleanup.", severity="warning")

    def action_cleanup_revoked(self) -> None:
        self.app.push_screen(RevokedAccessScreen())

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

    def _update_roms_task(self) -> Dict[str, object]:
        catalog = fetch_rom_catalog_metadata()
        roms = catalog.get("roms")
        if not isinstance(roms, list):
            raise BackendError("ROM catalog payload missing 'roms' list")
        total = len(roms) or 1
        saved = []
        for index, entry in enumerate(roms, start=1):
            identifier = entry.get("slug") or entry.get("guid")
            if not identifier:
                continue
            dataset = download_rom_dataset(identifier)
            path = save_rom_dataset(dataset)
            saved.append(path)
            progress = int(index / total * 100)
            self._set_row(
                "roms",
                status="Downloading",
                progress=progress,
                notes=f"{index}/{total} catalogs",
            )
        fetched_at = None
        for entry in roms:
            ts = entry.get("fetched_at") if isinstance(entry, dict) else None
            if ts and (fetched_at is None or ts > fetched_at):
                fetched_at = ts
        return {
            "fetched_at": fetched_at,
            "path": str(RDB_DIR),
            "count": len(saved),
        }

    def _update_cache_task(self) -> Dict[str, object]:
        self._set_row("cache", status="Downloading", progress=40, notes="Downloading archive…")
        result = download_cache_archive()
        self._set_row("cache", status="Downloading", progress=80, notes="Extracting cache…")
        meta = load_cache_local_metadata() or {}
        fetched_at = meta.get("fetched_at") or result.get("fetched_at")
        count = meta.get("count") or result.get("count")
        path = meta.get("path") or result.get("path")
        return {
            "fetched_at": fetched_at,
            "path": path,
            "count": count,
        }

    # ------------------------------------------------------------------
    # Metadata / UI helpers
    # ------------------------------------------------------------------

    def _refresh_account_status(self) -> None:
        try:
            manifest = fetch_client_sync_manifest()
        except BackendError as exc:
            self._set_status(
                "[b]ROMs Manager Update[/b]\n"
                f"Backend: {get_backend_base()}\n"
                f"Account not connected: {exc}"
            )
            return
        user = manifest.get("user") or {}
        datasets = manifest.get("datasets") or {}
        modules = datasets.get("modules") or {}
        providers = datasets.get("providers") or {}
        roms = datasets.get("roms") or {}
        cache = datasets.get("cache") or {}
        label = user.get("name") or user.get("id") or "client"
        self._set_status(
            "[b]ROMs Manager Update[/b]\n"
            f"Connected to {get_backend_base()} as {label}.\n"
            f"Allowed sync: {modules.get('count', 0)} console(s), "
            f"{providers.get('count', 0)} provider console(s), "
            f"{roms.get('count', 0)} ROM catalog(s), "
            f"{cache.get('count', 0)} cache file(s). Press [s] to sync."
        )

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

    def _format_reconcile_report(self, report: Dict[str, object]) -> str:
        lines = [
            "[b]Account sync complete[/b]",
            f"Collection sources found: {report.get('collection_sources_seen', 0)}",
            f"Download jobs queued: {report.get('jobs_created', 0)}",
            f"Completed collection jobs: {report.get('jobs_completed', 0)}",
        ]
        install_report = report.get("install_report")
        if isinstance(install_report, dict):
            lines.extend(
                [
                    "",
                    f"RetroArch frontend: {install_report.get('frontend')}",
                    f"ROMs installed: {install_report.get('roms_installed', 0)}",
                    f"ROMs skipped: {install_report.get('roms_skipped', 0)}",
                    f"BIOS installed: {install_report.get('bios_installed', 0)}",
                    f"BIOS skipped: {install_report.get('bios_skipped', 0)}",
                    f"Playlists written: {len(install_report.get('playlists_written') or [])}",
                ]
            )
        errors = report.get("errors") or []
        if errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(str(error) for error in errors)
        return "\n".join(lines)

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
