#!/usr/bin/env python3
import json
import os
import threading
import time
import urllib.request
from datetime import datetime
from typing import Dict, Optional
from urllib.parse import urlparse, urlunparse, quote

import libtorrent as lt

from utils.paths import DATA_DIR, torrent_file_path

# ---------------- Paths ---------------- #
BASE_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))

LEGACY_TORRENT_DIR = os.path.join(DATA_DIR, "torrents")        # backwards compatibility
DOWNLOADS_DIR = os.path.join(ROOT_DIR, "downloads")     # user ROM downloads
JOBS_FILE = os.path.join(DOWNLOADS_DIR, "jobs.json")

os.makedirs(LEGACY_TORRENT_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


# ======================================================================
# TorrentWrapper: Manages ONE .torrent file and multiple file jobs inside it
# ======================================================================
class TorrentWrapper:
    def __init__(self, torrent_path, destination, session):
        self.torrent_path = torrent_path
        self.destination = destination
        self.session = session
        self.info = lt.torrent_info(torrent_path)
        self.handle = session.add_torrent({"ti": self.info, "save_path": destination})
        self.jobs = {}  # rom_name -> {index, job}
        print(f"🌀 Added new torrent handle for: {os.path.basename(torrent_path)}")

    def add_file_job(self, job):
        """Register a specific ROM file inside this torrent for downloading."""
        rom_name = job["rom_name"]
        rom_name_lower = rom_name.lower()
        rom_base, rom_ext = os.path.splitext(rom_name_lower)
        files = self.info.files()
        matched_index = None

        # ✅ iterate using num_files() and file_path() — compatible with libtorrent v2
        for idx in range(files.num_files()):
            fpath = files.file_path(idx).lower()
            basename = os.path.basename(fpath)
            base_no_ext, _ = os.path.splitext(basename)

            # Exact filename match takes priority.
            if basename == rom_name_lower:
                matched_index = idx
                self.jobs[job["rom_name"]] = {"index": idx, "job": job}
                break

            # Fall back to matching on base name when extensions differ (e.g., regional variants).
            if rom_base and rom_base in base_no_ext:
                matched_index = idx
                self.jobs[job["rom_name"]] = {"index": idx, "job": job}
                break

        if matched_index is None:
            job["status"] = "not_found"
            print(f"⚠️ No matching file found for {job['rom_name']}")
            return False

        matched_path = files.file_path(matched_index)
        print(f"✅ Matched file: {matched_path}")
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

    def __init__(self):
        self.session = lt.session()
        self.session.listen_on(6881, 6891)
        print("✅ Torrent session initialized on ports 6881–6891")

        self._lock = threading.RLock()
        self.jobs = []
        self.torrent_wrappers = {}  # {torrent_path: TorrentWrapper}
        self.load_jobs()

        # Resume any incomplete jobs
        self.resume_incomplete_jobs()

    # ---------------- Internal helpers ---------------- #

    def _resolve_torrent_path(self, source: str, manufacturer: Optional[str], console: Optional[str]) -> str:
        parsed = urlparse(source)
        path = parsed.path or source
        filename = os.path.basename(path) or path.replace("/", "_")

        candidates = []
        if manufacturer and manufacturer not in ("", "Unknown") and console and console not in ("", "Unknown"):
            candidates.append(torrent_file_path(manufacturer, console, filename))
        candidates.append(os.path.join(LEGACY_TORRENT_DIR, filename))

        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate

        # If the file does not exist anywhere yet, return the primary slot so callers can create it later.
        return candidates[0]

    # ---------------- Persistence ---------------- #

    def load_jobs(self):
        with self._lock:
            if os.path.exists(JOBS_FILE):
                try:
                    with open(JOBS_FILE) as f:
                        self.jobs = json.load(f)
                except json.JSONDecodeError:
                    print("⚠️ Invalid jobs.json, resetting file.")
                    self.jobs = []
            else:
                self.jobs = []
        return self.jobs

    def _write_jobs_to_disk(self):
        with open(JOBS_FILE, "w") as f:
            json.dump(self.jobs, f, indent=2)

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
    ):
        """Add a new download job; reuse existing torrent handle if possible."""
        with self._lock:
            completed = next(
                (
                    j
                    for j in self.jobs
                    if j["rom_name"] == rom_name and j.get("status") == "completed"
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
                    completed.setdefault("protocol", "local")
                    completed.setdefault("local_path", local_path)
                    self._write_jobs_to_disk()
                    print(f"✅ {rom_name} already in library at {local_path}")
                    return completed

            existing = next(
                (j for j in self.jobs if j["rom_name"] == rom_name and j["status"] in ("downloading", "queued")),
                None
            )
            if existing:
                print(f"⚠️ Job for {rom_name} already exists, skipping.")
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
                if existing.get("protocol") in (None, "Unknown"):
                    existing["protocol"] = "torrent" if source else "http"
                self._write_jobs_to_disk()
                return existing

            filename = os.path.basename(rom_name)
            target_path = os.path.join(destination, filename)
            if os.path.exists(target_path):
                job = {
                    "id": len(self.jobs) + 1,
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
                }
                self.jobs.append(job)
                self._write_jobs_to_disk()
                print(f"✅ Skipping download; file already exists at {target_path}")
                return job

            protocol = None
            wrapper = None
            torrent_path = None

            if source:
                protocol = "torrent"
                torrent_path = self._resolve_torrent_path(source, manufacturer, console)
                torrent_name = os.path.basename(torrent_path)

                if not os.path.exists(torrent_path):
                    raise FileNotFoundError(
                        f"Missing torrent file: {torrent_path}\n"
                        "→ You may need to run `fetch` for this provider first."
                    )

                print(f"🌀 Using torrent file: {torrent_name}")

                if torrent_path not in self.torrent_wrappers:
                    self.torrent_wrappers[torrent_path] = TorrentWrapper(torrent_path, destination, self.session)

                wrapper = self.torrent_wrappers[torrent_path]

            elif http_url:
                protocol = "http"
            else:
                raise ValueError("No download source provided (torrent or HTTP URL required).")

            job = {
                "id": len(self.jobs) + 1,
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

        t = threading.Thread(target=self._download_http, args=(job,), daemon=True)
        t.start()

        return job

    def list_jobs(self):
        with self._lock:
            return [job.copy() for job in self.jobs]

    def remove_job(self, job_id):
        with self._lock:
            self.jobs = [j for j in self.jobs if j["id"] != job_id]
            self._write_jobs_to_disk()

    def resume_incomplete_jobs(self):
        """Resume all previously queued or downloading jobs."""
        print("🔁 Resuming incomplete jobs...")
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
                        torrent_path = self._resolve_torrent_path(src, manufacturer, console)
                        torrents_grouped.setdefault(torrent_path, []).append(job)
                    elif protocol == "http":
                        job["status"] = "downloading"
                        threading.Thread(target=self._download_http, args=(job,), daemon=True).start()

            for torrent_path, jobs in torrents_grouped.items():
                if not os.path.exists(torrent_path):
                    continue
                wrapper = self.torrent_wrappers.get(torrent_path)
                if not wrapper:
                    destination = jobs[0].get("destination", DOWNLOADS_DIR)
                    wrapper = TorrentWrapper(torrent_path, destination, self.session)
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
                    print(f"✅ Torrent {os.path.basename(wrapper.torrent_path)} completed")
                    break

                time.sleep(2)
            except Exception as e:
                print(f"⚠️ Monitor error for {wrapper.torrent_path}: {e}")
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
            safe_path = quote(parsed.path, safe="/")
            parsed = parsed._replace(path=safe_path)
            url = urlunparse(parsed)

        destination_dir = job.get("destination") or DOWNLOADS_DIR
        os.makedirs(destination_dir, exist_ok=True)
        filename = os.path.basename(job["rom_name"])
        filepath = os.path.join(destination_dir, filename)

        try:
            with urllib.request.urlopen(url) as response:
                total = int(response.headers.get("Content-Length") or 0)
                chunk_size = 64 * 1024
                downloaded = 0
                start = time.time()

                with open(filepath, "wb") as out:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        with self._lock:
                            job["progress"] = round(downloaded / total * 100, 2) if total else 0.0
                            elapsed = max(time.time() - start, 0.001)
                            job["speed_kb"] = round(downloaded / elapsed / 1024, 2)
                            job["peers"] = 0
                            self._write_jobs_to_disk()

            with self._lock:
                job["status"] = "completed"
                job["progress"] = 100.0
                job["speed_kb"] = 0.0
                job["peers"] = 0
                job["local_path"] = filepath
                self._write_jobs_to_disk()
            print(f"✅ Downloaded {job['rom_name']} via HTTP")
        except Exception as exc:
            with self._lock:
                job["status"] = "error"
                job["error"] = str(exc)
                self._write_jobs_to_disk()
            print(f"⚠️ HTTP download failed for {job['rom_name']}: {exc}")
