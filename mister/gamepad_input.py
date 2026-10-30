"""Application-local Linux joystick + keyboard input for dialog; stdlib only."""
import fcntl
import os
import re
import select
import signal
import struct
import sys
import termios
import threading
import time
import tty
from pathlib import Path

EVENT = struct.Struct('=IhBB')
ARROWS = {'left': b'\x1b[D', 'right': b'\x1b[C', 'up': b'\x1b[A', 'down': b'\x1b[B'}


class PadKeys:
    """Translate kernel axis/button codes, without assuming joystick numbering."""
    def __init__(self, axes=None, buttons=None):
        self.axes = axes if axes is not None else {0: 'horizontal', 1: 'vertical', 6: 'horizontal', 7: 'vertical'}
        self.buttons = buttons if buttons is not None else {0: b'\n', 1: b'\x1b', 4: b'\x1b[Z', 5: b'\t'}
        self.held = {}

    @classmethod
    def from_fd(cls, fd):
        axes, buttons = bytearray(64), bytearray(1024)
        try:
            fcntl.ioctl(fd, 0x80406a32, axes, True)  # JSIOCGAXMAP
            fcntl.ioctl(fd, 0x84006a34, buttons, True)  # JSIOCGBTNMAP
            axis_keys = {0: 'horizontal', 1: 'vertical', 16: 'horizontal', 17: 'vertical'}
            button_keys = {0x130: b'\n', 0x131: b'\x1b', 0x136: b'\x1b[Z', 0x137: b'\t'}
            # Only actual entries are used by events; trailing zeroes are irrelevant.
            return cls({i: axis_keys[code] for i, code in enumerate(axes) if code in axis_keys},
                       {i: button_keys[code] for i, code in enumerate(struct.unpack('=512H', buttons)) if code in button_keys})
        except OSError:
            return cls()

    def event(self, packet, now):
        _, value, kind, number = EVENT.unpack(packet)
        if kind & 0x80:  # Initial state is never an action (especially a held confirm).
            return b''
        if kind == 1:
            return self.buttons.get(number, b'') if value else b''
        if kind != 2 or number not in self.axes:
            return b''
        if abs(value) < 16000:
            self.held.pop(number, None)
            return b''
        direction = ('left' if value < 0 else 'right') if self.axes[number] == 'horizontal' else ('up' if value < 0 else 'down')
        key = ARROWS[direction]
        if self.held.get(number, (None,))[0] == key:
            return b''
        self.held[number] = (key, now + .4)
        return key

    def repeat(self, now):
        output = b''
        for number, (key, due) in list(self.held.items()):
            if now >= due:
                output += key
                self.held[number] = (key, now + .15)
        return output


def joystick_paths():
    if not sys.platform.startswith('linux'):
        return []
    paths = []
    for path in sorted(Path('/dev/input').glob('js[0-9]*')):
        try:
            name = (Path('/sys/class/input') / path.name / 'device/name').read_text().lower()
        except OSError:
            continue
        if 'virtual' not in name and 'companion' not in name:
            paths.append(path)
    return paths


def mister_console_input():
    """MiSTer supplies its own mapped pad-to-keyboard events on the Scripts VT."""
    try:
        return sys.platform.startswith('linux') and bool(re.fullmatch(r'/dev/tty[0-9]+', os.ttyname(sys.stdin.fileno())))
    except (OSError, ValueError):
        return False


class DialogInput:
    """Forward keys to this widget's private input FD, never to a system input device."""
    def __init__(self, cancel_only=False, paths=None, keyboard_fd=None, interrupt=None):
        self.cancel_only = cancel_only
        # Reading raw JS *and* the native keyboard events duplicates each press
        # and can map Select to both Enter and Escape. Never open JS on the VT.
        self.keyboard_only = paths is None and mister_console_input()
        self.paths = [] if self.keyboard_only else joystick_paths() if paths is None else paths
        self.keyboard_fd = keyboard_fd
        self.interrupt = interrupt or (lambda: os.kill(os.getpid(), signal.SIGINT))
        self.pads, self.buffers = {}, {}
        self.read_fd = self.write_fd = None
        self.saved_terminal = None
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self):
        if self.keyboard_fd is None:
            if not sys.stdin.isatty():
                return self
            self.keyboard_fd = sys.stdin.fileno()
        try:
            for path in self.paths:
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                except OSError:
                    continue
                self.pads[fd] = PadKeys.from_fd(fd)
                self.buffers[fd] = b''
            if not self.pads and not self.keyboard_only:
                return self
            self.saved_terminal = termios.tcgetattr(self.keyboard_fd)
            self.read_fd, self.write_fd = os.pipe()
            os.set_blocking(self.write_fd, False)
            tty.setraw(self.keyboard_fd)
            self.thread = threading.Thread(target=self.pump, name='roms-manager-pad', daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def send(self, data):
        if not data:
            return
        if b'\x03' in data or self.cancel_only and data == b'\x1b':
            self.stop.set()
            self.interrupt()
            return
        if not self.cancel_only:
            try:
                os.write(self.write_fd, data)
            except (BlockingIOError, BrokenPipeError):
                pass

    def pump(self):
        keyboard = self.keyboard_fd
        try:
            while not self.stop.is_set():
                readers = list(self.pads) + ([keyboard] if keyboard is not None else [])
                ready, _, _ = select.select(readers, [], [], .05)
                now = time.monotonic()
                for fd in ready:
                    try:
                        data = os.read(fd, 512)
                    except BlockingIOError:
                        continue
                    except OSError:
                        data = b''
                    if not data:
                        if fd == keyboard:
                            keyboard = None
                        else:
                            os.close(fd)
                            del self.pads[fd]
                        continue
                    if fd == keyboard:
                        self.send(data)
                    else:
                        self.buffers[fd] += data
                        while len(self.buffers[fd]) >= EVENT.size:
                            packet, self.buffers[fd] = self.buffers[fd][:EVENT.size], self.buffers[fd][EVENT.size:]
                            self.send(self.pads[fd].event(packet, now))
                for pad in self.pads.values():
                    self.send(pad.repeat(now))
        except (OSError, ValueError):
            self.stop.set()

    def __exit__(self, *_):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=1)
        try:
            if self.saved_terminal is not None:
                termios.tcsetattr(self.keyboard_fd, termios.TCSADRAIN, self.saved_terminal)
        finally:
            for fd in list(self.pads) + [self.read_fd, self.write_fd]:
                if fd is not None:
                    os.close(fd)
            self.pads.clear()
