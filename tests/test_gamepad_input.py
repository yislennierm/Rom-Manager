"""Synthetic joystick events and real private pipes/PTYs; no physical pad presses."""
import os
import pty
import select
import termios
import tty
import unittest
from unittest.mock import Mock, patch

from mister.gamepad_input import ARROWS, DialogInput, EVENT, PadKeys


def event(value, kind, number):
    return EVENT.pack(0, value, kind, number)


class PadKeysCase(unittest.TestCase):
    def test_initial_state_and_button_release_never_confirm(self):
        keys = PadKeys()
        self.assertEqual(keys.event(event(1, 0x81, 0), 0), b'')
        self.assertEqual(keys.event(event(32767, 0x82, 1), 0), b'')
        self.assertEqual(keys.repeat(10), b'')
        self.assertEqual(keys.event(event(0, 1, 0), 0), b'')
        self.assertEqual(keys.event(event(1, 1, 0), 0), b'\n')
        self.assertEqual(keys.event(event(1, 1, 1), 0), b'\x1b')

    def test_dpad_stick_deadzone_and_controlled_repeat(self):
        keys = PadKeys()
        self.assertEqual(keys.event(event(-32767, 2, 6), 0), ARROWS['left'])
        self.assertEqual(keys.repeat(.3), b'')
        self.assertEqual(keys.repeat(.4), ARROWS['left'])
        self.assertEqual(keys.event(event(-30000, 2, 6), .45), b'')
        self.assertEqual(keys.event(event(0, 2, 6), .5), b'')
        self.assertEqual(keys.repeat(1), b'')
        self.assertEqual(keys.event(event(32000, 2, 1), 2), ARROWS['down'])
        self.assertEqual(keys.event(event(800, 2, 1), 2.1), b'')
        self.assertEqual(keys.repeat(3), b'')

    def test_kernel_mapping_and_shoulders_for_buttons(self):
        def ioctl(fd, operation, buffer, mutate):
            if len(buffer) == 64:
                buffer[:] = bytes([16, 17]) + b'\xff' * 62
            else:
                import struct
                buffer[:] = struct.pack('=512H', 0x131, 0x130, 0x137, 0x136, *([0] * 508))
        with patch('mister.gamepad_input.fcntl.ioctl', side_effect=ioctl):
            keys = PadKeys.from_fd(10)
        self.assertEqual(keys.event(event(1, 1, 1), 0), b'\n')
        self.assertEqual(keys.event(event(1, 1, 0), 0), b'\x1b')
        self.assertEqual(keys.event(event(1, 1, 2), 0), b'\t')
        self.assertEqual(keys.event(event(1, 1, 3), 0), b'\x1b[Z')
        self.assertEqual(keys.event(event(32000, 2, 1), 0), ARROWS['down'])


