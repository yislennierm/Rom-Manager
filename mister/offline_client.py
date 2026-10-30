"""Local catalog, queue, provider downloads, and MiSTer-only installation adapter."""
import hashlib
import json
import os
import shutil
from functools import wraps
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from core.client_downloads import DownloadError
from core.mister_sources import SYSTEM_GUIDS, prepare_rom
from core.download_preparation import SourcePreparer
if __package__:
    from .catalog_cache import CatalogCache, cache_identity
    from .local_state import LocalStore
    from .library import SyncedLibrary
    from .roms_manager_client import Api, AccessDenied, BackendUnavailable, Client, ClientError, checksum_file, destination_path, discover_games_root, matches
else:
    from catalog_cache import CatalogCache, cache_identity
    from local_state import LocalStore
    from library import SyncedLibrary
    from roms_manager_client import Api, AccessDenied, BackendUnavailable, Client, ClientError, checksum_file, destination_path, discover_games_root, matches


def local_errors(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except DownloadError as exc:
            raise ClientError(str(exc)) from None
    return wrapped


class CachedResponse:
    def __init__(self, path, headers, etag):
        self.file = path.open('rb')
        size = path.stat().st_size
        offset = 0
        if headers.get('If-Range') == etag and headers.get('Range', '').startswith('bytes='):
            offset = int(headers['Range'][6:].split('-', 1)[0])
            if not 0 < offset < size:
                offset = 0
        self.status = 206 if offset else 200
        self.headers = {'Content-Length': str(size - offset), 'ETag': etag}
        if offset:
            self.headers['Content-Range'] = 'bytes %d-%d/%d' % (offset, size - 1, size)
        self.file.seek(offset)

    def read(self, size):
        return self.file.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.file.close()


class LocalApi:
    def __init__(self, runtime):
        self.runtime = runtime
        self.cache, self.store = runtime.cache, runtime.store

    def systems(self):
        return {system for system, guid in SYSTEM_GUIDS.items() if self.cache.can_use(guid)}

    def library(self, slug, query='', available_only=False):
        return self.cache.title_list(slug, query, available_only)

    def game_entry(self, slug, key):
        return self.cache.entry(slug, key)[1]

    def get(self, path):
        url = urlsplit(path)
        query = parse_qs(url.query)
        if url.path == '/me':
            return {'user': self.cache.user()}
        if url.path == '/client/downloads/targets':
            return {'targets': [{'id': 'mister', 'system_guids': {system: SYSTEM_GUIDS[system] for system in self.systems()}}]}
        if url.path == '/roms':
            return {'roms': self.cache.catalogs()}
        if url.path.startswith('/roms/'):
            return self.cache.page(unquote(url.path[6:]), limit=int(query.get('limit', ['5'])[0]),
                offset=int(query.get('offset', ['0'])[0]), query=query.get('q', [''])[0], available_only='download_target' in query)
        if url.path == '/client/downloads':
            return self.store.list_jobs(limit=int(query.get('limit', ['5'])[0]), offset=int(query.get('offset', ['0'])[0]), systems=self.systems())
        raise ClientError('Unsupported local catalog operation')

    @local_errors
    def request(self, method, path, body=None):
        if method == 'GET':
            return self.get(path)
        self.cache.require_download()
        url = urlsplit(path)
        if method == 'POST' and url.path == '/client/downloads':
            guid, entry = self.cache.entry(body['dataset'], body['entry_key'])
            plan = prepare_rom(entry, guid)
            self.cache.require_download(plan['system'])
            plan['catalog'] = {'dataset': body['dataset'], 'entry_key': body['entry_key']}
            job = self.store.enqueue(plan)
            if job['state'] == 'completed':
                roots = self.runtime.config.get('games_roots') or {}
                configured = roots.get(job['system']) or (self.runtime.config.get('games_root') if job['system'] == 'snes' else None)
                root = Path(configured) if configured else discover_games_root(job['system'])
                path = destination_path(root, job['destination'], job['system'])
                if not path.is_file() or not matches(path, job):
                    self.store.requeue_missing(job['id'])
                    job = self.store.get_job(job['id'])
            return {'job': job}
        if method == 'POST' and url.path.startswith('/client/downloads/') and url.path.endswith('/retry'):
            job_id = url.path.split('/')[3]
            job = self.store.get_job(job_id)
            if not job:
                raise ClientError('Local download job not found')
            self.cache.require_download(job['system'])
            self.runtime.preparer.retry(self.store.get_plan(job_id))
            self.store.retry(job_id)
            return {'queued': True}
        raise ClientError('Unsupported local queue operation')

    @local_errors
    def post(self, path, body=None):
        self.cache.require_download()
        if path == 'heartbeat':
            # Called after acquiring the shared transfer lock. Any prior active
            # local lease belonged to an interrupted invocation, so resume now.
            self.store.recover()
            self.store.heartbeat(body)
            return {'ok': True}
        if path == 'claim':
            job = self.store.claim(self.systems())
            if job:
                job.update(self.store.get_plan(job['id']))
                job.pop('download_path', None)
                job.pop('preparation_path', None)
                if job.get('_preparation'):
                    job['local_preparation'] = True
                    job['source_identity'] = 'local:' + self.runtime.preparer.key(job)
            return {'job': job}
        if path.startswith('jobs/'):
            job_id = path.split('/')[1]
            job = self.store.get_job(job_id)
            if not job:
                raise ClientError('Local job not found')
            self.cache.require_download(job['system'])
            reported = self.store.report(job_id, body['lease'], body['state'], body['downloaded'], body.get('error'))
            self.runtime.wake.set()
            return {'job': reported}
        raise ClientError('Unsupported local transfer operation')


class LocalClient(Client):
    def __init__(self, runtime, observer=None, allow_test_storage=False):
        self.runtime = runtime
        super().__init__(runtime.config, api=runtime.local_api, observer=observer, allow_test_storage=allow_test_storage)

    def validate_source(self, job):
        if not job.get('_preparation'):
            super().validate_source(job)

    @local_errors
    def prepare_job(self, job):
        if not job.get('_preparation'):
            return
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            self.runtime.cache.require_download(job['system'])
            self.check_storage()
            status = self.runtime.preparer.status(job)
            if status['state'] == 'ready':
                if status.get('content'):
                    self.runtime.store.materialize(job['id'], status['content'])
                    job.update(self.runtime.store.get_plan(job['id']))
                job['_local_content'] = status['path']
                job['_local_etag'] = status['etag']
                return
            if status['state'] == 'failed':
                raise ClientError(status['error'])
            self.report(job, 'preparing', 0)
            self.event('progress', system=job['system'], state='preparing locally', filename=job['destination'],
                       downloaded=status.get('downloaded', 0), total=status.get('total', 0))
            time.sleep(.5)
        raise ClientError('Provider preparation timed out; retry to reuse cached data')

    def direct_cache(self, job):
        identity = json.dumps({key: job.get(key) for key in ('source_url', 'size', 'checksum')}, sort_keys=True)
        directory = self.runtime.root / 'downloads' / 'direct'
        directory.mkdir(exist_ok=True, parents=True)
        return directory / (hashlib.sha256(identity.encode()).hexdigest() + '.rom')

    def open_source(self, job, headers, offset):
        if job.get('_preparation'):
            self.prepare_job(job)
            return CachedResponse(job['_local_content'], headers, job['_local_etag'])
        cached = self.direct_cache(job)
        if not cached.is_symlink() and cached.is_file() and matches(cached, job):
            digest = checksum_file(cached, {'algorithm': 'sha256'})
            return CachedResponse(cached, headers, '"' + digest + '"')
        return super().open_source(job, headers, offset)

    @local_errors
    def install(self, job):
        self.runtime.cache.require_download(job['system'])
        super().install(job)
        if not job.get('_preparation'):
            temporary = None
            try:
                cached = self.direct_cache(job)
                if cached.is_symlink():
                    raise OSError('Unsafe cache path')
                if not cached.is_file() or not matches(cached, job):
                    source = destination_path(self.root, job['destination'], job['system'])
                    with tempfile.NamedTemporaryFile(dir=str(cached.parent), delete=False) as output:
                        temporary = Path(output.name)
                        with source.open('rb') as content:
                            shutil.copyfileobj(content, output, 65536)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, cached)
            except OSError:
                self.message('Installed successfully; download cache copy could not be saved.')
            finally:
                if temporary:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass



