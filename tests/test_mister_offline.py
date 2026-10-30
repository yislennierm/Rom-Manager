"""Offline catalogs and local downloads must never depend on a live backend."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from core.mister import MisterStore
from core.mister_sources import SNES_GUID, NES_GUID, prepare_rom
from mister.demo import demo_catalog
from mister.offline_client import OfflineRuntime, LocalClient
from mister.roms_manager_client import BackendUnavailable, AccessDenied, ClientError
from mister.roms_manager_ui import MisterUI
from test_mister import RomServer, ROM
from test_mister_ui import FakeDialog
from http.server import ThreadingHTTPServer
import threading


class SyncApi:
    def __init__(self, base):
        self.entries = [dict(demo_catalog(base)['entries'][0], name='Demo %03d' % i) for i in range(503)]
        self.allowed = [SNES_GUID, NES_GUID]
        self.failure = None
        self.calls = []
        self.version = 'first'
        self.fail_page = False

    def get(self, path):
        self.calls.append(path)
        if self.failure:
            raise self.failure('test backend outage')
        url = urlsplit(path)
        if url.path == '/client/sync':
            return {'user': {'id': 'mister', 'name': 'MiSTer', 'enabled': True, 'admin': False,
                             'allowed_console_guids': self.allowed}, 'modules': []}
        if url.path == '/roms':
            return {'roms': [{'slug': 'snes', 'guid': SNES_GUID, 'console': 'SNES', 'entry_count': len(self.entries), 'fetched_at': self.version}]}
        if url.path == '/roms/snes':
            offset = int(parse_qs(url.query)['offset'][0])
            if offset and self.fail_page:
                raise BackendUnavailable('interrupted refresh')
            entries = self.entries[offset:offset+500]
            return {'entries': entries, 'total': len(self.entries), 'has_more': offset + len(entries) < len(self.entries)}
        if url.path == '/client/downloads':
            return {'jobs': [], 'has_more': False}
        raise AssertionError(path)


class WebQueueApi(SyncApi):
    def __init__(self, base, store):
        super().__init__(base)
        self.store = store
        self.published = 0

    def get(self, path):
        if self.failure:
            raise self.failure('test backend outage')
        if path.startswith('/client/downloads?'):
            return self.store.list_jobs(limit=100)
        if '/recipe?' in path:
            return {'plan': self.store.get_plan(path.split('/')[3])}
        return super().get(path)

    def request(self, method, path, body=None):
        if self.failure:
            raise self.failure('test backend outage')
        if '/claim?' in path:
            return {'job': self.store.claim(job_id=path.split('/')[3])}
        if path == '/client/downloads':
            self.published += 1
            return {'job': self.store.enqueue(prepare_rom(self.entries[0], SNES_GUID))}
        raise AssertionError(path)

    def post(self, path, body):
        if self.failure:
            raise self.failure('test backend outage')
        return {'job': self.store.report(path.split('/')[1], body['lease'], body['state'], body['downloaded'])}


class OfflineCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), RomServer)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'SNES').mkdir()
        self.config = {'backend_url': 'http://backend.invalid', 'device_token': 'normal-client-key',
                       'games_roots': {'snes': str(root / 'SNES')}, 'lock_file': str(root / 'lock')}
        self.path = root / 'config.json'
        self.api = SyncApi(self.base)
        self.runtime = OfflineRuntime(self.config, self.path, api=self.api)
        self.addCleanup(lambda: self.runtime.close())
        RomServer.requests.clear()

    def enqueue(self):
        entry = self.runtime.cache.page('snes')['entries'][0]
        return self.runtime.local_api.request('POST', '/client/downloads', {'dataset': 'snes', 'entry_key': entry['mister']['entry_key']})['job']

    def test_cached_pagination_search_and_download_after_backend_outage_and_restart(self):
        self.runtime.refresh()
        self.assertEqual(self.runtime.cache.page('snes', limit=5, offset=500)['total'], 503)
        self.assertEqual(self.runtime.cache.page('snes', query='Demo 501')['entries'][0]['name'], 'Demo 501')
        self.runtime.close()
        self.api.failure = BackendUnavailable
        self.runtime = OfflineRuntime(self.config, self.path, api=self.api)
        with self.assertRaises(BackendUnavailable):
            self.runtime.refresh()
        calls = len(self.api.calls)
        job = self.enqueue()
        self.assertEqual(self.enqueue()['id'], job['id'])
        self.assertEqual(LocalClient(self.runtime, allow_test_storage=True).sync(), 0)
        target = Path(self.config['games_roots']['snes']) / job['destination']
        self.assertEqual(target.read_bytes(), ROM)
        self.assertEqual(self.runtime.store.get_job(job['id'])['state'], 'completed')
        self.assertEqual(len(self.api.calls), calls, 'local browsing and transfer must not call backend')
        self.assertFalse(any('Authorization' in headers for headers in RomServer.requests))
        # Delete installed game and restore entirely from verified local bytes.
        target.unlink()
        self.enqueue()
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('No network permitted')):
            self.assertEqual(LocalClient(self.runtime, allow_test_storage=True).sync(), 0)
        self.assertEqual(target.read_bytes(), ROM)

    def test_local_completion_publishes_once_after_reconnect_and_restart(self):
        server = MisterStore(Path(self.tmp.name) / 'server.sqlite3')
        server.bind_account('mister', 'MiSTer')
        api = WebQueueApi(self.base, server)
        self.runtime.api = api
        self.runtime.refresh()
        job = self.enqueue()
        api.failure = BackendUnavailable
        self.assertEqual(LocalClient(self.runtime, allow_test_storage=True).sync(), 0)
        self.assertFalse(server.snapshot()['jobs'])
        api.failure = None
        self.runtime.refresh()
        self.assertEqual(server.snapshot()['jobs'][0]['state'], 'completed')
        self.runtime.refresh()
        self.runtime.close()
        self.runtime = OfflineRuntime(self.config, self.path, api=api)
        self.runtime.refresh()
        self.assertEqual(api.published, 1)
        self.assertEqual(self.runtime.store.get_job(job['id'])['state'], 'completed')

    def test_imported_web_job_downloads_offline_and_reconciles_on_reconnect(self):
        server = MisterStore(Path(self.tmp.name) / 'server.sqlite3')
        server.bind_account('mister', 'MiSTer')
        api = WebQueueApi(self.base, server)
        queued = server.enqueue(prepare_rom(api.entries[0], SNES_GUID))
        self.runtime.api = api
        self.runtime.refresh()
        self.assertEqual(len(self.runtime.cache.links()), 1)
        api.failure = BackendUnavailable
        self.assertEqual(LocalClient(self.runtime, allow_test_storage=True).sync(), 0)
        self.assertEqual(server.get_job(queued['id'])['state'], 'queued')
        api.failure = None
        self.runtime.refresh()
        self.assertEqual(server.get_job(queued['id'])['state'], 'completed')
        self.assertEqual(self.runtime.cache.links(), [])

    def test_interrupted_refresh_retains_last_complete_snapshot(self):
        self.runtime.refresh()
        self.api.entries[0] = {**self.api.entries[0], 'name': 'Changed game'}
        self.api.version = 'second'
        self.api.fail_page = True
        with self.assertRaises(BackendUnavailable):
            self.runtime.refresh()
        self.assertEqual(self.runtime.cache.page('snes')['total'], 503)
        self.assertFalse(self.runtime.cache.page('snes', query='Changed game')['entries'])

    def test_initial_catalog_refresh_resumes_checkpoint_after_outage(self):
        self.api.fail_page = True
        with self.assertRaises(BackendUnavailable):
            self.runtime.refresh()
        self.assertEqual(self.runtime.cache.page('snes')['total'], 500)
        self.assertFalse(self.runtime.cache.catalogs()[0]['cache_complete'])
        self.api.calls.clear()
        self.api.fail_page = False
        self.runtime.refresh()
        self.assertFalse(any('/roms/snes?' in path and 'offset=0&' in path for path in self.api.calls))
        self.assertEqual(self.runtime.cache.page('snes')['total'], 503)
        self.assertTrue(self.runtime.cache.catalogs()[0]['cache_complete'])

    def test_revoked_assignments_and_known_denial_block_new_downloads(self):
        self.runtime.refresh()
        self.api.failure = AccessDenied
        with self.assertRaises(AccessDenied):
            self.runtime.refresh()
        with self.assertRaises(AccessDenied):
            self.enqueue()
        self.api.failure = None
        self.api.allowed = [NES_GUID]
        self.runtime.refresh()
        self.assertFalse(self.runtime.cache.catalogs())
        with self.assertRaises(ClientError):
            self.runtime.cache.entry('snes', 'a' * 64)

    def test_no_cache_outage_keeps_menu_usable_and_explains_refresh(self):
        self.api.failure = BackendUnavailable
        with self.assertRaises(BackendUnavailable):
            self.runtime.refresh()
        dialog = FakeDialog(menus=['1', '6'])
        dialog.mode = 'desktop'
        ui = MisterUI(self.config, dialog, api=self.runtime.local_api, runtime=self.runtime)
        self.assertEqual(ui.run(), 0)
        self.assertIn('cached yet', dialog.messages[0][1])

    def test_changed_key_cannot_reuse_previous_account_cache(self):
        self.runtime.refresh()
        other = OfflineRuntime({**self.config, 'device_token': 'different-account'}, self.path, api=self.api)
        self.addCleanup(other.close)
        with self.assertRaises(ClientError):
            other.cache.user()
        self.assertNotEqual(other.root, self.runtime.root)

    def test_unsupported_entries_are_cached_but_never_enqueued(self):
        self.api.entries = [{'name': 'Unsupported', 'http_url': 'https://provider.invalid/game.torrent'}]
        self.runtime.refresh()
        page = self.runtime.cache.page('snes')
        self.assertFalse(page['entries'][0]['mister']['supported'])
        self.assertFalse(self.runtime.cache.page('snes', available_only=True)['entries'])
        with self.assertRaises(ClientError):
            self.enqueue()

    def test_crashed_local_transfer_recovers_without_waiting_for_backend_lease(self):
        self.runtime.refresh()
        queued = self.enqueue()
        old = self.runtime.local_api.post('claim')['job']
        self.assertEqual(old['id'], queued['id'])
        self.api.failure = BackendUnavailable
        self.assertEqual(LocalClient(self.runtime, allow_test_storage=True).sync(), 0)
        self.assertEqual(self.runtime.store.get_job(queued['id'])['state'], 'completed')
