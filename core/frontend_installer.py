import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from data.storage.storage_config_loader import load_storage_config
from utils.cores_registry import load_registry
from utils.library_sync import load_modules


MIN_FREE_AFTER_INSTALL_BYTES = 512 * 1024 * 1024
NINTENDO_DSI_CONSOLE_NAME = "Nintendo - Nintendo DSi"
SONY_PLAYSTATION_VITA_CONSOLE_NAME = "Sony - PlayStation Vita"
MELONDS_SYSTEM_SUBDIR = "melonDS DS"
MELONDS_DSI_REQUIRED_FILES = (
    "bios7.bin",
    "bios9.bin",
    "dsi_bios7.bin",
    "dsi_bios9.bin",
    "dsi_firmware.bin",
    "dsi_nand.bin",
    "DSi_Nand_EUR.bin",
    "DSi_Nand_JPN.bin",
)
KNOWN_CONSOLE_GUIDS = {
    "Amstrad - GX4000": "e6efac9f-20f1-538e-80f8-152d9adf4678",
    "Atari - Jaguar": "5565f7db-0d0c-55fe-aa7e-fe719791d1b9",
    "Commodore - CD32": "b0ef410b-72a4-57b7-a26a-896c6d751c73",
    "Commodore - CDTV": "9c668a1c-e2b5-5a8c-9f2e-0ec7ef74dd3c",
    "Magnavox - Odyssey2": "a7d519f9-601d-539c-9d49-c7c7f1f17f16",
    "NEC - PC Engine - TurboGrafx 16": "2bd2cd12-6d99-5379-86dc-be6a5904e280",
    "NEC - PC Engine CD - TurboGrafx-CD": "1ff5f144-8d13-51b9-82c6-365169ba2455",
    "NEC - PC Engine SuperGrafx": "e63c6451-836b-5dbe-8532-63492762b3d9",
    "NEC - PC-FX": "98e5f218-c591-5a14-bd69-fa25b516c3ed",
    "Nintendo - Family Computer Disk System": "e8d2cd7c-e53c-5916-928d-9af9aa48c409",
    "Nintendo - Nintendo Entertainment System": "fad2a671-5d13-54b9-8562-3b058c610348",
    "Nintendo - Super Nintendo Entertainment System": "bf3926b2-106d-56c9-8166-0fe29c33bad0",
    "Nintendo - Wii": "ba79594a-da19-5127-9a46-9eee6bccf034",
    "Nintendo - Wii U": "f9bc37b4-6d5b-518c-bfe3-e3a9571332ab",
    "Nintendo - Pokemon Mini": "ed4e8377-e783-581f-ac90-9d8c98e481ae",
    "Nintendo - Satellaview": "78fd1160-3b20-5192-bd96-9c28af2e8a61",
    "Nintendo - Sufami Turbo": "1ab4ffbd-5190-5b57-9dc1-92b24fec2845",
    "Nintendo - Virtual Boy": "42411369-42f4-5fcc-9500-caa3ed220f32",
    "Philips - Videopac+": "390f4a10-ddc9-57bd-bfec-b08cb8250c7b",
    "Sega - SG-1000": "ca066374-6220-5f44-828c-302f50a10146",
    "Sony - PlayStation": "6cbd1842-2231-5e82-92fc-73613e2f21a8",
    "Sony - PlayStation 2": "f250f78e-521b-5d39-b11c-7754f2e8ebc6",
    "Sony - PlayStation Portable": "61637bb4-0f3b-5458-b458-8fcc6541c6f8",
    "The 3DO Company - 3DO": "2ba124e3-51ac-535f-b0fa-0ed4fd88ea00",
    "Tiger - Game.com": "cf88421d-dd86-5d2d-85d0-c640b1730604",
    "VTech - CreatiVision": "90188f7f-aeff-501b-a10f-f41fb617829f",
    "VTech - V.Smile": "c59cbdeb-e157-529b-b360-a193d53dc65b",
    "Watara - Supervision": "57249b7f-c969-55d0-a129-6a9eb48cd100",
}

ROM_EXTENSIONS = {
    ".fds",
    ".fpk",
    ".nes",
    ".zip",
    ".7z",
    ".unif",
    ".unf",
    ".a26",
    ".bin",
    ".chd",
    ".chf",
    ".cpr",
    ".cso",
    ".cue",
    ".ccd",
    ".gdi",
    ".gcm",
    ".gcz",
    ".3ds",
    ".3dsx",
    ".z3dsx",
    ".cci",
    ".zcci",
    ".cxi",
    ".zcxi",
    ".app",
    ".axf",
    ".rvz",
    ".wud",
    ".wux",
    ".wua",
    ".rpx",
    ".ciso",
    ".wbfs",
    ".wad",
    ".wia",
    ".tmd",
    ".tgc",
    ".dol",
    ".elf",
    ".img",
    ".iso",
    ".pbp",
    ".ecm",
    ".mds",
    ".psf",
    ".psexe",
    ".m3u",
    ".mds",
    ".nrg",
    ".toc",
    ".hex",
    ".arduboy",
    ".mgw",
    ".neo",
    ".ngc",
    ".ngp",
    ".n64",
    ".v64",
    ".z64",
    ".tgc",
    ".nds",
    ".ids",
    ".dsi",
    ".ws",
    ".wsc",
    ".a52",
    ".a78",
    ".u1",
    ".u3",
    ".ndd",
    ".col",
    ".cv",
    ".rom",
    ".j64",
    ".jag",
    ".vb",
    ".vboy",
    ".abs",
    ".sms",
    ".gg",
    ".gb",
    ".gbc",
    ".gba",
    ".agb",
    ".dmg",
    ".sgb",
    ".sg",
    ".sc",
    ".st2",
    ".st",
    ".bs",
    ".mv",
    ".md",
    ".gen",
    ".smd",
    ".sv",
    ".sfc",
    ".swc",
    ".fig",
    ".smc",
    ".min",
    ".32x",
    ".lnx",
    ".pce",
    ".sgx",
    ".cart",
    ".0",
    ".1",
    ".2",
    ".3",
    ".pkg",
    ".vci",
    ".vpk",
}

SKIP_NAMES = {
    "gamelist.txt",
    "...gamelist.txt",
    "update log.txt",
    "...updates.txt",
}

BIOS_ALIASES = {
    "amiga-ext-310-cd32.rom": "kick40060.CD32.ext",
    "amiga-os-310-cd32-ext.rom": "kick40060.CD32.ext",
    "amiga-os-310-cd32.rom": "kick40060.CD32",
    "cd32 extended-rom r40.60 (1993)(commodore)(cd32).rom": "kick40060.CD32.ext",
    "disksys.rom": "disksys.rom",
    "kick40060.cd32": "kick40060.CD32",
    "kick40060.cd32.ext": "kick40060.CD32.ext",
    "kickstart v3.1 r40.60 (1993)(commodore)(cd32).rom": "kick40060.CD32",
    "kickstart v3.1 rev 40.60 (1993)(commodore)(cd32).rom": "kick40060.CD32",
}


class InstallError(RuntimeError):
    pass


class InsufficientSpaceError(InstallError):
    def __init__(self, target: Path, required: int, available: int, reserve: int) -> None:
        self.target = target
        self.required = required
        self.available = available
        self.reserve = reserve
        shortfall = required + reserve - available
        super().__init__(
            "Not enough disk space for install. "
            f"Target: {target}; required: {_format_bytes(required)}; "
            f"available: {_format_bytes(available)}; reserve: {_format_bytes(reserve)}; "
            f"short by: {_format_bytes(max(shortfall, 0))}."
        )


def install_completed_jobs(jobs: Iterable[Dict]) -> Dict[str, object]:
    completed = [job for job in jobs if job.get("status") == "completed"]
    targets = _frontend_install_targets(completed)
    if not targets:
        installer = FrontendInstaller()
        return installer.install_completed_jobs(completed)

    reports = []
    errors: List[str] = []
    for frontend_key, frontend, target_jobs in targets:
        try:
            installer = FrontendInstaller(frontend_key=frontend_key, frontend=frontend)
            reports.append(installer.install_completed_jobs(target_jobs))
        except Exception as exc:
            errors.append(f"{frontend.get('name') or frontend_key}: {exc}")

    if not reports and errors:
        raise InstallError("; ".join(errors))
    return _combine_install_reports(reports, errors)


def _frontend_install_targets(jobs: List[Dict]) -> List[Tuple[str, Dict, List[Dict]]]:
    if not jobs:
        return []

    config = load_storage_config()
    frontends = config.get("frontends") or {}
    module_guid_by_name = {
        module.get("name"): module.get("guid")
        for module in load_modules()
        if module.get("name") and module.get("guid")
    }

    targets: List[Tuple[str, Dict, List[Dict]]] = []
    active_keys = {
        key
        for key, frontend in frontends.items()
        if isinstance(frontend, dict) and frontend.get("active")
    }
    for key, frontend in frontends.items():
        if not isinstance(frontend, dict):
            continue
        if active_keys and key not in active_keys:
            continue
        supported = set(frontend.get("supported_guids") or [])
        if not supported:
            continue
        target_jobs = [
            job
            for job in jobs
            if _job_console_guid(job, module_guid_by_name) in supported
        ]
        if target_jobs:
            targets.append((key, frontend, target_jobs))
    return targets


