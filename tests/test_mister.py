"""MiSTer queue/API security and real HTTP client transfer tests.

Run: backend/.venv/bin/python -m unittest discover -s tests -v
"""
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.app.mister_api import make_router
from backend.app.client_downloads import DownloadTarget, make_client_download_router
from core.mister import LEASE_SECONDS, MisterError, MisterStore, SNES_GUID, NES_GUID, GENESIS_GUID, SYSTEM_GUIDS, compatibility, entry_key, prepare_rom
from mister.demo import build_demo_rom, build_nes_demo_rom, demo_catalog
from mister.roms_manager_client import Api, ApiError, Client, ClientError, destination_path, matches, publish_no_replace


ROM = build_demo_rom()
NES_ROM = build_nes_demo_rom()


class RomServer(BaseHTTPRequestHandler):
    requests = []
    corrupt = True

    def do_POST(self):
        self.requests.append(dict(self.headers))
        self.send_response(302)
        self.send_header('Location', '/credential-trap')
        self.end_headers()

    def do_GET(self):
        self.requests.append(dict(self.headers))
        if self.path == '/forbidden.sfc':
            self.send_error(403)
            return
        if self.path == '/redirect.sfc':
            self.send_response(302)
            self.send_header('Location', '/demo.sfc')
            self.end_headers()
            return
        rom = NES_ROM if self.path.endswith('.nes') and self.path != '/headerless.nes' else ROM
        offset = 0
        if self.headers.get('Range') and self.path not in ('/ignore-range.sfc', '/bad-checksum.sfc'):
            offset = int(self.headers['Range'].split('=')[1].split('-')[0])
            self.send_response(206)
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (offset, len(rom)-1, len(rom)))
        else:
            self.send_response(200)
        data = b'x' * len(rom) if self.path == '/bad-checksum.sfc' and self.corrupt else rom[offset:]
        self.send_header('Content-Length', str(len(data)))
        self.send_header('ETag', '"v1"' if self.path != '/bad-validator.sfc' else '"different"')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000
        self.store = MisterStore(Path(self.tmp.name) / 'state.sqlite3', clock=lambda: self.now)
        self.token = 'test-mister-key'
        self.store.bind_account('mister-fpga', 'Test MiSTer')
        self.entry = demo_catalog('http://backend.test')['entries'][0]
        self.plan = prepare_rom(self.entry, SNES_GUID)

    def enqueue(self):
        return self.store.enqueue(self.plan)


class StoreCase(StoreFixture):
    def test_nes_preparation_uses_its_own_format_and_claim_filter(self):
        entry = demo_catalog('http://backend.test', 'nes')['entries'][0]
        plan = prepare_rom(entry, NES_GUID)
        self.assertEqual(plan['system'], 'nes')
        self.assertEqual(plan['core'], 'NES')
        self.assertTrue(plan['destination'].endswith('.nes'))
        self.assertFalse(compatibility(entry, SNES_GUID)['supported'])
        self.assertFalse(compatibility(self.entry, NES_GUID)['supported'])
        self.enqueue()
        queued = self.store.enqueue(plan)
        self.assertEqual(self.store.claim({'nes'})['id'], queued['id'])
        self.assertIsNone(self.store.claim({'snes'}))  # One active transfer across consoles.

    def test_plan_requires_snes_and_exact_source_metadata(self):
        self.assertFalse(compatibility(self.entry, 'other')['supported'])
        for change in ({'http_url': 'http://test/rom.zip'}, {'size': None}, {'sha256': None},
                       {'http_url': 'file:///rom.sfc'}, {'http_url': 'http://name:secret@test/a.sfc'},
                       {'rom_name': '../escape.sfc'}, {'_archive_member': 'a.sfc'}):
            with self.subTest(change=change), self.assertRaises(MisterError):
                prepare_rom({**self.entry, **change}, SNES_GUID)
        # The RDB hash describes a raw ROM, not the provider archive.
        archive = {'http_url': 'https://source/game.zip', 'name': 'game.zip'}
        with self.assertRaises(MisterError):
            prepare_rom({**self.entry, '_providers': [{'rom': archive}]}, SNES_GUID)

    def test_deduplication_persistence_and_completed_replay(self):
        first = self.enqueue()
        self.assertEqual(first['id'], self.enqueue()['id'])
        restarted = MisterStore(self.store.path, clock=lambda: self.now)
        job = restarted.claim()
        restarted.report(job['id'], job['lease'], 'verifying', job['size'])
        restarted.report(job['id'], job['lease'], 'completed', job['size'])
        self.assertEqual(restarted.report(job['id'], job['lease'], 'completed', job['size'])['state'], 'completed')
        self.assertEqual(self.enqueue()['id'], first['id'])
        self.assertIsNone(restarted.claim())
        self.assertNotIn('source_url', restarted.snapshot()['jobs'][0])

    def test_claim_is_exclusive_under_concurrency(self):
        self.enqueue()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.store.claim(), range(4)))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_restart_recovers_expired_claim_and_rejects_stale_worker(self):
        self.enqueue()
        old = self.store.claim()
        self.assertIsNone(self.store.claim())
        self.now += LEASE_SECONDS + 1
        new = MisterStore(self.store.path, clock=lambda: self.now).claim()
        self.assertEqual(old['id'], new['id'])
        self.assertNotEqual(old['lease'], new['lease'])
        with self.assertRaises(MisterError):
            self.store.report(old['id'], old['lease'], 'completed', old['size'])

    def test_expired_claim_cannot_report_before_reclaim(self):
        self.enqueue()
        job = self.store.claim()
        self.now += LEASE_SECONDS + 1
        with self.assertRaises(MisterError):
            self.store.report(job['id'], job['lease'], 'downloading', 1)

    def test_repeated_interruptions_fail_until_manual_retry(self):
        self.enqueue()
        for _ in range(3):
            job = self.store.claim()
            self.now += LEASE_SECONDS + 1
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.snapshot()['jobs'][0]['state'], 'failed')
        self.store.retry(job['id'])
        self.assertIsNotNone(self.store.claim())

    def test_account_binding_persists_without_parallel_credentials(self):
        restarted = MisterStore(self.store.path)
        self.assertEqual(restarted.bound_account_id(), 'mister-fpga')
        with restarted.connect() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(device)')}
        self.assertNotIn('token_hash', columns)
        self.assertNotIn('enabled', columns)

    def test_completion_requires_verified_size_and_valid_claim(self):
        self.enqueue()
        job = self.store.claim()
        for lease, state, size in [(job['lease'], 'completed', job['size']), ('wrong', 'verifying', job['size']),
                                    (job['lease'], 'downloading', job['size']+1)]:
            with self.subTest(state=state), self.assertRaises(MisterError):
                self.store.report(job['id'], lease, state, size)


