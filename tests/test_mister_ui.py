"""Scripted dialog interactions: no terminal or MiSTer is modified."""
import io
import subprocess
import unittest
import tempfile
import json
from pathlib import Path
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

from mister.roms_manager_client import ApiError, ClientError
from mister.roms_manager_ui import Dialog, MisterUI, PAGE_SIZE, SNES_GUID, NES_GUID, clean_text, run_ui, load_display_mode, save_display_mode


ENTRY = {'name': 'Original Demo', 'rom_name': 'Demo.sfc', 'size': 32768,
         'http_url': 'https://source.invalid/private.sfc?secret=hidden',
         'mister': {'supported': True, 'entry_key': 'a' * 64, 'size': 32768, 'format': 'SFC'}}
CATALOG = {'slug': 'snes', 'guid': SNES_GUID, 'console': 'SNES', 'source_label': 'Homebrew', 'entry_count': 13}
NES_CATALOG = {**CATALOG, 'slug': 'nes', 'guid': NES_GUID, 'console': 'NES'}
JOB = {'id': 'b' * 32, 'title': 'Original Demo', 'destination': 'ROMs Manager/Demo.sfc',
       'state': 'failed', 'size': 32768, 'downloaded': 120, 'error': 'Checksum verification failed'}


class FakeDialog:
    def __init__(self, menus=(), answers=(), inputs=()):
        self.choices, self.answers, self.inputs = deque(menus), deque(answers), deque(inputs)
        self.menus, self.messages, self.questions, self.events = [], [], [], []
        self.started, self.stopped = 0, 0

    def menu(self, title, text, items, default=None):
        self.menus.append((title, text, items))
        if not self.choices:
            raise AssertionError('Unexpected menu: ' + title)
        choice = self.choices.popleft()
        if choice is not None and choice not in dict(items):
            raise AssertionError('Nonexistent selection %s in %s' % (choice, title))
        return choice

    def message(self, title, text):
        self.messages.append((title, text))

    def yesno(self, title, text):
        self.questions.append((title, text))
        if not self.answers:
            raise AssertionError('Unexpected confirmation: ' + title)
        return self.answers.popleft()

    def input(self, *args):
        return self.inputs.popleft()

    def start_gauge(self):
        self.started += 1

    def progress(self, event):
        self.events.append(event)

    def stop_gauge(self):
        self.stopped += 1


class FakeApi:
    def __init__(self):
        self.calls = []
        self.job_state = 'queued'
        self.fail_catalog = False
        self.assigned = [SNES_GUID]
        self.catalogs = [CATALOG, {'slug': 'unrelated', 'guid': 'other-console'}]

    def get(self, path):
        self.calls.append(('GET', path, None))
        url = urlsplit(path)
        if url.path == '/client/downloads/targets':
            if self.fail_catalog:
                raise ApiError('Backend unavailable. Try again after checking the connection.')
            return {'targets': [{'id': 'mister', 'system_guids': {system: guid for system, guid in
                {'snes': SNES_GUID, 'nes': NES_GUID}.items() if guid in self.assigned}}]}
        if url.path == '/roms':
            return {'roms': self.catalogs}
        if url.path.startswith('/roms/'):
            params = parse_qs(url.query)
            offset = int(params.get('offset', [0])[0])
            count = min(PAGE_SIZE, max(0, 13 - offset))
            return {'entries': [{**ENTRY, 'name': 'Demo%d' % i} for i in range(offset, offset+count)],
                    'total': 13, 'has_more': offset + count < 13}
        if url.path == '/client/downloads':
            return {'jobs': [JOB], 'total': 1, 'has_more': False}
        if url.path == '/me':
            return {'user': {'id': 'mister-fpga', 'name': 'MiSTer', 'allowed_console_guids': self.assigned}}
        raise AssertionError('Unexpected request: ' + path)

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == '/client/downloads':
            return {'job': {**JOB, 'state': self.job_state}}
        if path.startswith('/client/downloads/') and '/retry?' in path:
            return {'queued': True}
        raise AssertionError(path)


