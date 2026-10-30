"""Real ZIP/HTTP preparation, ownership checks, and client install integration."""
import hashlib
import io
import json
import os
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.client_downloads import DownloadTarget, make_client_download_router
from backend.app.mister_api import make_router
from core.download_preparation import SourcePreparer, safe_member, SEVEN_ZIP
from core.client_downloads import DownloadError
from core.mister import MisterStore, NES_GUID, SNES_GUID, SYSTEM_GUIDS, entry_key, prepare_rom
from mister.demo import build_nes_demo_rom, build_demo_rom
from mister.roms_manager_client import Api, Client, ClientError

ROM = build_nes_demo_rom()
MEMBER = 'roms/Original NES demo.nes'


def zip_bytes(member=MEMBER, payload=ROM, symlink=False, second=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(member)
        if symlink:
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, payload)
        if second:
            archive.writestr(second, payload)
    return buffer.getvalue()


class Upstream(BaseHTTPRequestHandler):
    payload = zip_bytes()
    status = 200
    requests = []

    def do_GET(self):
        self.requests.append(dict(self.headers))
        if self.path.startswith('/client/downloads/redirect/'):
            self.send_response(302)
            self.send_header('Location', '/credential-trap')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self.send_response(self.status)
        self.send_header('Content-Length', str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *_):
        pass


class PreparationCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        cls.upstream_thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.upstream_thread.start()
        cls.url = 'http://127.0.0.1:%d/fixture.zip' % cls.upstream.server_port

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.upstream_thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.preparer = SourcePreparer(self.root / 'prepared')
        self.addCleanup(self.preparer.close)
        self.store = MisterStore(self.root / 'jobs.sqlite3')
        self.store.bind_account('mister-fpga', 'MiSTer')
        Upstream.requests.clear()
        Upstream.payload, Upstream.status = zip_bytes(), 200
        self.entry = {'name': 'Original NES demo', 'http_url': self.url, '_archive_member': True,
                      '_archive_member_path': MEMBER, '_source_bundle_size': len(Upstream.payload),
                      'size': len(ROM), 'sha256': hashlib.sha256(ROM).hexdigest()}
        self.plan = prepare_rom(self.entry, NES_GUID)
        with patch.dict(os.environ, {'ROMS_MANAGER_MISTER_DB': str(self.root / 'import.sqlite3'),
                                     'ROMS_MANAGER_PREPARATION_CACHE': str(self.root / 'import-prepared')}):
            from backend.app import main
        self.main = main
        self.token = 'ordinary-client-key-not-for-provider'
        self.device = {'Authorization': 'Bearer ' + self.token}
        self.admin = {'Authorization': 'Bearer admin-key'}
        users = self.root / 'users.json'
        users.write_text(json.dumps({'users': [
            {'id': 'mister-fpga', 'enabled': True, 'admin': False, 'allowed_console_guids': [NES_GUID, SNES_GUID], 'api_key_hash': main._hash_api_key(self.token)},
            {'id': 'other', 'enabled': True, 'admin': False, 'allowed_console_guids': [NES_GUID], 'api_key_hash': main._hash_api_key('other-key')},
            {'id': 'admin', 'enabled': True, 'admin': True, 'api_key_hash': main._hash_api_key('admin-key')},
        ]}))
        self.users = users
        self.datasets = {'zip-demo': {'guid': NES_GUID, 'entries': [self.entry]}}
        self.targets = {'mister': DownloadTarget(self.store, SYSTEM_GUIDS, prepare_rom, entry_key, self.preparer)}
        self.app = FastAPI()
        self.app.include_router(make_router(self.store, main.require_admin, self.datasets.get, main.current_user,
            main._mister_account, main._provision_mister_account, main._disable_mister_account, self.preparer))
        self.app.include_router(make_client_download_router(main.current_user, main._guid_allowed, self.datasets.get, self.targets))
        self.app.add_api_route('/roms/{identifier}', main.fetch_rom_dataset, methods=['GET'])
        for name, value in (('USERS_FILE', users), ('CLIENT_DOWNLOAD_TARGETS', self.targets), ('_load_rom_dataset', self.datasets.get)):
            context = patch.object(main, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.http = TestClient(self.app)
        self.addCleanup(self.http.close)

    def ready(self, plan=None):
        plan = plan or self.plan
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.preparer.status(plan)
            if status['state'] != 'preparing':
                return status
            time.sleep(.01)
        self.fail('Preparation did not finish')

    def enqueue(self):
        response = self.http.post('/client/downloads', headers=self.device,
            json={'target': 'mister', 'dataset': 'zip-demo', 'entry_key': entry_key(self.entry)})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['job']

    def dynamic_plan(self, data, title='Original NES demo', suffix='zip', guid=NES_GUID):
        Upstream.payload = data
        source = {'name': 'fixture.' + suffix, 'http_url': self.url.rsplit('.', 1)[0] + '.' + suffix,
                  'size': len(data), 'sha1': hashlib.sha1(data).hexdigest()}
        entry = {'name': title, '_providers': [{'rom': source}]}
        self.datasets['archive-demo'] = {'guid': guid, 'entries': [entry]}
        return prepare_rom(entry, guid), entry

    def test_single_rom_archive_is_verified_before_computing_rom_metadata(self):
        plan, _ = self.dynamic_plan(zip_bytes())
        self.assertEqual(plan['size'], 0)
        self.assertIsNone(plan['checksum'])
        status = self.ready(plan)
        self.assertEqual(status['state'], 'ready', status)
        self.assertEqual(status['content']['size'], len(ROM))
        self.assertEqual(status['content']['checksum']['value'], hashlib.sha256(ROM).hexdigest())
        self.assertEqual(status['content']['filename'], MEMBER.rsplit('/', 1)[-1])

    def test_ambiguous_archive_is_rejected_until_an_exact_title_can_be_selected(self):
        plan, _ = self.dynamic_plan(zip_bytes(second='roms/Second.nes'), title='Unmatched game')
        self.assertEqual(self.ready(plan)['state'], 'failed')
        exact, _ = self.dynamic_plan(Upstream.payload, title='Second')
        self.assertEqual(self.ready(exact)['state'], 'ready')
        self.assertEqual(self.ready(exact)['content']['filename'], 'Second.nes')

    def test_catalog_region_matching_selects_verified_dump_and_avoids_hacks(self):
        from utils.catalog import select_archive_rom_name
        names = ['Game (U) [h1C].smc', 'Game (U) [t1].smc', 'Game (J) [!].smc', 'Game (U) [!].smc']
        self.assertEqual(select_archive_rom_name('Game (USA)', names), 'Game (U) [!].smc')
        self.assertEqual(select_archive_rom_name('Game (Japan)', names), 'Game (J) [!].smc')
        self.assertIsNone(select_archive_rom_name('Game (Europe)', names))

    def test_archive_checksum_cannot_be_mistaken_for_rom_checksum(self):
        plan, entry = self.dynamic_plan(zip_bytes())
        entry['_providers'][0]['rom']['sha1'] = '0' * 40
        bad = prepare_rom(entry, NES_GUID)
        self.assertEqual(self.ready(bad)['state'], 'failed')
        self.assertFalse(list(self.preparer.root.glob('*.rom')))

    def test_materialized_archive_job_survives_restart_and_deduplicates(self):
        plan, entry = self.dynamic_plan(zip_bytes())
        job = self.store.enqueue(plan)
        status = self.ready(plan)
        path = '/client/downloads/%s/preparation?target=mister' % job['id']
        response = self.http.get(path, headers=self.device)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['job']['size'], len(ROM))
        self.assertEqual(self.store.get_job(job['id'])['checksum'], status['content']['checksum'])
        self.assertEqual(self.store.enqueue(plan)['id'], job['id'])
        restarted = MisterStore(self.store.path)
        self.assertEqual(restarted.get_job(job['id'])['size'], len(ROM))
        with self.assertRaises(DownloadError):
            self.store.materialize(job['id'], {**status['content'], 'size': len(ROM) + 1})

    @unittest.skipUnless(SEVEN_ZIP, 'Backend 7z is not installed')
    def test_7z_archive_prepares_only_the_selected_snes_rom(self):
        folder = self.root / '7z-fixture'
        folder.mkdir()
        payload = build_demo_rom()
        (folder / 'Original SNES demo.sfc').write_bytes(payload)
        archive = folder / 'fixture.7z'
        subprocess.run([SEVEN_ZIP, 'a', '-t7z', str(archive), 'Original SNES demo.sfc'], cwd=folder,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        plan, _ = self.dynamic_plan(archive.read_bytes(), title='Original SNES demo', suffix='7z', guid=SNES_GUID)
        status = self.ready(plan)
        self.assertEqual(status['state'], 'ready', status)
        self.assertEqual(status['path'].read_bytes(), payload)
        self.assertEqual(status['content']['checksum']['value'], hashlib.sha256(payload).hexdigest())

    def test_prepare_exact_member_verify_both_checksums_and_reuse_after_restart(self):
        status = self.ready()
        self.assertEqual(status['state'], 'ready', status)
        self.assertEqual(status['path'].read_bytes(), ROM)
        self.assertEqual(len(Upstream.requests), 1)
        self.preparer.close()
        restarted = SourcePreparer(self.preparer.root)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.status(self.plan)['state'], 'ready')
        self.assertEqual(len(Upstream.requests), 1)
        self.assertNotIn('Authorization', Upstream.requests[0])

    def test_archive_cache_is_shared_across_catalogued_members(self):
        Upstream.payload = zip_bytes(second='roms/Second.nes')
        self.entry['_source_bundle_size'] = len(Upstream.payload)
        first = prepare_rom(self.entry, NES_GUID)
        second = prepare_rom({**self.entry, '_archive_member_path': 'roms/Second.nes'}, NES_GUID)
        self.assertEqual(self.ready(first)['state'], 'ready')
        self.assertEqual(self.ready(second)['state'], 'ready')
        self.assertEqual(len(Upstream.requests), 1)

    def test_nested_zip_uses_the_existing_catalog_expanded_path(self):
        inner = zip_bytes(member='Original NES demo.nes')
        Upstream.payload = zip_bytes(member='Game.zip', payload=inner)
        plan = prepare_rom({**self.entry, '_archive_member_path': 'Game/Original NES demo.nes',
                            '_source_bundle_size': len(Upstream.payload)}, NES_GUID)
        status = self.ready(plan)
        self.assertEqual(status['state'], 'ready', status)
        self.assertEqual(status['path'].read_bytes(), ROM)
        self.assertEqual(len(Upstream.requests), 1)

    def test_nested_zip_limit_rejects_a_deep_archive_without_publishing(self):
        data = zip_bytes(member='Original NES demo.nes')
        member = 'Original NES demo.nes'
        for _ in range(5):
            data = zip_bytes(member='Game.zip', payload=data)
            member = 'Game/' + member
        Upstream.payload = data
        plan = prepare_rom({**self.entry, '_archive_member_path': member,
                            '_source_bundle_size': len(data)}, NES_GUID)
        self.assertEqual(self.ready(plan)['state'], 'failed')
        self.assertFalse(list(self.preparer.root.glob('*.rom')))

    def test_checksum_failure_never_publishes_and_errors_hide_source(self):
        plan = prepare_rom({**self.entry, 'sha256': '0' * 64}, NES_GUID)
        status = self.ready(plan)
        self.assertEqual(status['state'], 'failed')
        self.assertIn('checksum', status['error'])
        self.assertNotIn(self.url, status['error'])
        self.assertFalse(list(self.preparer.root.glob('*.rom')))

    def test_member_paths_symlinks_and_size_mismatches_fail_without_extraction(self):
        for member in ('../escape.nes', '/tmp/a.nes', 'roms/../a.nes', 'roms\\a.nes'):
            with self.subTest(member=member), self.assertRaises(DownloadError):
                safe_member(member)
        Upstream.payload = zip_bytes(symlink=True)
        plan = prepare_rom({**self.entry, '_source_bundle_size': len(Upstream.payload)}, NES_GUID)
        self.assertEqual(self.ready(plan)['state'], 'failed')
        self.assertFalse(list(self.preparer.root.glob('*.rom')))

    def test_member_size_mismatch_never_publishes(self):
        plan = prepare_rom({**self.entry, 'size': len(ROM) - 1}, NES_GUID)
        self.assertEqual(self.ready(plan)['state'], 'failed')
        self.assertFalse(list(self.preparer.root.glob('*.rom')))

    def test_missing_and_duplicate_members_are_rejected(self):
        missing = prepare_rom({**self.entry, '_archive_member_path': 'roms/Missing.nes'}, NES_GUID)
        self.assertEqual(self.ready(missing)['state'], 'failed')
        self.assertFalse(list(self.preparer.root.glob('*.rom')))
        # Repeated names are ambiguous even when their contents match.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            Upstream.payload = zip_bytes(second=MEMBER)
        duplicate = prepare_rom({**self.entry, '_source_bundle_size': len(Upstream.payload)}, NES_GUID)
        self.assertEqual(self.ready(duplicate)['state'], 'failed')

    def test_prepared_download_never_follows_redirects_with_api_key(self):
        origin = self.url.rsplit('/', 1)[0]
        api = Api(origin, self.token)
        with self.assertRaises(ClientError):
            api.open_download('/client/downloads/redirect/content?target=mister', {})
        self.assertEqual(len(Upstream.requests), 1)

    def test_corrupted_prepared_file_is_rebuilt_from_cached_zip(self):
        status = self.ready()
        status['path'].write_bytes(b'corrupt')
        self.assertEqual(self.ready()['state'], 'ready')
        self.assertEqual(status['path'].read_bytes(), ROM)
        self.assertEqual(len(Upstream.requests), 1)

    def test_prepared_content_enforces_owner_key_type_and_live_console_permissions(self):
        job = self.enqueue()
        path = '/client/downloads/%s/content?target=mister' % job['id']
        for headers, expected in (({}, 401), (self.admin, 403), ({'Authorization': 'Bearer other-key'}, 403)):
            self.assertEqual(self.http.get(path, headers=headers).status_code, expected)
        self.assertEqual(self.ready()['state'], 'ready')
        response = self.http.get(path, headers=self.device)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, ROM)
        self.assertTrue(response.headers['etag'].startswith('"'))
        self.assertNotIn('_preparation', str(job))
        self.assertNotIn(self.url, str(job))
        claimed = self.http.post('/mister/client/claim', headers=self.device).json()['job']
        self.assertIn('download_path', claimed)
        self.assertNotIn('_preparation', claimed)
        self.assertNotIn('source_url', claimed)
        payload = json.loads(self.users.read_text())
        payload['users'][0]['allowed_console_guids'] = [SNES_GUID]
        self.users.write_text(json.dumps(payload))
        self.assertEqual(self.http.get(path, headers=self.device).status_code, 404)

    def test_failed_preparation_can_retry_same_persistent_job(self):
        job = self.enqueue()
        Upstream.status = 503
        self.assertEqual(self.ready()['state'], 'failed')
        claimed = self.store.claim({'nes'})
        self.store.report(job['id'], claimed['lease'], 'failed', 0, 'Provider unavailable')
        Upstream.status = 200
        response = self.http.post('/client/downloads/%s/retry?target=mister' % job['id'], headers=self.device)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ready()['state'], 'ready')
        self.assertEqual(self.store.get_job(job['id'])['id'], job['id'])

    def test_catalog_filter_returns_only_preparable_entries_and_preserves_pagination(self):
        self.datasets['zip-demo']['entries'].append({'name': 'No source'})
        response = self.http.get('/roms/zip-demo?download_target=mister&limit=1&offset=0', headers=self.device)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['total'], 1)
        self.assertTrue(response.json()['entries'][0]['mister']['supported'])
        all_entries = self.http.get('/roms/zip-demo?limit=1', headers=self.device).json()
        self.assertEqual(all_entries['total'], 2)
        self.assertEqual(self.http.get('/roms/zip-demo?download_target=mister', headers={'Authorization': 'Bearer other-key'}).status_code, 403)

    def test_preparing_lease_survives_restart_and_expires_for_reclaim(self):
        self.enqueue()
        job = self.store.claim({'nes'})
        self.store.report(job['id'], job['lease'], 'preparing', 0)
        self.assertIsNone(self.store.claim({'nes'}))
        restarted = MisterStore(self.store.path, clock=lambda: time.time() + 121)
        claimed = restarted.claim({'nes'})
        self.assertEqual(claimed['id'], job['id'])
        self.assertNotEqual(claimed['lease'], job['lease'])

    def start_backend(self):
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        server = uvicorn.Server(uvicorn.Config(self.app, host='127.0.0.1', port=port, log_level='error', lifespan='off'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        def shutdown():
            server.should_exit = True
            thread.join(timeout=3)
        self.addCleanup(shutdown)
        deadline = time.monotonic() + 3
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(server.started)
        return 'http://127.0.0.1:%d' % port

    def test_real_client_polls_preparation_then_verifies_and_installs_rom(self):
        job = self.enqueue()
        base = self.start_backend()
        root = self.root / 'NES'
        root.mkdir()
        events = []
        client = Client({'games_roots': {'nes': str(root)}, 'lock_file': str(self.root / 'lock')},
            api=Api(base, self.token), allow_test_storage=True, observer=events.append)
        self.assertEqual(client.sync(), 0)
        self.assertEqual((root / job['destination']).read_bytes(), ROM)
        self.assertEqual(self.store.get_job(job['id'])['state'], 'completed')
        self.assertTrue(any('preparing' in e.get('state', '') for e in events))
        self.assertNotIn(self.token, str(Upstream.requests))
        before = (root / job['destination']).stat().st_mtime_ns
        self.assertEqual(client.sync(), 0)
        self.assertEqual((root / job['destination']).stat().st_mtime_ns, before)
        self.assertEqual(len(Upstream.requests), 1)

    def test_real_client_fetches_discovered_rom_metadata_before_transferring(self):
        plan, entry = self.dynamic_plan(zip_bytes())
        job = self.store.enqueue(plan)
        self.assertEqual(job['size'], 0)
        base = self.start_backend()
        root = self.root / 'NES'
        root.mkdir()
        client = Client({'games_roots': {'nes': str(root)}, 'lock_file': str(self.root / 'dynamic-lock')},
            api=Api(base, self.token), allow_test_storage=True)
        self.assertEqual(client.sync(), 0)
        complete = self.store.get_job(job['id'])
        self.assertEqual(complete['state'], 'completed')
        self.assertEqual(complete['size'], len(ROM))
        self.assertEqual(complete['checksum']['value'], hashlib.sha256(ROM).hexdigest())
        self.assertEqual((root / complete['destination']).read_bytes(), ROM)
        self.assertEqual(self.store.enqueue(plan)['id'], job['id'])

    def test_installed_archive_rom_recovers_without_preparing_or_downloading_again(self):
        job = self.enqueue()
        base = self.start_backend()
        root = self.root / 'NES'
        root.mkdir()
        client = Client({'games_roots': {'nes': str(root)}, 'lock_file': str(self.root / 'recover-lock')},
            api=Api(base, self.token), allow_test_storage=True, observer=lambda event: None)
        self.assertEqual(client.sync(), 0)
        path = root / job['destination']
        mtime = path.stat().st_mtime_ns
        with self.store.connect() as database:
            database.execute("UPDATE jobs SET state='verifying',lease_until=? WHERE id=?", (time.time() - 1, job['id']))
        for cached in self.preparer.root.iterdir():
            if cached.is_file():
                cached.unlink()
        Upstream.status = 503
        self.assertEqual(client.sync(), 0)
        self.assertEqual(self.store.get_job(job['id'])['state'], 'completed')
        self.assertEqual(path.stat().st_mtime_ns, mtime)
        self.assertEqual(len(Upstream.requests), 1)


if __name__ == '__main__':
    unittest.main()
