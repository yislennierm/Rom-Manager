"""MiSTer installation plans and persistent transactional jobs."""
import hashlib
import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from core.mister_sources import (
    SNES_GUID, NES_GUID, GENESIS_GUID, SYSTEMS, SYSTEM_GUIDS, MAX_ROM_SIZE, LEASE_SECONDS,
    MisterError, entry_key, prepare_rom, compatibility,
)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class MisterStore:
    def __init__(self, path, clock=time.time, migrate_account=None):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS device (
                    id TEXT PRIMARY KEY CHECK(id='mister'), name TEXT NOT NULL,
                    account_id TEXT,
                    last_seen REAL, telemetry TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, device_id TEXT NOT NULL, identity TEXT NOT NULL UNIQUE,
                    plan TEXT NOT NULL, state TEXT NOT NULL, downloaded INTEGER NOT NULL DEFAULT 0,
                    error TEXT, attempts INTEGER NOT NULL DEFAULT 0, lease_hash TEXT,
                    lease_until REAL, created REAL NOT NULL, updated REAL NOT NULL
                );
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(device)')}
            if 'token_hash' in columns:
                if migrate_account is None:
                    raise MisterError('Legacy MiSTer credentials require an account migration')
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT * FROM device').fetchone()
                account_id = migrate_account(dict(old)) if old else None
                db.execute("CREATE TABLE device_accounts (id TEXT PRIMARY KEY CHECK(id='mister'), name TEXT NOT NULL, account_id TEXT, last_seen REAL, telemetry TEXT NOT NULL DEFAULT '{}')")
                if old:
                    db.execute('INSERT INTO device_accounts VALUES(?,?,?,?,?)',
                               (old['id'], old['name'], account_id, old['last_seen'], old['telemetry']))
                db.execute('DROP TABLE device')
                db.execute('ALTER TABLE device_accounts RENAME TO device')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def public_device(row):
        if row is None:
            return None
        return {"id": row["id"], "name": row["name"], "account_id": row["account_id"],
                "last_seen": row["last_seen"], "telemetry": json.loads(row["telemetry"]),
                "systems": list(SYSTEM_GUIDS)}

    @staticmethod
    def public_job(row, source=False):
        plan = json.loads(row["plan"])
        preparation = plan.pop('_preparation', None)
        if preparation and source:
            plan['download_path'] = '/client/downloads/%s/content?target=mister' % row['id']
            plan['preparation_path'] = '/client/downloads/%s/preparation?target=mister' % row['id']
        if not source:
            plan.pop("source_url", None)
        return {**plan, **{k: row[k] for k in ("id", "state", "downloaded", "error", "attempts", "created", "updated")}}

    def snapshot(self):
        with self.connect() as db:
            device = self.public_device(db.execute("SELECT * FROM device").fetchone())
            jobs = [self.public_job(r) for r in db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 200")]
        if device:
            device["online"] = bool(device["last_seen"] and self.clock() - device["last_seen"] < 30)
        return {"device": device, "jobs": jobs}

    def bound_account_id(self):
        with self.connect() as db:
            row = db.execute("SELECT account_id FROM device WHERE id='mister'").fetchone()
            return row['account_id'] if row else None

    def get_job(self, job_id, source=False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND device_id='mister'", (job_id,)).fetchone()
            return self.public_job(row, source=source) if row else None

    def get_plan(self, job_id):
        """Internal preparation inputs; never serialize provider secrets to clients."""
        with self.connect() as db:
            row = db.execute("SELECT plan FROM jobs WHERE id=? AND device_id='mister'", (job_id,)).fetchone()
            return json.loads(row['plan']) if row else None

    def materialize(self, job_id, content, lease=None):
        if not content:
            return
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE id=? AND device_id='mister'", (job_id,)).fetchone()
            if row is None:
                raise MisterError('Download job not found', 404)
            if lease is not None and (not row['lease_hash'] or not secrets.compare_digest(row['lease_hash'], token_hash(lease)) or not row['lease_until'] or row['lease_until'] <= self.clock()):
                raise MisterError('Job claim is no longer valid', 409)
            plan = json.loads(row['plan'])
            if plan.get('_preparation', {}).get('kind') != 'archive_rom':
                raise MisterError('Download does not require archive discovery')
            mapping = SYSTEMS[SYSTEM_GUIDS[plan['system']]]
            name = content['filename']
            if '/' in name or '\\' in name or Path(name).suffix.lower() not in mapping['extensions']:
                raise MisterError('Prepared file does not match its console')
            name = re.sub(r'[\x00-\x1f<>:"|?*]', '_', name).strip(' .')
            if not name or len(name.encode()) > 200 or not 0 < content['size'] <= MAX_ROM_SIZE:
                raise MisterError('Invalid prepared ROM metadata')
            if content.get('checksum', {}).get('algorithm') != 'sha256' or not re.fullmatch('[0-9a-f]{64}', content.get('checksum', {}).get('value', '')):
                raise MisterError('Prepared ROM requires SHA-256')
            fields = {'size': content['size'], 'checksum': content['checksum'], 'destination': 'ROMs Manager/' + name}
            if plan.get('prepared') and any(plan[key] != value for key, value in fields.items()):
                raise MisterError('Prepared ROM changed; refresh the catalog and create a new job', 409)
            if not plan.get('prepared'):
                plan.update(fields, prepared=True)
                db.execute('UPDATE jobs SET plan=?,updated=? WHERE id=?', (json.dumps(plan), self.clock(), job_id))

    def list_jobs(self, limit=20, offset=0, systems=None):
        page, total = [], 0
        with self.connect() as db:
            for row in db.execute("SELECT * FROM jobs WHERE device_id='mister' ORDER BY created DESC,id DESC"):
                job = self.public_job(row)
                if systems is not None and job['system'] not in systems:
                    continue
                if offset <= total < offset + limit:
                    page.append(job)
                total += 1
        return {'jobs': page, 'total': total, 'limit': limit, 'offset': offset, 'has_more': offset + limit < total}

    def bind_account(self, account_id, name):
        with self.connect() as db:
            db.execute("INSERT INTO device(id,name,account_id) VALUES('mister',?,?) "
                       "ON CONFLICT(id) DO UPDATE SET name=excluded.name, account_id=excluded.account_id",
                       (name.strip() or "MiSTer", account_id))
            db.execute("UPDATE jobs SET state='queued', lease_hash=NULL, lease_until=NULL WHERE state IN ('preparing','downloading','verifying')")

    def heartbeat(self, telemetry):
        with self.connect() as db:
            db.execute("UPDATE device SET last_seen=?, telemetry=? WHERE id='mister'", (self.clock(), json.dumps(telemetry)))

    def enqueue(self, plan):
        identity_fields = {k: plan[k] for k in ('system', 'destination', 'size', 'checksum')}
        if plan.get('_preparation', {}).get('kind') == 'archive_rom':
            identity_fields['archive_checksum'] = plan['_preparation']['archive_checksum']
        identity = token_hash(json.dumps(identity_fields, sort_keys=True))
        now = self.clock()
        with self.connect() as db:
            device = db.execute("SELECT account_id FROM device WHERE id='mister'").fetchone()
            if not device or not device["account_id"]:
                raise MisterError("Set up the MiSTer device first", 409)
            db.execute("INSERT OR IGNORE INTO jobs(id,device_id,identity,plan,state,created,updated) VALUES(?,'mister',?,?,'queued',?,?)",
                       (uuid.uuid4().hex, identity, json.dumps(plan), now, now))
            return self.public_job(db.execute("SELECT * FROM jobs WHERE identity=?", (identity,)).fetchone())

    def claim(self, systems=None, job_id=None):
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE device SET last_seen=? WHERE id='mister'", (now,))
            db.execute("UPDATE jobs SET state=CASE WHEN attempts>=3 THEN 'failed' ELSE 'queued' END, "
                       "error='Previous sync interrupted; lease expired', lease_hash=NULL, lease_until=NULL, updated=? "
                       "WHERE state IN ('preparing','downloading','verifying') AND lease_until<=?", (now, now))
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('preparing','downloading','verifying')").fetchone():
                return None
            row = next((r for r in db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,id")
                        if (job_id is None or r['id'] == job_id) and (systems is None or json.loads(r['plan'])['system'] in systems)), None)
            if row is None:
                return None
            lease = secrets.token_urlsafe(24)
            db.execute("UPDATE jobs SET state='downloading', attempts=attempts+1, lease_hash=?, lease_until=?, updated=?, error=NULL WHERE id=?",
                       (token_hash(lease), now + LEASE_SECONDS, now, row["id"]))
            job = self.public_job(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone(), source=True)
            return {**job, "lease": lease, "lease_seconds": LEASE_SECONDS}

    def report(self, job_id, lease, state, downloaded, error=None):
        if state not in ("preparing", "downloading", "verifying", "completed", "failed"):
            raise MisterError("Invalid job state")
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=? AND device_id='mister'", (job_id,)).fetchone()
            if row is None:
                raise MisterError("Job not found", 404)
            if not row["lease_hash"] or not secrets.compare_digest(row["lease_hash"], token_hash(lease)):
                raise MisterError("Job claim is no longer valid", 409)
            if row["state"] in ("completed", "failed"):
                if state != row["state"]:
                    raise MisterError("Job already finished", 409)
                return self.public_job(row)
            if row["lease_until"] <= now:
                raise MisterError("Job claim expired; sync again", 409)
            size = json.loads(row["plan"])["size"]
            if state in ('verifying', 'completed') and size <= 0:
                raise MisterError('Archive preparation must finish before verification')
            if not 0 <= downloaded <= size or (state == "completed" and (downloaded != size or row["state"] != "verifying")):
                raise MisterError("Invalid progress or completion before verification")
            if row["state"] == "verifying" and state in ("preparing", "downloading"):
                raise MisterError("Verification cannot transition back to downloading", 409)
            if state == "failed" and downloaded == 0:
                downloaded = row["downloaded"]
            db.execute("UPDATE jobs SET state=?, downloaded=?, error=?, updated=?, lease_until=? WHERE id=?",
                       (state, downloaded, error, now, now + LEASE_SECONDS, job_id))
            db.execute("UPDATE device SET last_seen=? WHERE id='mister'", (now,))
            return self.public_job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def retry(self, job_id):
        with self.connect() as db:
            result = db.execute("UPDATE jobs SET state='queued', attempts=0, error=NULL, lease_hash=NULL, lease_until=NULL, updated=? WHERE id=? AND state='failed'",
                                (self.clock(), job_id))
            if not result.rowcount:
                raise MisterError("Only failed jobs can be retried", 409)

