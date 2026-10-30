"""Local browser preview of the real MiSTer dialog UI; never deploys or installs ROMs."""
import argparse
import copy
from functools import wraps
import fcntl
import io
import json
import os
import pty
import re
import secrets
import select
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import tempfile
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from core.mister_sources import SYSTEM_GUIDS
from mister.catalog_cache import CatalogCache
from mister.roms_manager_client import Api, ClientError, load_config
from mister.roms_manager_ui import Dialog, MisterUI

HERE = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = HERE / '.preview-cache'
GEOMETRY = {'sofa': (53, 22), 'desktop': (160, 45)}
KEYS = {'up': '\x1b[A', 'down': '\x1b[B', 'left': '\x1b[D', 'right': '\x1b[C',
        'enter': '\n', 'back': '\x1b', 'tab': '\t', 'pageup': '\x1b[5~',
        'pagedown': '\x1b[6~', 'home': '\x1b[1~', 'end': '\x1b[4~'}
PALETTE = {'default': '#bdbdbd', 'black': '#000000', 'red': '#aa0000', 'green': '#00aa00',
           'brown': '#aaaa00', 'blue': '#0000aa', 'magenta': '#aa00aa', 'cyan': '#00aaaa',
           'white': '#aaaaaa', 'brightblack': '#555555', 'brightred': '#ff5555',
           'brightgreen': '#55ff55', 'brightbrown': '#ffff55', 'brightblue': '#5555ff',
           'brightmagenta': '#ff55ff', 'brightcyan': '#55ffff', 'brightwhite': '#ffffff'}


def color(value, background=False, bold=False):
    if value == 'default':
        return '#000000' if background else '#bdbdbd'
    if bold and 'bright' + value in PALETTE:
        value = 'bright' + value
    return PALETTE.get(value, '#' + value if re.fullmatch('[0-9a-fA-F]{6}', value) else '#bdbdbd')


class PreviewCatalogCache(CatalogCache):
    """Display captured device compatibility regardless of host extractor tools."""
    def read_entry(self, catalog, row):
        with (self.path.parent / catalog['entries']).open('rb') as source:
            source.seek(row[4])
            return json.loads(source.read(row[5]))

    @staticmethod
    def compact(entry, guid):
        data, search = CatalogCache.compact(entry, guid)
        if isinstance(entry.get('mister'), dict):
            data['mister'] = dict(entry['mister'])
        return data, search


class PreviewApi:
    """Real cached browsing, with a separate in-memory queue and no network client."""
    def __init__(self, snapshot):
        self.cache = PreviewCatalogCache(Path(snapshot) / 'catalog.json')
        self.jobs = []

    def library(self, slug, query='', available_only=False):
        return self.cache.title_list(slug, query, available_only)

    def game_entry(self, slug, key):
        return self.cache.entry(slug, key)[1]

    def get(self, path):
        from urllib.parse import parse_qs, unquote
        url = urlsplit(path)
        query = parse_qs(url.query)
        if url.path == '/me':
            return {'user': self.cache.user()}
        if url.path == '/client/downloads/targets':
            return {'targets': [{'id': 'mister', 'system_guids': {system: guid for system, guid in SYSTEM_GUIDS.items() if self.cache.can_use(guid)}}]}
        if url.path == '/roms':
            return {'roms': self.cache.catalogs()}
        if url.path.startswith('/roms/'):
            return self.cache.page(unquote(url.path[6:]), limit=int(query.get('limit', ['5'])[0]),
                offset=int(query.get('offset', ['0'])[0]), query=query.get('q', [''])[0], available_only='download_target' in query)
        if url.path == '/client/downloads':
            limit, offset = int(query.get('limit', ['5'])[0]), int(query.get('offset', ['0'])[0])
            return {'jobs': copy.deepcopy(self.jobs[offset:offset+limit]), 'total': len(self.jobs), 'has_more': offset+limit < len(self.jobs)}
        raise ClientError('This operation is unavailable in local preview.')

    def request(self, method, path, body=None):
        if method == 'GET':
            return self.get(path)
        if method == 'POST' and path == '/client/downloads':
            guid, entry = self.cache.entry(body['dataset'], body['entry_key'])
            if not (entry.get('mister') or {}).get('supported'):
                raise ClientError('No compatible source for this entry.')
            existing = next((job for job in self.jobs if job['_entry_key'] == body['entry_key'] and job['_dataset'] == body['dataset']), None)
            if existing:
                return {'job': copy.deepcopy(existing)}
            system = next(system for system, value in SYSTEM_GUIDS.items() if value == guid)
            job = {'id': secrets.token_hex(16), '_entry_key': body['entry_key'], '_dataset': body['dataset'],
                'title': entry.get('name') or entry.get('rom_name'), 'destination': 'ROMs Manager/' + str(entry.get('name') or 'Game'),
                'system': system, 'size': entry['mister'].get('size') or 1024*1024, 'downloaded': 0,
                'state': 'queued', 'attempts': 0, 'error': None}
            self.jobs.append(job)
            return {'job': copy.deepcopy(job)}
        if method == 'POST' and '/retry?' in path:
            job_id = path.split('/')[3]
            job = next(job for job in self.jobs if job['id'] == job_id)
            job.update(state='queued', downloaded=0, error=None)
            return {'queued': True}
        raise ClientError('This operation is unavailable in local preview.')