class InputCase(unittest.TestCase):
    def setUp(self):
        self.master, self.keyboard = pty.openpty()
        # Normalize macOS's kernel-managed PENDIN bit before taking snapshots.
        cooked = termios.tcgetattr(self.keyboard)
        tty.setraw(self.keyboard)
        termios.tcsetattr(self.keyboard, termios.TCSANOW, cooked)
        self.pad_read, self.pad_write = os.pipe()
        self.addCleanup(self.close_fds)

    def close_fds(self):
        for fd in (self.master, self.keyboard, self.pad_read, self.pad_write):
            os.close(fd)

    def read_key(self, fd):
        self.assertTrue(select.select([fd], [], [], 1)[0], 'No forwarded key')
        return os.read(fd, 128)

    def test_mister_native_pad_keys_are_forwarded_once_without_opening_raw_pad(self):
        saved = termios.tcgetattr(self.keyboard)
        with patch('mister.gamepad_input.mister_console_input', return_value=True), \
             patch('mister.gamepad_input.joystick_paths') as discover, \
             patch('mister.gamepad_input.os.open') as open_pad:
            with DialogInput(keyboard_fd=self.keyboard) as keys:
                self.assertTrue(keys.keyboard_only)
                self.assertFalse(keys.pads)
                for key in (ARROWS['down'], b'\n', b'\x1b', b'Star'):
                    os.write(self.master, key)
                    self.assertEqual(self.read_key(keys.read_fd), key)
                self.assertFalse(select.select([keys.read_fd], [], [], .1)[0])
            discover.assert_not_called()
            open_pad.assert_not_called()
        self.assertEqual(termios.tcgetattr(self.keyboard), saved)

    def test_native_back_key_cancels_gauge_without_a_raw_pad_reader(self):
        interrupt = Mock()
        with patch('mister.gamepad_input.mister_console_input', return_value=True):
            with DialogInput(cancel_only=True, keyboard_fd=self.keyboard, interrupt=interrupt) as keys:
                os.write(self.master, b'\x1b')
                keys.thread.join(timeout=1)
                interrupt.assert_called_once()
                self.assertFalse(keys.pads)

    def test_native_mapping_is_used_only_on_linux_virtual_consoles(self):
        from mister.gamepad_input import mister_console_input
        for console, expected in (('/dev/tty2', True), ('/dev/pts/0', False)):
            with patch('mister.gamepad_input.sys.platform', 'linux'), \
                 patch('mister.gamepad_input.os.ttyname', return_value=console):
                self.assertEqual(mister_console_input(), expected)

    def test_keyboard_and_controller_share_only_private_widget_pipe(self):
        saved = termios.tcgetattr(self.keyboard)
        original_open = os.open
        def open_pad(path, flags):
            return os.dup(self.pad_read) if path == 'fake-pad' else original_open(path, flags)
        with patch('mister.gamepad_input.os.open', side_effect=open_pad):
            with DialogInput(paths=['fake-pad'], keyboard_fd=self.keyboard) as keys:
                os.write(self.pad_write, event(1, 0x81, 0))
                self.assertFalse(select.select([keys.read_fd], [], [], .1)[0])
                os.write(self.pad_write, event(32767, 2, 7))
                self.assertEqual(self.read_key(keys.read_fd), ARROWS['down'])
                os.write(self.pad_write, event(0, 2, 7))
                os.write(self.master, b'Star')
                self.assertEqual(self.read_key(keys.read_fd), b'Star')
                os.write(self.pad_write, event(1, 1, 1))
                self.assertEqual(self.read_key(keys.read_fd), b'\x1b')
                thread = keys.thread
        self.assertFalse(thread.is_alive())
        self.assertEqual(termios.tcgetattr(self.keyboard), saved)
        with self.assertRaises(OSError):
            os.fstat(keys.read_fd)

    def test_cleanup_on_interrupt_and_no_pad_keyboard_fallback(self):
        saved = termios.tcgetattr(self.keyboard)
        with DialogInput(paths=[], keyboard_fd=self.keyboard) as keys:
            self.assertIsNone(keys.read_fd)
        self.assertEqual(termios.tcgetattr(self.keyboard), saved)
        with patch('mister.gamepad_input.os.open', return_value=os.dup(self.pad_read)):
            with self.assertRaises(KeyboardInterrupt):
                with DialogInput(paths=['fake'], keyboard_fd=self.keyboard) as keys:
                    raise KeyboardInterrupt
        self.assertFalse(keys.thread.is_alive())
        self.assertEqual(termios.tcgetattr(self.keyboard), saved)

    def test_gauge_back_and_control_c_interrupt_but_arrows_do_not(self):
        interrupt = Mock()
        keys = DialogInput(cancel_only=True, paths=[], interrupt=interrupt)
        keys.send(ARROWS['down'])
        interrupt.assert_not_called()
        keys.send(b'\x1b')
        interrupt.assert_called_once()
        interrupt.reset_mock()
        keys.send(b'\x03')
        interrupt.assert_called_once()

    def test_virtual_controllers_are_excluded(self):
        from pathlib import Path
        from mister.gamepad_input import joystick_paths
        with patch('mister.gamepad_input.sys.platform', 'linux'), \
             patch('mister.gamepad_input.Path.glob', return_value=[Path('/dev/input/js0'), Path('/dev/input/js2')]), \
             patch('mister.gamepad_input.Path.read_text', side_effect=['Pico W Gamepad', 'MiSTer Virtual Controller']):
            self.assertEqual(joystick_paths(), [Path('/dev/input/js0')])


if __name__ == '__main__':
    unittest.main()
