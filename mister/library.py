"""The desktop TUI's catalog matching with snapshots from the normal client APIs."""
import json
import re
import zipfile
from pathlib import Path
from utils.catalog import _build_provider_lookup, match_catalog_entries, _is_rdb_rom_entry
if __package__:
    from .local_state import atomic_json
else:
    from local_state import atomic_json


def slug(value):
    return re.sub(r'[^a-z0-9]+', '_', str(value).lower()).strip('_') or 'default'


class SyncedLibrary:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / 'providers.json'
        self.state = json.loads(path.read_text()) if path.exists() else {}
        self.lookups = {}

    def sync(self, api, manifest):
        datasets = manifest.get('datasets') or {}
        cache = datasets.get('cache') or {}
        if not cache.get('count'):
            return
        signature = json.dumps([datasets.get('providers', {}).get('version'), cache.get('version'), cache.get('count')])
        if self.state.get('signature') == signature:
            return
        registry = api.get('/update?target=providers').get('providers') or {}
        # The archive is already filtered to this account by the same endpoint
        # used by the desktop client. Only configured provider JSON is read.
        api.fetch_cache_archive(self.root / 'providers.zip')
        self.state = {'signature': signature, 'registry': registry}
        atomic_json(self.root / 'providers.json', self.state)
        self.lookups.clear()

    @property
    def signature(self):
        signature = self.state.get('signature')
        return 'tui-catalog-2:' + signature if signature else None

    def lookup(self, meta):
        guid = str(meta.get('guid') or '').lower()
        if guid in self.lookups:
            return self.lookups[guid]
        root = (self.state.get('registry') or {}).get('console_root') or {}
        catalogs = []
        archive_path = self.root / 'providers.zip'
        if archive_path.is_file():
            with zipfile.ZipFile(archive_path) as archive:
                for manufacturer, consoles in root.items():
                    for console, configured in consoles.items():
                        items = configured if isinstance(configured, list) else [configured]
                        for item in items:
                            if not isinstance(item, dict) or str(item.get('libretro_guid') or item.get('guid') or '').lower() != guid:
                                continue
                            provider_id = slug(item.get('archive_id') or item.get('provider') or item.get('name'))
                            prefix = '%s/%s/%s/exports/' % (slug(manufacturer), slug(console), provider_id)
                            for info in archive.infolist():
                                if not info.filename.startswith(prefix) or not info.filename.endswith('.json') or info.file_size > 64*1024*1024:
                                    continue
                                payload = json.loads(archive.read(info))
                                entries = payload.get('roms') if isinstance(payload, dict) else payload
                                if isinstance(entries, list):
                                    catalogs.append({'id': provider_id, 'label': item.get('provider') or item.get('name') or provider_id,
                                        'metadata': {**(payload if isinstance(payload, dict) else {}), **item}, 'roms': entries})
        lookup = _build_provider_lookup(catalogs) if catalogs else None
        self.lookups[guid] = lookup
        return lookup

    def match(self, entries, meta, offset=0):
        entries = [entry for entry in entries if _is_rdb_rom_entry(entry)]
        lookup = self.lookup(meta)
        if not lookup:
            return entries
        brand = meta.get('brand') or str(meta.get('module') or '').split(' - ', 1)[0]
        console = meta.get('console') or str(meta.get('module') or '').split(' - ', 1)[-1]
        matched = match_catalog_entries(entries, [], brand, console, offset=offset, lookup=lookup)
        for original, merged in zip(entries, matched):
            # Backend catalog identity stays stable across local enrichment.
            merged['mister'] = original.get('mister') or {}
        return matched