class PreviewTransfer:
    def __init__(self, api, observer=None):
        self.api, self.observer = api, observer

    def telemetry(self):
        with self.api.cache.transaction() as data:
            return data.get('preview_telemetry') or {'games_root': 'Preview only', 'systems': {}}

    def sync(self):
        for job in self.api.jobs:
            if job['state'] != 'queued':
                continue
            job['attempts'] += 1
            for state, percent in [('downloading', 0), ('downloading', 25), ('downloading', 50), ('downloading', 75), ('verifying', 100), ('completed', 100)]:
                job.update(state=state, downloaded=job['size']*percent//100)
                if self.observer:
                    self.observer({'state': state, 'system': job['system'], 'filename': job['destination'],
                                   'downloaded': job['downloaded'], 'total': job['size']})
                time.sleep(.25)
        return 0


def set_geometry(fd, mode):
    columns, lines = GEOMETRY[mode]
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', lines, columns, 0, 0))


def run_child(snapshot, executable, mode):
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    # This display adapter changes this private PTY only. No setfont or device I/O.
    class Display:
        notice = None
        def __init__(self):
            self.mode = mode
        def set_mode(self, value):
            self.mode = value
            set_geometry(0, value)
    api = PreviewApi(snapshot)
    runtime = SimpleNamespace(cache=api.cache, wake=threading.Event(), import_web_queue=lambda: 0,
                              refresh=lambda observer=None: {'catalogs': len(api.cache.catalogs()), 'updated': 0})
    display = Display()
    with Dialog(executable=executable) as dialog:
        # Ordinary PTY keyboard input; no controller/device discovery on the host.
        dialog.input_factory = None
        return MisterUI({}, dialog, api=api, runtime=runtime, display=display,
            client_factory=lambda config, api=api, observer=None: PreviewTransfer(api, observer)).run()


def session_operation(method):
    @wraps(method)
    def serialized(self, *args, **kwargs):
        with self.lifecycle:
            return method(self, *args, **kwargs)
    return serialized


class PreviewSession:
    def __init__(self, snapshot, executable, mode='sofa'):
        try:
            import pyte
        except ImportError:
            raise ClientError('Install host preview requirements: backend/.venv/bin/python -m pip install -r mister/requirements-preview.txt') from None
        self.pyte, self.snapshot, self.executable = pyte, Path(snapshot), executable
        self.lock = threading.RLock()
        self.lifecycle = threading.RLock()
        self.process = None
        self.master = None
        self.reader = None
        self.stopping = threading.Event()
        self.restart(mode)

    @session_operation
    def restart(self, mode):
        if mode not in GEOMETRY:
            raise ValueError('Unknown display mode')
        self.close()
        self.stopping = threading.Event()
        master, slave = pty.openpty()
        set_geometry(slave, mode)
        columns, lines = GEOMETRY[mode]
        with self.lock:
            self.master = master
            self.screen = self.pyte.Screen(columns, lines)
            self.stream = self.pyte.ByteStream(self.screen)
            self.process = subprocess.Popen([sys.executable, '-m', 'mister.preview', '--child', '--snapshot', str(self.snapshot),
                '--dialog', self.executable, '--mode', mode], stdin=slave, stdout=slave, stderr=slave,
                cwd=str(HERE.parent), env={**os.environ, 'TERM': 'linux', 'ESCDELAY': '150'}, start_new_session=True)
        os.close(slave)
        self.reader = threading.Thread(target=self.read_output, daemon=True)
        self.reader.start()

    def read_output(self):
        while not self.stopping.is_set():
            try:
                ready, _, _ = select.select([self.master], [], [], .1)
                if not ready:
                    continue
                output = os.read(self.master, 65536)
                if not output:
                    break
                with self.lock:
                    size = os.get_terminal_size(self.master)
                    if (self.screen.columns, self.screen.lines) != (size.columns, size.lines):
                        self.screen.resize(lines=size.lines, columns=size.columns)
                    self.stream.feed(output)
            except (OSError, ValueError):
                break

    @session_operation
    def input(self, value):
        if not isinstance(value, str) or len(value) > 256:
            raise ValueError('Invalid keyboard input')
        with self.lock:
            if self.process and self.process.poll() is None:
                os.write(self.master, value.encode('utf-8'))

    @session_operation
    def snapshot_screen(self):
        with self.lock:
            rows = []
            for y in range(self.screen.lines):
                runs = []
                for x in range(self.screen.columns):
                    char = self.screen.buffer[y][x]
                    fg, bg = color(char.fg, bold=char.bold), color(char.bg, background=True)
                    if char.reverse:
                        fg, bg = bg, fg
                    style = [fg, bg, bool(char.bold)]
                    if runs and runs[-1]['style'] == style:
                        runs[-1]['text'] += char.data
                    else:
                        runs.append({'style': style, 'text': char.data})
                rows.append(runs)
            return {'rows': rows, 'columns': self.screen.columns, 'lines': self.screen.lines,
                    'running': bool(self.process and self.process.poll() is None)}

    @session_operation
    def close(self):
        self.stopping.set()
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except OSError:
                    pass
                self.process.wait(timeout=2)
        if self.reader:
            self.reader.join(timeout=1)
        if self.master is not None:
            try:
                os.close(self.master)
            except OSError:
                pass
        self.process, self.master, self.reader = None, None, None


CAPTURE_SCRIPT = '''import io,json,sys,tarfile
from pathlib import Path
sys.path.insert(0,'/media/fat/Scripts/.config/roms_manager')
from catalog_cache import CatalogCache,cache_identity
from roms_manager_client import load_config,Client
path=Path('/media/fat/Scripts/.config/roms_manager/config.json')
config=load_config(path)
root=Path(config.get('cache_root') or path.with_name('cache'))/cache_identity(config)
cache=CatalogCache(root/'catalog.json')
with cache.transaction() as data:
 manifest={'state':{'user':data['state']['user'],'synced':data['state'].get('synced'),'network':'Cached local preview'},'catalogs':data['catalogs']}
manifest['preview_telemetry']=Client(config,api=object()).telemetry()
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
 payload=json.dumps(manifest).encode();info=tarfile.TarInfo('catalog.json');info.size=len(payload)
 archive.addfile(info,io.BytesIO(payload))
 for name in sorted({record[field] for record in manifest['catalogs'].values() for field in ('entries','index')}):
  assert Path(name).name==name and not (root/name).is_symlink()
  archive.add(root/name,arcname=name,recursive=False)
'''


def capture_snapshot(host, destination, ssh_options=()):
    if not re.fullmatch(r'[A-Za-z0-9_.@:\[\]-]+', host) or host.startswith('-'):
        raise ClientError('Use an SSH host or user@host from your existing SSH setup.')
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ClientError('Snapshot directory already exists. Choose a new --snapshot directory to preserve it.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = (['sshpass', '-e'] if os.environ.get('SSHPASS') else []) + ['ssh', '-T', '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2']
    for option in ssh_options:
        command += ['-o', option]
    command += [host, 'python3', '-']
    with tempfile.TemporaryDirectory(dir=str(destination.parent)) as temporary:
        root = Path(temporary)
        bundle = root / 'snapshot.tgz'
        with bundle.open('wb') as output:
            subprocess.run(command, input=CAPTURE_SCRIPT.encode(), stdout=output, check=True, timeout=120)
        with tarfile.open(bundle) as archive:
            members = archive.getmembers()
            if len(members) > 200 or sum(member.size for member in members) > 256*1024*1024:
                raise ClientError('Catalog snapshot exceeds preview limits.')
            for member in members:
                if not member.isfile() or not (member.name == 'catalog.json' or re.fullmatch(r'[0-9a-f]{64}\.[0-9a-f]{32}\.(jsonl|index\.json)', member.name)):
                    raise ClientError('Unexpected file in catalog snapshot.')
            extracted = root / 'cache'
            extracted.mkdir()
            for member in members:
                with archive.extractfile(member) as source, (extracted/member.name).open('wb') as output:
                    shutil.copyfileobj(source, output)
        if not (extracted/'catalog.json').is_file():
            raise ClientError('Snapshot is missing its catalog manifest.')
        if destination.exists():
            destination.rmdir()
        os.replace(extracted, destination)
    print('Captured catalog metadata only:', destination)


def sync_snapshot(config_path, destination):
    from mister.library import SyncedLibrary
    config = load_config(config_path)
    root = Path(destination)
    cache = PreviewCatalogCache(root/'catalog.json', library=SyncedLibrary(root/'metadata'))
    result = cache.sync(Api(config['backend_url'], config['device_token'], timeout=45),
        observer=lambda event: print('Caching %s: %s/%s' % (event['filename'], event['downloaded'], event['total']), flush=True))
    print('Preview snapshot:', result)


def make_handler(session, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def send(self, status, value, kind='application/json'):
            body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind+'; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        def local_host(self):
            return urlsplit('http://' + self.headers.get('Host', '')).hostname in ('127.0.0.1', 'localhost')
        def do_GET(self):
            if not self.local_host():
                self.send(403, {'error':'Local preview host required'})
                return
            if self.path == '/':
                page = (HERE/'preview.html').read_text().replace('__PREVIEW_TOKEN__', token)
                self.send(200, page, 'text/html')
            elif self.path == '/screen':
                self.send(200, session.snapshot_screen())
            else:
                self.send(404, {'error':'Not found'})
        def do_POST(self):
            origin = self.headers.get('Origin')
            if not self.local_host() or self.headers.get('X-Preview-Token') != token or (origin and urlsplit(origin).netloc != self.headers.get('Host')):
                self.send(403, {'error':'Local preview request required'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length < 4096:
                    raise ValueError('Invalid input length')
                data = json.loads(self.rfile.read(length))
                if self.path == '/input':
                    session.input(KEYS.get(data.get('key'), data.get('text', '')))
                elif self.path == '/restart':
                    session.restart(data.get('mode', 'sofa'))
                else:
                    self.send(404, {'error':'Not found'})
                    return
                self.send(200, {'ok':True})
            except (ValueError, KeyError, TypeError):
                self.send(400, {'error':'Invalid preview input'})
    return Handler


def find_dialog(supplied=None):
    matching = HERE/'.preview-tools/dialog-1.3-20200327/dialog'
    executable = supplied or (str(matching) if matching.is_file() else shutil.which('dialog'))
    if not executable:
        paths = sorted((HERE/'.preview-tools').glob('dialog-*/dialog'))
        executable = str(paths[-1]) if paths else None
    if not executable or not os.access(executable, os.X_OK):
        raise ClientError('dialog is required on the development host. Run bash mister/setup_preview.sh, or pass --dialog /path/to/dialog.')
    return str(Path(executable).resolve())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument('--dialog')
    parser.add_argument('--mode', choices=GEOMETRY, default='sofa')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--capture', metavar='USER@HOST', help='Copy cached catalog metadata over existing SSH access; no device writes')
    parser.add_argument('--ssh-option', action='append', default=[])
    parser.add_argument('--sync-catalog', action='store_true', help='Sync a preview snapshot through normal read-only backend client APIs')
    parser.add_argument('--config', type=Path, default=HERE/'config.json')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.capture:
            capture_snapshot(args.capture, args.snapshot, args.ssh_option)
            return 0
        if args.sync_catalog:
            sync_snapshot(args.config, args.snapshot)
            return 0
        if not (args.snapshot/'catalog.json').is_file():
            raise ClientError('No local catalog snapshot. Use --capture user@mister or --sync-catalog --config mister/config.json first.')
        executable = find_dialog(args.dialog)
        if args.child:
            return run_child(args.snapshot, executable, args.mode)
        session = PreviewSession(args.snapshot, executable, args.mode)
        try:
            server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(session, secrets.token_hex(24)))
            print('MiSTer UI preview: http://127.0.0.1:%d' % server.server_port, flush=True)
            print('Actual dialog UI. Catalog snapshot only; downloads simulated. No deployment.', flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
        finally:
            session.close()
    except KeyboardInterrupt:
        return 0
    except (ClientError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc) if isinstance(exc, ClientError) else 'Preview operation failed; check the local snapshot, dialog installation, and connection.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