class ApiCase(StoreFixture):
    def setUp(self):
        super().setUp()
        # Exercise the repository's actual admin authentication dependency.
        with patch.dict(os.environ, {'ROMS_MANAGER_MISTER_DB': str(Path(self.tmp.name) / 'import-state.sqlite3')}):
            from backend.app import main
        self.main = main
        users = Path(self.tmp.name) / 'users.json'
        users.write_text(json.dumps({'users': [{'id': 'admin', 'enabled': True, 'admin': True, 'api_key_hash': main._hash_api_key('test-admin')},
                                              {'id': 'regular', 'enabled': True, 'admin': False, 'api_key_hash': main._hash_api_key('test-regular')},
                                              {'id': 'mister-fpga', 'enabled': True, 'admin': False, 'api_key_hash': main._hash_api_key(self.token), 'allowed_console_guids': [SNES_GUID]},
                                              {'id': 'other-snes', 'enabled': True, 'admin': False, 'api_key_hash': main._hash_api_key('other-snes-key'), 'allowed_console_guids': [SNES_GUID]}]}))
        self.users_file = users
        self.patch_users = patch.object(main, 'USERS_FILE', users)
        self.patch_users.start()
        self.addCleanup(self.patch_users.stop)
        dataset = demo_catalog('http://backend.test')
        self.datasets = {'demo': dataset, 'nes-demo': demo_catalog('http://backend.test', 'nes'), 'not-assigned': {**dataset, 'guid': 'unassigned-console'}}
        self.app = FastAPI()
        self.app.include_router(make_router(self.store, main.require_admin, self.datasets.get,
            main.current_user, main._mister_account, main._provision_mister_account, main._disable_mister_account))
        self.app.include_router(make_client_download_router(main.current_user, main._guid_allowed, self.datasets.get, {
            'mister': DownloadTarget(self.store, SYSTEM_GUIDS, prepare_rom, entry_key),
        }))
        self.app.add_api_route('/access/users', main.list_users, methods=['GET'])
        self.app.add_api_route('/access/users/{user_id}', main.update_user, methods=['PATCH'])
        self.app.add_api_route('/me', main.me, methods=['GET'])

        @self.app.get('/admin-probe')
        def admin_probe(_=Depends(main.require_admin)):
            return {'ok': True}

        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.admin = {'Authorization': 'Bearer test-admin'}
        self.device = {'Authorization': 'Bearer ' + self.token}

    def assign(self, guids):
        response = self.client.patch('/access/users/mister-fpga', headers=self.admin, json={'allowed_console_guids': guids})
        self.assertEqual(response.status_code, 200, response.text)

    def nes_body(self):
        entry = self.datasets['nes-demo']['entries'][0]
        return {'target': 'mister', 'dataset': 'nes-demo', 'entry_key': entry_key(entry)}

    def test_nes_assignment_enables_queue_without_rotating_existing_key(self):
        body = self.nes_body()
        self.assertEqual(self.client.post('/client/downloads', headers=self.device, json=body).status_code, 403)
        self.assertEqual(self.client.post('/mister/admin/jobs', headers=self.admin,
            json={k: v for k, v in body.items() if k != 'target'}).status_code, 403)
        self.assign([SNES_GUID, NES_GUID])
        targets = self.client.get('/client/downloads/targets', headers=self.device).json()['targets']
        self.assertEqual(targets[0]['system_guids'], {'snes': SNES_GUID, 'nes': NES_GUID})
        first = self.client.post('/client/downloads', headers=self.device, json=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()['job']['system'], 'nes')
        duplicate = self.client.post('/client/downloads', headers=self.device, json=body).json()['job']
        self.assertEqual(first.json()['job']['id'], duplicate['id'])
        other = {'Authorization': 'Bearer other-snes-key'}
        self.assertEqual(self.client.post('/client/downloads', headers=other, json=body).status_code, 403)

    def test_sega_assignment_uses_same_account_and_own_jobs(self):
        from core.mister_sources import GENESIS_GUID
        entry = {**self.entry, 'name': 'Sega fixture', 'rom_name': 'Sega fixture.md', 'http_url': 'https://source.invalid/fixture.md'}
        self.datasets['sega'] = {'guid': GENESIS_GUID, 'entries': [entry]}
        body = {'target': 'mister', 'dataset': 'sega', 'entry_key': entry_key(entry)}
        self.assertEqual(self.client.post('/client/downloads', headers=self.device, json=body).status_code, 403)
        self.assign([GENESIS_GUID])
        response = self.client.post('/client/downloads', headers=self.device, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()['job']
        self.assertEqual(job['system'], 'genesis')
        claimed = self.client.post('/mister/client/claim', headers=self.device).json()['job']
        self.assertEqual(job['id'], claimed['id'])
        self.assign([NES_GUID, SNES_GUID])
        path = '/client/downloads/%s/recipe?target=mister' % job['id']
        self.assertEqual(self.client.get(path, headers=self.device).status_code, 404)

    def test_nes_only_account_can_sync_without_snes_and_cannot_claim_snes(self):
        snes = self.enqueue()
        self.assign([NES_GUID])
        response = self.client.post('/client/downloads', headers=self.device, json=self.nes_body())
        self.assertEqual(response.status_code, 200, response.text)
        nes = response.json()['job']
        jobs = self.client.get('/client/downloads?target=mister', headers=self.device).json()['jobs']
        self.assertEqual([job['id'] for job in jobs], [nes['id']])
        claimed = self.client.post('/mister/client/claim', headers=self.device).json()['job']
        self.assertEqual(claimed['id'], nes['id'])
        self.assertEqual(self.store.get_job(snes['id'])['state'], 'queued')
        self.assertTrue(self.client.get('/mister/admin', headers=self.admin).json()['device']['enabled'])

    def test_revoking_nes_blocks_active_progress_and_retry_even_with_snes_assigned(self):
        self.assign([SNES_GUID, NES_GUID])
        nes = self.client.post('/client/downloads', headers=self.device, json=self.nes_body()).json()['job']
        claimed = self.client.post('/mister/client/claim', headers=self.device).json()['job']
        self.assign([SNES_GUID])
        report = {'lease': claimed['lease'], 'state': 'verifying', 'downloaded': nes['size']}
        self.assertEqual(self.client.post('/mister/client/jobs/%s/progress' % nes['id'], headers=self.device, json=report).status_code, 404)
        self.assertEqual(self.client.post('/client/downloads/%s/retry?target=mister' % nes['id'], headers=self.device).status_code, 404)
        self.assertEqual(self.client.post('/mister/admin/jobs/%s/retry' % nes['id'], headers=self.admin).status_code, 404)
        self.now += LEASE_SECONDS + 1
        self.assertIsNone(self.client.post('/mister/client/claim', headers=self.device).json()['job'])
        self.assertEqual(self.client.get('/client/downloads?target=mister', headers=self.device).json()['jobs'], [])

    def test_device_cannot_access_admin_and_admin_token_is_not_device(self):
        for path in ('/admin-probe', '/mister/admin'):
            self.assertEqual(self.client.get(path, headers=self.device).status_code, 403)
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get('/mister/admin', headers={'Authorization': 'Bearer test-regular'}).status_code, 403)
        self.assertEqual(self.client.post('/mister/client/claim', headers=self.admin).status_code, 403)
        self.assertEqual(self.client.post('/mister/client/claim').status_code, 401)
        self.assertEqual(self.client.get('/mister/admin', headers=self.admin).status_code, 200)
        self.assertEqual(self.client.post('/mister/client/claim', headers={'Authorization': 'Bearer other-snes-key'}).status_code, 403)

    def test_api_queue_transfer_protocol_and_stale_catalog_rejection(self):
        response = self.client.post('/mister/admin/jobs', headers=self.admin, json={'dataset': 'demo', 'entry_key': entry_key(self.entry)})
        self.assertEqual(response.status_code, 200, response.text)
        job = self.client.post('/mister/client/claim', headers=self.device).json()['job']
        self.assertEqual(job['size'], len(ROM))
        self.assertIn('source_url', job)
        for state in ('verifying', 'completed'):
            result = self.client.post('/mister/client/jobs/%s/progress' % job['id'], headers=self.device,
                json={'lease': job['lease'], 'state': state, 'downloaded': len(ROM)})
            self.assertEqual(result.status_code, 200, result.text)
        result = self.client.get('/mister/admin', headers=self.admin).json()
        self.assertEqual(result['jobs'][0]['state'], 'completed')
        self.assertNotIn('token_hash', str(result))
        self.assertNotIn('source_url', result['jobs'][0])
        response = self.client.post('/mister/admin/jobs', headers=self.admin, json={'dataset': 'demo', 'entry_key': '0'*64})
        self.assertEqual(response.status_code, 404)

    def test_revocation_immediately_blocks_device_requests(self):
        self.assertEqual(self.client.delete('/mister/admin/device/credential', headers=self.admin).status_code, 200)
        self.assertEqual(self.client.post('/mister/client/claim', headers=self.device).status_code, 403)
        account = next(u for u in self.client.get('/access/users', headers=self.admin).json()['users'] if u['id'] == 'mister-fpga')
        self.assertFalse(account['enabled'])

    def test_standard_client_account_visible_and_permissions_enforced(self):
        users = self.client.get('/access/users', headers=self.admin).json()['users']
        account = next(u for u in users if u['id'] == 'mister-fpga')
        self.assertFalse(account['admin'])
        self.assertEqual(account['allowed_console_guids'], [SNES_GUID])
        self.assertTrue(account['has_api_key'])
        self.assertEqual(self.client.get('/me', headers=self.device).json()['user']['id'], account['id'])
        device = self.client.get('/mister/admin', headers=self.admin).json()['device']
        self.assertEqual(device['account_id'], account['id'])
        self.assertNotIn('api_key_hash', str(device))
        response = self.client.patch('/access/users/mister-fpga', headers=self.admin, json={'allowed_console_guids': []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/mister/client/claim', headers=self.device).status_code, 403)
        self.assertFalse(self.client.get('/mister/admin', headers=self.admin).json()['device']['enabled'])

    def test_resetting_key_in_users_access_invalidates_installed_key(self):
        response = self.client.patch('/access/users/mister-fpga', headers=self.admin, json={'reset_api_key': True})
        self.assertEqual(response.status_code, 200)
        new_key = response.json()['api_key']
        self.assertTrue(new_key.startswith('rommgr_'))
        self.assertEqual(self.client.post('/mister/client/claim', headers=self.device).status_code, 401)
        self.assertEqual(self.client.post('/mister/client/claim', headers={'Authorization': 'Bearer ' + new_key}).status_code, 200)

    def test_provision_rotates_normal_account_key_without_duplicate_user(self):
        result = self.client.post('/mister/admin/device', headers=self.admin, json={'name': 'Updated MiSTer'})
        self.assertEqual(result.status_code, 200, result.text)
        token = result.json()['device_token']
        self.assertTrue(token.startswith('rommgr_'))
        users = self.client.get('/access/users', headers=self.admin).json()['users']
        self.assertEqual(sum(u['id'] == 'mister-fpga' for u in users), 1)
        self.assertEqual(self.client.post('/mister/client/claim', headers=self.device).status_code, 401)
        self.assertEqual(self.client.post('/mister/client/claim', headers={'Authorization': 'Bearer ' + token}).status_code, 200)

    def test_preview_token_cannot_claim_device_jobs(self):
        payload = json.loads(self.users_file.read_text())
        user = next(u for u in payload['users'] if u['id'] == 'mister-fpga')
        user['preview_keys'] = [{'api_key_hash': self.main._hash_api_key('preview-token'), 'expires_at': self.main._preview_key_expiry()}]
        self.users_file.write_text(json.dumps(payload))
        preview = {'Authorization': 'Bearer preview-token'}
        self.assertEqual(self.client.get('/me', headers=preview).status_code, 200)
        self.assertEqual(self.client.post('/mister/client/claim', headers=preview).status_code, 403)
        self.assertEqual(self.client.get('/client/downloads?target=mister', headers=preview).status_code, 403)
        self.assertEqual(self.client.post('/client/downloads', headers=preview, json={
            'target': 'mister', 'dataset': 'demo', 'entry_key': entry_key(self.entry)}).status_code, 403)

    def test_legacy_migration_preserves_installed_key_jobs_and_telemetry(self):
        self.enqueue()
        payload = json.loads(self.users_file.read_text())
        payload['users'] = [u for u in payload['users'] if u['id'] != 'mister-fpga']
        self.users_file.write_text(json.dumps(payload))
        with sqlite3.connect(self.store.path) as db:
            db.execute('DROP TABLE device')
            db.execute("CREATE TABLE device(id TEXT PRIMARY KEY,name TEXT,enabled INTEGER,token_hash TEXT,last_seen REAL,telemetry TEXT)")
            db.execute('INSERT INTO device VALUES(?,?,?,?,?,?)',
                       ('mister', 'MiSTer FPGA', 1, self.main._hash_api_key(self.token), 1000, '{"core_present":true}'))
        migrated = MisterStore(self.store.path, migrate_account=self.main._migrate_mister_account)
        self.assertEqual(migrated.bound_account_id(), 'mister-fpga')
        self.assertEqual(len(migrated.snapshot()['jobs']), 1)
        self.assertTrue(migrated.snapshot()['device']['telemetry']['core_present'])
        self.assertEqual(self.client.get('/me', headers=self.device).status_code, 200)
        self.assertEqual(self.client.post('/mister/client/claim', headers=self.device).status_code, 200)
        MisterStore(self.store.path, migrate_account=self.main._migrate_mister_account)
        users = self.client.get('/access/users', headers=self.admin).json()['users']
        self.assertEqual(sum(u['id'] == 'mister-fpga' for u in users), 1)
        with migrated.connect() as db:
            self.assertNotIn('token_hash', {r['name'] for r in db.execute('PRAGMA table_info(device)')})

    def test_client_can_queue_list_and_retry_own_download_without_admin(self):
        body = {'target': 'mister', 'dataset': 'demo', 'entry_key': entry_key(self.entry)}
        response = self.client.post('/client/downloads', headers=self.device, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()['job']['id']
        duplicate = self.client.post('/client/downloads', headers=self.device, json=body).json()['job']
        self.assertEqual(job_id, duplicate['id'])
        page = self.client.get('/client/downloads?target=mister&limit=1', headers=self.device).json()
        self.assertEqual(page['total'], 1)
        self.assertNotIn('source_url', str(page))
        claimed = self.store.claim()
        self.store.report(job_id, claimed['lease'], 'failed', 0, 'Test failure')
        response = self.client.post('/client/downloads/%s/retry?target=mister' % job_id, headers=self.device)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.get_job(job_id)['state'], 'queued')
        targets = self.client.get('/client/downloads/targets', headers=self.device).json()['targets']
        self.assertEqual(targets, [{'id': 'mister', 'system_guids': {'snes': SNES_GUID}}])

    def test_client_operations_enforce_owner_permissions_and_key_type(self):
        queued = self.enqueue()
        paths = [('/client/downloads?target=mister', 'get', None),
                 ('/client/downloads', 'post', {'target': 'mister', 'dataset': 'demo', 'entry_key': entry_key(self.entry)}),
                 ('/client/downloads/%s/retry?target=mister' % queued['id'], 'post', None)]
        for key in ('test-admin', 'test-regular', 'other-snes-key'):
            headers = {'Authorization': 'Bearer ' + key}
            for path, method, body in paths:
                result = getattr(self.client, method)(path, headers=headers, **({'json': body} if body else {}))
                self.assertEqual(result.status_code, 403, (key, path, result.text))
        self.client.patch('/access/users/mister-fpga', headers=self.admin, json={'allowed_console_guids': []})
        for path, method, body in paths:
            result = getattr(self.client, method)(path, headers=self.device, **({'json': body} if body else {}))
            self.assertEqual(result.status_code, 403, (path, result.text))
        self.assertEqual(self.client.get('/client/downloads/targets', headers=self.device).json()['targets'], [])

    def test_offline_recipe_claim_and_receipt_enforce_ownership_and_lease(self):
        job_id = self.enqueue()['id']
        recipe = '/client/downloads/%s/recipe?target=mister' % job_id
        claim = '/client/downloads/%s/claim?target=mister' % job_id
        receipt = '/client/downloads/%s/prepared?target=mister' % job_id
        self.assertEqual(self.client.get(recipe, headers=self.device).json()['plan'], self.plan)
        for key in ('test-admin', 'test-regular', 'other-snes-key'):
            headers = {'Authorization': 'Bearer ' + key}
            self.assertEqual(self.client.get(recipe, headers=headers).status_code, 403)
            self.assertEqual(self.client.post(claim, headers=headers).status_code, 403)
        claimed = self.client.post(claim, headers=self.device).json()['job']
        self.assertEqual(claimed['id'], job_id)
        body = {'lease': 'wrong', 'filename': 'forged.sfc', 'size': 32, 'sha256': 'a' * 64}
        self.assertEqual(self.client.post(receipt, headers=self.device, json=body).status_code, 409)
        # Receipt registration must never overwrite a known direct-file plan.
        body['lease'] = claimed['lease']
        self.client.post(receipt, headers=self.device, json=body)
        self.assertEqual(self.store.get_plan(job_id), self.plan)
        self.client.patch('/access/users/mister-fpga', headers=self.admin, json={'allowed_console_guids': []})
        self.assertEqual(self.client.get(recipe, headers=self.device).status_code, 403)
        self.assertEqual(self.client.post(receipt, headers=self.device, json=body).status_code, 403)

    def test_archive_receipt_requires_claim_and_preserves_registered_metadata(self):
        source = {'name': 'Demo.zip', 'http_url': 'https://provider.invalid/Demo.zip', 'size': 1200, 'sha256': 'a' * 64}
        plan = prepare_rom({'name': 'Demo', '_providers': [{'rom': source}]}, SNES_GUID)
        job_id = self.store.enqueue(plan)['id']
        path = '/client/downloads/%s/prepared?target=mister' % job_id
        body = {'lease': 'wrong', 'filename': 'Demo.sfc', 'size': 32768, 'sha256': 'b' * 64}
        self.assertEqual(self.client.post(path, headers=self.device, json=body).status_code, 409)
        claim = self.client.post('/client/downloads/%s/claim?target=mister' % job_id, headers=self.device).json()['job']
        body['lease'] = claim['lease']
        valid = self.client.post(path, headers=self.device, json=body)
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertEqual(valid.json()['job']['destination'], 'ROMs Manager/Demo.sfc')
        for change in ({'filename': '../escape.sfc'}, {'filename': 'wrong.nes'}, {'size': 0}, {'sha256': 'b' * 63}):
            self.assertGreaterEqual(self.client.post(path, headers=self.device, json={**body, **change}).status_code, 400)
        self.assertEqual(self.client.post(path, headers=self.device, json={**body, 'sha256': 'c' * 64}).status_code, 409)

    def test_client_cannot_submit_unassigned_dataset_or_forged_source(self):
        body = {'target': 'mister', 'dataset': 'not-assigned', 'entry_key': entry_key(self.entry)}
        self.assertEqual(self.client.post('/client/downloads', headers=self.device, json=body).status_code, 403)
        body.update(dataset='demo', source_url='https://untrusted.invalid/anything.sfc')
        self.assertEqual(self.client.post('/client/downloads', headers=self.device, json=body).status_code, 422)
        self.assertEqual(self.client.get('/client/downloads?target=other', headers=self.device).status_code, 404)

    def test_client_job_pagination_and_per_job_system_permissions(self):
        for i in range(3):
            self.store.enqueue({**self.plan, 'destination': 'ROMs Manager/Test%d.sfc' % i})
        hidden = self.store.enqueue({**self.plan, 'system': 'other', 'destination': 'Other/Hidden.rom'})
        first = self.client.get('/client/downloads?target=mister&limit=2', headers=self.device).json()
        second = self.client.get('/client/downloads?target=mister&limit=2&offset=2', headers=self.device).json()
        self.assertEqual(first['total'], 3)
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        self.assertEqual(len(first['jobs']) + len(second['jobs']), 3)
        self.assertEqual(self.client.post('/client/downloads/%s/retry?target=mister' % hidden['id'], headers=self.device).status_code, 404)


class StoreApi:
    """Client-side protocol exercised against a real persistent queue."""
    def __init__(self, store):
        self.store = store

    def post(self, path, body=None):
        if path == 'heartbeat':
            self.store.heartbeat(body)
            return {'ok': True}
        if path == 'claim':
            return {'job': self.store.claim()}
        if path.startswith('jobs/'):
            return {'job': self.store.report(path.split('/')[1], body['lease'], body['state'], body['downloaded'], body.get('error'))}
        raise AssertionError(path)


class ClientCase(unittest.TestCase):
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
        self.root = Path(self.tmp.name) / 'SNES'
        self.root.mkdir()
        self.store = MisterStore(Path(self.tmp.name) / 'state.sqlite3')
        self.store.bind_account('mister-fpga', 'Test')
        self.plan = prepare_rom(demo_catalog(self.base)['entries'][0], SNES_GUID)
        self.store.enqueue(self.plan)
        self.client = Client({'games_root': str(self.root), 'lock_file': str(Path(self.tmp.name) / 'lock')}, api=StoreApi(self.store), allow_test_storage=True)
        RomServer.requests.clear()
        RomServer.corrupt = True

    def job(self, source='/demo.sfc'):
        job = self.store.claim()
        job['source_url'] = self.base + source
        return job

    def nes_plan(self):
        nes_root = Path(self.tmp.name) / 'NES'
        nes_root.mkdir(exist_ok=True)
        self.client.config['games_roots'] = {'nes': str(nes_root)}
        return prepare_rom(demo_catalog(self.base, 'nes')['entries'][0], NES_GUID), nes_root

    def test_both_consoles_install_into_separate_roots_and_repeat_safely(self):
        plan, nes_root = self.nes_plan()
        self.store.enqueue(plan)
        self.assertEqual(self.client.sync(), 0)
        self.assertEqual((nes_root / plan['destination']).read_bytes(), NES_ROM)
        self.assertEqual((self.root / self.plan['destination']).read_bytes(), ROM)
        self.assertFalse(list(self.root.rglob('*.nes')))
        self.assertFalse(list(nes_root.rglob('*.sfc')))
        before = (nes_root / plan['destination']).stat().st_mtime_ns
        self.assertEqual(self.client.sync(), 0)
        self.assertEqual((nes_root / plan['destination']).stat().st_mtime_ns, before)
        self.assertEqual(len(RomServer.requests), 2)

    def test_nes_header_failure_and_cross_console_destinations_never_publish(self):
        plan, nes_root = self.nes_plan()
        plan.update(size=len(ROM), checksum={'algorithm': 'sha256', 'value': hashlib.sha256(ROM).hexdigest()},
                    source_url=self.base + '/headerless.nes')
        self.store.enqueue(plan)
        self.assertEqual(self.client.sync(), 1)
        self.assertFalse((nes_root / plan['destination']).exists())
        failed = next(job for job in self.store.snapshot()['jobs'] if job['system'] == 'nes')
        self.assertIn('header', failed['error'])
        for root, relative, system in ((nes_root, 'ROMs Manager/a.sfc', 'nes'),
                                        (self.root, 'ROMs Manager/a.nes', 'snes'),
                                        (nes_root, '../SNES/escape.nes', 'nes')):
            with self.assertRaises(ClientError):
                destination_path(root, relative, system)

    def test_nes_existing_file_conflict_preserves_both_console_roots(self):
        plan, nes_root = self.nes_plan()
        self.store.enqueue(plan)
        target = destination_path(nes_root, plan['destination'], 'nes')
        target.write_bytes(b'previous NES game')
        self.assertEqual(self.client.sync(), 1)
        self.assertEqual(target.read_bytes(), b'previous NES game')
        self.assertEqual((self.root / self.plan['destination']).read_bytes(), ROM)
        self.assertEqual(len(RomServer.requests), 1)

    def test_nes_only_storage_does_not_require_snes_folder(self):
        plan, nes_root = self.nes_plan()
        def discovery(system):
            if system == 'snes':
                raise ClientError('No SNES games folder')
            return nes_root
        with patch('mister.roms_manager_client.discover_games_root', side_effect=discovery):
            client = Client({}, api=StoreApi(self.store), allow_test_storage=True)
            self.assertEqual(client.system, 'nes')
            self.assertEqual(client.root, nes_root.resolve())

    def test_nes_core_is_checked_before_transfer(self):
        plan, nes_root = self.nes_plan()
        self.store.enqueue(plan)
        job = self.store.claim({'nes'})
        self.client.select_system('nes')
        self.client.allow_test_storage = False
        with patch.object(self.client, 'core_present', return_value=False), self.assertRaisesRegex(ClientError, 'NES core not found'):
            self.client.install(job)
        self.assertFalse(RomServer.requests)
        self.assertFalse((nes_root / plan['destination']).exists())

    def test_demo_is_valid_size_with_reset_vector_and_header_checksum(self):
        self.assertEqual(len(ROM), 32768)
        self.assertEqual(int.from_bytes(ROM[0x7ffc:0x7ffe], 'little'), 0x8000)
        self.assertEqual(int.from_bytes(ROM[0x7fde:0x7fe0], 'little'), sum(ROM) & 0xffff)
        self.assertEqual(int.from_bytes(ROM[0x7fdc:0x7fde], 'little') ^ int.from_bytes(ROM[0x7fde:0x7fe0], 'little'), 0xffff)

    def seed_partial(self, job, etag='"v1"'):
        target = destination_path(self.root, job['destination'])
        part = target.parent / ('.' + job['id'] + '.part')
        part.write_bytes(ROM[:8192])
        source_id = hashlib.sha256(json.dumps({k: job[k] for k in ('source_url', 'size', 'checksum')}, sort_keys=True).encode()).hexdigest()
        part.with_suffix('.resume.json').write_text(json.dumps({'source_id': source_id, 'etag': etag}))
        return part

    def test_end_to_end_sync_installs_verified_rom_and_no_duplicate_download(self):
        self.assertEqual(self.client.sync(), 0)
        job = self.store.snapshot()['jobs'][0]
        self.assertEqual(job['state'], 'completed')
        target = self.root / job['destination']
        self.assertEqual(target.read_bytes(), ROM)
        before = target.stat().st_mtime_ns
        self.client.sync()
        self.assertEqual(target.stat().st_mtime_ns, before)
        self.assertEqual(len(RomServer.requests), 1)

    def test_progress_callback_uses_engine_without_exposing_credentials(self):
        events = []
        self.client.observer = events.append
        self.assertEqual(self.client.sync(), 0)
        self.assertTrue(any(e.get('state') == 'verifying' for e in events))
        self.assertTrue(any(e.get('state') == 'completed' for e in events))
        self.assertNotIn('source_url', str(events))
        self.assertNotIn('lease', str(events))
        self.assertNotIn('http://', str(events))

    def test_interrupt_retains_partial_and_recovers_without_redownload(self):
        def interrupt(event):
            if event.get('state') == 'downloading':
                raise KeyboardInterrupt
        self.client.observer = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.client.sync()
        self.assertEqual(self.store.snapshot()['jobs'][0]['state'], 'downloading')
        self.assertTrue(list(self.root.rglob('*.part')))
        self.assertFalse((self.root / self.plan['destination']).exists())
        self.store.clock = lambda: time.time() + LEASE_SECONDS + 1
        self.client.observer = None
        self.assertEqual(self.client.sync(), 0)
        self.assertEqual(self.store.snapshot()['jobs'][0]['state'], 'completed')
        self.assertEqual(len(RomServer.requests), 1)

    def test_resume_with_strong_validator(self):
        job = self.job()
        self.seed_partial(job)
        self.client.install(job)
        self.assertTrue(matches(self.root / job['destination'], job))
        self.assertEqual(RomServer.requests[-1].get('Range'), 'bytes=8192-')
        self.assertEqual(RomServer.requests[-1].get('If-Range'), '"v1"')
        self.assertNotIn('Authorization', RomServer.requests[-1])

    def test_server_ignoring_range_restarts_instead_of_appending(self):
        job = self.job('/ignore-range.sfc')
        self.seed_partial(job)
        self.client.install(job)
        self.assertEqual((self.root / job['destination']).read_bytes(), ROM)

    def test_weak_etag_forces_fresh_download(self):
        job = self.job()
        self.seed_partial(job, 'W/"v1"')
        self.client.install(job)
        self.assertNotIn('Range', RomServer.requests[-1])

    def test_inconsistent_resume_validator_rejected(self):
        job = self.job('/bad-validator.sfc')
        self.seed_partial(job)
        with self.assertRaises(ClientError):
            self.client.install(job)
        self.assertFalse((self.root / job['destination']).exists())

    def test_bad_checksum_never_publishes_and_removes_partial(self):
        job = self.job('/bad-checksum.sfc')
        with self.assertRaisesRegex(ClientError, 'verification'):
            self.client.install(job)
        self.assertFalse((self.root / job['destination']).exists())
        self.assertFalse(list(self.root.rglob('*.part')))

    def test_failed_checksum_is_reported_and_manual_retry_recovers(self):
        bad = {**self.plan, 'source_url': self.base + '/bad-checksum.sfc', 'destination': 'ROMs Manager/Retry Demo.sfc'}
        queued = self.store.enqueue(bad)
        self.assertEqual(self.client.sync(), 1)
        failed = next(j for j in self.store.snapshot()['jobs'] if j['id'] == queued['id'])
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('verification', failed['error'])
        self.assertFalse((self.root / bad['destination']).exists())
        RomServer.corrupt = False
        self.store.retry(queued['id'])
        self.assertEqual(self.client.sync(), 0)
        finished = next(j for j in self.store.snapshot()['jobs'] if j['id'] == queued['id'])
        self.assertEqual(finished['state'], 'completed')
        self.assertEqual((self.root / bad['destination']).read_bytes(), ROM)

    def test_conflicting_existing_game_preserved(self):
        job = self.job()
        target = destination_path(self.root, job['destination'])
        target.write_bytes(b'existing content')
        with self.assertRaisesRegex(ClientError, 'conflict'):
            self.client.install(job)
        self.assertEqual(target.read_bytes(), b'existing content')
        self.assertEqual(len(RomServer.requests), 0)

    def test_installed_file_recovers_lost_completion_ack(self):
        job = self.job()
        target = destination_path(self.root, job['destination'])
        target.write_bytes(ROM)
        self.client.install(job)
        self.assertEqual(self.store.snapshot()['jobs'][0]['state'], 'completed')
        self.assertEqual(len(RomServer.requests), 0)

    def test_path_and_symlink_escapes_rejected(self):
        for relative in ('../escape.sfc', '/tmp/escape.sfc', 'ROMs Manager/../escape.sfc', 'ROMs Manager\\escape.sfc', 'other/escape.sfc'):
            with self.subTest(relative=relative), self.assertRaises(ClientError):
                destination_path(self.root, relative)
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (self.root / 'ROMs Manager').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ClientError):
            destination_path(self.root, 'ROMs Manager/escape.sfc')
        self.assertEqual(list(outside.iterdir()), [])

    def test_atomic_publish_never_overwrites_racing_writer(self):
        part = self.root / 'part'
        target = self.root / 'existing.sfc'
        part.write_bytes(ROM)
        target.write_bytes(b'other writer')
        with self.assertRaises(ClientError):
            publish_no_replace(part, target)
        self.assertEqual(target.read_bytes(), b'other writer')
        self.assertTrue(part.exists())

    def test_storage_change_and_insufficient_space_prevent_download(self):
        job = self.job()
        self.client.storage_device = -1
        with self.assertRaisesRegex(ClientError, 'disconnected'):
            self.client.install(job)
        self.client.storage_device = self.root.stat().st_dev
        with patch('mister.roms_manager_client.shutil.disk_usage') as usage:
            usage.return_value.free = 1
            with self.assertRaisesRegex(ClientError, 'Insufficient'):
                self.client.install(job)
        self.assertFalse(RomServer.requests)

    def test_device_api_never_follows_redirects_with_credential(self):
        with self.assertRaises(ApiError) as exc:
            Api(self.base, 'secret-device-token').post('claim')
        self.assertNotIn('secret-device-token', str(exc.exception))
        self.assertEqual(len(RomServer.requests), 1)
        self.assertIn('302', str(exc.exception))

    def test_rom_redirect_does_not_forward_device_credential(self):
        job = self.job('/redirect.sfc')
        self.client.install(job)
        self.assertEqual(len(RomServer.requests), 2)
        self.assertTrue(all('Authorization' not in request for request in RomServer.requests))


if __name__ == '__main__':
    unittest.main()
