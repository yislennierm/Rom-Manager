"""Temporary large console text for TV use; never changes SSH or boot settings."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FONTS = ('solar24x32.psfu.gz', 'latarcyrheb-sun32.psfu.gz', 'LatGrkCyr-12x22.psfu.gz')


def console_size(console):
    fd = os.open(console, os.O_RDWR | os.O_NOCTTY)
    try:
        return os.get_terminal_size(fd)
    finally:
        os.close(fd)


class ConsoleDisplay:
    def __init__(self, console=None, runner=subprocess.run, size_reader=console_size,
                 font_dir='/usr/share/consolefonts', executable=None, mode='sofa'):
        self.console = console
        self.runner, self.size_reader = runner, size_reader
        self.font_dir = Path(font_dir)
        self.executable = executable or shutil.which('setfont')
        self.directory = None
        self.changed = False
        self.active = False
        self.notice = None
        self.mode = mode

    def call(self, *args):
        return self.runner([self.executable, '-C', self.console, *map(str, args)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def __enter__(self):
        if self.console is None:
            try:
                self.console = os.ttyname(sys.stdin.fileno()) if sys.stdin.isatty() else ''
            except OSError:
                self.console = ''
        # /dev/pts/* belongs to SSH. Font changes apply only to our real VT.
        if not re.fullmatch(r'/dev/tty[0-9]+', self.console or '') or not self.executable:
            return self
        self.directory = Path(tempfile.mkdtemp(prefix='roms_manager_font_'))
        self.saved_font = self.directory / 'original.psfu'
        self.saved_map = self.directory / 'original.map'
        try:
            self.call('-O', self.saved_font, '-om', self.saved_map)
            self.set_mode(self.mode)
        except (OSError, subprocess.CalledProcessError):
            self.restore()
            self.notice = 'The console font could not be changed safely.'
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def set_mode(self, mode):
        if mode not in ('sofa', 'desktop'):
            raise ValueError('Unknown display mode')
        self.mode, self.notice = mode, None
        if self.directory is None:
            return
        if mode == 'desktop':
            self.restore()
            return
        if self.active:
            return
        try:
            for name in FONTS:
                font = self.font_dir / name
                if not font.is_file():
                    continue
                self.changed = True
                try:
                    self.call(font)
                    columns, rows = self.size_reader(self.console)
                    if columns >= 40 and rows >= 18:
                        self.active = True
                        return
                except (OSError, subprocess.CalledProcessError):
                    continue
            self.restore()
            self.notice = 'Large TV text is unavailable. Using the original console font.'
        except (OSError, subprocess.CalledProcessError):
            self.restore()
            self.notice = 'The console font could not be changed safely.'

    def restore(self):
        if self.changed:
            self.call('-m', self.saved_map, self.saved_font)
            self.changed = False
            self.active = False

    def __exit__(self, *_):
        # Retain the snapshot on a restore failure so it remains recoverable.
        self.restore()
        if self.directory is not None:
            shutil.rmtree(self.directory)
