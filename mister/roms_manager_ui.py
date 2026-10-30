"""Small dialog frontend for the existing MiSTer client; Python 3.8+."""
import re
import json
import os
import shutil
import signal
import subprocess
import sys
import termios
import tempfile
from pathlib import Path
from urllib.parse import quote, urlencode

if __package__:
    from .roms_manager_client import Api, Client, ClientError, load_config
    from .console_display import ConsoleDisplay
    from .gamepad_input import DialogInput
else:
    from roms_manager_client import Api, Client, ClientError, load_config
    from console_display import ConsoleDisplay
    from gamepad_input import DialogInput

from core.mister_sources import SNES_GUID, NES_GUID, GENESIS_GUID
CONSOLE_NAMES = {SNES_GUID: 'SNES', NES_GUID: 'NES', GENESIS_GUID: 'Mega Drive / Genesis'}
PAGE_SIZE = 5
TARGET = 'mister'


def load_display_mode(path):
    try:
        path = Path(path)
        if path.is_symlink() or path.stat().st_size > 1024:
            return 'sofa'
        mode = json.loads(path.read_text()).get('display_mode')
        return mode if mode in ('sofa', 'desktop') else 'sofa'
    except (OSError, ValueError, AttributeError):
        return 'sofa'


def save_display_mode(path, mode):
    if mode not in ('sofa', 'desktop'):
        raise ValueError('Unknown display mode')
    path = Path(path)
    if path.is_symlink():
        raise ClientError('Cannot save display settings through a symbolic link.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=str(path.parent), prefix='.ui_settings.', delete=False) as output:
            temporary = Path(output.name)
            json.dump({'display_mode': mode}, output)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()

TV_THEME = '''use_shadow = OFF
use_colors = ON
screen_color = (WHITE,BLACK,OFF)
dialog_color = (WHITE,BLACK,OFF)
title_color = (YELLOW,BLACK,ON)
border_color = (WHITE,BLACK,OFF)
border2_color = (WHITE,BLACK,OFF)
menubox_color = (WHITE,BLACK,OFF)
menubox_border_color = (WHITE,BLACK,OFF)
menubox_border2_color = (WHITE,BLACK,OFF)
tag_key_color = (WHITE,BLACK,ON)
tag_key_selected_color = (BLACK,YELLOW,OFF)
item_color = (WHITE,BLACK,ON)
item_selected_color = (BLACK,YELLOW,OFF)
tag_color = (WHITE,BLACK,OFF)
tag_selected_color = (BLACK,YELLOW,OFF)
button_active_color = (BLACK,YELLOW,ON)
button_inactive_color = (WHITE,BLACK,OFF)
button_key_active_color = (BLACK,YELLOW,ON)
button_key_inactive_color = (YELLOW,BLACK,ON)
button_label_active_color = (BLACK,YELLOW,ON)
button_label_inactive_color = (WHITE,BLACK,OFF)
inputbox_color = (WHITE,BLACK,ON)
inputbox_border_color = (WHITE,BLACK,OFF)
gauge_color = (BLACK,YELLOW,ON)
'''


def clean_text(value):
    text = re.sub(r'https?://\S+', '[URL]', str(value))
    text = ''.join(c for c in text if c == '\n' or ord(c) >= 32 and not 127 <= ord(c) <= 159)
    text = re.sub(r'(?m)^XXX$', 'X X X', text)
    return ' ' + text if text.startswith('--') else text


def fit_game_title(value, width):
    title = clean_text(value).replace('\n', ' ')
    if len(title) <= width:
        return title
    suffix = min(18, max(6, width // 3))
    return title[:width-suffix-3] + '...' + title[-suffix:]


def size_text(value):
    if value in (None, ''):
        return 'Unknown'
    try:
        size = int(value or 0)
    except (ValueError, TypeError):
        return 'Unknown'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return ('%d %s' if size == int(size) else '%.1f %s') % (size, unit)
        size /= 1024


class Dialog:
    def __init__(self, runner=subprocess.run, popen=subprocess.Popen, executable=None):
        self.executable = executable or shutil.which('dialog')
        if not self.executable:
            raise ClientError('dialog is not installed. Use --sync for console downloads.')
        self.runner, self.popen = runner, popen
        self.gauge = None
        self.saved_terminal = None
        self.saved_sigterm = None
        self.environment = dict(os.environ)
        self.theme_directory = None
        self.input_factory = None
        self.gauge_input = None
        self.mode = 'sofa'

    def __enter__(self):
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise ClientError('The interactive menu requires a terminal. Use --sync without a terminal.')
        self.saved_terminal = termios.tcgetattr(sys.stdin.fileno())
        self.saved_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda *_: self.interrupt())
        self.theme_directory = tempfile.TemporaryDirectory(prefix='roms_manager_theme_')
        theme = Path(self.theme_directory.name) / 'dialogrc'
        theme.write_text(TV_THEME)
        self.environment['DIALOGRC'] = str(theme)
        self.environment['ESCDELAY'] = '150'
        self.environment.pop('COLUMNS', None)
        self.environment.pop('LINES', None)
        try:
            if re.fullmatch(r'/dev/tty[0-9]+', os.ttyname(sys.stdin.fileno())):
                # MiSTer inherits TERM=vt102; this is a Linux VT. vt102 treats
                # our Linux arrow sequences as Escape, cancelling navigation.
                self.environment['TERM'] = 'linux'
        except OSError:
            pass
        self.input_factory = DialogInput
        return self

    @staticmethod
    def interrupt():
        raise KeyboardInterrupt

    def __exit__(self, *_):
        try:
            self.stop_gauge()
        finally:
            try:
                if self.saved_terminal is not None:
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.saved_terminal)
            finally:
                if self.saved_sigterm is not None:
                    signal.signal(signal.SIGTERM, self.saved_sigterm)
                if self.theme_directory is not None:
                    self.theme_directory.cleanup()
                sys.stderr.write('\033[0m\033[?25h\033[2J\033[H')
                sys.stderr.flush()

    def terminal_size(self):
        try:
            return os.get_terminal_size(sys.stderr.fileno())
        except (OSError, ValueError, AttributeError):
            return shutil.get_terminal_size((80, 24))

    def command(self, title):
        columns, lines = self.terminal_size()
        if columns < 40 or lines < 18:
            raise ClientError('Terminal is too small. Use at least 40 columns and 18 rows, or --sync.')
        return [self.executable, '--stdout', '--title', clean_text(title), '--no-shadow', '--no-tags', '--visit-items',
                '--ascii-lines', '--no-collapse', '--no-nl-expand'], min(30 if self.mode == 'desktop' else 20, lines - 2), min(100 if self.mode == 'desktop' else 70, columns - 4)

    def run(self, command, navigation=None):
        if self.input_factory is None:
            result = self.runner(command, stdout=subprocess.PIPE, text=True, env=self.environment)
        else:
            with self.input_factory() as keys:
                kwargs = {'pass_fds': (keys.read_fd,)} if keys.read_fd is not None else {}
                if kwargs:
                    command = command[:1] + ['--input-fd', str(keys.read_fd)] + command[1:]
                result = self.runner(command, stdout=subprocess.PIPE, text=True, env=self.environment, **kwargs)
        if navigation and result.returncode in navigation:
            return navigation[result.returncode]
        if result.returncode in (1, 255):
            return None
        if result.returncode != 0:
            raise ClientError('dialog could not display the menu. Check TERM and terminal dimensions.')
        return result.stdout.rstrip('\n')

    def menu(self, title, text, items, default=None):
        command, height, width = self.command(title)
        controls = dict(items)
        navigation = {}
        if 'next' in controls:
            command += ['--extra-button', '--extra-label', 'Next']
            navigation[3] = 'next'
        if 'prev' in controls:
            command += ['--help-button', '--help-label', 'Previous']
            navigation[2] = 'prev'
        command += ['--cancel-label', 'Exit' if title == 'ROMs Manager' else 'Back']
        if default:
            command += ['--default-item', default]
        choices = [(tag, label) for tag, label in items if tag not in ('prev', 'next', 'back')]
        menu_rows = min(len(choices), max(3, height - 8 - min(3, text.count('\n'))))
        command += ['--menu', clean_text(text), str(height), str(width), str(menu_rows)]
        for tag, label in choices:
            command += [tag, clean_text(label).replace('\n', ' ')[:180]]
        return self.run(command, navigation)

    def library(self, title, text, items, default=None, filter_enabled=True, available_only=False):
        command, _, _ = self.command(title)
        columns, lines = self.terminal_size()
        rows = max(5, lines - 7 - text.count('\n'))
        command += ['--scrollbar', '--ok-label', 'Open', '--cancel-label', 'Back',
                    '--extra-button', '--extra-label', 'Search']
        navigation = {3: 'search'}
        if filter_enabled:
            command += ['--help-button', '--help-label', 'All' if available_only else 'Ready']
            navigation[2] = 'filter'
        if default:
            command += ['--default-item', default]
        command += ['--menu', clean_text(text), str(lines), str(columns), str(rows)]
        for tag, label in items:
            command += [tag, clean_text(label).replace('\n', ' ')]
        # Native dialog scrolls the complete lightweight title list. A response
        # file avoids ARG_MAX even for large cached catalogs such as NES.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='roms-library-', suffix='.args') as arguments:
            arguments.write('\n'.join('"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"' for value in command[1:]))
            arguments.flush()
            return self.run([self.executable, '--file', arguments.name], navigation)

    def message(self, title, text):
        command, height, width = self.command(title)
        return self.run(command + ['--msgbox', clean_text(text), str(height), str(width)])

    def input(self, title, text, initial=''):
        command, _, width = self.command(title)
        return self.run(command + ['--max-input', '120', '--inputbox', clean_text(text), '10', str(width), clean_text(initial)])

    def yesno(self, title, text):
        command, height, width = self.command(title)
        return self.run(command + ['--defaultno', '--yesno', clean_text(text), str(height), str(width)]) is not None

    def start_gauge(self):
        command, _, width = self.command('Sync pending downloads')
        self.gauge = self.popen(command + ['--gauge', 'Getting downloads...', '9', str(width), '0'],
                                stdin=subprocess.PIPE, text=True, env=self.environment)
        if self.input_factory is not None:
            self.gauge_input = self.input_factory(cancel_only=True)
            try:
                self.gauge_input.__enter__()
            except BaseException:
                self.gauge_input = None
                self.stop_gauge()
                raise

    def progress(self, event):
        if self.gauge is None:
            return
        if self.gauge.poll() is not None:
            raise KeyboardInterrupt
        done, total = event.get('downloaded', 0), event.get('total', 0)
        percent = min(100, max(0, int(done * 100 / total))) if total else 0
        state = event.get('state', 'downloading').capitalize()
        if event.get('system'):
            state = event['system'].upper() + ': ' + state
        name = event.get('filename', '').rsplit('/', 1)[-1]
        text = '%s\n%s\n%s / %s' % (clean_text(name), state, size_text(done), size_text(total))
        try:
            self.gauge.stdin.write('XXX\n%d\n%s\nXXX\n' % (percent, text))
            self.gauge.stdin.flush()
        except (BrokenPipeError, OSError):
            raise KeyboardInterrupt from None

    def stop_gauge(self):
        if self.gauge_input is not None:
            self.gauge_input.__exit__(None, None, None)
            self.gauge_input = None
        gauge, self.gauge = self.gauge, None
        if gauge is not None:
            try:
                try:
                    gauge.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                gauge.wait(timeout=2)
            except subprocess.TimeoutExpired:
                gauge.kill()
                gauge.wait()


class MisterUI:
    def __init__(self, config, dialog, api=None, client_factory=Client, display=None, settings_path=None, runtime=None):
        self.config, self.dialog = config, dialog
        self.api = api or Api(config['backend_url'], config['device_token'])
        self.client_factory = client_factory
        self.display, self.settings_path = display, settings_path
        self.runtime = runtime
        self.dialog.mode = display.mode if display is not None else 'sofa'

    @property
    def page_size(self):
        return 12 if self.dialog.mode == 'desktop' else PAGE_SIZE

    def toggle_display(self):
        mode = 'desktop' if self.dialog.mode == 'sofa' else 'sofa'
        if self.display is not None:
            self.display.set_mode(mode)
        self.dialog.mode = mode
        if self.settings_path is not None:
            try:
                save_display_mode(self.settings_path, mode)
            except OSError:
                raise ClientError('Display changed, but the preference could not be saved. Check the client folder is writable.') from None
        if self.display is not None and self.display.notice:
            self.dialog.message('Display', self.display.notice)

    def run(self):
        actions = {'1': self.browse, '2': self.search, '3': self.queue,
                   '4': self.sync, '5': self.status, '7': self.toggle_display, '8': self.refresh_catalog}
        while True:
            items = [
                ('1', 'Browse games'), ('2', 'Search'), ('3', 'Downloads'),
                ('4', 'Download queued games'), ('5', 'Status')]
            if self.runtime:
                items.append(('8', 'Refresh catalog'))
            items += [('7', 'Display: %s (switch)' % self.dialog.mode.capitalize()), ('6', 'Exit')]
            text = 'Browse your library or continue downloads.'
            if self.runtime:
                text += '\n' + ('Cached catalog | ' if self.runtime.cache.get('user') else 'Initial sync | ') + self.runtime.cache.get('network', 'sync starting')
            choice = self.dialog.menu('ROMs Manager', text, items)
            if choice in (None, '6'):
                return 0
            action = actions.get(choice)
            if action:
                try:
                    action()
                except KeyboardInterrupt:
                    self.dialog.stop_gauge()
                    self.dialog.message('Cancelled', 'Cached data and partial downloads were kept. Return to Downloads to continue.')
                except (ClientError, OSError, ValueError, KeyError, TypeError):
                    exc = sys.exc_info()[1]
                    self.dialog.message('Unable to complete action', str(exc) if isinstance(exc, ClientError) else
                                        'Check local storage and configuration, then try again. No existing games were replaced.')

    def refresh_catalog(self):
        if not self.runtime:
            return
        self.dialog.start_gauge()
        try:
            result = self.runtime.refresh(observer=self.dialog.progress)
        finally:
            self.dialog.stop_gauge()
        self.dialog.message('Catalog cache', 'A catalog refresh is already running.' if result.get('busy') else 'Assigned catalog cache updated. Browsing and provider downloads work without the backend.')

    def catalogs(self):
        targets = self.api.get('/client/downloads/targets').get('targets', [])
        own = next((t for t in targets if t.get('id') == TARGET), None)
        self.download_guids = {str(guid).lower() for guid in (own or {}).get('system_guids', {}).values()}
        user = self.api.get('/me')['user']
        allowed = {str(guid).lower() for guid in user.get('allowed_console_guids', [])}
        return [m for m in self.api.get('/roms').get('roms', [])
                if '*' in allowed or str(m.get('guid', '')).lower() in allowed]

    @staticmethod
    def console_name(catalog):
        return CONSOLE_NAMES.get(str(catalog.get('guid', '')).lower()) or catalog.get('console') or 'Console'

    def browse(self, query=''):
        catalogs = self.catalogs()
        if not catalogs:
            self.dialog.message('Game catalogs', 'No assigned catalogs are cached yet. Refresh the catalog when connected.')
            return
        groups = {}
        for catalog in catalogs:
            groups.setdefault(str(catalog['guid']).lower(), []).append(catalog)
        if len(groups) == 1:
            return self.libraries(catalogs, query)
        while True:
            choice = self.dialog.menu('Consoles', 'Choose your library', [
                (guid, self.console_name(group[0]) + '  ' + format(sum(int(m.get('cached_count', m.get('entry_count')) or 0) for m in ([c for c in group if not self.fixture_catalog(c)] or group)), ',') + ('' if guid in self.download_guids else ' (browse only)'))
                for guid, group in sorted(groups.items(), key=lambda item: self.console_name(item[1][0]))])
            if choice is None:
                return
            if choice in groups:
                self.libraries(groups[choice], query)

    @staticmethod
    def fixture_catalog(catalog):
        slug = str(catalog.get('slug') or '')
        return slug.startswith('mister_') and slug.endswith('_demo')

    def libraries(self, catalogs, query):
        # Like the desktop TUI, open the assigned console's real catalog directly.
        regular = [catalog for catalog in catalogs if not self.fixture_catalog(catalog)]
        catalogs = regular or catalogs
        if len(catalogs) == 1:
            return self.games(catalogs[0], query)
        console = self.console_name(catalogs[0])
        caption = 'Choose a library' + ('' if str(catalogs[0]['guid']).lower() in self.download_guids else ' (browse only)')
        while True:
            choice = self.dialog.menu(console + ' libraries', caption, [
                (str(i), '%s (%s games)' % ('ZIP demo' if 'zip smoke test' in str(m.get('source_label', '')).lower() else
                  'Demo games' if 'smoke test' in str(m.get('source_label', '')).lower() else
                  m.get('source_label') or 'Main library', format(m.get('catalog_total') or m.get('entry_count') or 0, ',')))
                for i, m in enumerate(catalogs)])
            if choice is None:
                return
            if choice.isdigit() and int(choice) < len(catalogs):
                self.games(catalogs[int(choice)], query)

    def search(self):
        query = self.dialog.input('Search games', 'Game title, region, serial, or checksum:')
        if query is not None and query.strip():
            self.browse(query.strip())

    def games(self, catalog, query):
        if callable(getattr(self.api, 'library', None)) and callable(getattr(self.dialog, 'library', None)):
            return self.scrolling_games(catalog, query)
        # Backward compatibility for direct server adapters without a local
        # title index. Normal MiSTer and preview use the cached browser above.
        return self.paged_games(catalog, query)

    def scrolling_games(self, catalog, query):
        slug = catalog.get('slug') or catalog['guid']
        can_install = str(catalog.get('guid', '')).lower() in self.download_guids
        available_only, selected = False, None
        while True:
            library = self.api.library(slug, query=query, available_only=available_only)
            entries = library['entries']
            keys = {entry['key'] for entry in entries}
            if selected not in keys:
                selected = entries[0]['key'] if entries else None
            title = '%s | %s games' % (self.console_name(catalog), format(library['total'], ','))
            text = ('Ready only' if available_only else 'All games') + ' | + ready / - no source'
            if query:
                columns = self.dialog.terminal_size()[0]
                label = 'Search: ' + query
                text += '\n' + (label if len(label) <= columns-6 else label[:columns-9] + '...')
            columns = self.dialog.terminal_size()[0]
            items = [('game:' + entry['key'], ('+ ' if entry['supported'] and can_install else '- ')
                      + fit_game_title(entry['title'], columns-10)) for entry in entries]
            if not items:
                items = [('empty', 'No games match. Choose Search or All games.')]
            choice = self.dialog.library(title, text, items, default='game:' + selected if selected else None,
                                         filter_enabled=can_install, available_only=available_only)
            if choice is None:
                return
            if choice == 'search':
                new_query = self.dialog.input('Search library', 'Title, region, serial or checksum. Blank shows all.', query)
                if new_query is not None:
                    query, selected = new_query.strip(), None
            elif choice == 'filter' and can_install:
                available_only, selected = not available_only, None
            elif choice.startswith('game:'):
                selected = choice[5:]
                if selected not in keys:
                    continue
                entry = self.api.game_entry(slug, selected)
                if not can_install:
                    entry = {**entry, 'mister': {**(entry.get('mister') or {}), 'supported': False,
                        'reason': 'This console has no MiSTer installation mapping.'}}
                self.game_detail(slug, entry)

    def paged_games(self, catalog, query):
        offset = 0
        slug = catalog.get('slug') or catalog['guid']
        can_install = str(catalog.get('guid', '')).lower() in self.download_guids
        available_only = False
        while True:
            filters = {'limit': self.page_size, 'offset': offset, 'q': query, 'sort': 'name'}
            if available_only:
                filters['download_target'] = TARGET
            params = urlencode(filters)
            page = self.api.get('/roms/%s?%s' % (quote(slug, safe=''), params))
            entries, total = page.get('entries', []), int(page.get('total', 0))
            if str(catalog.get('guid', '')).lower() not in self.download_guids:
                entries = [{**entry, 'mister': {**(entry.get('mister') or {}), 'supported': False,
                    'reason': (entry.get('mister') or {}).get('reason') or 'This console has no MiSTer installation mapping.'}}
                    for entry in entries]
            items = [('game:%d' % i, '%s%s' % (e.get('game_title') or e.get('name') or e.get('rom_name') or 'Untitled',
                     '' if e.get('mister', {}).get('supported') else ' [unavailable]')) for i, e in enumerate(entries)]
            if offset:
                items.append(('prev', 'Previous page'))
            if page.get('has_more'):
                items.append(('next', 'Next page'))
            if can_install:
                items.append(('availability', 'View all games' if available_only else 'Downloadable games only'))
            items += [('refresh', 'Refresh'), ('back', 'Back')]
            choice = self.dialog.menu(self.console_name(catalog) + ' games', '%d-%d of %s%s%s' % (
                offset + 1 if total else 0, min(offset + len(entries), total), format(total, ','),
                '\nReady to download' if available_only else '\nAll catalog games',
                '\nSearch: ' + query if query else ''), items)
            if choice in (None, 'back'):
                return
            if choice == 'next' and page.get('has_more'):
                offset += self.page_size
            elif choice == 'prev':
                offset = max(0, offset - self.page_size)
            elif choice == 'availability' and can_install:
                available_only, offset = not available_only, 0
            elif choice and choice.startswith('game:'):
                index = int(choice.split(':')[1])
                if 0 <= index < len(entries):
                    self.game_detail(slug, entries[index])

    def game_detail(self, dataset, entry):
        support = entry.get('mister') or {}
        name = entry.get('rom_name') or entry.get('name') or ''
        extension = support.get('format') or (name.rsplit('.', 1)[-1].upper() if '.' in name else 'Unknown')
        details = '%s\n\n%s   %s' % (entry.get('game_title') or entry.get('name') or 'Untitled',
            extension, size_text(support.get('size') or entry.get('size')))
        if support.get('source_kind') in ('zip_member', 'archive'):
            details += '\nPrepared locally from the provider archive' if self.runtime else '\nPrepared from archive by the backend'
        if not support.get('supported'):
            self.dialog.message('Unavailable', details + '\n\n' + (support.get('reason') or 'No compatible source is available.'))
            return
        if not self.dialog.yesno('Add to downloads?', details):
            return
        result = self.api.request('POST', '/client/downloads', {'target': TARGET, 'dataset': dataset, 'entry_key': support['entry_key']})
        state = result['job']['state']
        if state == 'completed':
            self.dialog.message('Already completed', 'This game has already been downloaded.')
        elif state == 'failed':
            self.dialog.message('Download needs attention', 'Open Downloads to review the error and retry.')
        elif state in ('preparing', 'downloading', 'verifying'):
            self.dialog.message('Preparing' if state == 'preparing' else 'Downloading', 'This game already has an active download.')
        elif self.dialog.yesno('Download now?', 'The game is in your downloads.\n\nStart downloading?'):
            self.sync()

    def queue(self):
        offset = 0
        while True:
            page = self.api.get('/client/downloads?' + urlencode({'target': TARGET, 'limit': self.page_size, 'offset': offset}))
            jobs = page.get('jobs', [])
            states = {'completed': 'Ready', 'queued': 'Queued', 'preparing': 'Preparing', 'downloading': 'Downloading', 'verifying': 'Checking', 'failed': 'Failed'}
            items = [('job:%d' % i, '%s - %s' % (states.get(j['state'], j['state']), j.get('title') or j['destination'].rsplit('/', 1)[-1])) for i, j in enumerate(jobs)]
            if offset:
                items.append(('prev', 'Previous page'))
            if page.get('has_more'):
                items.append(('next', 'Next page'))
            items += [('refresh', 'Refresh'), ('back', 'Back')]
            if self.runtime:
                items.insert(-1, ('import', 'Fetch web queue'))
            choice = self.dialog.menu('Downloads', '%s downloads' % page.get('total', 0), items)
            if choice in (None, 'back'):
                return
            if choice == 'next' and page.get('has_more'):
                offset += self.page_size
            elif choice == 'prev':
                offset = max(0, offset - self.page_size)
            elif choice == 'import' and self.runtime:
                try:
                    count = self.runtime.import_web_queue()
                    self.dialog.message('Web queue', '%d queued downloads imported to the local queue.' % count)
                except ClientError:
                    self.dialog.message('Web queue unavailable', 'Cached browsing and local downloads still work. Import web jobs when the backend is reachable.')
            elif choice and choice.startswith('job:'):
                index = int(choice.split(':')[1])
                if 0 <= index < len(jobs):
                    job = jobs[index]
                    details = '%s\n\n%s\n%s / %s' % (job.get('title', ''), states.get(job['state'], job['state']),
                        size_text(job.get('downloaded')), size_text(job.get('size') or None))
                    if job.get('error'):
                        details += '\nError: ' + job['error']
                    if job['state'] == 'failed' and self.dialog.yesno('Retry failed job?', details + '\n\nQueue this job for retry?'):
                        self.api.request('POST', '/client/downloads/%s/retry?%s' % (quote(job['id'], safe=''), urlencode({'target': TARGET})))
                        self.dialog.message('Retry queued', 'The existing job is queued. Choose Sync pending downloads to run it.')
                    elif job['state'] != 'failed':
                        self.dialog.message('Download details', details)

    def sync(self):
        completed, failed = set(), []

        def observe(event):
            self.dialog.progress(event)
            if event.get('state') == 'completed':
                completed.add(event['filename'])
            if event.get('state') == 'failed':
                failed.append(event.get('error') or 'Download failed')

        client = self.client_factory(self.config, api=self.api, observer=observe)
        self.dialog.start_gauge()
        try:
            failures = client.sync()
        finally:
            self.dialog.stop_gauge()
        if self.runtime:
            self.runtime.wake.set()
        self.dialog.message('Downloads finished', '%d ready\n%d need attention%s' % (
            len(completed), failures, '\n\n' + '\n'.join(failed) if failed else ''))

    def status(self):
        user = self.api.get('/me')['user']
        allowed = {str(guid).lower() for guid in user.get('allowed_console_guids', [])}
        lines = ['Account: ' + str(user.get('name') or user['id']), 'Account ID: ' + user['id'],
                 'Assigned supported consoles: ' + ', '.join(name for guid, name in CONSOLE_NAMES.items() if guid in allowed or '*' in allowed)]
        if self.runtime:
            lines.append('Backend: ' + self.runtime.cache.get('network', 'not checked'))
            synced = self.runtime.cache.get('synced')
            if synced:
                import time
                lines.append('Catalog sync: ' + time.strftime('%Y-%m-%d %H:%M', time.localtime(synced)))
        targets = self.api.get('/client/downloads/targets').get('targets', [])
        lines.append('MiSTer target assigned: ' + ('Yes' if any(t.get('id') == TARGET for t in targets) else 'No'))
        try:
            telemetry = self.client_factory(self.config, api=self.api).telemetry()
            systems = telemetry.get('systems') or {'snes': telemetry}
            for system, details in systems.items():
                guid = SNES_GUID if system == 'snes' else NES_GUID if system == 'nes' else None
                if guid not in allowed and '*' not in allowed:
                    continue
                name = CONSOLE_NAMES.get(guid, system.upper())
                lines += [name + ' core installed: ' + ('Yes' if details.get('core_present') else 'No'),
                          name + ' folder: ' + (details.get('games_root') or 'Unavailable'),
                          'Free space: ' + size_text(details.get('free_bytes'))]
                if details.get('error'):
                    lines.append(details['error'])
        except (ClientError, OSError):
            lines.append('Storage unavailable. Check the selected games folder and mount before syncing.')
        self.dialog.message('Device/account status', '\n'.join(lines))


def run_ui(config_path):
    dialog = Dialog()
    runtime = None
    settings_path = Path(config_path).with_name('ui_settings.json')
    with dialog, ConsoleDisplay(mode=load_display_mode(settings_path)) as display:
        try:
            if display.notice:
                dialog.message('TV text', display.notice)
            config = load_config(config_path)
            if __package__:
                from .offline_client import OfflineRuntime
            else:
                from offline_client import OfflineRuntime
            runtime = OfflineRuntime(config, config_path)
            runtime.start_refresh()
            return MisterUI(config, dialog, api=runtime.local_api, client_factory=runtime.client_factory,
                           display=display, settings_path=settings_path, runtime=runtime).run()
        except KeyboardInterrupt:
            dialog.stop_gauge()
            dialog.message('Interrupted', 'Cached data and partial downloads were retained.')
            return 130
        except (ClientError, OSError, ValueError, KeyError):
            exc = sys.exc_info()[1]
            dialog.message('Setup required', str(exc) if isinstance(exc, ClientError) else 'Check config.json and storage permissions. No credentials are shown here.')
            return 1
        finally:
            if runtime:
                runtime.close()
