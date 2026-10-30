"""Atomic JSON state on MiSTer's FAT filesystem; no optional Python extensions."""
import copy
import fcntl
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from core.mister_sources import SYSTEMS, SYSTEM_GUIDS, MAX_ROM_SIZE, LEASE_SECONDS, MisterError


class JsonState:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        digest = hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()
        self.lock_path = Path(tempfile.gettempdir()) / ('roms-manager-state-' + digest + '.lock')

    @contextmanager
    def transaction(self):
        with self.lock, self.lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            previous = json.dumps(state, sort_keys=True)
            yield state
            if json.dumps(state, sort_keys=True) != previous:
                atomic_json(self.path, state)


def atomic_json(path, value):
    stage = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with stage.open('w') as output:
            json.dump(value, output, separators=(',', ':'))
            output.flush()
            os.fsync(output.fileno())
        os.replace(stage, path)
    finally:
        stage.unlink(missing_ok=True)


class LocalStore(JsonState):
    def bound_account_id(self):
        with self.transaction() as data:
            return data.get('device', {}).get('account_id')

    def bind_account(self, account_id, name):
        with self.transaction() as data:
            data['device'] = {'id': 'mister', 'account_id': account_id, 'name': name}
            data.setdefault('jobs', {})

    @staticmethod
    def public(row, source=False):
        plan = copy.deepcopy(row['plan'])
        plan.pop('_preparation', None)
        if not source:
            plan.pop('source_url', None)
        return {**plan, **{key: row[key] for key in ('id', 'state', 'downloaded', 'error', 'attempts', 'created', 'updated')}}

    def snapshot(self):
        with self.transaction() as data:
            return {'device': copy.deepcopy(data.get('device')), 'jobs': [self.public(row) for row in data.get('jobs', {}).values()]}

    def get_job(self, job_id, source=False):
        with self.transaction() as data:
            row = data.get('jobs', {}).get(job_id)
            return self.public(row, source) if row else None

    def get_plan(self, job_id):
        with self.transaction() as data:
            return copy.deepcopy(data.get('jobs', {}).get(job_id, {}).get('plan'))

    def heartbeat(self, telemetry):
        with self.transaction() as data:
            data['device'].update(last_seen=time.time(), telemetry=telemetry)

    def recover(self):
        # Caller holds the process-wide transfer lock. Any active job is stale.
        with self.transaction() as data:
            for row in data.get('jobs', {}).values():
                if row['state'] in ('preparing', 'downloading', 'verifying'):
                    row.update(state='queued', lease=None)

    def requeue_missing(self, job_id):
        with self.transaction() as data:
            row = data['jobs'][job_id]
            if row['state'] == 'completed':
                row.update(state='queued', attempts=0, error=None)

    def enqueue(self, plan):
        fields = {key: plan[key] for key in ('system', 'destination', 'size', 'checksum')}
        if plan.get('_preparation', {}).get('kind') == 'archive_rom':
            fields['archive_checksum'] = plan['_preparation']['archive_checksum']
        identity = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
        with self.transaction() as data:
            for row in data['jobs'].values():
                if row['identity'] == identity:
                    return self.public(row)
            now, job_id = time.time(), uuid.uuid4().hex
            row = {'id': job_id, 'identity': identity, 'plan': copy.deepcopy(plan), 'state': 'queued',
                   'downloaded': 0, 'error': None, 'attempts': 0, 'created': now, 'updated': now}
            data['jobs'][job_id] = row
            return self.public(row)

    def claim(self, systems=None):
        with self.transaction() as data:
            if any(row['state'] in ('preparing', 'downloading', 'verifying') for row in data['jobs'].values()):
                return None
            for row in sorted(data['jobs'].values(), key=lambda r: (r['created'], r['id'])):
                if row['state'] == 'queued' and (systems is None or row['plan']['system'] in systems):
                    row.update(state='downloading', attempts=row['attempts'] + 1, lease=secrets.token_urlsafe(24),
                               lease_until=time.time() + LEASE_SECONDS, updated=time.time(), error=None)
                    return {**self.public(row, True), 'lease': row['lease'], 'lease_seconds': LEASE_SECONDS}

    def report(self, job_id, lease, state, downloaded, error=None):
        with self.transaction() as data:
            row = data['jobs'][job_id]
            if row.get('lease') != lease or row.get('lease_until', 0) < time.time():
                raise MisterError('Local transfer interrupted; sync again')
            if state not in ('preparing', 'downloading', 'verifying', 'completed', 'failed'):
                raise MisterError('Invalid job state')
            size = row['plan']['size']
            if not 0 <= downloaded <= size or (state == 'completed' and (row['state'] != 'verifying' or downloaded != size or size <= 0)):
                raise MisterError('Invalid transfer progress or completion before verification')
            if row['state'] in ('completed', 'failed') and state != row['state']:
                raise MisterError('Job already finished')
            if row['state'] == 'verifying' and state in ('preparing', 'downloading'):
                raise MisterError('Verification cannot transition back to downloading')
            row.update(state=state, downloaded=downloaded, error=error, updated=time.time(), lease_until=time.time() + LEASE_SECONDS)
            return self.public(row)

    def retry(self, job_id):
        with self.transaction() as data:
            row = data['jobs'][job_id]
            if row['state'] != 'failed':
                raise MisterError('Only failed jobs can be retried')
            row.update(state='queued', attempts=0, error=None, lease=None)

    def materialize(self, job_id, content):
        with self.transaction() as data:
            plan = data['jobs'][job_id]['plan']
            name = content['filename']
            mapping = SYSTEMS[SYSTEM_GUIDS[plan['system']]]
            if '/' in name or '\\' in name or Path(name).suffix.lower() not in mapping['extensions']:
                raise MisterError('Prepared file does not match its console')
            name = re.sub(r'[\x00-\x1f<>:"|?*]', '_', name).strip(' .')
            if not name or len(name.encode()) > 200 or not 0 < content['size'] <= MAX_ROM_SIZE:
                raise MisterError('Invalid prepared ROM metadata')
            if content.get('checksum', {}).get('algorithm') != 'sha256' or not re.fullmatch('[0-9a-f]{64}', content['checksum'].get('value', '')):
                raise MisterError('Prepared ROM requires SHA-256')
            fields = {'destination': 'ROMs Manager/' + name, 'size': content['size'], 'checksum': content['checksum']}
            if plan.get('prepared') and any(plan[key] != value for key, value in fields.items()):
                raise MisterError('Prepared ROM changed; refresh the catalog')
            plan.update(fields, prepared=True)

    def list_jobs(self, limit=20, offset=0, systems=None):
        with self.transaction() as data:
            rows = sorted(data['jobs'].values(), key=lambda r: (r['created'], r['id']), reverse=True)
            rows = [row for row in rows if systems is None or row['plan']['system'] in systems]
            return {'jobs': [self.public(row) for row in rows[offset:offset+limit]], 'total': len(rows),
                    'has_more': offset + limit < len(rows), 'offset': offset, 'limit': limit}