def _job_console_guid(job: Dict, module_guid_by_name: Dict[str, str]) -> Optional[str]:
    manufacturer = job.get("manufacturer") or "Unknown"
    console = job.get("console") or "Unknown"
    if manufacturer == console:
        console_name = str(console)
    else:
        console_name = f"{manufacturer} - {console}"
    return (
        module_guid_by_name.get(console_name)
        or module_guid_by_name.get(str(console))
        or KNOWN_CONSOLE_GUIDS.get(console_name)
        or KNOWN_CONSOLE_GUIDS.get(str(console))
    )


def _combine_install_reports(reports: List[Dict[str, object]], errors: List[str]) -> Dict[str, object]:
    if not reports:
        return {
            "frontend_key": None,
            "frontend": None,
            "frontends": [],
            "roms_root": "",
            "bios_root": "",
            "playlists_root": "",
            "jobs_seen": 0,
            "roms_installed": 0,
            "roms_skipped": 0,
            "bios_installed": 0,
            "bios_skipped": 0,
            "bytes_required": 0,
            "bytes_available": None,
            "space_checked": False,
            "playlists_written": [],
            "installed_paths_by_job": {},
            "errors": errors,
        }

    combined = dict(reports[0])
    combined["frontend_key"] = ",".join(str(report.get("frontend_key")) for report in reports if report.get("frontend_key"))
    combined["frontend"] = ", ".join(str(report.get("frontend")) for report in reports if report.get("frontend"))
    combined["frontends"] = reports
    if len(reports) > 1:
        combined["roms_root"] = "Multiple frontends"
        combined["bios_root"] = "Multiple frontends"
        combined["playlists_root"] = "Multiple frontends"
    for field in ("roms_installed", "roms_skipped", "bios_installed", "bios_skipped", "bytes_required"):
        combined[field] = sum(int(report.get(field) or 0) for report in reports)
    combined["jobs_seen"] = max(int(report.get("jobs_seen") or 0) for report in reports)
    available_values = [report.get("bytes_available") for report in reports if report.get("bytes_available") is not None]
    combined["bytes_available"] = min(available_values) if available_values else None
    combined["space_checked"] = any(bool(report.get("space_checked")) for report in reports)
    combined["playlists_written"] = [
        path
        for report in reports
        for path in (report.get("playlists_written") or [])
    ]
    combined["installed_paths_by_job"] = {}
    for report in reports:
        installed_by_job = report.get("installed_paths_by_job") or {}
        if isinstance(installed_by_job, dict):
            combined["installed_paths_by_job"].update(installed_by_job)
    combined["errors"] = [
        str(error)
        for report in reports
        for error in (report.get("errors") or [])
    ] + errors
    return combined


