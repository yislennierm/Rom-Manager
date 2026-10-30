"""MiSTer uses the desktop TUI catalog engine, with platform paths kept separate."""
import builtins
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.mister_sources import GENESIS_GUID, SNES_GUID, NES_GUID, prepare_rom, compatibility, entry_key
from mister.library import SyncedLibrary
from mister.catalog_cache import CatalogCache
from mister.roms_manager_ui import MisterUI
from test_mister_ui import FakeApi, FakeDialog, CATALOG, NES_CATALOG
from utils.catalog import match_catalog_entries, select_preferred_provider


class LibraryCase(unittest.TestCase):
    def test_portable_catalog_import_needs_no_desktop_runtime_dependencies(self):
        script = '''import builtins
original=builtins.__import__
def restricted(name,*args,**kwargs):
 if name.split('.')[0] in ('requests','msgpack','textual','sqlite3','libtorrent'): raise AssertionError(name)
 return original(name,*args,**kwargs)
builtins.__import__=restricted
import utils.catalog
import mister.offline_client
'''
        subprocess.run([sys.executable, '-c', script], check=True)

    def test_mega_drive_uses_existing_tui_provider_preference_and_safe_mapping(self):
        sources = [
            {'provider_id': 'bundle', 'rom': {'name': 'Sonic.md', 'http_url': 'https://source/Sonic.md', 'size': 512, 'sha256': 'a'*64, '_archive_member': False}},
            {'provider_id': 'unplayable', 'metadata': {'runtime_playable': False}, 'rom': {'name': 'Sonic.md', 'http_url': 'https://source/blocked.md', 'size': 512, 'sha256': 'a'*64}},
            {'provider_id': 'no_intro', 'rom': {'name': 'Sonic.md', 'http_url': 'https://source/preferred.md', 'size': 512, 'sha256': 'b'*64}},
        ]
        entry = {'name': 'Sonic', '_providers': sources}
        expected = select_preferred_provider(sources)['rom']['http_url']
        plan = prepare_rom(entry, GENESIS_GUID)
        self.assertEqual(plan['source_url'], expected)
        self.assertEqual(plan['system'], 'genesis')
        self.assertEqual(plan['core'], 'MegaDrive')
        self.assertEqual(plan['destination'], 'ROMs Manager/Sonic.md')
        for suffix in ('.smd', '.cue'):
            self.assertFalse(compatibility({'name': 'bad'+suffix, 'http_url': 'https://source/bad'+suffix, 'size': 512, 'sha256': 'b'*64}, GENESIS_GUID)['supported'])

    def test_synced_provider_export_matches_identically_to_desktop_and_keeps_server_key(self):
        source = {'name': 'Sonic The Hedgehog (USA, Europe).md', 'http_url': 'https://source/Sonic.md', 'size': 512, 'sha256': 'a'*64, 'md5': 'b'*32}
        rdb = {'name': 'Sonic The Hedgehog (USA, Europe)', 'md5': 'b'*32, 'size': 512}
        remote_key = entry_key(rdb)
        registry = {'console_root': {'Sega': {'Mega Drive - Genesis': [{'archive_id': 'nointro.md', 'libretro_guid': GENESIS_GUID}]}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = {'id': 'nointro_md', 'label': 'nointro_md', 'roms': [source], 'metadata': registry['console_root']['Sega']['Mega Drive - Genesis'][0]}
            with zipfile.ZipFile(root / 'providers.zip','w') as archive:
                archive.writestr('sega/mega_drive_genesis/nointro_md/exports/sega_mega_drive_genesis_nointro_md_roms.json', json.dumps([source]))
            (root/'providers.json').write_text(json.dumps({'registry': registry, 'signature': 'cached'}))
            library = SyncedLibrary(root)
            meta = {'guid': GENESIS_GUID, 'brand': 'Sega', 'console': 'Mega Drive - Genesis'}
            actual = library.match([{**rdb, 'mister': {'entry_key': remote_key}}], meta)[0]
            expected = match_catalog_entries([rdb],[catalog], 'Sega','Mega Drive - Genesis')[0]
            self.assertEqual(actual['_providers'],expected['_providers'])
            compact,_ = CatalogCache.compact(actual,GENESIS_GUID)
            self.assertTrue(compact['mister']['supported'])
            self.assertEqual(compact['mister']['entry_key'],remote_key)
            self.assertEqual(prepare_rom(compact,GENESIS_GUID)['source_url'],source['http_url'])

    def test_retail_selection_does_not_download_beta_or_collection_variant(self):
        base = {'name':'Sonic The Hedgehog (USA, Europe)', 'size':512}
        names = ['Sonic The Hedgehog (USA, Europe) (Beta).zip',
                 'Sonic The Hedgehog (USA, Europe) (Sonic Classic Collection).zip',
                 'Sonic The Hedgehog (USA, Europe).zip']
        providers = [{'provider_id':'nointro', 'rom':{'name':name, 'http_url':'https://source/'+name.replace(' ','%20'), 'size':512,'sha256':str(i+1)*64}}
                     for i,name in enumerate(names)]
        planned = prepare_rom({**base,'_providers':providers}, GENESIS_GUID)
        self.assertEqual(planned['_preparation']['url'],providers[-1]['rom']['http_url'])
        from utils.catalog import _provider_name_compatible
        self.assertFalse(_provider_name_compatible(base,names[0]))
        self.assertFalse(_provider_name_compatible(base,names[1]))
        self.assertTrue(_provider_name_compatible({'name':base['name']+' (Beta)'},names[0]))
        from utils.catalog import select_archive_rom_name
        roms = [name.replace('.zip','.md') for name in names]
        self.assertEqual(select_archive_rom_name(base['name'],roms),roms[-1])
        self.assertEqual(select_archive_rom_name(base['name']+' (Beta)',roms),roms[0])

    def test_backend_guid_lookup_prefers_real_master_over_demo(self):
        from backend.app import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            demo, real = root/'mister_snes_demo.json', root/'nintendo_snes.json'
            for path, title in ((demo,'Demo'),(real,'Real catalog')):
                path.write_text(json.dumps({'guid':SNES_GUID,'module':title,'entries':[{'name':title}]}))
            with patch.object(main,'_iter_rom_files',return_value=[demo,real]):
                self.assertEqual(main._load_rom_dataset(SNES_GUID)['module'],'Real catalog')
                self.assertEqual(main._load_rom_dataset('mister_snes_demo')['module'],'Demo')

    def test_assigned_consoles_open_real_libraries_directly_instead_of_demos(self):
        class Api(FakeApi):
            def get(self,path):
                if path == '/client/downloads/targets':
                    return {'targets':[{'id':'mister','system_guids':{'snes':SNES_GUID,'nes':NES_GUID,'genesis':GENESIS_GUID}}]}
                return super().get(path)
        sega = {**CATALOG, 'slug':'sega', 'guid':GENESIS_GUID, 'console':'Mega Drive / Genesis'}
        for guid, title, slug in ((SNES_GUID,'SNES','snes'),(NES_GUID,'NES','nes'),(GENESIS_GUID,'Mega Drive / Genesis','sega')):
            api = Api();api.assigned = [SNES_GUID,NES_GUID,GENESIS_GUID]
            api.catalogs = [{**CATALOG,'slug':'mister_snes_demo','entry_count':2},
                            {**NES_CATALOG,'slug':'mister_nes_demo','entry_count':1}, CATALOG,NES_CATALOG,sega]
            dialog = FakeDialog(menus=[guid,None,None])
            ui = MisterUI({},dialog,api=api)
            ui.browse()
            self.assertEqual([menu[0] for menu in dialog.menus],['Consoles',title+' games','Consoles'])
            self.assertNotIn('browse only',dict(dialog.menus[0][2])[guid])
            calls = [path for _,path,_ in api.calls if path.startswith('/roms/')]
            self.assertTrue(all(path.startswith('/roms/'+slug+'?') for path in calls))
            self.assertIn('All catalog games',dialog.menus[1][1])