class OfflineRuntime:
    def __init__(self, config, config_path, api=None):
        self.config = config
        base = Path(config.get('cache_root') or Path(config_path).with_name('cache'))
        self.root = base / cache_identity(config)
        self.root.mkdir(parents=True, exist_ok=True)
        self.library = SyncedLibrary(self.root / 'metadata')
        self.cache = CatalogCache(self.root / 'catalog.json', library=self.library)
        self.store = LocalStore(self.root / 'queue.json')
        if not self.store.bound_account_id():
            self.store.bind_account(cache_identity(config), 'Local MiSTer downloads')
        self.api = api or Api(config['backend_url'], config['device_token'], timeout=45)
        self.preparer = SourcePreparer(self.root / 'downloads', network_timeout=8)
        self.local_api = LocalApi(self)
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.web_leases = {}
        self.web_states = {}

    def client_factory(self, config=None, api=None, observer=None):
        return LocalClient(self, observer=observer)

    def refresh(self, observer=None):
        result = self.cache.sync(self.api, observer=observer, stop=self.stop)
        if not result.get('busy') and not result.get('cancelled'):
            try:
                self.export_local_jobs()
                self.import_web_queue()
                self.report_web_progress()
                self.report_web_receipts()
            except BackendUnavailable:
                self.cache.set('network', 'offline — using cached data')
            except AccessDenied:
                self.cache.set('network', 'online (web queue unavailable)')
            except ClientError:
                pass  # Server queue reconciliation never gates local downloads.
        return result

    def export_local_jobs(self):
        # Publication is optional: provider transfer never waits for this call.
        published = self.cache.get('published_jobs', {})
        for local in self.store.snapshot()['jobs']:
            if local['id'] in published:
                continue
            catalog = (self.store.get_plan(local['id']) or {}).get('catalog')
            if not catalog or not self.cache.can_use(SYSTEM_GUIDS[local['system']]):
                continue
            server = self.api.request('POST', '/client/downloads', {'target': 'mister', **catalog})['job']
            published[local['id']] = server['id']
            self.cache.set('published_jobs', published)
            self.web_states[server['id']] = server['state']
            if server['state'] != 'completed':
                self.cache.link(server['id'], local['id'])

    def import_web_queue(self):
        imported, offset = 0, 0
        while True:
            page = self.api.get('/client/downloads?target=mister&limit=100&offset=%d' % offset)
            for job in page.get('jobs', []):
                self.web_states[job['id']] = job['state']
                if job['state'] == 'completed':
                    self.cache.link(job['id'])
                if job['state'] not in ('queued', 'preparing', 'downloading', 'verifying'):
                    continue
                self.cache.require_download(job['system'])
                plan = self.api.get('/client/downloads/%s/recipe?target=mister' % job['id'])['plan']
                local = self.store.enqueue(plan)
                self.cache.link(job['id'], local['id'])
                imported += 1
            if not page.get('has_more'):
                break
            offset += 100
        return imported

    def web_claim(self, server_id):
        job = self.web_leases.get(server_id)
        if job and job.get('_receipt_until', 0) <= time.monotonic():
            self.web_leases.pop(server_id, None)
            job = None
        if not job:
            if self.web_states.get(server_id) == 'failed':
                self.api.request('POST', '/client/downloads/%s/retry?target=mister' % server_id)
            job = self.api.request('POST', '/client/downloads/%s/claim?target=mister' % server_id).get('job')
            if job:
                job['_receipt_until'] = time.monotonic() + job.get('lease_seconds', 120) - 5
                self.web_leases[server_id] = job
        return job

    def web_metadata(self, server_id, job, local):
        if not job.get('size') and local.get('checksum', {}).get('algorithm') == 'sha256':
            body = {'lease': job['lease'], 'filename': local['destination'].rsplit('/', 1)[-1],
                    'size': local['size'], 'sha256': local['checksum']['value']}
            prepared = self.api.request('POST', '/client/downloads/%s/prepared?target=mister' % server_id, body)['job']
            job.update(prepared)

    def report_web_progress(self):
        links = self.cache.links()
        for link in links:
            local = self.store.get_job(link['local_id'])
            if not local or local['state'] not in ('preparing', 'downloading', 'verifying', 'failed'):
                continue
            self.cache.require_download(local['system'])
            server_id = link['server_id']
            job = self.web_claim(server_id)
            if not job:
                continue
            self.web_metadata(server_id, job, local)
            phase = local['state'] if local['state'] != 'verifying' else 'downloading'
            downloaded = min(local['downloaded'], job['size'])
            self.api.post('jobs/%s/progress' % server_id, {'lease': job['lease'], 'state': phase,
                          'downloaded': downloaded, 'error': local.get('error')})

    def report_web_receipts(self):
        links = self.cache.links()
        for link in links:
            local = self.store.get_job(link['local_id'])
            if not local or local['state'] != 'completed':
                continue
            self.cache.require_download(local['system'])
            server_id = link['server_id']
            job = self.web_claim(server_id)
            if not job:
                continue
            self.web_metadata(server_id, job, local)
            if job['size'] != local['size'] or job['checksum'] != local['checksum']:
                # Different provider digests can still describe identical bytes.
                client = LocalClient(self)
                client.select_system(local['system'])
                target = destination_path(client.root, local['destination'], local['system'])
                if not matches(target, job):
                    continue
            for phase in ('verifying', 'completed'):
                self.api.post('jobs/%s/progress' % server_id, {'lease': job['lease'], 'state': phase, 'downloaded': job['size']})
            self.cache.link(server_id)
            self.web_leases.pop(server_id, None)

    def start_refresh(self):
        if self.thread and self.thread.is_alive():
            return
        def worker():
            next_refresh = 0
            while not self.stop.is_set():
                try:
                    if time.monotonic() >= next_refresh:
                        next_refresh = time.monotonic() + 30
                        self.refresh()
                    else:
                        self.export_local_jobs()
                        self.report_web_progress()
                        self.report_web_receipts()
                except (ClientError, OSError, ValueError) as exc:
                    self.cache.set('last_sync_error', type(exc).__name__)
                    if not isinstance(exc, (BackendUnavailable, AccessDenied)):
                        self.cache.set('network', 'sync interrupted — cached data available')
                self.wake.wait(2 if self.cache.get('network') == 'online' else 30)
                self.wake.clear()
        self.thread = threading.Thread(target=worker, name='catalog-sync', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.preparer.close()
