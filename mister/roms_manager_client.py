#!/usr/bin/env python3
"""Dependency-free, manually invoked MiSTer download client (Python 3.8+)."""
import argparse
import ctypes
import errno
import fcntl
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path, PurePosixPath

MAX_ROM_SIZE = 64 * 1024 * 1024
RESERVE_BYTES = 16 * 1024 * 1024
from core.mister_sources import SYSTEMS as CONSOLE_MAPPINGS
SYSTEMS = {mapping['id']: mapping for mapping in CONSOLE_MAPPINGS.values()}



class ClientError(Exception):
    pass


class ApiError(ClientError):
    pass


class BackendUnavailable(ApiError):
    pass


class AccessDenied(ApiError):
    pass


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ClientError("Invalid HTTP download URL")
    return parsed


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = validate_url(newurl)
        if urllib.parse.urlsplit(req.full_url).scheme == "https" and new.scheme != "https":
            raise ClientError("HTTPS download redirected to HTTP")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Api:
    def __init__(self, base_url, token, timeout=15):
        parsed = validate_url(base_url)
        if parsed.query:
            raise ClientError("Backend URL must not contain query parameters")
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, body=None):
        if not path.startswith('/') or path.startswith('//') or urllib.parse.urlsplit(path).scheme or '#' in path:
            raise ClientError('API requests must use a relative backend path')
        request = urllib.request.Request(self.base + path,
            data=json.dumps(body or {}).encode() if method != 'GET' else None,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code in (401, 403):
                raise AccessDenied('Backend denied access (HTTP %d). Check the client API key, account, and console assignments in Users & access.' % code) from None
            if code >= 500:
                raise BackendUnavailable('Backend unavailable; cached catalog data is retained.') from None
            raise ApiError("Backend returned HTTP %d; refresh the catalog or queue and try again" % code) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise BackendUnavailable('Backend unavailable; cached catalog data and partial downloads are retained.') from None

    def get(self, path):
        return self.request('GET', path)

    def post(self, path, body=None):
        return self.request('POST', '/mister/client/' + path, body)

    def fetch_cache_archive(self, destination):
        """Fetch the same account-filtered metadata archive as the desktop TUI."""
        temporary = destination.with_suffix('.tmp')
        request = urllib.request.Request(self.base + '/cache/archive', headers={'Authorization': 'Bearer ' + self.token})
        try:
            with self.opener.open(request, timeout=self.timeout) as response, temporary.open('wb') as output:
                total = 0
                while True:
                    block = response.read(65536)
                    if not block:
                        break
                    total += len(block)
                    if total > 128 * 1024 * 1024:
                        raise ClientError('Provider metadata archive is too large for this client')
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code in (401, 403):
                raise AccessDenied('Backend denied provider snapshot access; refresh the account.') from None
            raise BackendUnavailable('Provider metadata sync failed; cached catalogs remain available.') from None
        except OSError:
            raise BackendUnavailable('Provider metadata sync unavailable; cached catalogs remain available.') from None
        finally:
            temporary.unlink(missing_ok=True)

    def open_download(self, path, headers):
        if not path.startswith('/client/downloads/') or path.startswith('//') or urllib.parse.urlsplit(path).scheme or '#' in path:
            raise ClientError('Invalid prepared download path')
        request = urllib.request.Request(self.base + path, headers={**headers, 'Authorization': 'Bearer ' + self.token})
        try:
            return self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            code = exc.code
            if code in (401, 403):
                exc.close()
                raise ApiError('Backend denied prepared download access; check the account and console assignments') from None
            try:
                payload = json.load(exc)
                message = payload.get('error') if isinstance(payload, dict) else None
            except (OSError, ValueError):
                message = None
            finally:
                exc.close()
            raise ClientError(message or 'Prepared download returned HTTP %d; retry the job' % code) from None


def discover_games_root(system='snes'):
    if system not in SYSTEMS:
        raise ClientError('No installation mapping for this console')
    # Follow Main_MiSTer's documented search priority, including USB roots.
    bases = (["/media/usb%d" % i for i in range(6)] + ["/media/usb%d/games" % i for i in range(6)] +
             ["/media/network", "/media/network/games", "/media/fat/cifs", "/media/fat/cifs/games", "/media/fat", "/media/fat/games"])
    for base in bases:
        path = Path(base) / SYSTEMS[system]['core']
        if path.is_dir():
            return path
    raise ClientError("No %s games folder found. Set games_roots.%s to its effective, mounted games folder." % (SYSTEMS[system]['core'], system))


def find_mount(root):
    for candidate in (root,) + tuple(root.parents):
        if os.path.ismount(str(candidate)):
            return candidate
    raise ClientError("Game storage is not mounted")


def fat_config(path):
    """FAT/exFAT use mount-wide modes; chmod cannot restrict an individual file."""
    try:
        mounts = []
        for line in Path('/proc/mounts').read_text().splitlines():
            fields = line.split()
            mount = fields[1].replace('\\040', ' ')
            if str(path.resolve()).startswith(mount.rstrip('/') + '/'):
                mounts.append((len(mount), fields[2]))
        return bool(mounts and max(mounts)[1] in ('vfat', 'exfat') and path.stat().st_uid == os.getuid() == 0)
    except OSError:
        return False


def destination_path(root, relative, system='snes'):
    root = Path(root).resolve(strict=True)
    if not isinstance(relative, str) or not relative or "\\" in relative or any(ord(c) < 32 for c in relative):
        raise ClientError("Unsafe destination path")
    raw_parts = relative.split("/")
    if relative.startswith("/") or any(p in ("", ".", "..") for p in raw_parts):
        raise ClientError("Destination must stay inside the approved games folder")
    rel = PurePosixPath(relative)
    if system not in SYSTEMS or len(rel.parts) != 2 or rel.parts[0] != "ROMs Manager" or rel.suffix.lower() not in SYSTEMS[system]['extensions']:
        raise ClientError("Destination does not match the console installation mapping")
    parent = root / rel.parts[0]
    if parent.is_symlink():
        raise ClientError("Destination contains a symbolic link")
    parent.mkdir(exist_ok=True)
    target = parent / rel.name
    if target.is_symlink() or not str(target.resolve()).startswith(str(root) + os.sep):
        raise ClientError("Destination escapes approved games folder")
    return target


def checksum_file(path, checksum):
    algorithm = checksum.get("algorithm")
    if algorithm not in ("sha256", "sha1", "md5", "crc32"):
        raise ClientError("Unsupported checksum algorithm")
    digest = hashlib.new(algorithm) if algorithm != "crc32" else None
    crc = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            if digest:
                digest.update(chunk)
            else:
                crc = zlib.crc32(chunk, crc)
    return digest.hexdigest() if digest else "%08x" % (crc & 0xffffffff)


def matches(path, job):
    return path.is_file() and not path.is_symlink() and path.stat().st_size == job["size"] and checksum_file(path, job["checksum"]) == job["checksum"]["value"].lower()


def publish_no_replace(part, target):
    """Atomic install without overwriting another writer; works on exFAT too."""
    try:
        os.link(str(part), str(target), follow_symlinks=False)
        part.unlink()
        return
    except FileExistsError:
        raise ClientError("File conflict: existing game was preserved") from None
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV):
            raise
    if sys.platform != "linux":
        raise ClientError("Filesystem does not support safe atomic installation")
    libc = ctypes.CDLL(None, use_errno=True)
    arguments = (ctypes.c_int(-100), ctypes.c_char_p(os.fsencode(part)), ctypes.c_int(-100), ctypes.c_char_p(os.fsencode(target)), ctypes.c_uint(1))
    rename = getattr(libc, "renameat2", None)
    if rename is not None:
        result = rename(*arguments)
    else:
        numbers = {"armv7l": 382, "armv6l": 382, "aarch64": 276, "x86_64": 316}
        number = numbers.get(platform.machine())
        if number is None:
            raise ClientError("No safe atomic installation method for this architecture")
        result = libc.syscall(ctypes.c_long(number), *arguments)
    if result != 0:
        if ctypes.get_errno() == errno.EEXIST:
            raise ClientError("File conflict: existing game was preserved")
        raise ClientError("Filesystem rejected safe atomic installation")


