"""Preview shares the real UI while isolating transfers and persistent state."""
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from core.mister_sources import GENESIS_GUID
from mister.catalog_cache import CatalogCache
from mister.roms_manager_client import ClientError
from mister.preview import PreviewApi, PreviewTransfer, PreviewSession, find_dialog, make_handler


class PreviewCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        entry = {'name': 'Preview cartridge', 'rom_name':'Preview cartridge.md', 'size':512,
                 'sha256':'a'*64, 'http_url':'https://provider.invalid/game.md'}
        entry,_ = CatalogCache.compact(entry, GENESIS_GUID)
        payload = json.dumps(entry).encode()
        stem = 'a'*64+'.'+'b'*32
        (self.root/(stem+'.jsonl')).write_bytes(payload)
        (self.root/(stem+'.index.json')).write_text(json.dumps([[entry['mister']['entry_key'],'preview cartridge','preview cartridge',True,0,len(payload)]]))
        self.manifest = self.root/'catalog.json'
        self.manifest.write_text(json.dumps({'state':{'user':{'id':'preview','name':'Preview','allowed_console_guids':[GENESIS_GUID]}, 'network':'Local snapshot'},
            'catalogs':{'sega':{'guid':GENESIS_GUID,'metadata':{'slug':'sega','guid':GENESIS_GUID,'console':'Mega Drive / Genesis','entry_count':1},
                               'entries':stem+'.jsonl','index':stem+'.index.json','count':1,'complete':True}}}))
        self.key = entry['mister']['entry_key']
        self.api = PreviewApi(self.root)

    def enqueue(self):
        return self.api.request('POST','/client/downloads',{'dataset':'sega','entry_key':self.key})['job']

    def test_browse_uses_snapshot_and_transfers_never_use_network_or_change_cache(self):
        before = {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir()}
        with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Preview cannot download')):
            page = self.api.get('/roms/sega?limit=5&offset=0')
            self.assertEqual(page['total'],1)
            first = self.enqueue()
            self.assertEqual(self.enqueue()['id'],first['id'])
            events = []
            with patch('mister.preview.time.sleep'):
                self.assertEqual(PreviewTransfer(self.api, events.append).sync(),0)
            self.assertEqual(self.api.jobs[0]['state'],'completed')
            self.assertEqual(events[-1]['state'],'completed')
        after = {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir()}
        self.assertEqual(before,after)
        self.assertFalse(PreviewApi(self.root).jobs,'Queue must not persist across preview sessions')

    def test_scrolling_index_preserves_titles_and_assignment_boundaries(self):
        result = self.api.library('sega')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['entries'][0]['title'], 'Preview cartridge')
        self.assertEqual(set(result['entries'][0]), {'key','title','supported'})
        self.assertEqual(self.api.library('sega', query='not present')['total'], 0)
        self.assertEqual(self.api.library('sega', available_only=True)['total'], 1)
        manifest = json.loads(self.manifest.read_text())
        manifest['state']['user']['allowed_console_guids'] = []
        self.manifest.write_text(json.dumps(manifest))
        with self.assertRaises(ClientError):
            PreviewApi(self.root).library('sega')

    def test_preview_preserves_device_compatibility_without_host_extractor(self):
        catalog = self.api.cache.catalog('sega')
        path = self.root/catalog['entries']
        entry = json.loads(path.read_text())
        entry.update(name='Preview archive.7z', http_url='https://provider.invalid/game.7z')
        payload = json.dumps(entry).encode()
        path.write_bytes(payload)
        index = json.loads((self.root/catalog['index']).read_text())
        index[0][5] = len(payload)
        (self.root/catalog['index']).write_text(json.dumps(index))
        with patch('core.download_preparation.SEVEN_ZIP',None):
            self.assertTrue(self.api.get('/roms/sega')['entries'][0]['mister']['supported'])
            self.assertEqual(self.enqueue()['state'],'queued')

    def test_unknown_and_unsupported_entries_never_enter_preview_queue(self):
        with self.assertRaises(ClientError):
            self.api.request('POST','/client/downloads',{'dataset':'sega','entry_key':'f'*64})
        self.assertEqual(self.api.jobs,[])
        with self.assertRaises(ClientError):
            self.api.get('/not-supported')

    def test_http_inputs_require_launch_token_and_same_origin(self):
        from unittest.mock import Mock
        session = Mock()
        server = ThreadingHTTPServer(('127.0.0.1',0),make_handler(session,'test-session-token'))
        worker = threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),worker.join()))
        base = 'http://127.0.0.1:%d' % server.server_port
        for headers in ({}, {'X-Preview-Token':'wrong'}, {'X-Preview-Token':'test-session-token','Origin':'http://elsewhere.invalid'}):
            with self.assertRaises(HTTPError) as result:
                urlopen(Request(base+'/input',data=b'{"key":"enter"}',headers=headers),timeout=2)
            self.assertEqual(result.exception.code,403)
            result.exception.close()
        session.input.assert_not_called()
        with urlopen(Request(base+'/input',data=b'{"key":"enter"}',headers={'X-Preview-Token':'test-session-token','Origin':base}),timeout=2) as response:
            self.assertEqual(response.status,200)
        session.input.assert_called_once_with('\n')

    def test_real_dialog_renders_and_navigates_both_geometries(self):
        try:
            import pyte
            executable = find_dialog()
        except (ImportError, ClientError):
            self.skipTest('Host preview tools are optional')
        session = PreviewSession(self.root,executable)
        self.addCleanup(session.close)
        def wait_for(text):
            deadline = time.monotonic()+6
            while time.monotonic()<deadline:
                screen = session.snapshot_screen()
                plain = '\n'.join(''.join(run['text'] for run in row) for row in screen['rows'])
                if text in plain:
                    return screen
                time.sleep(.05)
            self.fail('Real dialog did not show '+text+'\n'+plain)
        first = wait_for('Browse games')
        self.assertEqual((first['columns'],first['lines']),(53,22))
        session.input('\n')
        wait_for('Mega Drive / Genesis')
        session.input('\x1b')
        wait_for('Browse games')
        session.restart('desktop')
        second = wait_for('Browse games')
        self.assertEqual((second['columns'],second['lines']),(160,45))