class FakeClient:
    def __init__(self, config, api, observer=None):
        self.observer = observer

    def sync(self):
        self.observer({'kind': 'start', 'filename': 'ROMs Manager/Demo.sfc', 'total': 32768})
        self.observer({'kind': 'progress', 'filename': 'ROMs Manager/Demo.sfc', 'state': 'downloading', 'downloaded': 16384, 'total': 32768})
        self.observer({'kind': 'progress', 'filename': 'ROMs Manager/Demo.sfc', 'state': 'completed', 'downloaded': 32768, 'total': 32768})
        return 0

    def telemetry(self):
        return {'games_root': '/media/fat/games/SNES', 'free_bytes': 1024**3, 'core_present': True}


class NavigationCase(unittest.TestCase):
    def test_downloadable_filter_is_server_side_and_can_be_toggled(self):
        dialog = FakeDialog(menus=['availability', None])
        ui, api = self.ui(dialog)
        ui.browse()
        pages = [parse_qs(urlsplit(path).query) for _, path, _ in api.calls if path.startswith('/roms/snes?')]
        self.assertNotIn('download_target', pages[0])
        self.assertEqual(pages[1]['download_target'], ['mister'])
        self.assertTrue(all(p['limit'] == [str(PAGE_SIZE)] for p in pages))

    def test_added_nes_assignment_appears_without_new_key_or_client_config(self):
        api = FakeApi()
        api.catalogs.append(NES_CATALOG)
        dialog = FakeDialog(menus=[NES_GUID, None, None])
        ui, _ = self.ui(dialog, api)
        self.assertEqual([catalog['guid'] for catalog in ui.catalogs()], [SNES_GUID])
        api.assigned.append(NES_GUID)
        ui.browse()
        consoles = next(menu for menu in dialog.menus if menu[0] == 'Consoles')
        self.assertEqual(set(dict(consoles[2])), {SNES_GUID, NES_GUID})
        self.assertFalse(any(menu[0] == 'NES libraries' for menu in dialog.menus))
        self.assertTrue(any(menu[0] == 'NES games' for menu in dialog.menus))
        self.assertTrue(any(path.startswith('/roms/nes?') for _, path, _ in api.calls))
        api.assigned.remove(NES_GUID)
        self.assertEqual([catalog['guid'] for catalog in ui.catalogs()], [SNES_GUID])
        self.assertFalse(any(method == 'POST' for method, _, _ in api.calls))

    def test_assigned_unsupported_console_is_visible_as_browse_only(self):
        api = FakeApi()
        api.assigned.append('other-console')
        api.catalogs[1]['console'] = 'Other console'
        dialog = FakeDialog(menus=[None])
        ui, _ = self.ui(dialog, api)
        ui.browse()
        consoles = dict(dialog.menus[0][2])
        self.assertIn('browse only', consoles['other-console'])

    def test_only_unsupported_assignments_still_allow_authorized_catalog_browsing(self):
        api = FakeApi()
        api.assigned = ['other-console']
        api.catalogs[1]['console'] = 'Other console'
        dialog = FakeDialog(menus=['game:0', None])
        ui, _ = self.ui(dialog, api)
        ui.browse()
        self.assertEqual(dialog.messages[0][0], 'Unavailable')
        self.assertIn('installation mapping', dialog.messages[0][1])
        self.assertIn('All catalog games', dialog.menus[0][1])
        self.assertFalse(any(method == 'POST' for method, _, _ in api.calls))

    def test_nes_search_keeps_server_pagination_and_console_selection(self):
        api = FakeApi()
        api.assigned.append(NES_GUID)
        api.catalogs.append(NES_CATALOG)
        dialog = FakeDialog(menus=[NES_GUID, 'next', None, None], inputs=['NES demo'])
        ui, _ = self.ui(dialog, api)
        ui.search()
        pages = [parse_qs(urlsplit(path).query) for _, path, _ in api.calls if path.startswith('/roms/nes?')]
        self.assertEqual([p['offset'] for p in pages], [['0'], [str(PAGE_SIZE)]])
        self.assertTrue(all(p['q'] == ['NES demo'] for p in pages))
    def test_display_toggle_persists_only_presentation_and_never_calls_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ui_settings.json'
            dialog = FakeDialog(menus=['7', '6'])
            ui, api = self.ui(dialog)
            ui.settings_path = path
            ui.display = SimpleNamespace(mode='sofa', set_mode=Mock(), notice=None)
            self.assertEqual(ui.run(), 0)
            ui.display.set_mode.assert_called_once_with('desktop')
            self.assertEqual(json.loads(path.read_text()), {'display_mode': 'desktop'})
            self.assertEqual(load_display_mode(path), 'desktop')
            self.assertEqual(ui.page_size, 12)
            self.assertFalse(api.calls)
            self.assertIn('Desktop', dict(dialog.menus[-1][2])['7'])

    def test_invalid_display_preferences_fall_back_without_touching_account(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ui_settings.json'
            self.assertEqual(load_display_mode(path), 'sofa')
            for value in ('invalid JSON', '[]', '{"display_mode":"unknown"}'):
                path.write_text(value)
                self.assertEqual(load_display_mode(path), 'sofa')
            target = Path(directory) / 'account.json'
            target.write_text('untouched')
            path.unlink()
            path.symlink_to(target)
            with self.assertRaises(ClientError):
                save_display_mode(path, 'desktop')
            self.assertEqual(target.read_text(), 'untouched')

    def ui(self, dialog, api=None, client_factory=FakeClient):
        api = api or FakeApi()
        return MisterUI({'backend_url': 'https://backend.invalid', 'device_token': 'secret-key-never-display'},
                       dialog, api=api, client_factory=client_factory), api

    def test_exit_and_escape_make_no_network_or_download_requests(self):
        for choice in (None, '6'):
            dialog = FakeDialog(menus=[choice])
            ui, api = self.ui(dialog)
            self.assertEqual(ui.run(), 0)
            self.assertFalse(api.calls)

    def test_browse_cancel_confirmation_returns_to_previous_menus(self):
        dialog = FakeDialog(menus=['1', 'game:0', None, '6'], answers=[False])
        ui, api = self.ui(dialog)
        self.assertEqual(ui.run(), 0)
        self.assertFalse(any(method == 'POST' for method, _, _ in api.calls))
        self.assertTrue(any(menu[0] == 'SNES games' for menu in dialog.menus))
        self.assertFalse(any(menu[0] == 'SNES libraries' for menu in dialog.menus))

    def test_search_cancellation_and_blank_input_fetch_nothing(self):
        for query in (None, '  '):
            ui, api = self.ui(FakeDialog(inputs=[query]))
            ui.search()
            self.assertFalse(api.calls)

    def test_search_and_pagination_are_server_requests_with_small_pages(self):
        dialog = FakeDialog(menus=['next', 'prev', None], inputs=['Star & Moon'])
        ui, api = self.ui(dialog)
        ui.search()
        pages = [parse_qs(urlsplit(path).query) for _, path, _ in api.calls if path.startswith('/roms/')]
        self.assertEqual([p['offset'] for p in pages], [['0'], [str(PAGE_SIZE)], ['0']])
        self.assertTrue(all(p['limit'] == [str(PAGE_SIZE)] and p['q'] == ['Star & Moon'] for p in pages))

    def test_unsupported_game_explains_source_without_queueing(self):
        dialog = FakeDialog()
        ui, api = self.ui(dialog)
        ui.game_detail('snes', {**ENTRY, 'mister': {'supported': False, 'reason': 'Archive extraction is unsupported.'}})
        self.assertIn('Archive extraction', dialog.messages[0][1])
        self.assertFalse(api.calls)
        self.assertNotIn('secret=hidden', str(dialog.messages))

    def test_completed_duplicate_does_not_offer_another_download(self):
        dialog = FakeDialog(answers=[True])
        ui, api = self.ui(dialog)
        api.job_state = 'completed'
        ui.game_detail('snes', ENTRY)
        self.assertEqual(dialog.messages[0][0], 'Already completed')
        self.assertEqual(dialog.started, 0)
        self.assertEqual(len(dialog.questions), 1)
        self.assertTrue(all('/admin' not in path for _, path, _ in api.calls))

    def test_preparing_duplicate_does_not_start_another_sync(self):
        dialog = FakeDialog(answers=[True])
        ui, api = self.ui(dialog)
        api.job_state = 'preparing'
        ui.game_detail('snes', ENTRY)
        self.assertEqual(dialog.messages[0][0], 'Preparing')
        self.assertEqual(dialog.started, 0)
        self.assertEqual(len(dialog.questions), 1)

    def test_queue_and_sync_now_use_existing_engine_progress(self):
        dialog = FakeDialog(answers=[True, True])
        ui, api = self.ui(dialog)
        ui.game_detail('snes', ENTRY)
        self.assertEqual(dialog.started, 1)
        self.assertEqual(dialog.stopped, 1)
        self.assertTrue(any(e.get('downloaded') == 16384 for e in dialog.events))
        self.assertIn('1 ready\n0 need attention', dialog.messages[-1][1])
        request = next(call for call in api.calls if call[0] == 'POST')
        self.assertEqual(request[1], '/client/downloads')
        self.assertNotIn('source_url', request[2])

    def test_retry_failed_job_is_explicit_and_uses_client_endpoint(self):
        dialog = FakeDialog(menus=['job:0', 'back'], answers=[True])
        ui, api = self.ui(dialog)
        ui.queue()
        retry = [path for method, path, _ in api.calls if method == 'POST']
        self.assertEqual(retry, ['/client/downloads/%s/retry?target=mister' % JOB['id']])
        self.assertEqual(dialog.messages[-1][0], 'Retry queued')

    def test_cancelling_retry_never_posts(self):
        dialog = FakeDialog(menus=['job:0', None], answers=[False])
        ui, api = self.ui(dialog)
        ui.queue()
        self.assertFalse(any(method == 'POST' for method, _, _ in api.calls))

    def test_backend_outage_returns_to_main_menu(self):
        dialog = FakeDialog(menus=['1', '6'])
        ui, api = self.ui(dialog)
        api.fail_catalog = True
        self.assertEqual(ui.run(), 0)
        self.assertIn('Backend unavailable', dialog.messages[0][1])

    def test_status_does_not_display_api_key_or_source_url(self):
        dialog = FakeDialog()
        ui, _ = self.ui(dialog)
        ui.status()
        output = str(dialog.messages)
        self.assertIn('mister-fpga', output)
        self.assertIn('SNES core installed: Yes', output)
        self.assertNotIn('secret-key', output)
        self.assertNotIn('http', output)

    def test_gauge_is_closed_on_error_or_interrupt(self):
        for error in (ApiError('Account access revoked'), KeyboardInterrupt()):
            factory = Mock(return_value=Mock(sync=Mock(side_effect=error)))
            dialog = FakeDialog()
            ui, _ = self.ui(dialog, client_factory=factory)
            with self.assertRaises(type(error)):
                ui.sync()
            self.assertEqual(dialog.stopped, 1)


class DialogCase(unittest.TestCase):
    def test_scripts_console_uses_linux_term_without_changing_ssh_or_parent(self):
        for console, expected in (('/dev/tty2', 'linux'), ('/dev/pts/0', 'vt102')):
            stdin = SimpleNamespace(isatty=lambda: True, fileno=lambda: 0)
            stderr = io.StringIO()
            stderr.isatty = lambda: True
            with patch.dict('os.environ', {'TERM': 'vt102'}), \
                 patch('mister.roms_manager_ui.sys.stdin', stdin), \
                 patch('mister.roms_manager_ui.sys.stderr', stderr), \
                 patch('mister.roms_manager_ui.os.ttyname', return_value=console), \
                 patch('mister.roms_manager_ui.termios.tcgetattr', return_value=['saved']), \
                 patch('mister.roms_manager_ui.termios.tcsetattr'):
                with Dialog(executable='/usr/bin/dialog') as dialog:
                    self.assertEqual(dialog.environment['TERM'], expected)
                    import os
                    self.assertEqual(os.environ['TERM'], 'vt102')

    def test_controller_input_is_passed_only_to_the_current_dialog(self):
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout='1\n'))
        dialog = Dialog(runner=runner, executable='/usr/bin/dialog')
        keys = MagicMock()
        keys.__enter__.return_value.read_fd = 17
        dialog.input_factory = Mock(return_value=keys)
        self.assertEqual(dialog.menu('Games', '', [('1', 'Demo')]), '1')
        self.assertEqual(runner.call_args.kwargs['pass_fds'], (17,))
        self.assertEqual(runner.call_args.args[0][1:3], ['--input-fd', '17'])
        keys.__exit__.assert_called_once()

    def test_gauge_controller_is_cancel_only_and_released_before_exit(self):
        process = Mock(stdin=io.StringIO())
        dialog = Dialog(popen=Mock(return_value=process), executable='/usr/bin/dialog')
        keys = MagicMock()
        dialog.input_factory = Mock(return_value=keys)
        dialog.start_gauge()
        dialog.input_factory.assert_called_once_with(cancel_only=True)
        dialog.stop_gauge()
        keys.__exit__.assert_called_once()
        self.assertTrue(process.stdin.closed)

    def test_tv_menu_hides_internal_tags_and_uses_buttons_for_pagination(self):
        runner = Mock(return_value=SimpleNamespace(returncode=3, stdout=''))
        dialog = Dialog(runner=runner, executable='/usr/bin/dialog')
        items = [('game:0', 'Demo'), ('prev', 'Previous page'), ('next', 'Next page'), ('back', 'Back')]
        self.assertEqual(dialog.menu('Games', '1-5 of 20', items), 'next')
        command = runner.call_args.args[0]
        self.assertIn('--no-tags', command)
        self.assertIn('--no-shadow', command)
        self.assertIn('--extra-label', command)
        self.assertIn('Previous', command)
        self.assertNotIn('Previous page', command)
        self.assertNotIn('Next page', command)
        runner.return_value.returncode = 2
        self.assertEqual(dialog.menu('Games', '', items), 'prev')

    def test_every_widget_routes_display_to_terminal_when_stdout_is_captured(self):
        runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=''))
        dialog = Dialog(runner=runner, executable='/usr/bin/dialog')
        dialog.message('Status', 'Account status')
        dialog.yesno('Queue?', 'Confirm')
        dialog.input('Search', 'Game title')
        dialog.menu('Games', 'Select', [('1', 'Demo')])
        for call in runner.call_args_list:
            self.assertIn('--stdout', call.args[0])
            self.assertEqual(call.kwargs['stdout'], subprocess.PIPE)

    def test_missing_config_is_reported_inside_restored_dialog_context(self):
        dialog = MagicMock()
        with patch('mister.roms_manager_ui.Dialog', return_value=dialog), \
             patch('mister.roms_manager_ui.load_config', side_effect=FileNotFoundError('private path')):
            self.assertEqual(run_ui('missing.json'), 1)
        dialog.__exit__.assert_called_once()
        self.assertEqual(dialog.message.call_args.args[0], 'Setup required')
        self.assertNotIn('private path', str(dialog.message.call_args))

    def test_dialog_cancel_escape_and_safe_default(self):
        for code in (1, 255):
            runner = Mock(return_value=SimpleNamespace(returncode=code, stdout=''))
            dialog = Dialog(runner=runner, executable='/usr/bin/dialog')
            self.assertIsNone(dialog.menu('Title', 'Prompt', [('1', 'Game')]))
            self.assertFalse(dialog.yesno('Queue?', 'Confirm'))
            self.assertIn('--defaultno', runner.call_args.args[0])

    def test_missing_dialog_reports_clear_error(self):
        with patch('mister.roms_manager_ui.shutil.which', return_value=None), self.assertRaisesRegex(ClientError, 'dialog is not installed'):
            Dialog()

    def test_terminal_state_and_cursor_restored_after_interrupt(self):
        stderr = io.StringIO()
        stderr.isatty = lambda: True
        stdin = SimpleNamespace(isatty=lambda: True, fileno=lambda: 0)
        with patch('mister.roms_manager_ui.sys.stdin', stdin), patch('mister.roms_manager_ui.sys.stderr', stderr), \
             patch('mister.roms_manager_ui.termios.tcgetattr', return_value=['saved']), \
             patch('mister.roms_manager_ui.termios.tcsetattr') as restore, \
             patch('mister.roms_manager_ui.signal.getsignal', return_value=0), \
             patch('mister.roms_manager_ui.signal.signal') as handlers:
            with self.assertRaises(KeyboardInterrupt):
                with Dialog(executable='/usr/bin/dialog'):
                    raise KeyboardInterrupt
            self.assertEqual(restore.call_args.args[2], ['saved'])
            self.assertEqual(handlers.call_args.args[1], 0)
            self.assertIn('\033[?25h', stderr.getvalue())

    def test_gauge_protocol_and_child_cleanup(self):
        pipe = io.StringIO()
        process = Mock(stdin=pipe, poll=Mock(return_value=None))
        popen = Mock(return_value=process)
        dialog = Dialog(popen=popen, executable='/usr/bin/dialog')
        dialog.start_gauge()
        dialog.progress({'state': 'downloading', 'filename': 'Demo.sfc', 'downloaded': 50, 'total': 100})
        self.assertIn('XXX\n50\n', pipe.getvalue())
        self.assertIn('Demo.sfc', pipe.getvalue())
        self.assertNotIn('secret', str(popen.call_args))
        dialog.stop_gauge()
        self.assertTrue(pipe.closed)
        process.wait.assert_called_once_with(timeout=2)

    def test_control_sequences_urls_and_gauge_markers_are_sanitized(self):
        text = clean_text('--trace\x1b\x9b https://source.invalid/?token=hidden\nXXX\n')
        self.assertTrue(text.startswith(' --trace'))
        self.assertNotIn('\x1b', text)
        self.assertNotIn('\x9b', text)
        self.assertNotIn('token=', text)
        self.assertNotIn('\nXXX\n', text)


