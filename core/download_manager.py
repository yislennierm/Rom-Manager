#!/usr/bin/env python3
import json
import os
import queue
import shutil
import threading
import time
import urllib.request
from datetime import datetime
from typing import Dict, Optional
from urllib.parse import urlparse, urlunparse, quote, unquote

import libtorrent as lt

from utils.paths import DATA_DIR, console_cache_dir, torrent_file_path

# ---------------- Paths ---------------- #
BASE_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))

LEGACY_TORRENT_DIR = os.path.join(DATA_DIR, "torrents")        # backwards compatibility
DOWNLOADS_DIR = os.path.join(ROOT_DIR, "downloads")     # user ROM downloads
JOBS_FILE = os.path.join(DOWNLOADS_DIR, "jobs.json")
HTTP_WORKER_COUNT = max(1, int(os.environ.get("ROMS_MANAGER_HTTP_WORKERS", "4") or "4"))
PROGRESS_SAVE_INTERVAL_SECONDS = 1.0

os.makedirs(LEGACY_TORRENT_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


ARCHIVE_DOWNLOAD_EXTENSIONS = {".zip", ".7z", ".rar"}


def _torrent_file_name_matches(requested: str, candidate: str) -> bool:
    if candidate == requested:
        return True
    requested_base, requested_ext = os.path.splitext(requested)
    candidate_base, _ = os.path.splitext(candidate)
    if requested_ext in ARCHIVE_DOWNLOAD_EXTENSIONS:
        return False
    return bool(requested_base and requested_base in candidate_base)


def _same_job_identity(job: Dict, *, rom_name: str, console: str, provider_slug: Optional[str], archive_member_path: Optional[str]) -> bool:
    if job.get("rom_name") != rom_name:
        return False
    if (job.get("console") or "Unknown") != (console or "Unknown"):
        return False
    if (job.get("provider_slug") or None) != (provider_slug or None):
        return False
    return (job.get("archive_member_path") or None) == (archive_member_path or None)


def _job_member_key(job: Dict) -> str:
    return "::".join([
        str(job.get("rom_name") or ""),
        str(job.get("archive_member_path") or ""),
        str(job.get("console") or ""),
        str(job.get("provider_slug") or ""),
    ])


def _download_filename_for_job(rom_name: str, http_url: Optional[str], archive_member_path: Optional[str]) -> str:
    if archive_member_path and http_url:
        parsed = urlparse(http_url)
        url_name = unquote(os.path.basename(parsed.path or ""))
        if os.path.splitext(url_name.lower())[1] in ARCHIVE_DOWNLOAD_EXTENSIONS:
            return url_name
    return os.path.basename(rom_name)


def _cached_archive_path_for_job(
    *,
    http_url: Optional[str],
    rom_name: Optional[str] = None,
    manufacturer: Optional[str],
    console: Optional[str],
    provider_slug: Optional[str],
    archive_member_path: Optional[str],
) -> Optional[str]:
    if not archive_member_path or not manufacturer or not console or not provider_slug:
        return None
    filename = ""
    if http_url:
        parsed = urlparse(http_url)
        filename = unquote(os.path.basename(parsed.path or ""))
    if not filename and rom_name:
        filename = os.path.basename(rom_name)
    if os.path.splitext(filename.lower())[1] not in ARCHIVE_DOWNLOAD_EXTENSIONS:
        return None
    archive_path = os.path.join(console_cache_dir(manufacturer, console, provider_slug), "archives", filename)
    if os.path.exists(archive_path) and os.path.getsize(archive_path) > 0:
        return archive_path
    return None


# ======================================================================
# TorrentWrapper: Manages ONE .torrent file and multiple file jobs inside it
# ======================================================================
class TorrentWrapper:
    def __init__(self, torrent_path, destination, session, log=None):
        self.torrent_path = torrent_path
        self.destination = destination
        self.session = session
        self._log = log or (lambda _message: None)
        self.info = lt.torrent_info(torrent_path)
        self.handle = session.add_torrent({"ti": self.info, "save_path": destination})
        self.jobs = {}  # job identity -> {index, job}
        self._log(f"🌀 Added new torrent handle for: {os.path.basename(torrent_path)}")

    def add_file_job(self, job):
        """Register a specific ROM file inside this torrent for downloading."""
        rom_name = job["rom_name"]
        rom_name_lower = rom_name.lower()
        files = self.info.files()
        matched_index = None

        # ✅ iterate using num_files() and file_path() — compatible with libtorrent v2
        for idx in range(files.num_files()):
            fpath = files.file_path(idx).lower()
            basename = os.path.basename(fpath)

            # Exact filename match takes priority.
            if basename == rom_name_lower:
                matched_index = idx
                self.jobs[_job_member_key(job)] = {"index": idx, "path": files.file_path(idx), "job": job}
                break

            # Fall back to matching on base name when extensions differ (e.g., regional variants).
            if _torrent_file_name_matches(rom_name_lower, basename):
                matched_index = idx
                self.jobs[_job_member_key(job)] = {"index": idx, "path": files.file_path(idx), "job": job}
                break

        if matched_index is None:
            job["status"] = "not_found"
            self._log(f"⚠️ No matching file found for {job['rom_name']}")
            return False

        matched_path = files.file_path(matched_index)
        self._log(f"✅ Matched file: {matched_path}")
        self.update_priorities()
        return True

    def update_priorities(self):
        """Update per-file priorities so only wanted files download."""
        files = self.info.files()
        pri = [0] * files.num_files()
        for entry in self.jobs.values():
            pri[entry["index"]] = 1
        self.handle.prioritize_files(pri)

    def update_progress(self):
        """Update progress, speed, and peer count for all active jobs."""
        files = self.info.files()
        progress_list = self.handle.file_progress()
        status = self.handle.status()

        all_completed = True

        for entry in self.jobs.values():
            idx = entry["index"]
            job = entry["job"]

            # ✅ use file_size() for v2 compatibility
            fsize = files.file_size(idx)
            downloaded = progress_list[idx] if idx < len(progress_list) else 0

            if fsize > 0:
                pct = min(downloaded / fsize * 100, 100)
                job["progress"] = round(pct, 2)
            else:
                job["progress"] = 0.0

            if job["progress"] >= 100.0:
                job["progress"] = 100.0
                if job.get("status") != "completed":
                    job["status"] = "completed"
                job["local_path"] = os.path.join(self.destination, entry.get("path") or job["rom_name"])
                job["speed_kb"] = 0.0
            else:
                all_completed = False
                if job.get("status") != "downloading":
                    job["status"] = "downloading"
                job["speed_kb"] = round(status.download_rate / 1000, 2)

            job["peers"] = status.num_peers

        return all_completed


# ======================================================================
# DownloadManager: Oversees multiple torrents and jobs
# ======================================================================
class DownloadManager:
    """Manages torrent-based download jobs (persistent, multi-torrent)."""

    def __init__(self, *, verbose: bool = True, auto_resume: bool = True):
        self.verbose = verbose
        self.auto_resume = auto_resume
        self.session = lt.session()
        self.session.listen_on(6881, 6891)
        self._log("✅ Torrent session initialized on ports 6881–6891")

        self._lock = threading.RLock()
        self._http_queue = queue.Queue()
        self._http_workers_started = False
        self.jobs = []
        self.torrent_wrappers = {}  # {torrent_path: TorrentWrapper}
        self.load_jobs()
        self._repair_completed_local_paths()
        self._start_http_workers()

        if self.auto_resume:
            self.resume_incomplete_jobs()
        else:
            self.pause_incomplete_jobs()

    # ---------------- Internal helpers ---------------- #

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def _resolve_torrent_path(
        self,
        source: str,
        manufacturer: Optional[str],
        console: Optional[str],
        provider_slug: Optional[str] = None,
    ) -> str:
        parsed = urlparse(source)
        path = parsed.path or source
        filename = os.path.basename(path) or path.replace("/", "_")

        candidates = []
        has_console = manufacturer and manufacturer not in ("", "Unknown") and console and console not in ("", "Unknown")
        if has_console and provider_slug:
            candidates.append(torrent_file_path(manufacturer, console, filename, provider_slug))
        if has_console:
            candidates.append(torrent_file_path(manufacturer, console, filename))
        candidates.append(os.path.join(LEGACY_TORRENT_DIR, filename))

        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate

        # If the file does not exist anywhere yet, return the primary slot so callers can create it later.
        return candidates[0]

    def _resolve_downloaded_path(self, job: Dict) -> Optional[str]:
        raw = job.get("local_path")
        if raw:
            path = os.path.abspath(os.path.expanduser(raw))
            if os.path.exists(path):
                return path
        destination = job.get("destination")
        rom_name = job.get("rom_name")
        if not destination or not rom_name:
            return None
        destination_path = os.path.abspath(os.path.expanduser(destination))
        filename = os.path.basename(rom_name)
        direct = os.path.join(destination_path, filename)
        if os.path.exists(direct):
            return direct
        if os.path.isdir(destination_path):
            for root, _, files in os.walk(destination_path):
                if filename in files:
                    return os.path.join(root, filename)
        cached_archive = _cached_archive_path_for_job(
            http_url=job.get("http_url"),
            rom_name=job.get("rom_name"),
            manufacturer=job.get("cache_manufacturer") or job.get("manufacturer"),
            console=job.get("cache_console") or job.get("console"),
            provider_slug=job.get("provider_slug"),
            archive_member_path=job.get("archive_member_path"),
        )
        if cached_archive:
            return cached_archive
        torrent_match = self._resolve_torrent_downloaded_path(job, destination_path)
        if torrent_match:
            return torrent_match
        return None

    def _resolve_torrent_downloaded_path(self, job: Dict, destination_path: str) -> Optional[str]:
        source = job.get("source")
        if not source:
            return None
        try:
            torrent_path = self._resolve_torrent_path(
                source,
                job.get("cache_manufacturer") or job.get("manufacturer"),
                job.get("cache_console") or job.get("console"),
                job.get("provider_slug"),
            )
            if not os.path.exists(torrent_path):
                return None
            info = lt.torrent_info(torrent_path)
            files = info.files()
        except Exception:
            return None

        rom_name_lower = str(job.get("rom_name") or "").lower()
        for idx in range(files.num_files()):
            torrent_rel = files.file_path(idx)
            basename = os.path.basename(torrent_rel).lower()
            if not _torrent_file_name_matches(rom_name_lower, basename):
                continue
            candidate = os.path.join(destination_path, torrent_rel)
            if os.path.exists(candidate):
                return candidate
        return None

    def _repair_completed_local_paths(self) -> None:
        changed = False
        with self._lock:
            for job in self.jobs:
                if job.get("status") != "completed":
                    continue
                resolved = self._resolve_downloaded_path(job)
                if resolved and job.get("local_path") != resolved:
                    job["local_path"] = resolved
                    changed = True
            if changed:
                self._write_jobs_to_disk()

    # ---------------- Persistence ---------------- #

    def load_jobs(self):
        with self._lock:
            if os.path.exists(JOBS_FILE):
                try:
                    with open(JOBS_FILE) as f:
                        self.jobs = json.load(f)
                except json.JSONDecodeError:
                    backup_path = f"{JOBS_FILE}.invalid"
                    try:
                        shutil.copy2(JOBS_FILE, backup_path)
                        self._log(f"⚠️ Invalid jobs.json, saved backup to {backup_path}.")
                    except OSError:
                        self._log("⚠️ Invalid jobs.json, resetting file.")
                    self.jobs = []
            else:
                self.jobs = []
        return self.jobs

    def _write_jobs_to_disk(self):
        temp_path = f"{JOBS_FILE}.tmp"
        with open(temp_path, "w") as f:
            json.dump(self.jobs, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, JOBS_FILE)

    def save_jobs(self):
        with self._lock:
            self._write_jobs_to_disk()

    # ---------------- Job Management ---------------- #

    def add_job(
        self,
        rom_name,
        source=None,
        destination="./Downloads",
        console="Unknown",
        manufacturer=None,
        size_bytes=None,
        md5=None,
        http_url=None,
        provider_slug=None,
        cache_manufacturer=None,
        cache_console=None,
        auto_install=False,
        archive_member_path=None,
    ):
        """Add a new download job; reuse existing torrent handle if possible."""
        with self._lock:
            completed = next(
                (
                    j
                    for j in self.jobs
                    if j.get("status") == "completed"
                    and _same_job_identity(
                        j,
                        rom_name=rom_name,
                        console=console,
                        provider_slug=provider_slug,
                        archive_member_path=archive_member_path,
                    )
                ),
                None,
            )
            if completed:
                local_path = completed.get("local_path") or os.path.join(destination, os.path.basename(rom_name))
                if os.path.exists(local_path):
                    if source and not completed.get("source"):
                        completed["source"] = source
                    if http_url and not completed.get("http_url"):
                        completed["http_url"] = http_url
                    if auto_install:
                        completed["auto_install"] = True
                    if archive_member_path:
                        completed["archive_member_path"] = archive_member_path
                    completed.setdefault("protocol", "local")
                    completed.setdefault("local_path", local_path)
                    self._write_jobs_to_disk()
                    self._log(f"✅ {rom_name} already in library at {local_path}")
                    return completed

            existing = next(
                (
                    j
                    for j in self.jobs
                    if j.get("status") in ("downloading", "queued")
                    and _same_job_identity(
                        j,
                        rom_name=rom_name,
                        console=console,
                        provider_slug=provider_slug,
                        archive_member_path=archive_member_path,
                    )
                ),
                None
            )
            if existing:
                self._log(f"⚠️ Job for {rom_name} already exists, skipping.")
                if console and existing.get("console") in (None, "Unknown"):
                    existing["console"] = console
                if manufacturer and existing.get("manufacturer") in (None, "Unknown"):
                    existing["manufacturer"] = manufacturer
                if size_bytes is not None and not existing.get("size_bytes"):
                    existing["size_bytes"] = size_bytes
                if md5 and not existing.get("md5"):
                    existing["md5"] = md5
                if source:
                    existing["source"] = source
                if http_url:
                    existing["http_url"] = http_url
                if provider_slug and not existing.get("provider_slug"):
                    existing["provider_slug"] = provider_slug
                if cache_manufacturer and not existing.get("cache_manufacturer"):
                    existing["cache_manufacturer"] = cache_manufacturer
                if cache_console and not existing.get("cache_console"):
                    existing["cache_console"] = cache_console
                if auto_install:
                    existing["auto_install"] = True
                if archive_member_path and not existing.get("archive_member_path"):
                    existing["archive_member_path"] = archive_member_path
                if existing.get("protocol") in (None, "Unknown"):
                    existing["protocol"] = "torrent" if source else "http"
                self._write_jobs_to_disk()
                return existing

            filename = _download_filename_for_job(rom_name, http_url, archive_member_path)
            target_path = os.path.join(destination, filename)
            cached_archive_path = _cached_archive_path_for_job(
                http_url=http_url,
                rom_name=rom_name,
                manufacturer=cache_manufacturer or manufacturer,
                console=cache_console or console,
                provider_slug=provider_slug,
                archive_member_path=archive_member_path,
            )
            if cached_archive_path:
                job = {
                    "id": self._next_job_id(),
                    "rom_name": rom_name,
                    "source": source,
                    "http_url": http_url,
                    "destination": destination,
                    "console": console,
                    "manufacturer": manufacturer or "Unknown",
                    "protocol": "local",
                    "status": "completed",
                    "progress": 100.0,
                    "speed_kb": 0.0,
                    "peers": 0,
                    "added": datetime.now().isoformat(),
                    "size_bytes": size_bytes,
                    "md5": md5,
                    "local_path": cached_archive_path,
                    "provider_slug": provider_slug,
                    "cache_manufacturer": cache_manufacturer,
                    "cache_console": cache_console,
                    "auto_install": bool(auto_install),
                    "archive_member_path": archive_member_path,
                }
                self.jobs.append(job)
                self._write_jobs_to_disk()
                self._log(f"✅ Reusing indexed provider archive at {cached_archive_path}")
                return job
            if os.path.exists(target_path):
                job = {
                    "id": self._next_job_id(),
                    "rom_name": rom_name,
                    "source": source,
                    "http_url": http_url,
                    "destination": destination,
                    "console": console,
                    "manufacturer": manufacturer or "Unknown",
                    "protocol": "local",
                    "status": "completed",
                    "progress": 100.0,
                    "speed_kb": 0.0,
                    "peers": 0,
                    "added": datetime.now().isoformat(),
                    "size_bytes": size_bytes,
                    "md5": md5,
                    "local_path": target_path,
                    "provider_slug": provider_slug,
                    "cache_manufacturer": cache_manufacturer,
                    "cache_console": cache_console,
                    "auto_install": bool(auto_install),
                    "archive_member_path": archive_member_path,
                }
                self.jobs.append(job)
                self._write_jobs_to_disk()
                self._log(f"✅ Skipping download; file already exists at {target_path}")
                return job

            protocol = None
            wrapper = None
            torrent_path = None

            def unavailable_torrent_job(message: str):
                job = {
                    "id": self._next_job_id(),
                    "rom_name": rom_name,
                    "source": source,
                    "http_url": http_url,
                    "destination": destination,
                    "console": console,
                    "manufacturer": manufacturer or "Unknown",
                    "protocol": "torrent",
                    "status": "not_found",
                    "progress": 0.0,
                    "speed_kb": 0.0,
                    "peers": 0,
                    "added": datetime.now().isoformat(),
                    "size_bytes": size_bytes,
                    "md5": md5,
                    "provider_slug": provider_slug,
                    "cache_manufacturer": cache_manufacturer,
                    "cache_console": cache_console,
                    "auto_install": bool(auto_install),
                    "archive_member_path": archive_member_path,
                    "error": message,
                }
                self.jobs.append(job)
                self._write_jobs_to_disk()
                self._log(f"⚠️ {message}")
                return job

            if source:
                protocol = "torrent"
                torrent_path = self._resolve_torrent_path(
                    source,
                    cache_manufacturer or manufacturer,
                    cache_console or console,
                    provider_slug,
                )
                torrent_name = os.path.basename(torrent_path)

                if not os.path.exists(torrent_path):
                    return unavailable_torrent_job(
                        f"Missing torrent file: {torrent_path}. Fetch this provider or use HTTP fallback."
                    )

                self._log(f"🌀 Using torrent file: {torrent_name}")

                try:
                    if torrent_path not in self.torrent_wrappers:
                        self.torrent_wrappers[torrent_path] = TorrentWrapper(
                            torrent_path,
                            destination,
                            self.session,
                            log=self._log,
                        )
                    wrapper = self.torrent_wrappers[torrent_path]
                except Exception as exc:
                    return unavailable_torrent_job(f"Invalid torrent file for {rom_name}: {exc}")

            elif http_url:
                protocol = "http"
            else:
                raise ValueError("No download source provided (torrent or HTTP URL required).")

            job = {
                "id": self._next_job_id(),
                "rom_name": rom_name,
                "source": source,
                "http_url": http_url,
                "destination": destination,
                "console": console,
                "manufacturer": manufacturer or "Unknown",
                "protocol": protocol,
                "status": "queued",
                "progress": 0.0,
                "speed_kb": 0.0,
                "peers": 0,
                "added": datetime.now().isoformat(),
                "size_bytes": size_bytes,
                "md5": md5,
                "provider_slug": provider_slug,
                "cache_manufacturer": cache_manufacturer,
                "cache_console": cache_console,
                "auto_install": bool(auto_install),
                "archive_member_path": archive_member_path,
            }
            self.jobs.append(job)

            if protocol == "torrent":
                ok = wrapper.add_file_job(job)
                if ok:
                    job["status"] = "downloading"
                else:
                    job["status"] = "not_found"
                self._write_jobs_to_disk()
                if ok:
                    t = threading.Thread(target=self._monitor_torrent, args=(wrapper,), daemon=True)
                    t.start()
                return job

            # HTTP protocol
            job["status"] = "downloading"
            self._write_jobs_to_disk()

        self._enqueue_http(job)

        return job

    def list_jobs(self):
        with self._lock:
            return [job.copy() for job in self.jobs]

    def _next_job_id(self) -> int:
        ids = [int(job.get("id") or 0) for job in self.jobs if str(job.get("id") or "").isdigit()]
        return (max(ids) if ids else 0) + 1

    def update_job_fields(self, job_id, **fields):
        with self._lock:
            for job in self.jobs:
                if job.get("id") == job_id:
                    job.update(fields)
                    self._write_jobs_to_disk()
                    return job.copy()
        return None

    def _start_http_workers(self) -> None:
        if self._http_workers_started:
            return
        self._http_workers_started = True
        for idx in range(HTTP_WORKER_COUNT):
            thread = threading.Thread(
                target=self._http_worker,
                name=f"rom-manager-http-{idx + 1}",
                daemon=True,
            )
            thread.start()

    def _enqueue_http(self, job: Dict) -> None:
        self._http_queue.put(job)

    def _http_worker(self) -> None:
        while True:
            job = self._http_queue.get()
            try:
                self._download_http(job)
            finally:
                self._http_queue.task_done()

    def remove_job(self, job_id):
        with self._lock:
            self.jobs = [j for j in self.jobs if j["id"] != job_id]
            self._write_jobs_to_disk()

    def pause_incomplete_jobs(self):
        """Park previously active jobs without opening network/file handles."""
        changed = False
        with self._lock:
            for job in self.jobs:
                if job.get("status") in ("downloading", "queued"):
                    job["status"] = "paused"
                    job["speed_kb"] = 0.0
                    job["peers"] = 0
                    changed = True
            if changed:
                self._write_jobs_to_disk()

    def resume_incomplete_jobs(self):
        """Resume all previously queued or downloading jobs."""
        self._log("🔁 Resuming incomplete jobs...")
        wrappers_to_monitor = set()

        with self._lock:
            torrents_grouped = {}
            for job in self.jobs:
                if job["status"] in ("downloading", "queued"):
                    protocol = job.get("protocol") or ("torrent" if job.get("source") else "http")
                    job["protocol"] = protocol
                    if protocol == "torrent":
                        src = job["source"]
                        if not src:
                            job["status"] = "error"
                            job["error"] = "Missing torrent source"
                            continue
                        manufacturer = job.get("manufacturer")
                        console = job.get("console")
                        torrent_path = self._resolve_torrent_path(
                            src,
                            job.get("cache_manufacturer") or manufacturer,
                            job.get("cache_console") or console,
                            job.get("provider_slug"),
                        )
                        torrents_grouped.setdefault(torrent_path, []).append(job)
                    elif protocol == "http":
                        job["status"] = "downloading"
                        self._enqueue_http(job)

            for torrent_path, jobs in torrents_grouped.items():
                if not os.path.exists(torrent_path):
                    for job in jobs:
                        if job.get("http_url"):
                            job["protocol"] = "http"
                            job["source"] = None
                            job["status"] = "downloading"
                            job["error"] = None
                            self._enqueue_http(job)
                        else:
                            job["status"] = "error"
                            job["error"] = f"Missing torrent file: {torrent_path}"
                    continue
                wrapper = self.torrent_wrappers.get(torrent_path)
                if not wrapper:
                    destination = jobs[0].get("destination", DOWNLOADS_DIR)
                    try:
                        wrapper = TorrentWrapper(torrent_path, destination, self.session, log=self._log)
                    except Exception as exc:
                        for job in jobs:
                            if job.get("http_url"):
                                job["protocol"] = "http"
                                job["source"] = None
                                job["status"] = "downloading"
                                job["error"] = None
                                self._enqueue_http(job)
                            else:
                                job["status"] = "error"
                                job["error"] = f"Invalid torrent file: {exc}"
                        continue
                    self.torrent_wrappers[torrent_path] = wrapper
                for job in jobs:
                    ok = wrapper.add_file_job(job)
                    if ok and job.get("status") != "completed":
                        job["status"] = "downloading"
                wrappers_to_monitor.add(wrapper)
            self._write_jobs_to_disk()

        for wrapper in wrappers_to_monitor:
            t = threading.Thread(target=self._monitor_torrent, args=(wrapper,), daemon=True)
            t.start()

    # ---------------- Torrent Monitor Loop ---------------- #

    def _monitor_torrent(self, wrapper: "TorrentWrapper"):
        """Continuously update progress for all jobs in a given torrent."""
        while True:
            try:
                with self._lock:
                    done = wrapper.update_progress()
                    self._write_jobs_to_disk()

                if done:
                    self._log(f"✅ Torrent {os.path.basename(wrapper.torrent_path)} completed")
                    break

                time.sleep(2)
            except Exception as e:
                self._log(f"⚠️ Monitor error for {wrapper.torrent_path}: {e}")
                break

    # ---------------- HTTP Downloader ---------------- #

    def _download_http(self, job: Dict) -> None:
        url = job.get("http_url") or job.get("source")
        if not url:
            with self._lock:
                job["status"] = "error"
                job["error"] = "Missing HTTP source URL"
                self._write_jobs_to_disk()
            return

        parsed = urlparse(url)
        if parsed.path:
            safe_path = quote(parsed.path, safe="/%")
            parsed = parsed._replace(path=safe_path)
            url = urlunparse(parsed)

        destination_dir = job.get("destination") or DOWNLOADS_DIR
        os.makedirs(destination_dir, exist_ok=True)
        filename = _download_filename_for_job(
            job["rom_name"],
            job.get("http_url") or job.get("source"),
            job.get("archive_member_path"),
        )
        filepath = os.path.join(destination_dir, filename)

        try:
            with urllib.request.urlopen(url) as response:
                total = int(response.headers.get("Content-Length") or 0)
                chunk_size = 64 * 1024
                downloaded = 0
                start = time.time()
                last_save = 0.0

                with open(filepath, "wb") as out:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_save >= PROGRESS_SAVE_INTERVAL_SECONDS:
                            with self._lock:
                                job["progress"] = round(downloaded / total * 100, 2) if total else 0.0
                                elapsed = max(now - start, 0.001)
                                job["speed_kb"] = round(downloaded / elapsed / 1024, 2)
                                job["peers"] = 0
                                self._write_jobs_to_disk()
                            last_save = now

            with self._lock:
                job["status"] = "completed"
                job["progress"] = 100.0
                job["speed_kb"] = 0.0
                job["peers"] = 0
                job["local_path"] = filepath
                self._write_jobs_to_disk()
            self._log(f"✅ Downloaded {job['rom_name']} via HTTP")
        except Exception as exc:
            with self._lock:
                job["status"] = "error"
                job["error"] = str(exc)
                self._write_jobs_to_disk()
            self._log(f"⚠️ HTTP download failed for {job['rom_name']}: {exc}")
