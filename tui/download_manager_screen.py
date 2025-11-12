from textual.app import ComposeResult
from textual.widgets import Header, Footer, DataTable, Static
from textual.containers import Container
from textual.screen import Screen
from textual import events
from textual.timer import Timer

class DownloadManagerScreen(Screen):
    """Screen for viewing and managing download jobs."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("d", "delete_job", "Remove Job"),
        ("r", "refresh", "Refresh"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static("[b]Download Manager[/b]\n(Press [r] to refresh, [Del] to remove, [Esc] to return)", id="label"),
            DataTable(id="job_table"),
        )
        yield Footer()

    def on_mount(self):
        manager = getattr(self.app, "download_manager", None)
        if manager is None:
            raise RuntimeError("Download manager is not available on the application.")
        self.manager = manager
        self.table = self.query_one("#job_table", DataTable)
        self.table.add_columns(
            "ROM",
            "Console",
            "Protocol",
            "Status",
            "Progress",
            "Speed",
            "Peers",
            "Size",
            "MD5",
            "Destination",
        )
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True

        # auto-refresh every 3 seconds
        self.refresh_timer: Timer = self.set_interval(3.0, self.refresh_table)
        self.refresh_table()

    def on_unmount(self):
        if hasattr(self, "refresh_timer"):
            self.refresh_timer.stop()

    def refresh_table(self):
        self.table.clear()
        #self.manager.load_jobs()  # reload updated JSON before listing
        jobs = self.manager.list_jobs()

        if not jobs:
            self.table.add_row("— No active jobs —", "", "", "", "", "", "", "", "")
            return

        for job in jobs:
            status = job["status"]
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
                progress,
                speed,
                peers,
                size_display,
                md5,
                job["destination"],
            )

    def action_refresh(self):
        self.refresh_table()

    def action_go_back(self):
        self.app.pop_screen()

    def action_delete_job(self):
        if not hasattr(self, "table") or not self.table.row_count:
            return
        row = self.table.cursor_row
        jobs = self.manager.list_jobs()
        if row < len(jobs):
            job = jobs[row]
            self.manager.remove_job(job["id"])
            self.refresh_table()
            self.app.bell()

    def _format_size(self, size_bytes):
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