if __name__ == '__main__':
    unittest.main()

class ScrollingLibraryCase(unittest.TestCase):
    def test_selection_search_cancellation_and_filter(self):
        entries = [{'key': str(i), 'title': 'Game %d (USA)' % i, 'supported': True} for i in range(1000)]
        api = Mock()
        api.library.return_value = {'entries': entries, 'total': len(entries)}
        api.game_entry.return_value = ENTRY
        dialog = Mock()
        dialog.terminal_size.return_value = (53, 22)
        dialog.library.side_effect = ['game:850', 'search', 'filter', None]
        dialog.input.return_value = None
        ui = MisterUI({}, dialog, api=api)
        ui.download_guids = {SNES_GUID}
        ui.game_detail = Mock()
        ui.scrolling_games(CATALOG, '')
        self.assertEqual(len(dialog.library.call_args_list[0].args[2]), 1000)
        self.assertEqual(dialog.library.call_args_list[1].kwargs['default'], 'game:850')
        self.assertEqual(dialog.library.call_args_list[2].kwargs['default'], 'game:850')
        self.assertTrue(api.library.call_args_list[-1].kwargs['available_only'])
        api.game_entry.assert_called_once_with('snes', '850')
        ui.game_detail.assert_called_once_with('snes', ENTRY)
        api.request.assert_not_called()

    def test_library_response_file_is_temporary_and_keeps_real_newlines(self):
        dialog = Dialog(executable='/fake/dialog')
        dialog.terminal_size = lambda: (53,22)
        captured = []
        def run(command, navigation):
            self.assertEqual(command[:2], ['/fake/dialog', '--file'])
            path = Path(command[2])
            captured.append(path)
            text = path.read_text()
            self.assertIn('All games\nSearch: Sonic', text)
            self.assertIn('Game \\"quoted\\"', text)
            self.assertEqual(navigation, {3:'search',2:'filter'})
            return None
        dialog.run = run
        dialog.library('Library', 'All games\nSearch: Sonic', [('a','Game "quoted"')])
        self.assertFalse(captured[0].exists())
