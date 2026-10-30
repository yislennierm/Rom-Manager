"""Reusable local preparation of exact, checksummed ZIP members."""
import hashlib
import json
import os
import re
import shutil
import stat
import selectors
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from core.client_downloads import DownloadError
from core.archive_names import select_archive_rom_name

MAX_ARCHIVE_SIZE = 512 * 1024 * 1024
MAX_MEMBER_SIZE = 64 * 1024 * 1024
SEVEN_ZIP = shutil.which('7zz') or shutil.which('7z') or shutil.which('7za') or shutil.which('7zr')


def safe_member(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or '\\' in value or any(ord(c) < 32 for c in value):
        raise DownloadError('Unsafe ZIP member path')
    if value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/')):
        raise DownloadError('Unsafe ZIP member path')
    return str(PurePosixPath(value))


def checksum_spec(source):
    for algorithm, length in (('sha256', 64), ('sha1', 40), ('md5', 32), ('crc32', 8)):
        value = str(source.get(algorithm) or (source.get('crc') if algorithm == 'crc32' else '') or '').lower()
        if re.fullmatch('[0-9a-f]{%d}' % length, value):
            return {'algorithm': algorithm, 'value': value}
    raise DownloadError('Source requires the exact file checksum')


def zip_member_source(source, extensions):
    """Only member-level hashes/sizes are usable here; ZIP hashes are different."""
    member = source.get('_archive_member_path') or source.get('archive_member_path')
    if not source.get('_archive_member') and not member:
        raise DownloadError('ZIP source has no catalogued member')
    member = safe_member(member)
    parsed = urlsplit(source.get('http_url') or '')
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise DownloadError('Invalid archive source')
    if not parsed.path.lower().endswith('.zip') or Path(member).suffix.lower() not in extensions:
        raise DownloadError('Source is not a supported ZIP member')
    if source.get('headers') or source.get('requires_auth'):
        raise DownloadError('Source requires an unsupported authentication flow')
    size = int(source.get('size') or source.get('_size_bytes') or 0)
    archive_size = int(source.get('_source_bundle_size') or 0)
    if not 0 < size <= MAX_MEMBER_SIZE or not 0 <= archive_size <= MAX_ARCHIVE_SIZE:
        raise DownloadError('ZIP source exceeds the preparation size limit')
    return {'url': source['http_url'], 'member': member, 'archive_size': archive_size,
            'size': size, 'checksum': checksum_spec(source)}


def archive_rom_source(source, extensions, title):
    parsed = urlsplit(source.get('http_url') or '')
    suffix = Path(parsed.path).suffix.lower()
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or suffix not in ('.zip', '.7z'):
        raise DownloadError('Not a supported archive source')
    filename = source.get('rom_name') or source.get('name') or Path(parsed.path).name
    if not isinstance(filename, str) or Path(filename).suffix.lower() != suffix:
        raise DownloadError('Archive metadata must describe the archive file itself')
    if suffix == '.7z' and not SEVEN_ZIP:
        raise DownloadError('7z is required to prepare this source')
    if source.get('headers') or source.get('requires_auth'):
        raise DownloadError('Source requires an unsupported authentication flow')
    size = int(source.get('size') or source.get('_size_bytes') or 0)
    if not 0 < size <= MAX_ARCHIVE_SIZE:
        raise DownloadError('Archive exceeds its preparation size limit')
    return {'kind': 'archive_rom', 'format': suffix[1:], 'url': source['http_url'],
            'archive_size': size, 'archive_checksum': checksum_spec(source), 'size': MAX_MEMBER_SIZE,
            'extensions': list(extensions), 'title': str(title)}


class SourceRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if new.scheme not in ('http', 'https') or not new.hostname or new.username or new.password:
            raise DownloadError('Archive redirected to an invalid source')
        if old.scheme == 'https' and new.scheme != 'https':
            raise DownloadError('Archive redirected from HTTPS to HTTP')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            redirected.remove_header('Authorization')
            trusted = lambda host: host == 'archive.org' or host.endswith('.archive.org')
            if old.hostname != new.hostname and not (trusted(old.hostname or '') and trusted(new.hostname)):
                redirected.remove_header('Cookie')
        return redirected


class SourcePreparer:
    def __init__(self, root, headers_provider=None, network_timeout=20):
        self.root = Path(root)
        if self.root.is_symlink():
            raise DownloadError('Preparation cache cannot be a symbolic link')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.Lock()
        self.states = {}
        self.headers_provider = headers_provider
        self.network_timeout = network_timeout
        self.stop = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='download-preparation')

    @staticmethod
    def key(plan):
        return hashlib.sha256(json.dumps(plan['_preparation'], sort_keys=True).encode()).hexdigest()

    def update(self, key, **status):
        with self.lock:
            self.states[key] = {**self.states.get(key, {}), **status}

    @staticmethod
    def verify_file(path, specification):
        if path.is_symlink() or not path.is_file() or path.stat().st_size != specification['size']:
            raise DownloadError('Prepared ROM has an unexpected size')
        algorithm = specification['checksum']['algorithm']
        expected = specification['checksum']['value']
        if algorithm not in ('sha256', 'sha1', 'md5', 'crc32'):
            raise DownloadError('Unsupported ROM checksum')
        digest = hashlib.new(algorithm) if algorithm != 'crc32' else None
        sha256, crc = hashlib.sha256(), 0
        with path.open('rb') as content:
            for block in iter(lambda: content.read(65536), b''):
                sha256.update(block)
                if digest:
                    digest.update(block)
                else:
                    crc = zlib.crc32(block, crc)
        actual = digest.hexdigest() if digest else '%08x' % (crc & 0xffffffff)
        if actual != expected:
            raise DownloadError('Prepared ROM checksum differs from the catalog; refresh the source or retry')
        return sha256.hexdigest()

    def status(self, plan):
        key = self.key(plan)
        path = self.root / (key + '.rom')
        specification = plan['_preparation']
        if path.exists() or path.is_symlink():
            try:
                content = None
                if specification.get('kind') == 'archive_rom':
                    metadata = path.with_suffix('.json')
                    if metadata.is_symlink():
                        raise DownloadError('Unsafe preparation metadata')
                    content = json.loads(metadata.read_text())
                    if not 0 < content['size'] <= MAX_MEMBER_SIZE or content['checksum']['algorithm'] != 'sha256' or not re.fullmatch('[0-9a-f]{64}', content['checksum']['value']):
                        raise DownloadError('Invalid prepared content metadata')
                    safe_member(content['filename'])
                    if '/' in content['filename'] or Path(content['filename']).suffix.lower() not in specification['extensions']:
                        raise DownloadError('Invalid prepared ROM filename')
                digest = self.verify_file(path, content or specification)
                return {'state': 'ready', 'path': path, 'etag': '"' + digest + '"', 'content': content}
            except (DownloadError, OSError, ValueError, KeyError, TypeError):
                path.unlink()
        with self.lock:
            if self.states.get(key, {}).get('state') == 'ready':
                self.states.pop(key, None)
            if key not in self.states:
                self.states[key] = {'state': 'preparing', 'downloaded': 0, 'total': plan['_preparation']['archive_size']}
                self.executor.submit(self.prepare, key, plan)
            return dict(self.states[key])

    def retry(self, plan):
        if not plan.get('_preparation'):
            return
        key = self.key(plan)
        with self.lock:
            if self.states.get(key, {}).get('state') == 'failed':
                self.states.pop(key, None)

    def progress(self, plan):
        if not plan or not plan.get('_preparation'):
            return None
        with self.lock:
            status = self.states.get(self.key(plan), {})
            return {k: status[k] for k in ('state', 'downloaded', 'total') if k in status} or None

    def archive(self, specification, staging, key):
        archive_key = hashlib.sha256(json.dumps({k: specification[k] for k in ('url', 'archive_size')}, sort_keys=True).encode()).hexdigest()
        cached = self.root / (archive_key + '.' + specification.get('format', 'zip'))
        expected = specification['archive_size']
        def valid(path):
            if not path.is_file() or path.is_symlink() or expected and path.stat().st_size != expected:
                return False
            if specification.get('format') == '7z':
                with path.open('rb') as stream:
                    if stream.read(6) != b'7z\xbc\xaf\x27\x1c':
                        return False
            elif not zipfile.is_zipfile(path):
                return False
            if specification.get('archive_checksum'):
                try:
                    self.verify_file(path, {'size': expected, 'checksum': specification['archive_checksum']})
                except DownloadError:
                    return False
            return True
        if valid(cached):
            self.update(key, downloaded=cached.stat().st_size, total=cached.stat().st_size)
            return cached
        if cached.exists() or cached.is_symlink():
            cached.unlink()
        if shutil.disk_usage(self.root).free < (expected or MAX_ARCHIVE_SIZE) + specification['size'] + 16 * 1024 * 1024:
            raise DownloadError('Preparation cache storage is full; free space and retry')
        host = urlsplit(specification['url']).hostname or ''
        # Reuse configured Internet Archive credentials only for its actual hosts.
        headers = self.headers_provider(specification['url']) if self.headers_provider and (host == 'archive.org' or host.endswith('.archive.org')) else {'User-Agent': 'ROMs-Manager/1.0'}
        headers['Accept-Encoding'] = 'identity'
        temporary = staging / 'source.zip'
        opener = urllib.request.build_opener(SourceRedirect())
        request = urllib.request.Request(specification['url'], headers=headers)
        deadline = time.monotonic() + 300
        with opener.open(request, timeout=self.network_timeout) as response, temporary.open('wb') as output:
            if response.status != 200 or response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise DownloadError('Archive source did not return a complete ZIP file')
            declared = int(response.headers.get('Content-Length') or 0)
            if declared > MAX_ARCHIVE_SIZE or expected and declared and declared != expected:
                raise DownloadError('Archive size differs from the catalog')
            downloaded = 0
            while True:
                if self.stop.is_set() or time.monotonic() > deadline:
                    raise DownloadError('Preparation interrupted or timed out; retry to start again')
                block = response.read(65536)
                if not block:
                    break
                downloaded += len(block)
                if downloaded > (expected or MAX_ARCHIVE_SIZE):
                    raise DownloadError('Archive exceeded its permitted size')
                output.write(block)
                self.update(key, downloaded=downloaded, total=expected or declared)
            output.flush()
            os.fsync(output.fileno())
        if expected and downloaded != expected or declared and downloaded != declared or not valid(temporary):
            raise DownloadError('Archive download was incomplete or invalid')
        os.replace(temporary, cached)
        return cached

    def copy_member(self, archive, info, output, limit):
        safe_member(info.filename)
        mode = stat.S_IFMT(info.external_attr >> 16)
        if info.is_dir() or mode not in (0, stat.S_IFREG) or info.flag_bits & 1:
            raise DownloadError('Selected ZIP member is not a regular unencrypted file')
        if not 0 < info.file_size <= limit:
            raise DownloadError('ZIP member exceeds the permitted size')
        if shutil.disk_usage(self.root).free < info.file_size + 16 * 1024 * 1024:
            raise DownloadError('Preparation cache storage is full; free space and retry')
        with archive.open(info) as source, output.open('wb') as destination:
            written = 0
            while True:
                if self.stop.is_set():
                    raise DownloadError('Preparation interrupted; retry after backend restart')
                block = source.read(65536)
                if not block:
                    break
                written += len(block)
                if written > limit or written > info.file_size:
                    raise DownloadError('ZIP member exceeded its expected size')
                destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())
        if written != info.file_size:
            raise DownloadError('ZIP member was incomplete')

    def extract_member(self, archive, member, staging, expected_size, depth=0):
        infos = archive.infolist()
        matches = [info for info in infos if info.filename == member]
        if len(matches) > 1:
            raise DownloadError('Selected ROM member is ambiguous; refresh the catalog')
        if matches:
            info = matches[0]
            if info.file_size != expected_size:
                raise DownloadError('ZIP member size differs from the catalog')
            output = staging / 'content.rom'
            self.copy_member(archive, info, output, expected_size)
            return output
        # Provider indexing expands "Game.zip" into "Game/". Follow only
        # that exact branch, matching the existing catalog's virtual path.
        nested = []
        for info in infos:
            if info.filename.lower().endswith('.zip'):
                prefix = info.filename[:-4] + '/'
                if member.startswith(prefix):
                    nested.append((info, member[len(prefix):]))
        if len(nested) != 1 or depth >= 4:
            raise DownloadError('Selected ROM member is missing, ambiguous, or nested too deeply; refresh the catalog')
        info, remainder = nested[0]
        safe_member(remainder)
        temporary = staging / ('nested-%d.zip' % depth)
        self.copy_member(archive, info, temporary, MAX_MEMBER_SIZE)
        with zipfile.ZipFile(temporary) as inner:
            return self.extract_member(inner, remainder, staging, expected_size, depth + 1)

    def run_7z(self, arguments, limit, timeout=30):
        if not SEVEN_ZIP:
            raise DownloadError('7z is not installed on the backend')
        process = subprocess.Popen([SEVEN_ZIP, *arguments], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        data = bytearray()
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    if self.stop.is_set() or time.monotonic() > deadline:
                        raise DownloadError('Archive preparation interrupted or timed out')
                    if not selector.select(.2):
                        continue
                    block = os.read(process.stdout.fileno(), 65536)
                    if not block:
                        break
                    data.extend(block)
                    if len(data) > limit:
                        raise DownloadError('Archive output exceeds its permitted size')
            if process.wait(timeout=2) != 0:
                raise DownloadError('Archive is damaged, encrypted, or unsupported by the backend extractor')
            return bytes(data)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()

    @staticmethod
    def choose_rom(names, specification):
        def title_key(name):
            name = name.rsplit('/', 1)[-1]
            if Path(name).suffix.lower() in specification['extensions']:
                name = name.rsplit('.', 1)[0]
            return re.sub('[^a-z0-9]', '', name.lower())
        names = [safe_member(name) for name in names if Path(name).suffix.lower() in specification['extensions']]
        exact = [name for name in names if title_key(name) == title_key(specification['title'])]
        selected = exact if exact else names
        if not exact and len(names) > 1:
            matched = select_archive_rom_name(specification['title'], names)
            selected = [matched] if matched else []
        if len(selected) != 1:
            raise DownloadError('Archive has no unique matching ROM; refresh the provider index to select an exact member')
        return selected[0]

    def prepare_archive_rom(self, cached, staging, specification):
        output = staging / 'content.rom'
        if specification['format'] == 'zip':
            with zipfile.ZipFile(cached) as archive:
                names = [info.filename for info in archive.infolist() if not info.is_dir()]
                member = self.choose_rom(names, specification)
                matches = [info for info in archive.infolist() if info.filename == member]
                if len(matches) != 1:
                    raise DownloadError('Archive has duplicate ROM members')
                self.copy_member(archive, matches[0], output, MAX_MEMBER_SIZE)
        else:
            listing = self.run_7z(['l', '-slt', '-ba', '-sccUTF-8', '--', str(cached)], 2 * 1024 * 1024).decode('utf-8')
            records = []
            for block in re.split(r'\r?\n\s*\r?\n', listing):
                record = {}
                for line in block.splitlines():
                    if ' = ' in line:
                        key, value = line.split(' = ', 1)
                        if key in record:
                            raise DownloadError('Archive member listing is ambiguous')
                        record[key] = value
                if 'Path' in record and record.get('Folder') != '+':
                    records.append(record)
            member = self.choose_rom([record['Path'] for record in records], specification)
            matches = [record for record in records if record['Path'] == member]
            if len(matches) != 1:
                raise DownloadError('Archive has duplicate ROM members')
            record = matches[0]
            if record.get('Encrypted') == '+' or any('Link' in key for key in record) or member.startswith(('-', '@')):
                raise DownloadError('Selected archive member is not an ordinary unencrypted ROM')
            size = int(record.get('Size') or 0)
            if not 0 < size <= MAX_MEMBER_SIZE:
                raise DownloadError('Archive ROM exceeds its permitted size')
            payload = self.run_7z(['x', '-so', '-y', '-spd', '-mmt=1', '--', str(cached), member], size)
            if len(payload) != size:
                raise DownloadError('Archive ROM was incomplete')
            output.write_bytes(payload)
        size = output.stat().st_size
        if not 0 < size <= MAX_MEMBER_SIZE:
            raise DownloadError('Prepared ROM exceeds its permitted size')
        checksum = hashlib.sha256(output.read_bytes()).hexdigest()
        return output, {'size': size, 'checksum': {'algorithm': 'sha256', 'value': checksum}, 'filename': member.rsplit('/', 1)[-1]}

    def prepare(self, key, plan):
        cached = None
        try:
            specification = plan['_preparation']
            with tempfile.TemporaryDirectory(prefix='.prepare-', dir=str(self.root)) as directory:
                staging = Path(directory)
                cached = self.archive(specification, staging, key)
                content = None
                if specification.get('kind') == 'archive_rom':
                    output, content = self.prepare_archive_rom(cached, staging, specification)
                else:
                    member = safe_member(specification['member'])
                    with zipfile.ZipFile(cached) as archive:
                        output = self.extract_member(archive, member, staging, specification['size'])
                    self.verify_file(output, specification)
                if content:
                    metadata = staging / 'content.json'
                    metadata.write_text(json.dumps(content))
                    os.replace(metadata, self.root / (key + '.json'))
                os.replace(output, self.root / (key + '.rom'))
                self.update(key, state='ready')
        except urllib.error.HTTPError as exc:
            self.update(key, state='failed', error='Archive provider returned HTTP %d; retry or check backend provider access' % exc.code)
        except DownloadError as exc:
            self.update(key, state='failed', error=str(exc))
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
            if cached and cached.exists():
                cached.unlink()
            self.update(key, state='failed', error='ZIP is damaged, encrypted, or uses unsupported compression; refresh the source')
        except Exception:
            self.update(key, state='failed', error='Backend could not prepare the ROM; check provider connectivity and cache storage, then retry')

    def close(self):
        self.stop.set()
        try:
            self.executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:  # Python 3.8 has no cancel_futures option.
            self.executor.shutdown(wait=True)
