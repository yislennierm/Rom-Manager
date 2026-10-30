"""Offline client snapshots and indexed browsing using the normal sync protocol."""
import hashlib
import fcntl
import json
import os
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode

if __package__:
    from .local_state import JsonState, atomic_json
else:
    from local_state import JsonState, atomic_json

from core.mister_sources import SYSTEM_GUIDS, compatibility
if __package__:
    from .roms_manager_client import AccessDenied, BackendUnavailable, ClientError
else:
    from roms_manager_client import AccessDenied, BackendUnavailable, ClientError


def cache_identity(config):
    return hashlib.sha256((config['backend_url'].rstrip('/') + '\n' + config['device_token']).encode()).hexdigest()


class CatalogCache(JsonState):
    def __init__(self, path, library=None):
        self.library = library
        super().__init__(path)
        self.sync_lock = threading.Lock()
        self.indexes = {}
        self.title_indexes = {}

    def get(self, key, default=None):
        with self.transaction() as data:
            return data.get('state', {}).get(key, default)

    def set(self, key, value):
        with self.transaction() as data:
            data.setdefault('state', {})[key] = value

    def user(self):
        user = self.get('user')
        if not user:
            raise ClientError('No account/catalog snapshot is cached yet. Choose Refresh catalog when the backend is reachable.')
        return user

    def allowed(self):
        return {str(guid).lower() for guid in self.user().get('allowed_console_guids', [])}

    def can_use(self, guid):
        allowed = self.allowed()
        return '*' in allowed or str(guid).lower() in allowed

    def require_download(self, system=None):
        self.user()
        if self.get('denied', False):
            raise AccessDenied('Account access was denied at the last sync. Restore access and refresh the catalog before downloading.')
        if system and not self.can_use(SYSTEM_GUIDS[system]):
            raise AccessDenied('This console was removed from the synced account assignments.')

    def apply_manifest(self, manifest):
        user = manifest.get('user') or {}
        if user.get('admin') or not user.get('enabled', True) or not user.get('id'):
            raise AccessDenied('Use an enabled non-admin client account for MiSTer.')
        public = {key: user.get(key) for key in ('id', 'name', 'enabled', 'admin', 'allowed_console_guids')}
        if not isinstance(public['allowed_console_guids'], list):
            public['allowed_console_guids'] = [m['guid'] for m in manifest.get('modules', []) if m.get('guid')]
        allowed = {str(guid).lower() for guid in public['allowed_console_guids']}
        with self.transaction() as data:
            data.setdefault('state', {}).update(user=public, denied=False, contact=time.time())
            catalogs = data.setdefault('catalogs', {})
            for slug, catalog in list(catalogs.items()):
                if '*' not in allowed and catalog['guid'].lower() not in allowed:
                    del catalogs[slug]

    def catalogs(self):
        self.user()
        with self.transaction() as data:
            rows = list(data.get('catalogs', {}).values())
        return [{**row['metadata'], 'cached': True, 'cache_complete': row.get('complete', True), 'cached_count': row['count']}
                for row in rows if self.can_use(row['guid'])]

    def catalog(self, slug):
        with self.transaction() as data:
            catalog = data.get('catalogs', {}).get(slug)
        if not catalog or not self.can_use(catalog['guid']):
            raise ClientError('This assigned catalog has not been cached yet. Refresh the catalog when connected.')
        return catalog

    def index(self, catalog):
        filename = catalog['index']
        identity = (filename, catalog['count'])
        if identity not in self.indexes:
            index = json.loads((self.path.parent / filename).read_text())
            self.indexes = {key: value for key, value in self.indexes.items() if key[0].split('.')[0] != filename.split('.')[0]}
            self.indexes[identity] = index
        return self.indexes[identity]

    def read_entry(self, catalog, row):
        with (self.path.parent / catalog['entries']).open('rb') as source:
            source.seek(row[4])
            entry = json.loads(source.read(row[5]))
            key = (entry.get('mister') or {}).get('entry_key')
            entry['mister'] = compatibility(entry, catalog['guid'])
            if key:
                entry['mister']['entry_key'] = key
            return entry

    def page(self, slug, limit=5, offset=0, query='', available_only=False):
        catalog = self.catalog(slug)
        rows = [row for row in self.index(catalog) if (not available_only or row[3]) and query.casefold() in row[2]]
        return {'entries': [self.read_entry(catalog, row) for row in rows[offset:offset+limit]],
                'total': len(rows), 'limit': limit, 'offset': offset, 'has_more': offset+limit < len(rows), 'cached': True}

    def title_list(self, slug, query='', available_only=False):
        """Thin cached titles for a scrolling browser; no provider records retained."""
        catalog = self.catalog(slug)
        rows = self.index(catalog)
        identity = (catalog['index'], catalog['count'])
        if identity not in self.title_indexes:
            names = {row[0]: row[6] for row in rows if len(row) > 6}
            # Older snapshots lack a display-case title. Read those records in
            # file order with one handle; only titles survive this migration.
            missing = sorted((row for row in rows if row[0] not in names), key=lambda row: row[4])
            if missing:
                with (self.path.parent / catalog['entries']).open('rb') as source:
                    for row in missing:
                        source.seek(row[4])
                        entry = json.loads(source.read(row[5]))
                        names[row[0]] = str(entry.get('game_title') or entry.get('name') or entry.get('rom_name') or 'Untitled')
            self.title_indexes = {key: value for key, value in self.title_indexes.items()
                                  if key[0].split('.')[0] != identity[0].split('.')[0]}
            self.title_indexes[identity] = names
        names = self.title_indexes[identity]
        query = query.casefold()
        entries = [{'key': row[0], 'title': names[row[0]], 'supported': bool(row[3])}
                   for row in rows if query in row[2] and (not available_only or row[3])]
        return {'entries': entries, 'total': len(entries), 'catalog_total': len(rows)}

    def entry(self, slug, key):
        catalog = self.catalog(slug)
        row = next((row for row in self.index(catalog) if row[0] == key), None)
        if row is None:
            raise ClientError('Game is not in the current assigned catalog cache. Refresh the catalog.')
        return catalog['guid'], self.read_entry(catalog, row)

    def links(self):
        with self.transaction() as data:
            return [{'server_id': key, 'local_id': value} for key, value in data.get('web_jobs', {}).items()]

    def link(self, server_id, local_id=None):
        with self.transaction() as data:
            links = data.setdefault('web_jobs', {})
            if local_id:
                links[server_id] = local_id
            else:
                links.pop(server_id, None)

    @staticmethod
    def compact(entry, guid):
        fields = ('_key', 'name', 'game_title', 'rom_name', 'size', '_size_bytes', 'md5', 'sha1', 'sha256',
                  'crc32', 'crc', 'serial', 'region', 'http_url', 'torrent_url', 'requires_auth',
                  '_archive_member', '_archive_member_path', 'archive_member_path', '_source_bundle_size')
        data = {key: entry[key] for key in fields if key in entry}
        if entry.get('headers'):
            data['requires_auth'] = True
        if entry.get('_providers'):
            data['_providers'] = [{**{key: provider[key] for key in ('provider_id', 'provider_label', 'metadata') if key in provider}, 'rom': {key: provider['rom'][key] for key in fields if key in provider.get('rom', {})}}
                                  for provider in entry['_providers'] if isinstance(provider, dict) and isinstance(provider.get('rom'), dict)]
            for provider, original in zip(data['_providers'], [p for p in entry['_providers'] if isinstance(p, dict) and isinstance(p.get('rom'), dict)]):
                if original['rom'].get('headers'):
                    provider['rom']['requires_auth'] = True
        data['mister'] = compatibility(data, guid)
        remote_key = (entry.get('mister') or {}).get('entry_key')
        if isinstance(remote_key, str) and len(remote_key) == 64:
            data['mister']['entry_key'] = remote_key
        search = ' '.join(str(entry.get(key) or '') for key in ('name', 'game_title', 'rom_name', 'region', 'serial', 'sha1', 'md5', 'crc32')).casefold()
        return data, search

    def sync(self, api, observer=None, stop=None):
        if not self.sync_lock.acquire(blocking=False):
            return {'busy': True}
        process_lock = self.lock_path.with_suffix('.sync.lock').open('a')
        try:
            fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            process_lock.close()
            self.sync_lock.release()
            return {'busy': True}
        try:
            manifest = api.get('/client/sync')
            self.apply_manifest(manifest)
            if self.library:
                self.library.sync(api, manifest)
            metadata = api.get('/roms').get('roms', [])
            synchronized = 0
            for meta in metadata:
                if stop and stop.is_set():
                    return {'cancelled': True}
                guid, slug = str(meta.get('guid') or '').lower(), meta.get('slug')
                if not slug or not self.can_use(guid):
                    continue
                signature_fields = {key: meta.get(key) for key in ('fetched_at', 'entry_count', 'catalog_total', 'source_kind')}
                if self.library and self.library.lookup(meta):
                    signature_fields['providers'] = self.library.signature
                signature = json.dumps(signature_fields, sort_keys=True)
                with self.transaction() as data:
                    old = data.get('catalogs', {}).get(slug)
                if old and old.get('complete', True) and old['signature'] == signature:
                    continue
                with self.transaction() as data:
                    pending = data.get('pending', {}).get(slug)
                if pending and pending['signature'] == signature and (self.path.parent / pending['entries']).is_file() and (self.path.parent / pending['index']).is_file():
                    entries_path, index_path = self.path.parent / pending['entries'], self.path.parent / pending['index']
                    index = json.loads(index_path.read_text())
                    offset = pending['offset']
                    # A crash may leave bytes beyond the last indexed checkpoint.
                    committed_end = max((row[4] + row[5] for row in index), default=0)
                    with entries_path.open('r+b') as output:
                        output.truncate(committed_end)
                else:
                    stem = hashlib.sha256(slug.encode()).hexdigest() + '.' + uuid.uuid4().hex
                    entries_path = self.path.parent / (stem + '.jsonl')
                    index_path = self.path.parent / (stem + '.index.json')
                    offset, index = 0, []
                keys = {row[0] for row in index}
                with entries_path.open('ab') as output:
                    while True:
                        if stop and stop.is_set():
                            return {'cancelled': True}
                        params = urlencode({'limit': 500, 'offset': offset, 'sort': 'name', 'include_artwork': 'false'})
                        page = api.get('/roms/%s?%s' % (quote(slug, safe=''), params))
                        rows = page.get('entries') or page.get('roms') or []
                        fetched_count = len(rows)
                        if self.library:
                            rows = self.library.match(rows, meta, offset)
                        for entry in rows:
                            compact, search = self.compact(entry, guid)
                            key = compact['mister']['entry_key']
                            if key in keys:
                                continue
                            keys.add(key)
                            packed = (json.dumps(compact, separators=(',', ':')) + '\n').encode()
                            index.append([key, str(compact.get('game_title') or compact.get('name') or '').casefold(), search,
                                          compact['mister']['supported'], output.tell(), len(packed),
                                          str(compact.get('game_title') or compact.get('name') or compact.get('rom_name') or 'Untitled')])
                            output.write(packed)
                        offset += fetched_count
                        output.flush()
                        os.fsync(output.fileno())
                        atomic_json(index_path, sorted(index, key=lambda row: (row[1], row[0])))
                        checkpoint = {'guid': guid, 'metadata': meta, 'signature': signature,
                            'entries': entries_path.name, 'index': index_path.name, 'count': len(index), 'offset': offset, 'complete': False}
                        with self.transaction() as data:
                            data.setdefault('pending', {})[slug] = checkpoint
                            if not old or not old.get('complete', True):
                                data.setdefault('catalogs', {})[slug] = checkpoint
                        if observer:
                            observer({'state': 'syncing catalog', 'filename': meta.get('console') or slug,
                                      'downloaded': offset, 'total': page.get('catalog_total') or page.get('total') or offset})
                        if not page.get('has_more'):
                            break
                        if not fetched_count:
                            raise ClientError('Catalog sync returned an empty intermediate page; previous cache retained.')
                    output.flush()
                    os.fsync(output.fileno())
                index.sort(key=lambda row: (row[1], row[0]))
                atomic_json(index_path, index)
                with self.transaction() as data:
                    data.setdefault('catalogs', {})[slug] = {'guid': guid, 'metadata': meta, 'signature': signature,
                        'entries': entries_path.name, 'index': index_path.name, 'count': len(index), 'complete': True}
                    data.setdefault('pending', {}).pop(slug, None)
                synchronized += 1
            with self.transaction() as data:
                current = {m.get('slug') for m in metadata}
                for slug in list(data.get('catalogs', {})):
                    if slug not in current:
                        del data['catalogs'][slug]
            self.set('synced', time.time())
            self.set('network', 'online')
            return {'catalogs': len(self.catalogs()), 'updated': synchronized}
        except AccessDenied:
            self.set('denied', True)
            self.set('network', 'access denied')
            raise
        except BackendUnavailable:
            self.set('network', 'offline — using cached data')
            raise
        finally:
            process_lock.close()
            self.sync_lock.release()
