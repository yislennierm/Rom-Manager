import os
import threading

if os.environ.get("TERM") in (None, "", "dumb"):
    os.environ["TERM"] = "xterm-256color"

from textual.app import App

from core.account_reconciler import activate_assigned_frontend_consoles, reconcile_assigned_consoles
from core.client_sync import assignment_signature, sync_client_metadata
from core.download_manager import DownloadManager
from core.frontend_installer import install_completed_jobs
from data.storage.storage_config_loader import load_storage_config
from utils.backend_client import BackendError, fetch_client_sync_manifest, get_api_key
from utils.paths import list_cached_consoles, manufacturer_slug, console_slug
from .menu_screen import MenuScreen


AUTO_INSTALL_BATCH_SIZE = max(1, int(os.environ.get("ROMS_MANAGER_AUTO_INSTALL_BATCH", "25") or "25"))
AUTO_INSTALL_ARCHIVE_BATCH_SIZE = max(
    AUTO_INSTALL_BATCH_SIZE,
    int(os.environ.get("ROMS_MANAGER_AUTO_INSTALL_ARCHIVE_BATCH", "250") or "250"),
)


class ROMManagerApp(App):
    TITLE = "ROMs Manager"
    SUB_TITLE = "LaRaspa"

    def on_mount(self) -> None:
        # Share a single DownloadManager across all screens to avoid races.
        auto_resume_downloads = os.environ.get("ROMS_MANAGER_AUTO_RESUME_DOWNLOADS") == "1"
        self.download_manager = DownloadManager(verbose=False, auto_resume=auto_resume_downloads)
        self._auto_sync_running = False
        self._auto_install_running = False
        self._last_sync_signature = None

        # Seed the current console from cached metadata if available.
        cached = list_cached_consoles()
        if cached:
            first = cached[0]
            self.current_manufacturer = first["manufacturer"]
            self.current_console = first["console"]
            self.current_roms_path = first["roms_path"]
            self.current_manufacturer_slug = first["manufacturer_slug"]
            self.current_console_slug = first["console_slug"]
            self.current_module_guid = first.get("guid")
        else:
            # Defaults match the values used by the CLI.
            self.current_manufacturer = "Sega"
            self.current_console = "Dreamcast"
            self.current_roms_path = None
            self.current_manufacturer_slug = manufacturer_slug(self.current_manufacturer)
            self.current_console_slug = console_slug(self.current_console)
            self.current_module_guid = None

        self.push_screen(MenuScreen("Main Menu"))
        self._start_auto_sync(initial=True)
        self.set_interval(30.0, self._start_auto_sync)
        self.set_interval(5.0, self._start_auto_install)

    def _start_auto_sync(self, initial: bool = False) -> None:
        if self._auto_sync_running or not get_api_key():
            return
        self._auto_sync_running = True
        thread = threading.Thread(target=self._auto_sync_worker, args=(initial,), daemon=True)
        thread.start()

    def _auto_sync_worker(self, initial: bool = False) -> None:
        try:
            manifest = fetch_client_sync_manifest()
            signature = assignment_signature(manifest)
            if not initial and signature == self._last_sync_signature:
                return
            result = sync_client_metadata(include_cache=True)
            frontend_report = activate_assigned_frontend_consoles(result.get("manifest"))
            reconcile_report = reconcile_assigned_consoles(self.download_manager, install_ready=False)
            self._last_sync_signature = assignment_signature(result["manifest"])
            revoked = result.get("revoked") or []
            modules = result.get("manifest", {}).get("modules") or []
            message = f"Synced {len(modules)} assigned console(s) from backend."
            frontend_added = frontend_report.get("added", 0)
            if frontend_added:
                message += f" Activated {frontend_added} in {frontend_report.get('frontend')}."
            frontend_removed = frontend_report.get("removed", 0)
            if frontend_removed:
                message += f" Deactivated {frontend_removed} revoked console(s)."
            queued = reconcile_report.get("jobs_created", 0)
            if queued:
                message += f" Queued {queued} install download(s)."
            if revoked:
                message += f" {len(revoked)} revoked console(s) need cleanup."
            self.call_from_thread(self.notify, message, severity="information" if not revoked else "warning")
        except BackendError:
            pass
        except Exception as exc:
            self.call_from_thread(self.notify, f"Backend auto-sync failed: {exc}", severity="warning")
        finally:
            self._auto_sync_running = False

    def _start_auto_install(self) -> None:
        if self._auto_install_running:
            return
        active_frontend_key = self._active_frontend_key()
        jobs = [
            job
            for job in self.download_manager.list_jobs()
            if job.get("auto_install")
            and job.get("status") == "completed"
            and job.get("install_status") != "installing"
            and not self._installed_for_frontend(job, active_frontend_key)
            and not self._failed_for_frontend(job, active_frontend_key)
        ]
        jobs = self._auto_install_batch(jobs)
        if not jobs:
            return
        self._auto_install_running = True
        for job in jobs:
            self.download_manager.update_job_fields(job["id"], install_status="installing")
        thread = threading.Thread(target=self._auto_install_worker, args=(jobs,), daemon=True)
        thread.start()

    def _auto_install_worker(self, jobs) -> None:
        try:
            report = install_completed_jobs(jobs)
        except Exception as exc:
            for job in jobs:
                self.download_manager.update_job_fields(
                    job["id"],
                    install_status="error",
                    install_error=str(exc),
                    install_frontend_key=self._active_frontend_key(),
                )
            self.call_from_thread(self.notify, f"Auto-install failed: {exc}", severity="warning")
        else:
            errors = report.get("errors") or []
            status = "error" if errors else "installed"
            for job in jobs:
                fields = {"install_status": status}
                if errors:
                    fields["install_error"] = "; ".join(str(error) for error in errors[:3])
                    fields["install_frontend_key"] = report.get("frontend_key")
                    fields["install_frontend"] = report.get("frontend")
                else:
                    fields["install_frontend_key"] = report.get("frontend_key")
                    fields["install_frontend"] = report.get("frontend")
                    fields["install_error"] = None
                self.download_manager.update_job_fields(job["id"], **fields)
            self.call_from_thread(
                self.notify,
                f"Installed {report.get('roms_installed', 0)} ROM(s) into RetroArch.",
                severity="success" if not errors else "warning",
            )
        finally:
            self._auto_install_running = False

    @staticmethod
    def _auto_install_batch(jobs):
        if not jobs:
            return []
        first = jobs[0]
        local_path = first.get("local_path")
        if first.get("archive_member_path") and local_path:
            same_archive = [
                job
                for job in jobs
                if job.get("archive_member_path") and job.get("local_path") == local_path
            ]
            return same_archive[:AUTO_INSTALL_ARCHIVE_BATCH_SIZE]
        return jobs[:AUTO_INSTALL_BATCH_SIZE]

    @staticmethod
    def _active_frontend_key() -> str | None:
        frontends = (load_storage_config() or {}).get("frontends") or {}
        for key, entry in frontends.items():
            if entry.get("active"):
                return key
        if frontends:
            return next(iter(frontends))
        return None

    @staticmethod
    def _installed_for_frontend(job, frontend_key: str | None) -> bool:
        if job.get("install_status") != "installed":
            return False
        return bool(frontend_key and job.get("install_frontend_key") == frontend_key)

    @staticmethod
    def _failed_for_frontend(job, frontend_key: str | None) -> bool:
        if job.get("install_status") != "error":
            return False
        return bool(frontend_key and job.get("install_frontend_key") == frontend_key)


if __name__ == "__main__":
    ROMManagerApp().run()
