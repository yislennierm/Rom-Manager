from textual.app import ComposeResult
from textual.widgets import Header, Footer, DataTable, Static
from textual.containers import Container
from textual.screen import Screen
from textual.timer import Timer

from core.frontend_installer import install_completed_jobs
from .message_screen import MessageScreen


DISPLAY_JOB_LIMIT = 250


class DownloadManagerScreen(Screen):
    """Screen for viewing and managing download jobs."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("d", "delete_job", "Remove Job"),
        ("i", "install_selected", "Install"),
        ("I", "install_all_completed", "Install All"),
        ("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("[b]Download Manager[/b]\n(Press [i] to install selected job, [I] to install all completed, [r] to refresh, [Del] to remove, [Esc] to return)", id="label"),
            DataTable(id="job_table"),
        )
        yield Footer()

    def on_mount(self):
        manager = getattr(self.app, "download_manager", None)
        if manager is None:
            raise RuntimeError("Download manager is not available on the application.")
        self.manager = manager
        self.table = self.query_one("#job_table", DataTable)
        self.table.add_column("ROM", width=60)
        self.table.add_column("Console", width=12)
        self.table.add_column("Protocol", width=8)
        self.table.add_column("Status", width=12)
        self.table.add_column("Install", width=12)
        self.table.add_column("Progress", width=12)
        self.table.add_column("Speed", width=8)
        self.table.add_column("Peers", width=5)
        self.table.add_column("Size", width=7)
        self.table.add_column("MD5", width=18)
        self.table.add_column("Path", width=40)
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True

        # auto-refresh every 3 seconds
        self._last_jobs_signature = None
        self.refresh_timer: Timer = self.set_interval(3.0, self.refresh_table)
        self.refresh_table(force=True)

    def on_unmount(self):
        if hasattr(self, "refresh_timer"):
            self.refresh_timer.stop()

    def refresh_table(self, force: bool = False):
        #self.manager.load_jobs()  # reload updated JSON before listing
        jobs = self.manager.list_jobs()
        signature = self._jobs_signature(jobs)
        if not force and signature == self._last_jobs_signature:
            return
        self._last_jobs_signature = signature

        cursor_row = getattr(self.table, "cursor_row", 0)
        self.table.clear()

        if not jobs:
            self.table.add_row("— No active jobs —", "", "", "", "", "", "", "", "", "", "")
            return

        display_jobs = jobs[-DISPLAY_JOB_LIMIT:]
        self._displayed_jobs = display_jobs
        if len(jobs) > len(display_jobs):
            hidden = len(jobs) - len(display_jobs)
            self.table.add_row(
                f"— Showing newest {len(display_jobs)} of {len(jobs)} jobs; {hidden} older hidden —",
                "",
                "",
                "[dim]summary[/]",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            )

        for job in display_jobs:
            status = job["status"]
            install_status = self._install_status(job)
            color = (
                "[green]" if status == "completed"
                else "[yellow]" if status.startswith("downloading")
                else "[red]" if status.startswith("error")
                else "[dim]"
            )
            progress = self._progress_bar(job.get("progress"))
            speed = f"{job.get('speed_kb', 0):.1f} kB/s"
            peers = str(job.get("peers", 0))
            console = job.get("console", "Unknown")
            size_display = self._format_size(job.get("size_bytes"))
            md5 = job.get("md5") or "—"
            protocol = job.get("protocol", "torrent")

            self.table.add_row(
                f"{job['rom_name']}",
                console,
                protocol,
                f"{color}{status}[/]",
                install_status,
                progress,
                speed,
                peers,
                size_display,
                md5,
                job["destination"],
            )
        self._restore_cursor(cursor_row)

    def action_refresh(self):
        self.refresh_table(force=True)

    def action_go_back(self):
        self.app.pop_screen()

    def action_delete_job(self):
        if not hasattr(self, "table") or not self.table.row_count:
            return
        row = self.table.cursor_row
        jobs = getattr(self, "_displayed_jobs", self.manager.list_jobs())
        has_summary_row = len(self.manager.list_jobs()) > len(jobs)
        if has_summary_row:
            row -= 1
        if 0 <= row < len(jobs):
            job = jobs[row]
            self.manager.remove_job(job["id"])
            self.refresh_table(force=True)
            self.app.bell()

    def action_install_selected(self):
        jobs = getattr(self, "_displayed_jobs", self.manager.list_jobs())
        if not jobs or not hasattr(self, "table") or not self.table.row_count:
            self.app.bell()
            return
        row = getattr(self.table, "cursor_row", 0)
        has_summary_row = len(self.manager.list_jobs()) > len(jobs)
        if has_summary_row:
            row -= 1
        if row < 0 or row >= len(jobs):
            self.app.bell()
            return
        job = jobs[row]
        if job.get("status") != "completed":
            self.app.push_screen(MessageScreen("Install Skipped", "Selected job is not completed yet."))
            return
        self._install_jobs([job])

    def action_install_all_completed(self):
        self._install_jobs(self.manager.list_jobs())

    def _install_jobs(self, jobs):
        try:
            report = install_completed_jobs(jobs)
        except Exception as exc:
            self.app.push_screen(MessageScreen("Install Failed", str(exc)))
            return
        errors = report.get("errors") or []
        status = "error" if errors else "installed"
        installed_paths_by_job = report.get("installed_paths_by_job") or {}
        for job in jobs:
            if job.get("status") != "completed":
                continue
            fields = {
                "install_status": status,
                "install_frontend_key": report.get("frontend_key"),
                "install_frontend": report.get("frontend"),
                "install_error": "; ".join(str(error) for error in errors[:3]) if errors else None,
            }
            installed_paths = installed_paths_by_job.get(str(job.get("id")))
            if installed_paths:
                fields["installed_paths"] = installed_paths
            self.manager.update_job_fields(job["id"], **fields)
        self.refresh_table(force=True)
        message = self._format_install_report(report)
        self.app.push_screen(MessageScreen("Install Complete", message))

    @staticmethod
    def _jobs_signature(jobs):
        return tuple(
            (
                job.get("id"),
                job.get("status"),
                job.get("install_status"),
                job.get("install_frontend_key"),
                job.get("progress"),
                job.get("speed_kb"),
                job.get("peers"),
                job.get("error"),
            )
            for job in jobs
        )

    def _restore_cursor(self, requested_row: int | None) -> None:
        if not self.table.row_count:
            return
        requested_row = max(0, min(requested_row or 0, self.table.row_count - 1))
        current_column = getattr(self.table, "cursor_column", 0)
        try:
            self.table.cursor_coordinate = (requested_row, current_column)
        except AttributeError:
            pass

    @staticmethod
    def _install_status(job):
        if job.get("install_status"):
            return str(job.get("install_status"))
        if job.get("auto_install"):
            return "queued"
        return "manual"

    @staticmethod
    def _format_install_report(report) -> str:
        lines = [
            f"Frontend: {report.get('frontend')}",
            f"ROMs path: {report.get('roms_root')}",
            f"BIOS path: {report.get('bios_root')}",
            "",
            f"Completed jobs seen: {report.get('jobs_seen', 0)}",
            f"Space required: {DownloadManagerScreen._format_bytes(report.get('bytes_required'))}",
            f"Space available: {DownloadManagerScreen._format_bytes(report.get('bytes_available'))}",
            f"ROMs installed: {report.get('roms_installed', 0)}",
            f"ROMs skipped: {report.get('roms_skipped', 0)}",
            f"BIOS installed: {report.get('bios_installed', 0)}",
            f"BIOS skipped: {report.get('bios_skipped', 0)}",
        ]
        playlists = report.get("playlists_written") or []
        if playlists:
            lines.append("")
            lines.append("Playlists:")
            lines.extend(str(path) for path in playlists)
        errors = report.get("errors") or []
        if errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(str(error) for error in errors)
        return "\n".join(lines)

    def _format_size(self, size_bytes):
        return self._format_bytes(size_bytes)

    @staticmethod
    def _format_bytes(size_bytes):
        if size_bytes is None:
            return "?"
        try:
            size_value = int(size_bytes)
        except (TypeError, ValueError):
            return str(size_bytes)
        if size_value < 0:
            return "?"
        thresholds = [
            (1 << 40, "TB"),
            (1 << 30, "GB"),
            (1 << 20, "MB"),
            (1 << 10, "KB"),
        ]
        for factor, unit in thresholds:
            if size_value >= factor:
                value = size_value / factor
                return f"{value:.1f} {unit}"
        if size_value == 0:
            return "0 B"
        return f"{size_value} B"

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