class FrontendInstaller:
    def __init__(self, frontend_key: Optional[str] = None, frontend: Optional[Dict] = None) -> None:
        if frontend_key and frontend:
            self.frontend_key = frontend_key
            self.frontend = frontend
        else:
            self.frontend_key, self.frontend = self._active_frontend()
        self.registry = load_registry()
        self.module_guid_by_name = {
            module.get("name"): module.get("guid")
            for module in load_modules()
            if module.get("name") and module.get("guid")
        }
        self.roms_root = Path(self.frontend.get("roms_path", "~/ROMs")).expanduser()
        self.bios_root = Path(self.frontend.get("bios_path", "~/BIOS")).expanduser()
        retroarch_root = self._retroarch_root(self.roms_root)
        self.playlists_root = Path(self.frontend.get("playlists_path") or retroarch_root / "playlists").expanduser()
        self.cores_root = Path(self.frontend.get("cores_path") or retroarch_root / "cores").expanduser()

    def install_completed_jobs(self, jobs: Iterable[Dict]) -> Dict[str, object]:
        completed = [job for job in jobs if job.get("status") == "completed"]
        if self.frontend.get("kind") == "external_emulator":
            return self._install_external_completed_jobs(completed)
        report: Dict[str, object] = {
            "frontend_key": self.frontend_key,
            "frontend": self.frontend.get("name") or self.frontend_key,
            "roms_root": str(self.roms_root),
            "bios_root": str(self.bios_root),
            "playlists_root": str(self.playlists_root),
            "jobs_seen": len(completed),
            "roms_installed": 0,
            "roms_skipped": 0,
            "bios_installed": 0,
            "bios_skipped": 0,
            "bytes_required": 0,
            "bytes_available": None,
            "space_checked": False,
            "playlists_written": [],
            "installed_paths_by_job": {},
            "errors": [],
        }

        installed_by_console: Dict[Tuple[str, str], List[Path]] = {}
        touched_consoles: set[Tuple[str, str]] = set()
        self.roms_root.mkdir(parents=True, exist_ok=True)
        self.bios_root.mkdir(parents=True, exist_ok=True)
        self.playlists_root.mkdir(parents=True, exist_ok=True)
        self._preflight_space(completed, report)

        grouped_jobs = self._archive_member_job_groups(completed)
        grouped_ids = {
            id(job)
            for jobs in grouped_jobs.values()
            for job in jobs
        }

        for (local_path, manufacturer, console), jobs in grouped_jobs.items():
            try:
                with tempfile.TemporaryDirectory(prefix="rom-manager-install-batch-") as tmp:
                    extract_root = Path(tmp)
                    self._extract_archive_tree(local_path, extract_root)
                    for job in jobs:
                        try:
                            before_counts = self._install_report_counts(report)
                            only = self._selected_archive_member(extract_root, job.get("archive_member_path"))
                            installed = self._install_tree(
                                extract_root,
                                manufacturer,
                                console,
                                report,
                                only=only,
                                install_name=job.get("rom_name"),
                            )
                            self._ensure_install_found_content(report, before_counts, job)
                            installed_by_console.setdefault((manufacturer, console), []).extend(installed)
                            self._record_installed_paths(report, job, installed)
                        except Exception as exc:
                            report["errors"].append(str(exc))
                touched_consoles.add((manufacturer, console))
            except Exception as exc:
                report["errors"].append(str(exc))

        for job in completed:
            if id(job) in grouped_ids:
                continue
            try:
                local_path = self._job_local_path(job)
                if not local_path or not local_path.exists():
                    raise InstallError(f"Downloaded file missing for {job.get('rom_name')}")
                manufacturer = job.get("manufacturer") or "Unknown"
                console = job.get("console") or "Unknown"
                before_counts = self._install_report_counts(report)
                installed = self._install_file(
                    local_path,
                    manufacturer,
                    console,
                    report,
                    job.get("archive_member_path"),
                    job.get("rom_name"),
                )
                self._ensure_install_found_content(report, before_counts, job)
                touched_consoles.add((manufacturer, console))
                installed_by_console.setdefault((manufacturer, console), []).extend(installed)
                self._record_installed_paths(report, job, installed)
            except Exception as exc:
                report["errors"].append(str(exc))

        for manufacturer, console in touched_consoles:
            playlist = self._write_playlist(manufacturer, console)
            report["playlists_written"].append(str(playlist))

        return report

    def _install_external_completed_jobs(self, completed: List[Dict]) -> Dict[str, object]:
        report: Dict[str, object] = {
            "frontend_key": self.frontend_key,
            "frontend": self.frontend.get("name") or self.frontend_key,
            "roms_root": str(self.roms_root),
            "bios_root": str(self.bios_root),
            "playlists_root": "",
            "jobs_seen": len(completed),
            "roms_installed": 0,
            "roms_skipped": 0,
            "bios_installed": 0,
            "bios_skipped": 0,
            "bytes_required": 0,
            "bytes_available": None,
            "space_checked": False,
            "playlists_written": [],
            "installed_paths_by_job": {},
            "errors": [],
        }
        self.roms_root.mkdir(parents=True, exist_ok=True)
        self.bios_root.mkdir(parents=True, exist_ok=True)
        self._preflight_external_space(completed, report)

        install_type = str(self.frontend.get("install_type") or "").lower()
        for job in completed:
            manufacturer = job.get("manufacturer") or "Unknown"
            console = job.get("console") or "Unknown"
            console_name = self._console_name(manufacturer, console)
            try:
                local_path = self._job_local_path(job)
                if not local_path or not local_path.exists():
                    raise InstallError(f"Downloaded file missing for {job.get('rom_name')}")
                if install_type == "vita3k" and console_name == SONY_PLAYSTATION_VITA_CONSOLE_NAME:
                    self._stage_vita3k_package(local_path, job, report)
                    continue
                raise InstallError(f"No external install strategy for {console_name}.")
            except Exception as exc:
                report["errors"].append(str(exc))
        return report

    def _preflight_external_space(self, jobs: List[Dict], report: Dict[str, object]) -> None:
        required = 0
        planned_hashes: set[str] = set()
        existing_hashes = self._existing_file_hashes(self.roms_root / "_roms_manager_imports")
        for job in jobs:
            local_path = self._job_local_path(job)
            if not local_path or not local_path.exists() or not local_path.is_file():
                continue
            digest = self._sha256(local_path)
            if digest in existing_hashes or digest in planned_hashes:
                continue
            planned_hashes.add(digest)
            required += local_path.stat().st_size

        usage = shutil.disk_usage(self.roms_root)
        available = usage.free
        report["bytes_required"] = required
        report["bytes_available"] = available
        report["space_checked"] = True
        if required + MIN_FREE_AFTER_INSTALL_BYTES > available:
            raise InsufficientSpaceError(
                self.roms_root,
                required,
                available,
                MIN_FREE_AFTER_INSTALL_BYTES,
            )

    def _stage_vita3k_package(self, source: Path, job: Dict, report: Dict[str, object]) -> None:
        suffix = source.suffix.lower()
        if suffix == ".pkg":
            raise InstallError(
                "Vita3K .pkg installs require a zRIF/license key; ROMs Manager does not store "
                "or automate those keys yet."
            )
        if suffix not in {".vpk", ".zip", ".vci"}:
            raise InstallError(f"Unsupported Vita3K package format: {suffix or source.name}")

        staging_root = self.roms_root / "_roms_manager_imports"
        staging_root.mkdir(parents=True, exist_ok=True)
        target = staging_root / _safe_cli_filename(job.get("rom_name") or source.name)
        if target.exists() and self._same_file(source, target):
            report["roms_skipped"] += 1
            self._record_installed_paths(report, job, [target])
            return
        if target.exists():
            target = self._unique_target(target)
        shutil.copy2(source, target)
        report["roms_installed"] += 1
        self._record_installed_paths(report, job, [target])
        report["errors"].append(
            "Vita3K package staged locally. Automatic Vita3K import/launch is still pending; "
            "open Vita3K and install the staged package manually for now."
        )

    @staticmethod
    def _record_installed_paths(report: Dict[str, object], job: Dict, installed: List[Path]) -> None:
        job_id = job.get("id")
        if job_id is None:
            return
        paths = [str(path) for path in installed if path]
        if not paths:
            return
        installed_by_job = report.setdefault("installed_paths_by_job", {})
        if isinstance(installed_by_job, dict):
            installed_by_job[str(job_id)] = paths

    @staticmethod
    def _install_report_counts(report: Dict[str, object]) -> Tuple[int, int, int, int]:
        return (
            int(report.get("roms_installed") or 0),
            int(report.get("roms_skipped") or 0),
            int(report.get("bios_installed") or 0),
            int(report.get("bios_skipped") or 0),
        )

    @staticmethod
    def _ensure_install_found_content(
        report: Dict[str, object],
        before_counts: Tuple[int, int, int, int],
        job: Dict,
    ) -> None:
        after_counts = FrontendInstaller._install_report_counts(report)
        if after_counts == before_counts:
            raise InstallError(f"No playable ROM or BIOS file found for {job.get('rom_name')}")

    def _archive_member_job_groups(self, jobs: List[Dict]) -> Dict[Tuple[Path, str, str], List[Dict]]:
        groups: Dict[Tuple[Path, str, str], List[Dict]] = {}
        for job in jobs:
            if not job.get("archive_member_path"):
                continue
            local_path = self._job_local_path(job)
            if not local_path or not local_path.exists():
                continue
            manufacturer = job.get("manufacturer") or "Unknown"
            console = job.get("console") or "Unknown"
            if self._should_preserve_archive(local_path, manufacturer, console, job.get("archive_member_path")):
                continue
            if not (
                (local_path.suffix.lower() == ".zip" and zipfile.is_zipfile(local_path))
                or self._can_extract_archive(local_path)
            ):
                continue
            groups.setdefault((local_path, manufacturer, console), []).append(job)
        return {
            key: grouped
            for key, grouped in groups.items()
            if len(grouped) > 1
        }

    def _preflight_space(self, jobs: List[Dict], report: Dict[str, object]) -> None:
        required = 0
        existing_hashes_by_console: Dict[Tuple[str, str], set[str]] = {}
        planned_hashes_by_console: Dict[Tuple[str, str], set[str]] = {}
        bios_hashes = self._existing_file_hashes(self.bios_root)
        planned_bios_hashes: set[str] = set()

        def account_item(item: Dict[str, object], key: Tuple[str, str]) -> None:
            nonlocal required
            if item["kind"] == "bios":
                digest = item["sha256"]
                if digest in bios_hashes or digest in planned_bios_hashes:
                    return
                planned_bios_hashes.add(str(digest))
                required += int(item["size"])
                return

            console_dir = self.roms_root / f"{key[0]} - {key[1]}"
            existing_hashes = existing_hashes_by_console.setdefault(key, self._existing_hashes(console_dir))
            planned_hashes = planned_hashes_by_console.setdefault(key, set())
            digest = item["sha256"]
            if digest in existing_hashes or digest in planned_hashes:
                return
            planned_hashes.add(str(digest))
            required += int(item["size"])

        grouped_jobs = self._archive_member_job_groups(jobs)
        grouped_ids = {
            id(job)
            for grouped in grouped_jobs.values()
            for job in grouped
        }
        for (local_path, manufacturer, console), grouped in grouped_jobs.items():
            key = (manufacturer, console)
            with tempfile.TemporaryDirectory(prefix="rom-manager-space-batch-") as tmp:
                extract_root = Path(tmp)
                self._extract_archive_tree(local_path, extract_root)
                for job in grouped:
                    for candidate in self._iter_install_candidates_from_tree(
                        extract_root,
                        job.get("archive_member_path"),
                        manufacturer,
                        console,
                    ):
                        account_item(candidate, key)

        for job in jobs:
            if id(job) in grouped_ids:
                continue
            local_path = self._job_local_path(job)
            if not local_path or not local_path.exists():
                continue
            manufacturer = job.get("manufacturer") or "Unknown"
            console = job.get("console") or "Unknown"
            key = (manufacturer, console)
            for item in self._iter_install_candidates(
                local_path,
                job.get("archive_member_path"),
                manufacturer,
                console,
            ):
                account_item(item, key)

        usage = shutil.disk_usage(self.roms_root)
        available = usage.free
        report["bytes_required"] = required
        report["bytes_available"] = available
        report["space_checked"] = True
        if required + MIN_FREE_AFTER_INSTALL_BYTES > available:
            raise InsufficientSpaceError(
                self.roms_root,
                required,
                available,
                MIN_FREE_AFTER_INSTALL_BYTES,
            )

    def _install_file(
        self,
        source: Path,
        manufacturer: str,
        console: str,
        report: Dict[str, object],
        archive_member_path: Optional[str] = None,
        install_name: Optional[str] = None,
    ) -> List[Path]:
        if self._is_nintendo_dsi(manufacturer, console):
            return self._install_dsiware_file(source, manufacturer, console, report, archive_member_path, install_name)
        if self._should_preserve_archive(source, manufacturer, console, archive_member_path):
            return self._install_tree(source.parent, manufacturer, console, report, only=source)
        if source.suffix.lower() == ".zip" and zipfile.is_zipfile(source):
            with tempfile.TemporaryDirectory(prefix="rom-manager-install-") as tmp:
                extract_root = Path(tmp)
                self._extract_archive_tree(source, extract_root)
                only = self._selected_archive_member(extract_root, archive_member_path)
                return self._install_tree(extract_root, manufacturer, console, report, only=only, install_name=install_name)
        if self._can_extract_archive(source):
            with tempfile.TemporaryDirectory(prefix="rom-manager-install-") as tmp:
                extract_root = Path(tmp)
                self._extract_archive_tree(source, extract_root)
                only = self._selected_archive_member(extract_root, archive_member_path)
                return self._install_tree(extract_root, manufacturer, console, report, only=only, install_name=install_name)
        return self._install_tree(source.parent, manufacturer, console, report, only=source, install_name=install_name)

    def _iter_install_candidates(
        self,
        source: Path,
        archive_member_path: Optional[str] = None,
        manufacturer: Optional[str] = None,
        console: Optional[str] = None,
    ) -> Iterable[Dict[str, object]]:
        if self._should_preserve_archive(source, manufacturer, console, archive_member_path):
            candidate = self._candidate_info(source)
            if candidate:
                yield candidate
            return
        if self._is_nintendo_dsi(manufacturer, console):
            if source.suffix.lower() == ".zip" and zipfile.is_zipfile(source):
                with tempfile.TemporaryDirectory(prefix="rom-manager-space-dsi-") as tmp:
                    extract_root = Path(tmp)
                    self._extract_archive_tree(source, extract_root)
                    yield from self._iter_install_candidates_from_tree(
                        extract_root,
                        archive_member_path,
                        manufacturer,
                        console,
                    )
                return
            if self._can_extract_archive(source):
                with tempfile.TemporaryDirectory(prefix="rom-manager-space-dsi-") as tmp:
                    extract_root = Path(tmp)
                    self._extract_archive_tree(source, extract_root)
                    yield from self._iter_install_candidates_from_tree(
                        extract_root,
                        archive_member_path,
                        manufacturer,
                        console,
                    )
                return
        if source.suffix.lower() == ".zip" and zipfile.is_zipfile(source):
            with tempfile.TemporaryDirectory(prefix="rom-manager-space-") as tmp:
                extract_root = Path(tmp)
                self._extract_archive_tree(source, extract_root)
                selected = self._selected_archive_member(extract_root, archive_member_path)
                paths = [selected] if selected else [path for path in extract_root.rglob("*") if path.is_file()]
                for path in paths:
                    if path and path.is_file():
                        candidate = self._candidate_info(path)
                        if candidate:
                            yield candidate
            return
        if self._can_extract_archive(source):
            with tempfile.TemporaryDirectory(prefix="rom-manager-space-") as tmp:
                extract_root = Path(tmp)
                self._extract_archive_tree(source, extract_root)
                selected = self._selected_archive_member(extract_root, archive_member_path)
                paths = [selected] if selected else [path for path in extract_root.rglob("*") if path.is_file()]
                for path in paths:
                    if path and path.is_file():
                        candidate = self._candidate_info(path)
                        if candidate:
                            yield candidate
            return
        candidate = self._candidate_info(source)
        if candidate:
            yield candidate

    def _iter_install_candidates_from_tree(
        self,
        root: Path,
        archive_member_path: Optional[str],
        manufacturer: Optional[str],
        console: Optional[str],
    ) -> Iterable[Dict[str, object]]:
        if self._is_nintendo_dsi(manufacturer, console):
            selected = self._selected_dsiware_payload(root, archive_member_path)
            if selected:
                yield {"kind": "rom", "size": selected.stat().st_size, "sha256": self._sha256(selected)}
            return
        selected = self._selected_archive_member(root, archive_member_path)
        candidate = self._candidate_info(selected) if selected else None
        if candidate:
            yield candidate

    @staticmethod
    def _should_preserve_archive(
        source: Path,
        manufacturer: Optional[str],
        console: Optional[str],
        archive_member_path: Optional[str] = None,
    ) -> bool:
        if archive_member_path:
            return False
        if source.suffix.lower() != ".zip":
            return False
        return (manufacturer or "").lower() == "snk" and (console or "").lower() == "neo geo"

    @staticmethod
    def _replace_existing_rom_by_name(manufacturer: Optional[str], console: Optional[str]) -> bool:
        return (manufacturer or "").lower() == "snk" and (console or "").lower() == "neo geo"

    @staticmethod
    def _selected_archive_member(root: Path, archive_member_path: Optional[str]) -> Optional[Path]:
        if not archive_member_path:
            return None
        normalized = str(Path(archive_member_path)).replace("\\", "/").lower()
        direct = root / archive_member_path
        if direct.is_file():
            return direct
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).replace("\\", "/").lower()
            if rel == normalized or rel.endswith(f"/{normalized}"):
                return path
        raise InstallError(f"Archive member not found after extraction: {archive_member_path}")

    @staticmethod
    def _can_extract_archive(source: Path) -> bool:
        suffix = source.suffix.lower()
        if suffix == ".zip":
            return zipfile.is_zipfile(source) or FrontendInstaller._tool_path("7z") is not None
        if suffix == ".7z":
            return FrontendInstaller._tool_path("7z") is not None
        if suffix == ".rar":
            return any(FrontendInstaller._tool_path(tool) for tool in ("unar", "unrar", "7z"))
        return False

    def _extract_archive_tree(self, source: Path, target: Path) -> None:
        self._extract_one_archive(source, target)
        self._expand_nested_archives(target)

    def _expand_nested_archives(self, root: Path) -> None:
        for _ in range(4):
            nested = [
                path
                for path in sorted(root.rglob("*"))
                if path.is_file() and (zipfile.is_zipfile(path) or self._can_extract_archive(path))
            ]
            if not nested:
                return
            for path in nested:
                target = path.parent / path.stem
                target.mkdir(parents=True, exist_ok=True)
                self._extract_one_archive(path, target)
                path.unlink(missing_ok=True)

    @staticmethod
    def _extract_one_archive(source: Path, target: Path) -> None:
        suffix = source.suffix.lower()
        if suffix == ".zip" and zipfile.is_zipfile(source):
            try:
                with zipfile.ZipFile(source) as archive:
                    archive.extractall(target)
                return
            except Exception:
                # Some Internet Archive ZIPs use compression methods or nested
                # headers Python's zipfile cannot handle; 7z usually can.
                pass

        commands: List[Tuple[List[str], Optional[Dict[str, str]]]] = []
        if suffix == ".rar":
            unar = FrontendInstaller._tool_path("unar")
            if unar:
                commands.append((
                    [unar, "-force-overwrite", "-output-directory", str(target), str(source)],
                    FrontendInstaller._tool_env(unar),
                ))
            unrar = FrontendInstaller._tool_path("unrar")
            if unrar:
                commands.append(([unrar, "x", "-o+", str(source), str(target)], None))
        seven_zip = FrontendInstaller._tool_path("7z")
        if seven_zip:
            commands.append(([seven_zip, "x", f"-o{target}", str(source)], None))

        if not commands:
            raise InstallError(f"No extractor is installed for {source.suffix} archives.")

        errors: List[str] = []
        for command, env in commands:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                check=False,
            )
            if result.returncode == 0:
                return
            errors.append(result.stdout.strip())

        if suffix == ".rar":
            raise InstallError(
                f"Unable to extract {source.name}. Install a RAR extractor with: "
                "sudo apt-get install unar"
            )
        raise InstallError(f"Unable to extract {source.name}: {errors[-1] if errors else 'unknown error'}")

    @staticmethod
    def _tool_path(name: str) -> Optional[str]:
        found = shutil.which(name)
        if found:
            return found
        project_root = Path(__file__).resolve().parent.parent
        local = project_root / ".local-tools" / "unar" / "usr" / "bin" / name
        if local.exists():
            return str(local)
        return None

    @staticmethod
    def _tool_env(path: str) -> Optional[Dict[str, str]]:
        project_root = Path(__file__).resolve().parent.parent
        local_root = project_root / ".local-tools" / "unar"
        try:
            Path(path).relative_to(local_root)
        except ValueError:
            return None
        env = dict(os.environ)
        lib_paths = [
            str(local_root / "usr" / "lib"),
            str(local_root / "usr" / "lib" / "x86_64-linux-gnu"),
        ]
        existing = env.get("LD_LIBRARY_PATH")
        if existing:
            lib_paths.append(existing)
        env["LD_LIBRARY_PATH"] = ":".join(lib_paths)
        return env

    def _candidate_info(self, path: Path) -> Optional[Dict[str, object]]:
        if self._should_skip_content(path):
            return None
        if self._is_bios(path):
            return {"kind": "bios", "size": path.stat().st_size, "sha256": self._sha256(path)}
        if self._is_rom(path):
            return {"kind": "rom", "size": path.stat().st_size, "sha256": self._sha256(path)}
        return None

    def _install_tree(
        self,
        root: Path,
        manufacturer: str,
        console: str,
        report: Dict[str, object],
        only: Optional[Path] = None,
        install_name: Optional[str] = None,
    ) -> List[Path]:
        installed: List[Path] = []
        files = [only] if only else [path for path in root.rglob("*") if path.is_file()]
        console_name = self._console_name(manufacturer, console)
        console_dir = self.roms_root / console_name
        console_dir.mkdir(parents=True, exist_ok=True)
        existing_hashes = self._existing_hashes(console_dir)

        for path in files:
            rel = path.name if only else str(path.relative_to(root))
            if self._should_skip_content(path):
                continue
            if self._is_bios(path):
                target_name = BIOS_ALIASES.get(path.name.lower(), path.name)
                target = self.bios_root / target_name
                if target.exists() and self._same_file(path, target):
                    report["bios_skipped"] += 1
                else:
                    shutil.copy2(path, target)
                    report["bios_installed"] += 1
                continue

            if not self._is_rom(path):
                continue

            digest = self._sha256(path)
            if digest in existing_hashes:
                report["roms_skipped"] += 1
                continue

            target_rel = install_name if self._is_nintendo_dsi(manufacturer, console) and install_name else rel
            target = console_dir / target_rel
            if target.exists() and not self._replace_existing_rom_by_name(manufacturer, console):
                target = self._unique_target(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            existing_hashes.add(digest)
            installed.append(target)
            report["roms_installed"] += 1

        self._normalize_cue_references(console_dir)
        return installed

    @staticmethod
    def _normalize_cue_references(console_dir: Path) -> None:
        for cue_path in console_dir.rglob("*.cue"):
            try:
                lines = cue_path.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            changed = False
            normalized: List[str] = []
            for line in lines:
                stripped = line.lstrip()
                prefix = line[: len(line) - len(stripped)]
                if not stripped.upper().startswith("FILE "):
                    normalized.append(line)
                    continue
                first_quote = stripped.find('"')
                second_quote = stripped.find('"', first_quote + 1) if first_quote >= 0 else -1
                if first_quote < 0 or second_quote < 0:
                    normalized.append(line)
                    continue
                reference = stripped[first_quote + 1 : second_quote]
                if Path(reference).suffix or (cue_path.parent / reference).exists():
                    normalized.append(line)
                    continue
                replacement = FrontendInstaller._cue_reference_replacement(cue_path.parent, reference)
                if not replacement:
                    normalized.append(line)
                    continue
                stripped = f'{stripped[: first_quote + 1]}{replacement}{stripped[second_quote:]}'
                normalized.append(f"{prefix}{stripped}")
                changed = True
            if changed:
                cue_path.write_text("\n".join(normalized) + "\n", encoding="utf-8")

    @staticmethod
    def _cue_reference_replacement(directory: Path, reference: str) -> Optional[str]:
        for suffix in (".bin", ".iso", ".img", ".wav", ".flac", ".chd"):
            candidate = directory / f"{reference}{suffix}"
            if candidate.exists():
                return candidate.name
        return None

    def _install_dsiware_file(
        self,
        source: Path,
        manufacturer: str,
        console: str,
        report: Dict[str, object],
        archive_member_path: Optional[str] = None,
        install_name: Optional[str] = None,
    ) -> List[Path]:
        missing = self._missing_melonds_dsi_files()
        if missing:
            raise InstallError(
                "Nintendo DSi requires melonDS DS system files before install: "
                + ", ".join(missing)
            )

        if source.suffix.lower() == ".zip" and zipfile.is_zipfile(source):
            with tempfile.TemporaryDirectory(prefix="rom-manager-install-dsi-") as tmp:
                extract_root = Path(tmp)
                self._extract_archive_tree(source, extract_root)
                payload = self._selected_dsiware_payload(extract_root, archive_member_path)
                return self._install_dsiware_payload(payload, manufacturer, console, report, install_name or source.name)
        if self._can_extract_archive(source):
            with tempfile.TemporaryDirectory(prefix="rom-manager-install-dsi-") as tmp:
                extract_root = Path(tmp)
                self._extract_archive_tree(source, extract_root)
                payload = self._selected_dsiware_payload(extract_root, archive_member_path)
                return self._install_dsiware_payload(payload, manufacturer, console, report, install_name or source.name)
        return self._install_dsiware_payload(source, manufacturer, console, report, install_name or source.name)

    def _install_dsiware_payload(
        self,
        source: Path,
        manufacturer: str,
        console: str,
        report: Dict[str, object],
        install_name: str,
    ) -> List[Path]:
        if not source or not source.is_file():
            raise InstallError("No DSiWare payload found in downloaded archive.")
        console_dir = self.roms_root / self._console_name(manufacturer, console)
        console_dir.mkdir(parents=True, exist_ok=True)
        target_name = self._dsiware_target_name(install_name, source)
        digest = self._sha256(source)
        if digest in self._existing_hashes(console_dir):
            report["roms_skipped"] += 1
            return []
        target = self._unique_target(console_dir / target_name)
        shutil.copy2(source, target)
        report["roms_installed"] += 1
        return [target]

    @staticmethod
    def _selected_dsiware_payload(root: Path, archive_member_path: Optional[str]) -> Path:
        if archive_member_path:
            return FrontendInstaller._selected_archive_member(root, archive_member_path)
        candidates = [
            path
            for path in root.rglob("*")
            if path.is_file()
            and not FrontendInstaller._should_skip_content(path)
            and path.name.lower() not in {"titlekeys.txt", "titlekeys.zip", "tmds.zip"}
            and path.suffix.lower() not in {".txt", ".xml", ".json", ".torrent", ".sqlite", ".db", ".tmd"}
        ]
        if not candidates:
            raise InstallError("No DSiWare payload found in downloaded archive.")
        rom_like = [path for path in candidates if path.suffix.lower() in {".nds", ".dsi", ".app"}]
        return max(rom_like or candidates, key=lambda item: item.stat().st_size)

    @staticmethod
    def _dsiware_target_name(install_name: str, source: Path) -> str:
        name = Path(str(install_name or source.name)).name
        while Path(name).suffix.lower() in {".zip", ".7z", ".rar"}:
            name = Path(name).stem
        if Path(name).suffix.lower() not in {".nds", ".dsi", ".ids"}:
            name = f"{Path(name).stem}.dsi"
        return _safe_filename(name)

    def _write_playlist(self, manufacturer: str, console: str) -> Path:
        console_name = self._console_name(manufacturer, console)
        console_dir = self.roms_root / console_name
        playlist_path = self.playlists_root / f"{console_name}.lpl"
        if console_name == NINTENDO_DSI_CONSOLE_NAME:
            self._ensure_melonds_dsi_layout_and_options()
        if console_name == "Amstrad - GX4000":
            self._ensure_cap32_gx4000_options()
        if console_name == "Commodore - CD32":
            self._ensure_puae_cd32_options()
        if console_name == "Commodore - CDTV":
            self._ensure_puae_cdtv_options()
        if console_name == "Philips - Videopac+":
            self._ensure_o2em_videopac_plus_options()
        if console_name == "Sony - PlayStation 2":
            self._ensure_lrps2_options()
        core_path, core_name = self._resolve_core(console_name)
        label_lookup = self._playlist_label_lookup(console_name)
        existing = self._load_playlist(playlist_path)
        install_strategy = self._install_strategy(console_name)
        use_mame_softlist_cmd = (
            isinstance(install_strategy, dict)
            and install_strategy.get("type") == "mame_softlist_cmd"
            and core_path is not None
        )
        replace_console_items = use_mame_softlist_cmd or console_name == "Nintendo - Nintendo 64DD"
        playlist_entries = (
            self._mame_softlist_playlist_entries(console_dir, install_strategy)
            if use_mame_softlist_cmd
            else [
                (rom_path, self._playlist_label(rom_path, label_lookup))
                for rom_path in self._playlist_rom_paths(console_dir, console_name=console_name)
            ]
        )
        seen_paths = {
            item.get("path")
            for item in existing.get("items", [])
            if isinstance(item, dict) and item.get("path")
        }

        items = []
        for item in existing.get("items", []):
            if not isinstance(item, dict):
                continue
            item_path = item.get("path")
            if item_path:
                item_file = Path(item_path)
                if self._path_is_relative_to(item_file, console_dir) and not item_file.exists():
                    seen_paths.discard(item_path)
                    continue
            if replace_console_items and item_path and self._path_is_relative_to(Path(item_path), console_dir):
                seen_paths.discard(item_path)
                continue
            items.append(item)
        core_path_str = str(core_path) if core_path else "DETECT"
        core_name_str = core_name or "DETECT"
        for item in items:
            if not isinstance(item, dict):
                continue
            item_path = item.get("path")
            if item_path and self._path_is_relative_to(Path(item_path), console_dir):
                item["core_path"] = core_path_str
                item["core_name"] = core_name_str
                item["label"] = self._playlist_label(Path(item_path), label_lookup)
        for rom_path, label in playlist_entries:
            rom_path_str = str(rom_path)
            if rom_path_str in seen_paths:
                continue
            items.append({
                "path": rom_path_str,
                "label": label,
                "core_path": core_path_str,
                "core_name": core_name_str,
                "crc32": "",
                "db_name": console_name,
            })
            seen_paths.add(rom_path_str)

        payload = {
            "version": existing.get("version", "1.5"),
            "default_core_path": existing.get("default_core_path", ""),
            "default_core_name": existing.get("default_core_name", ""),
            "label_display_mode": existing.get("label_display_mode", 0),
            "right_thumbnail_mode": existing.get("right_thumbnail_mode", 0),
            "left_thumbnail_mode": existing.get("left_thumbnail_mode", 0),
            "thumbnail_match_mode": existing.get("thumbnail_match_mode", 0),
            "sort_mode": existing.get("sort_mode", 2),
            "items": items,
        }
        playlist_path.parent.mkdir(parents=True, exist_ok=True)
        playlist_path.write_text(json.dumps(payload, indent=2))
        return playlist_path

    def _playlist_label_lookup(self, console_name: str) -> Dict[str, str]:
        module = next((item for item in load_modules() if item.get("name") == console_name), None)
        if not module:
            return {}
        try:
            from utils.library_sync import rdb_json_path

            path = rdb_json_path(console_name)
            if not path.exists():
                return {}
            payload = json.loads(path.read_text())
        except Exception:
            return {}
        lookup: Dict[str, str] = {}
        for entry in payload.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            label = entry.get("name") or entry.get("description")
            rom_name = entry.get("rom_name")
            if not label or not rom_name:
                continue
            stem = Path(str(rom_name)).stem.lower()
            lookup[stem] = str(label)
        return lookup

    @staticmethod
    def _console_name(manufacturer: str, console: str) -> str:
        if manufacturer and manufacturer == console:
            return console
        if manufacturer == "Various" and console == "Handheld Electronic Game":
            return console
        return f"{manufacturer} - {console}"

    @staticmethod
    def _playlist_label(path: Path, label_lookup: Dict[str, str]) -> str:
        return label_lookup.get(path.stem.lower()) or path.stem

    @staticmethod
    def _path_is_relative_to(path: Path, parent: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(parent.resolve(strict=False))
            return True
        except ValueError:
            return False

    def _mame_softlist_playlist_entries(self, console_dir: Path, strategy: Dict) -> List[Tuple[Path, str]]:
        machine = strategy.get("machine")
        software_list = strategy.get("software_list") or machine
        media = strategy.get("media") or "cart"
        if not machine or not software_list:
            return [(rom_path, rom_path.stem) for rom_path in self._playlist_rom_paths(console_dir)]

        software_by_crc = self._mame_software_by_crc(strategy)
        if not software_by_crc:
            return [(rom_path, rom_path.stem) for rom_path in self._playlist_rom_paths(console_dir)]

        mame_roms_root = self.bios_root / "mame" / "roms"
        mame_hash_root = self.bios_root / "mame" / "hash"
        softlist_dir = mame_roms_root / str(strategy.get("roms_subdir") or software_list)
        cmd_dir = console_dir / ".mame_cmd"
        softlist_dir.mkdir(parents=True, exist_ok=True)
        cmd_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_mame_cli_enabled(str(strategy.get("boot_from_cli_option") or "mame_boot_from_cli"))
        self._mirror_mame_bios(machine, mame_roms_root)

        matched: Dict[str, Dict[str, object]] = {}
        for rom_path in self._playlist_rom_paths(console_dir):
            software = self._mame_software_for_rom(rom_path, software_by_crc)
            if not software:
                continue
            group = matched.setdefault(
                str(software["name"]),
                {"software": software, "paths": []},
            )
            paths = group.get("paths")
            if isinstance(paths, list):
                paths.append(rom_path)

        entries: List[Tuple[Path, str]] = []
        for soft_name, group in sorted(matched.items()):
            software = group.get("software")
            paths = group.get("paths")
            if not isinstance(software, dict) or not isinstance(paths, list):
                continue
            soft_zip = softlist_dir / f"{soft_name}.zip"
            self._write_softlist_zip_from_paths(paths, soft_zip, software)
            cmd_path = cmd_dir / f"{_safe_filename(soft_name)}.cmd"
            cmd_path.write_text(
                f'{machine} -{media} {soft_name} -rp "{mame_roms_root}" -hashpath "{mame_hash_root}"\n'
            )
            entries.append((cmd_path, str(software.get("description") or soft_name)))
        return entries

    def _mame_software_by_crc(self, strategy: Dict) -> Dict[str, Dict[str, str]]:
        hash_file = strategy.get("hash_file") or f"{strategy.get('software_list') or strategy.get('machine')}.xml"
        hash_path = self.bios_root / "mame" / "hash" / str(hash_file)
        if not hash_path.exists():
            return {}
        try:
            root = ET.parse(hash_path).getroot()
        except Exception:
            return {}
        software_by_crc: Dict[str, Dict[str, str]] = {}
        for software in root.findall("software"):
            soft_name = software.get("name")
            description = software.findtext("description") or soft_name or ""
            if not soft_name:
                continue
            parts = []
            roms = {}
            for rom in software.findall(".//rom"):
                crc = (rom.get("crc") or "").lower()
                rom_name = rom.get("name")
                if not crc or not rom_name:
                    continue
                offset = _parse_int(rom.get("offset")) or 0
                size = _parse_int(rom.get("size"))
                roms[crc] = rom_name
                parts.append({
                    "crc": crc,
                    "name": rom_name,
                    "offset": offset,
                    "size": size,
                })
            for crc, rom_name in roms.items():
                software_by_crc[crc] = {
                    "name": soft_name,
                    "description": description,
                    "rom_name": rom_name,
                    "roms": roms,
                    "parts": parts,
                }
        return software_by_crc

    @staticmethod
    def _mame_software_for_rom(rom_path: Path, software_by_crc: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
        if zipfile.is_zipfile(rom_path):
            try:
                with zipfile.ZipFile(rom_path) as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        crc = f"{info.CRC:08x}".lower()
                        if crc in software_by_crc:
                            return software_by_crc[crc]
            except Exception:
                return None
            return None
        try:
            import zlib

            crc_value = 0
            with rom_path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    crc_value = zlib.crc32(chunk, crc_value)
            crc = f"{crc_value & 0xffffffff:08x}".lower()
        except OSError:
            return None
        software = software_by_crc.get(crc)
        if software:
            return software
        try:
            payload = rom_path.read_bytes()
        except OSError:
            return None
        for candidate in _unique_software_records(software_by_crc):
            if _payload_matches_software_parts(payload, candidate):
                return candidate
        return None

    def _write_softlist_zip(self, source_zip: Path, target_zip: Path, rom_name: str) -> None:
        if zipfile.is_zipfile(source_zip):
            with zipfile.ZipFile(source_zip) as source:
                source_info = next((info for info in source.infolist() if not info.is_dir()), None)
                if not source_info:
                    return
                payload = source.read(source_info)
        else:
            payload = source_zip.read_bytes()
        if target_zip.exists() and zipfile.is_zipfile(target_zip):
            try:
                with zipfile.ZipFile(target_zip) as existing:
                    names = {name.lower(): name for name in existing.namelist()}
                    current = names.get(rom_name.lower())
                    if current and existing.read(current) == payload:
                        return
            except Exception:
                pass
        with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as target:
            target.writestr(rom_name, payload)

    def _write_softlist_zip_from_paths(self, sources: List[Path], target_zip: Path, software: Dict) -> None:
        rom_map = software.get("roms") if isinstance(software.get("roms"), dict) else {}
        parts = software.get("parts") if isinstance(software.get("parts"), list) else []
        payloads: Dict[str, bytes] = {}
        for source in sources:
            crc, payload = self._rom_payload_with_crc(source)
            if not crc or payload is None:
                continue
            rom_name = rom_map.get(crc)
            if rom_name:
                payloads[rom_name] = payload
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                part_name = part.get("name")
                part_crc = part.get("crc")
                part_size = part.get("size")
                part_offset = int(part.get("offset") or 0)
                if not part_name or not part_crc or not part_size:
                    continue
                chunk = payload[part_offset:part_offset + int(part_size)]
                if len(chunk) != int(part_size):
                    continue
                if _crc32_hex(chunk) == str(part_crc).lower():
                    payloads[str(part_name)] = chunk
        if not payloads:
            return
        if target_zip.exists() and zipfile.is_zipfile(target_zip):
            try:
                with zipfile.ZipFile(target_zip) as existing:
                    names = {name.lower(): name for name in existing.namelist()}
                    if all(
                        (rom_name.lower() in names and existing.read(names[rom_name.lower()]) == payload)
                        for rom_name, payload in payloads.items()
                    ):
                        return
            except Exception:
                pass
        with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for rom_name, payload in sorted(payloads.items()):
                target.writestr(rom_name, payload)

    @staticmethod
    def _rom_payload_with_crc(path: Path) -> Tuple[Optional[str], Optional[bytes]]:
        try:
            import zlib

            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as archive:
                    info = next((item for item in archive.infolist() if not item.is_dir()), None)
                    if not info:
                        return None, None
                    return f"{info.CRC:08x}".lower(), archive.read(info)
            payload = path.read_bytes()
            return f"{zlib.crc32(payload) & 0xffffffff:08x}".lower(), payload
        except Exception:
            return None, None

    def _mirror_mame_bios(self, machine: str, mame_roms_root: Path) -> None:
        source = self.bios_root / f"{machine}.zip"
        target = mame_roms_root / f"{machine}.zip"
        if not source.exists():
            return
        if target.exists() and self._same_file(source, target):
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def _ensure_mame_cli_enabled(self, option_key: str) -> None:
        retroarch_root = self._retroarch_root(self.roms_root)
        option_keys = {option_key, "mame_mame_paths_enable"}
        for options_path in [
            retroarch_root / "retroarch-core-options.cfg",
            retroarch_root / "config" / "MAME" / "MAME.opt",
        ]:
            self._set_core_options(options_path, option_keys)
        self._set_core_options(
            retroarch_root / "retroarch.cfg",
            {"input_auto_game_focus"},
        )

    def _ensure_o2em_videopac_plus_options(self) -> None:
        retroarch_root = self._retroarch_root(self.roms_root)
        option_values = {"o2em_bios": "g7400.bin"}
        for options_path in [
            retroarch_root / "retroarch-core-options.cfg",
            retroarch_root / "config" / "O2EM" / "O2EM.opt",
        ]:
            self._set_core_options_values(options_path, option_values)

    def _ensure_cap32_gx4000_options(self) -> None:
        retroarch_root = self._retroarch_root(self.roms_root)
        option_values = {
            "cap32_gfx_colors": "24bit",
            "cap32_model": "6128+ (experimental)",
        }
        for options_path in [
            retroarch_root / "retroarch-core-options.cfg",
            retroarch_root / "config" / "Caprice32" / "Caprice32.opt",
            retroarch_root / "config" / "cap32" / "cap32.opt",
        ]:
            self._set_core_options_values(options_path, option_values)

    def _ensure_puae_cd32_options(self) -> None:
        retroarch_root = self._retroarch_root(self.roms_root)
        option_values = {
            "puae_model": "CD32",
            "puae_model_cd": "CD32",
        }
        for options_path in [
            retroarch_root / "retroarch-core-options.cfg",
            retroarch_root / "config" / "PUAE" / "PUAE.opt",
            retroarch_root / "config" / "puae" / "puae.opt",
        ]:
            self._set_core_options_values(options_path, option_values)

    def _ensure_puae_cdtv_options(self) -> None:
        retroarch_root = self._retroarch_root(self.roms_root)
        option_values = {
            "puae_model": "CDTV",
            "puae_model_cd": "CDTV",
        }
        for options_path in [
            retroarch_root / "retroarch-core-options.cfg",
            retroarch_root / "config" / "PUAE" / "PUAE.opt",
            retroarch_root / "config" / "puae" / "puae.opt",
        ]:
            self._set_core_options_values(options_path, option_values)

    def _ensure_lrps2_options(self) -> None:
        retroarch_root = self._retroarch_root(self.roms_root)
        option_values = {
            "pcsx2_renderer": "OpenGL",
        }
        for options_path in [
            retroarch_root / "retroarch-core-options.cfg",
            retroarch_root / "config" / "LRPS2" / "LRPS2.opt",
        ]:
            self._set_core_options_values(options_path, option_values)

    @staticmethod
    def _set_core_options(options_path: Path, option_keys: Iterable[str]) -> None:
        FrontendInstaller._set_core_options_values(
            options_path,
            {
                option_key: ("1" if option_key == "input_auto_game_focus" else "enabled")
                for option_key in option_keys
                if option_key
            },
        )

    @staticmethod
    def _set_core_options_values(options_path: Path, option_values: Dict[str, str]) -> None:
        options = {}
        if options_path.exists():
            for line in options_path.read_text(errors="ignore").splitlines():
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                options[key.strip()] = value.strip()
        for option_key, value in option_values.items():
            options[option_key] = json.dumps(str(value))
        options_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{key} = {value}" for key, value in sorted(options.items())]
        options_path.write_text("\n".join(lines) + "\n")

    def _playlist_rom_paths(self, console_dir: Path, console_name: Optional[str] = None) -> List[Path]:
        paths = sorted(path for path in console_dir.rglob("*") if path.is_file() and self._is_rom(path))
        if console_name == NINTENDO_DSI_CONSOLE_NAME:
            return [path for path in paths if path.suffix.lower() in {".dsi", ".nds", ".ids"}]
        if console_name == "Nintendo - Nintendo 64DD":
            return [
                path
                for path in paths
                if path.suffix.lower() == ".n64"
                and "/roms/" in path.as_posix().lower()
                and not path.name.lower().startswith("[bios]")
            ]
        descriptor_dirs = {
            path.parent
            for path in paths
            if path.suffix.lower() in {".cue", ".ccd", ".toc", ".m3u"}
        }
        return [
            path
            for path in paths
            if not (path.suffix.lower() in {".bin", ".img"} and path.parent in descriptor_dirs)
        ]

    def _resolve_core(self, console_name: str) -> Tuple[Optional[Path], Optional[str]]:
        registry_preferred = self._registry_core_candidates(console_name)
        if registry_preferred:
            for core_id, core_file in registry_preferred:
                core = self.cores_root / f"{core_file}_libretro.so"
                if core.exists():
                    return core, self._core_display_name(core_file) or core_id

        if "Family Computer Disk System" in console_name:
            preferred = ["fceumm", "nestopia"]
        elif "Atari - 2600" in console_name:
            preferred = ["stella", "stella2014"]
        elif "Atari - 5200" in console_name:
            preferred = ["a5200", "atari800"]
        elif "Atari - 7800" in console_name:
            preferred = ["prosystem"]
        elif "Atari - Jaguar" in console_name:
            preferred = ["virtualjaguar"]
        elif "Atari - Lynx" in console_name:
            preferred = ["handy"]
        elif "Amstrad - GX4000" in console_name:
            preferred = ["cap32"]
        elif "Commodore - CD32" in console_name or "Commodore - CDTV" in console_name:
            preferred = ["puae"]
        elif "Bandai - WonderSwan" in console_name:
            preferred = ["mednafen_wswan"]
        elif "Casio - PV-1000" in console_name:
            preferred = ["mame"]
        elif "Casio - Loopy" in console_name:
            preferred = ["mame"]
        elif "Coleco - ColecoVision" in console_name:
            preferred = ["gearcoleco", "bluemsx"]
        elif "Emerson - Arcadia 2001" in console_name:
            preferred = ["amiarcadia", "mame"]
        elif "Entex - Adventure Vision" in console_name:
            preferred = ["mame"]
        elif "Epoch - Super Cassette Vision" in console_name:
            preferred = ["emuscv", "mame"]
        elif "Fairchild - Channel F" in console_name:
            preferred = ["freechaf"]
        elif "GamePark - GP32" in console_name:
            preferred = ["mame"]
        elif "Funtech - Super Acan" in console_name:
            preferred = ["mame"]
        elif "GCE - Vectrex" in console_name:
            preferred = ["vecx"]
        elif "Hartung - Game Master" in console_name:
            if not self._game_master_bios_ok():
                return None, None
            preferred = ["mame"]
        elif "Handheld Electronic Game" in console_name:
            preferred = ["gw"]
        elif "LeapFrog - Leapster Learning Game System" in console_name:
            preferred = ["mame"]
        elif "RCA - Studio II" in console_name:
            preferred = ["mame"]
        elif "Nintendo - Nintendo 64DD" in console_name:
            preferred = ["mupen64plus_next", "parallel_n64"]
        elif "Nintendo - Nintendo 64" in console_name:
            preferred = ["mupen64plus_next", "parallel_n64"]
        elif "Nintendo - Pokemon Mini" in console_name:
            preferred = ["pokemini"]
        elif "Nintendo - Satellaview" in console_name:
            preferred = ["snes9x", "bsnes"]
        elif "Nintendo - Sufami Turbo" in console_name:
            preferred = ["snes9x", "bsnes"]
        elif "Nintendo - Super Nintendo Entertainment System" in console_name:
            preferred = ["snes9x", "bsnes"]
        elif "Nintendo - Virtual Boy" in console_name:
            preferred = ["mednafen_vb"]
        elif "Nintendo - Game Boy Advance" in console_name:
            preferred = ["mgba", "vba_m", "gpsp"]
        elif "Nintendo - Game Boy Color" in console_name:
            preferred = ["gambatte", "mgba", "sameboy", "gearboy"]
        elif "Nintendo - Game Boy" in console_name:
            preferred = ["gambatte", "mgba", "sameboy", "gearboy"]
        elif "Nintendo - GameCube" in console_name:
            preferred = ["dolphin"]
        elif "Nintendo - Wii" in console_name:
            preferred = ["dolphin"]
        elif "Nintendo - Wii U" in console_name:
            preferred = ["cemu"]
        elif "Nintendo - Nintendo 3DS" in console_name:
            preferred = ["azahar", "citra"]
        elif "Nintendo - Nintendo DS" in console_name:
            preferred = ["melondsds", "desmume"]
        elif "Sony - PlayStation 2" in console_name:
            preferred = ["pcsx2", "play"]
        elif "Sony - PlayStation Portable" in console_name:
            preferred = ["ppsspp"]
        elif "SNK - Neo Geo Pocket" in console_name:
            preferred = ["mednafen_ngp", "race"]
        elif "SNK - Neo Geo CD" in console_name:
            preferred = ["neocd", "fbneo"]
        elif "SNK - Neo Geo" in console_name:
            preferred = ["fbneo", "mame"]
        elif "NEC - PC Engine - TurboGrafx 16" in console_name:
            preferred = ["mednafen_pce_fast", "mednafen_pce"]
        elif "NEC - PC Engine CD - TurboGrafx-CD" in console_name:
            preferred = ["mednafen_pce_fast", "mednafen_pce"]
        elif "NEC - PC Engine SuperGrafx" in console_name:
            preferred = ["mednafen_supergrafx", "geargrafx"]
        elif "NEC - PC-FX" in console_name:
            preferred = ["mednafen_pcfx"]
        elif "Philips - Videopac+" in console_name:
            preferred = ["o2em_videopac_plus", "o2em"]
        elif "Sega - Mega-CD - Sega CD" in console_name:
            preferred = ["genesis_plus_gx", "picodrive"]
        elif "Sega - Mega Drive - Genesis" in console_name:
            preferred = ["genesis_plus_gx", "picodrive"]
        elif "Sega - PICO" in console_name:
            preferred = ["genesis_plus_gx", "picodrive"]
        elif "Sega - Game Gear" in console_name:
            preferred = ["genesis_plus_gx", "gearsystem", "picodrive"]
        elif "Sega - Master System - Mark III" in console_name:
            preferred = ["gearsystem", "genesis_plus_gx", "picodrive", "smsplus"]
        elif "Sega - SG-1000" in console_name:
            preferred = ["gearsystem", "genesis_plus_gx", "picodrive", "smsplus"]
        elif "Sega - Dreamcast" in console_name:
            preferred = ["flycast"]
        elif "Sega - 32X" in console_name:
            preferred = ["picodrive", "genesis_plus_gx"]
        elif "Sega - Saturn" in console_name:
            preferred = ["mednafen_saturn", "kronos", "yabause"]
        elif "Sony - PlayStation" in console_name:
            preferred = ["swanstation", "mednafen_psx_hw", "pcsx_rearmed"]
        elif "The 3DO Company - 3DO" in console_name:
            preferred = ["opera"]
        elif "Tiger - Game.com" in console_name:
            preferred = ["mame_gamecom", "mame"]
        elif "VTech - CreatiVision" in console_name:
            preferred = ["mame_crvision", "mame"]
        elif "VTech - V.Smile" in console_name:
            preferred = ["mame_vsmile", "mame"]
        elif "Watara - Supervision" in console_name:
            preferred = ["potator"]
        else:
            preferred = []
        for core_id in preferred:
            core = self.cores_root / f"{core_id}_libretro.so"
            if core.exists():
                return core, self._core_display_name(core_id) or core_id

        for info_path in sorted(self.cores_root.glob("*_libretro.info")):
            text = info_path.read_text(errors="ignore")
            if console_name in text:
                core_id = info_path.name.removesuffix("_libretro.info")
                core_path = self.cores_root / f"{core_id}_libretro.so"
                return (core_path if core_path.exists() else None), self._core_display_name(core_id)
        return None, None

    def _registry_core_candidates(self, console_name: str) -> List[Tuple[str, str]]:
        guid = self.module_guid_by_name.get(console_name) or self._known_strategy_guid(console_name)
        if not guid:
            return []
        candidates: List[Tuple[str, str]] = []
        for core_id, meta in (self.registry.get("cores") or {}).items():
            if guid not in (meta.get("console_guids") or []):
                continue
            core_file = meta.get("core_file") or core_id
            candidates.append((core_id, core_file))
        return candidates

    def _install_strategy(self, console_name: str):
        if console_name == NINTENDO_DSI_CONSOLE_NAME:
            return {"type": "melonds_dsiware"}
        if "Emerson - Arcadia 2001" in console_name and (self.cores_root / "amiarcadia_libretro.so").exists():
            return "standard_libretro"
        guid = self.module_guid_by_name.get(console_name) or self._known_strategy_guid(console_name)
        if not guid:
            return "standard_libretro"
        for meta in (self.registry.get("cores") or {}).values():
            if guid in (meta.get("console_guids") or []) and meta.get("install_strategy"):
                strategy = meta.get("install_strategy")
                if isinstance(strategy, dict) and strategy.get("console_guid") and strategy.get("console_guid") != guid:
                    continue
                return strategy
        for meta in (self.registry.get("cores") or {}).values():
            if guid in (meta.get("console_guids") or []):
                strategy = meta.get("install_strategy")
                if isinstance(strategy, dict) and strategy.get("console_guid") and strategy.get("console_guid") != guid:
                    return "standard_libretro"
                return strategy or "standard_libretro"
        return "standard_libretro"

    @staticmethod
    def _known_strategy_guid(console_name: str) -> Optional[str]:
        if "Funtech - Super Acan" in console_name:
            return "7b81f262-ec70-5234-928d-207322439020"
        if "GamePark - GP32" in console_name:
            return "a69e967b-f947-5962-8578-03ca389c6231"
        if "Handheld Electronic Game" in console_name:
            return "60e5b2cc-0649-54b1-b0e0-30d0acac4bcc"
        if "LeapFrog - Leapster Learning Game System" in console_name:
            return "329862d7-95e7-5bdd-9f9c-291cac53a1a8"
        if "Epoch - Super Cassette Vision" in console_name:
            return "947cc03e-2c72-5cd0-92d9-8f69f946d59f"
        if "Hartung - Game Master" in console_name:
            return "3a5c3ea5-f514-59fa-9039-75d98e1ca63c"
        if "Tiger - Game.com" in console_name:
            return "cf88421d-dd86-5d2d-85d0-c640b1730604"
        if "VTech - CreatiVision" in console_name:
            return "90188f7f-aeff-501b-a10f-f41fb617829f"
        if "VTech - V.Smile" in console_name:
            return "c59cbdeb-e157-529b-b360-a193d53dc65b"
        if "Casio - PV-1000" in console_name:
            return "71b2e78e-d776-59a6-8bfa-5f84f90e7eca"
        if "Casio - Loopy" in console_name:
            return "632b56c7-fdb2-5eff-81fc-0d9a5be9e208"
        if "Emerson - Arcadia 2001" in console_name:
            return "b7e90431-7e83-585a-90ed-eb636b009779"
        if "Entex - Adventure Vision" in console_name:
            return "2de2782e-37a2-5e1c-9f4e-ffbf96872531"
        if "RCA - Studio II" in console_name:
            return "713057d9-8ae9-5b0a-9d1b-cf121b9357ae"
        if "Nintendo - Nintendo DSi" in console_name:
            return "7f574026-e1ef-588c-9ce1-a90da8d55378"
        return None

    @staticmethod
    def _is_nintendo_dsi(manufacturer: Optional[str], console: Optional[str]) -> bool:
        return (manufacturer or "").lower() == "nintendo" and (console or "").lower() == "nintendo dsi"

    def _missing_melonds_dsi_files(self) -> List[str]:
        missing = []
        for filename in MELONDS_DSI_REQUIRED_FILES:
            if not self._melonds_system_file(filename):
                missing.append(filename)
        return missing

    def _melonds_system_file(self, filename: str) -> Optional[Path]:
        for candidate in [self.bios_root / MELONDS_SYSTEM_SUBDIR / filename, self.bios_root / filename]:
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
        return None

    def _ensure_melonds_dsi_layout_and_options(self) -> None:
        system_subdir = self.bios_root / MELONDS_SYSTEM_SUBDIR
        system_subdir.mkdir(parents=True, exist_ok=True)
        for filename in MELONDS_DSI_REQUIRED_FILES:
            source = self._melonds_system_file(filename)
            if not source:
                continue
            target = system_subdir / filename
            if source != target and (not target.exists() or not self._same_file(source, target)):
                shutil.copy2(source, target)
        self._set_core_options_values(
            self._retroarch_root(self.roms_root) / "config" / MELONDS_SYSTEM_SUBDIR / f"{MELONDS_SYSTEM_SUBDIR}.opt",
            {
                "melonds_console_mode": "dsi",
                "melonds_sysfile_mode": "native",
                "melonds_boot_mode": "direct",
                "melonds_firmware_nds_path": f"{MELONDS_SYSTEM_SUBDIR}/firmware.bin",
                "melonds_firmware_dsi_path": f"{MELONDS_SYSTEM_SUBDIR}/dsi_firmware.bin",
                "melonds_dsi_nand_path": "/auto",
                "melonds_dsi_sdcard": "disabled",
            },
        )

    def _game_master_bios_ok(self) -> bool:
        expected = "6bff08b5e5f96de405cd56d5f04a08f8"
        for bios_path in [
            self.bios_root / "gmaster.zip",
            self.bios_root / "mame" / "roms" / "gmaster.zip",
        ]:
            if not zipfile.is_zipfile(bios_path):
                continue
            try:
                with zipfile.ZipFile(bios_path) as archive:
                    names = {name.lower(): name for name in archive.namelist()}
                    member = names.get("d78c11agf_e19.u1")
                    if not member:
                        continue
                    digest = hashlib.md5(archive.read(member)).hexdigest()
                    if digest.lower() == expected:
                        return True
            except Exception:
                continue
        return False

    def _core_display_name(self, core_id: str) -> Optional[str]:
        info = self.cores_root / f"{core_id}_libretro.info"
        if not info.exists():
            return None
        for line in info.read_text(errors="ignore").splitlines():
            if line.startswith("display_name"):
                return line.split("=", 1)[1].strip().strip('"')
        return None

    @staticmethod
    def _active_frontend() -> Tuple[str, Dict]:
        config = load_storage_config()
        frontends = config.get("frontends") or {}
        for key, entry in frontends.items():
            if entry.get("active"):
                return key, entry
        if frontends:
            key = next(iter(frontends))
            return key, frontends[key]
        raise InstallError("No frontend configured. Set one active in Settings -> Storage.")

    @staticmethod
    def _retroarch_root(roms_root: Path) -> Path:
        expanded = roms_root.expanduser()
        if expanded.name == "downloads":
            return expanded.parent
        return expanded.parent

    @staticmethod
    def _job_local_path(job: Dict) -> Optional[Path]:
        raw = job.get("local_path")
        if raw:
            path = Path(raw).expanduser()
            path = path if path.is_absolute() else Path.cwd() / path
            if path.exists():
                return path
        destination = job.get("destination")
        rom_name = job.get("rom_name")
        if destination and rom_name:
            destination_path = Path(destination).expanduser()
            destination_path = destination_path if destination_path.is_absolute() else Path.cwd() / destination_path
            filename = Path(rom_name).name
            path = destination_path / filename
            if path.exists():
                return path
            if destination_path.exists():
                for candidate in destination_path.rglob(filename):
                    if candidate.is_file():
                        return candidate
        return None

    @staticmethod
    def _load_playlist(path: Path) -> Dict:
        if not path.exists():
            return {"items": []}
        try:
            payload = json.loads(path.read_text())
        except Exception:
            return {"items": []}
        if not isinstance(payload, dict):
            return {"items": []}
        payload.setdefault("items", [])
        return payload

    @staticmethod
    def _is_rom(path: Path) -> bool:
        if path.name.lower() in SKIP_NAMES:
            return False
        parts = {part.lower() for part in path.parts}
        if "bios files" in parts:
            return False
        return path.suffix.lower() in ROM_EXTENSIONS

    @staticmethod
    def _is_bios(path: Path) -> bool:
        return path.name.lower() in BIOS_ALIASES or "bios files" in {part.lower() for part in path.parts}

    @staticmethod
    def _should_skip_content(path: Path) -> bool:
        return any("[bios]" in part.lower() for part in path.parts)

    @classmethod
    def _existing_hashes(cls, root: Path) -> set[str]:
        hashes: set[str] = set()
        if not root.exists():
            return hashes
        for path in root.rglob("*"):
            if path.is_file() and cls._is_rom(path):
                try:
                    hashes.add(cls._sha256(path))
                except OSError:
                    continue
        return hashes

    @classmethod
    def _existing_file_hashes(cls, root: Path) -> set[str]:
        hashes: set[str] = set()
        if not root.exists():
            return hashes
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    hashes.add(cls._sha256(path))
                except OSError:
                    continue
        return hashes

    @staticmethod
    def _unique_target(target: Path) -> Path:
        if not target.exists():
            return target
        stem = target.stem
        suffix = target.suffix
        parent = target.parent
        counter = 1
        while True:
            candidate = parent / f"{stem} ({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    @classmethod
    def _same_file(cls, left: Path, right: Path) -> bool:
        try:
            return left.stat().st_size == right.stat().st_size and cls._sha256(left) == cls._sha256(right)
        except OSError:
            return False

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


def _format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024


def _safe_filename(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in " ._-" else "_" for char in value).strip()
    return safe or "content"


def _safe_cli_filename(value: str) -> str:
    stem = Path(value).stem
    suffix = Path(value).suffix.lower()
    safe = "".join(char.lower() if char.isascii() and char.isalnum() else "_" for char in stem)
    safe = "_".join(part for part in safe.split("_") if part)
    if suffix not in {".pkg", ".vci", ".vpk", ".zip"}:
        suffix = ""
    return f"{safe or 'content'}{suffix}"


def _parse_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return None


def _crc32_hex(payload: bytes) -> str:
    import zlib

    return f"{zlib.crc32(payload) & 0xffffffff:08x}".lower()


def _unique_software_records(software_by_crc: Dict[str, Dict[str, object]]) -> List[Dict[str, object]]:
    seen: set[str] = set()
    records: List[Dict[str, object]] = []
    for record in software_by_crc.values():
        name = str(record.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        records.append(record)
    return records


def _payload_matches_software_parts(payload: bytes, software: Dict[str, object]) -> bool:
    parts = software.get("parts") if isinstance(software.get("parts"), list) else []
    matched = 0
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_crc = part.get("crc")
        part_size = part.get("size")
        part_offset = int(part.get("offset") or 0)
        if not part_crc or not part_size:
            continue
        chunk = payload[part_offset:part_offset + int(part_size)]
        if len(chunk) != int(part_size):
            return False
        if _crc32_hex(chunk) != str(part_crc).lower():
            return False
        matched += 1
    return matched > 0