class Client:
    def __init__(self, config, api=None, allow_test_storage=False, observer=None):
        self.config = config
        self.api = api or Api(config["backend_url"], config["device_token"])
        self.allow_test_storage = allow_test_storage
        self.observer = observer
        self.storages = {}
        if config.get('games_root'):
            # Legacy games_root always means SNES, never a common games parent.
            self.select_system('snes')
        else:
            for system in SYSTEMS:
                try:
                    self.select_system(system)
                    break
                except (ClientError, OSError):
                    continue
            else:
                raise ClientError('No mounted supported games folder is available. Check games_roots in the client config.')

    def storage(self, system):
        if system not in SYSTEMS:
            raise ClientError('No installation mapping for this console')
        if system not in self.storages:
            overrides = self.config.get('games_roots') or {}
            supplied = overrides.get(system) or (self.config.get('games_root') if system == 'snes' else None)
            root = (Path(supplied) if supplied else discover_games_root(system)).resolve(strict=True)
            if not root.is_dir():
                raise ClientError('Configured %s games folder must exist' % SYSTEMS[system]['core'])
            mount = find_mount(root)
            if not self.allow_test_storage and (not str(root).startswith('/media/') or mount == Path('/')):
                raise ClientError('Game storage must be a mounted MiSTer SD, USB, or network filesystem')
            self.storages[system] = (root, mount, root.stat().st_dev)
        root, mount, device = self.storages[system]
        if not root.is_dir() or root.stat().st_dev != device or not os.path.ismount(str(mount)):
            raise ClientError('Selected game storage was disconnected or changed')
        return root, mount, device

    def select_system(self, system):
        if getattr(self, 'system', None) == system:
            self.check_storage()
            self.storage(system)
            return
        self.root, self.mount, self.storage_device = self.storage(system)
        self.system = system

    @staticmethod
    def core_present(system):
        return any(Path('/media/fat/_Console').glob(SYSTEMS[system]['core'] + '*.rbf'))

    def message(self, text):
        if self.observer is None:
            print(text, flush=True)

    def event(self, kind, **fields):
        if self.observer:
            self.observer({'kind': kind, **fields})

    def check_storage(self):
        if not self.root.is_dir() or self.root.stat().st_dev != self.storage_device or not os.path.ismount(str(self.mount)):
            raise ClientError("Selected game storage was disconnected or changed")

    def telemetry(self):
        self.check_storage()
        usage = shutil.disk_usage(str(self.root))
        systems = {}
        for system in SYSTEMS:
            details = {'core_present': self.core_present(system)}
            try:
                root, _, _ = self.storage(system)
                disk = shutil.disk_usage(str(root))
                details.update(games_root=str(root), free_bytes=disk.free, total_bytes=disk.total)
            except (ClientError, OSError):
                details['error'] = 'Games folder unavailable; check games_roots and storage mount'
            systems[system] = details
        return {"games_root": str(self.root), "free_bytes": usage.free, "total_bytes": usage.total,
                "storage_device": self.storage_device, "architecture": platform.machine(),
                "system": self.system, "systems": systems, "core_present": self.core_present(self.system)}

    @staticmethod
    def verify_format(path, system):
        if system == 'nes':
            with path.open('rb') as source:
                header = source.read(16)
            if len(header) != 16 or header[:4] != b'NES\x1a':
                raise ClientError('NES ROM requires an iNES or NES 2.0 header; existing games were preserved')

    def report(self, job, state, downloaded=0, error=None):
        result = self.api.post("jobs/%s/progress" % job["id"], {"lease": job["lease"], "state": state, "downloaded": downloaded, "error": error})
        self.event('progress', system=job['system'], state=state, filename=job['destination'], downloaded=downloaded, total=job['size'], error=error)
        return result

    def open_source(self, job, headers, offset):
        if not job.get('download_path'):
            return urllib.request.build_opener(SafeRedirect()).open(
                urllib.request.Request(job['source_url'], headers=headers), timeout=15)
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            self.check_storage()
            response = self.api.open_download(job['download_path'], headers)
            if response.status != 202:
                return response
            with response:
                status = json.load(response)
            self.report(job, 'preparing', offset)
            self.event('progress', system=job['system'], state='preparing on backend', filename=job['destination'],
                       downloaded=status.get('downloaded', 0), total=status.get('total', 0))
            self.message('Preparing ROM on backend...')
            time.sleep(1)
        raise ClientError('Backend preparation timed out; retry the job to reuse cached work')

    def validate_source(self, job):
        if not job.get('download_path'):
            validate_url(job['source_url'])

    def prepare_job(self, job):
        if not job.get('preparation_path'):
            return
        path = job['preparation_path']
        if not path.startswith('/client/downloads/') or '/preparation?' not in path:
            raise ClientError('Invalid preparation status path')
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            self.check_storage()
            status = self.api.get(path)
            if status.get('state') == 'ready':
                prepared = status.get('job') or {}
                if prepared.get('id') != job['id'] or prepared.get('system') != job['system']:
                    raise ClientError('Prepared ROM does not match the selected job')
                job.update(prepared)
                return
            if status.get('state') == 'failed':
                raise ClientError(status.get('error') or 'Backend could not prepare the ROM; retry the job')
            self.report(job, 'preparing', 0)
            self.event('progress', system=job['system'], state='preparing on backend', filename=job['destination'],
                       downloaded=status.get('downloaded', 0), total=status.get('total', 0))
            self.message('Preparing ROM on backend...')
            time.sleep(1)
        raise ClientError('Backend preparation timed out; retry to reuse cached work')

    def install(self, job):
        system = job.get('system')
        if system not in SYSTEMS or not re.fullmatch(r"[0-9a-f]{32}", job.get("id", "")):
            raise ClientError("Invalid console job")
        self.select_system(system)
        self.check_storage()
        if not self.allow_test_storage and not self.core_present(system):
            raise ClientError('%s core not found; use your normal MiSTer updater first' % SYSTEMS[system]['core'])
        # Persisted final metadata lets an installed file/full partial recover
        # a lost acknowledgement even when the provider/cache is unavailable.
        if not isinstance(job.get('size'), int) or job['size'] <= 0 or not job.get('checksum'):
            self.prepare_job(job)
        size = job.get("size")
        if not isinstance(size, int) or not 0 < size <= MAX_ROM_SIZE:
            raise ClientError("Invalid ROM file size")
        self.validate_source(job)
        target = destination_path(self.root, job["destination"], system)
        if target.exists():
            if not matches(target, job):
                raise ClientError("File conflict: existing game was preserved")
            self.verify_format(target, system)
            self.report(job, "verifying", size)
            self.report(job, "completed", size)
            return
        part = target.parent / ("." + job["id"] + ".part")
        metadata = part.with_suffix(".resume.json")
        if part.is_symlink() or metadata.is_symlink():
            raise ClientError("Unsafe partial download path")
        source = job.get('source_identity') or job.get('source_url') or 'backend:' + str(job.get('download_path', ''))
        source_id = hashlib.sha256(json.dumps({'source_url': source, 'size': job['size'], 'checksum': job['checksum']}, sort_keys=True).encode()).hexdigest()
        saved = {}
        try:
            saved = json.loads(metadata.read_text())
        except (OSError, ValueError):
            pass
        offset = part.stat().st_size if part.exists() else 0
        etag = saved.get("etag", "")
        resume = 0 < offset < size and saved.get("source_id") == source_id and etag.startswith('"') and etag.endswith('"')
        if offset == size and matches(part, job):
            self.report(job, "verifying", size)
        else:
            if shutil.disk_usage(str(self.root)).free < (size - offset if resume else size) + RESERVE_BYTES:
                raise ClientError("Insufficient free space; existing files were preserved")
            headers = {"Accept-Encoding": "identity", "User-Agent": "ROMs-Manager-MiSTer/0.1"}
            if resume:
                headers.update({"Range": "bytes=%d-" % offset, "If-Range": etag})
            with self.open_source(job, headers, offset) as response:
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ClientError("Source returned an encoded response; raw ROM required")
                append = False
                if response.status == 206:
                    expected_range = "bytes %d-%d/%d" % (offset, size - 1, size)
                    if not resume or response.headers.get("Content-Range") != expected_range or response.headers.get("ETag") != etag:
                        raise ClientError("Source returned an inconsistent resume response")
                    append = True
                elif response.status != 200:
                    raise ClientError("Source did not return a complete download")
                if not append:
                    offset = 0
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != size - offset:
                    raise ClientError("Source size differs from the catalog; refresh the source")
                flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | (os.O_APPEND if append else os.O_TRUNC)
                descriptor = os.open(str(part), flags, 0o600)
                # Cache only a URL fingerprint and validator, never credentials.
                meta_fd = os.open(str(metadata), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(meta_fd, "w") as fh:
                    json.dump({"source_id": source_id, "etag": response.headers.get("ETag", "")}, fh)
                with os.fdopen(descriptor, "ab" if append else "wb") as fh:
                    last_report = 0
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        self.check_storage()
                        if offset + len(chunk) > size:
                            raise ClientError("Source exceeded the expected file size")
                        fh.write(chunk)
                        offset += len(chunk)
                        if time.monotonic() - last_report >= 1:
                            self.report(job, "downloading", offset)
                            self.message("Downloading: %d%%" % (offset * 100 // size))
                            last_report = time.monotonic()
                    fh.flush()
                    os.fsync(fh.fileno())
            self.report(job, "verifying", offset)
        if not matches(part, job):
            part.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
            raise ClientError("Downloaded file failed size/checksum verification")
        self.check_storage()
        # Recheck parents immediately before publishing; never overwrite a game.
        self.verify_format(part, system)
        target = destination_path(self.root, job["destination"], system)
        publish_no_replace(part, target)
        metadata.unlink(missing_ok=True)
        self.report(job, "completed", size)

    def sync(self):
        # /tmp is local Linux storage, unlike FAT game storage which lacks flock.
        lock_path = Path(self.config.get("lock_file", "/tmp/roms_manager_mister.lock"))
        lock_fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(lock_fd, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ClientError("Another ROMs Manager sync is already running") from None
            self.api.post("heartbeat", self.telemetry())
            failed = 0
            for _ in range(100):
                job = self.api.post("claim")["job"]
                if job is None:
                    self.message("Sync finished. No claimable jobs remain.")
                    return failed
                self.message("Installing: " + job["destination"])
                self.event('start', system=job['system'], filename=job['destination'], total=job['size'])
                try:
                    self.install(job)
                    self.message("Verified and installed.")
                except ApiError:
                    raise
                except (ClientError, OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as exc:
                    failed += 1
                    if isinstance(exc, ClientError):
                        message = str(exc)
                    elif isinstance(exc, urllib.error.HTTPError):
                        message = "Download source returned HTTP %d" % exc.code
                    else:
                        message = "Download or storage operation failed; check source, connection, and free space"
                    self.report(job, "failed", error=message)
                    self.message(message)
                self.api.post("heartbeat", self.telemetry())
            self.message("Sync limit reached; run again for remaining jobs.")
            return failed


def load_config(path):
    config_path = Path(path)
    if config_path.is_symlink():
        raise ClientError("Config must not be a symbolic link")
    if stat.S_IMODE(config_path.stat().st_mode) & 0o077 and not fat_config(config_path):
        raise ClientError("Config contains a credential; run chmod 600 on the config file")
    config = json.loads(config_path.read_text())
    if not isinstance(config, dict) or not all(isinstance(config.get(key), str) and config[key].strip() for key in ('backend_url', 'device_token')):
        raise ClientError('Config must contain a backend address and client API key')
    roots = config.get('games_roots', {})
    if not isinstance(roots, dict) or any(system not in SYSTEMS or not isinstance(path, str) or not path.strip() for system, path in roots.items()):
        raise ClientError('games_roots must map supported console IDs to games folder paths')
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--probe", action="store_true", help="Inspect storage without contacting the backend")
    mode.add_argument('--ui', action='store_true', help='Open the interactive dialog menu')
    mode.add_argument('--sync', action='store_true', help='Sync pending jobs without menus (also the default)')
    mode.add_argument('--refresh-catalog', action='store_true', help='Refresh the locally cached assigned catalogs')
    mode.add_argument('--backend-sync', action='store_true', help='Legacy online backend-job sync')
    args = parser.parse_args()
    try:
        if args.ui:
            if __package__:
                from .roms_manager_ui import run_ui
            else:
                from roms_manager_ui import run_ui
            return run_ui(args.config)
        if args.probe:
            systems = {}
            for system in SYSTEMS:
                details = {'core_present': Client.core_present(system)}
                try:
                    root = discover_games_root(system).resolve(strict=True)
                    usage = shutil.disk_usage(str(root))
                    details.update(games_root=str(root), mount=str(find_mount(root)), free_bytes=usage.free, total_bytes=usage.total)
                except (ClientError, OSError):
                    details['error'] = 'Standard games folder is unavailable'
                systems[system] = details
            print(json.dumps({'architecture': platform.machine(), 'python': platform.python_version(), 'systems': systems}, indent=2))
            return 0
        config = load_config(args.config)
        if args.backend_sync:
            return 1 if Client(config).sync() else 0
        if __package__:
            from .offline_client import OfflineRuntime
        else:
            from offline_client import OfflineRuntime
        runtime = OfflineRuntime(config, args.config)
        try:
            if args.refresh_catalog:
                try:
                    result = runtime.refresh(observer=lambda event: print('Caching %s: %s/%s' % (event['filename'], event['downloaded'], event['total']), flush=True))
                    print('Catalog cache updated:', result)
                except BackendUnavailable:
                    print('Backend unavailable. Existing cached catalogs remain usable.')
                    return 0 if runtime.cache.get('user') else 1
                return 0
            return 1 if runtime.client_factory().sync() else 0
        finally:
            runtime.close()
    except KeyboardInterrupt:
        print('ROMs Manager: interrupted. Partial downloads are retained; run sync again to resume.', file=sys.stderr)
        return 130
    except (ClientError, OSError, ValueError, KeyError):
        # Tracebacks and raw network exceptions can contain source URLs/tokens.
        exc = sys.exc_info()[1]
        print("ROMs Manager: " + (str(exc) if isinstance(exc, ClientError) else "Check the local config file and storage permissions"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Imported adapters must share these error types with the executable module.
    sys.modules.setdefault("roms_manager_client", sys.modules[__name__])
    sys.exit(main())
