"""Console font changes must be scoped and restored, including on interruption."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from mister.console_display import ConsoleDisplay, FONTS


class ConsoleDisplayCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name in FONTS:
            (Path(self.tmp.name) / name).write_bytes(b'font')

    def runner(self, command, **kwargs):
        if '-O' in command:
            Path(command[command.index('-O') + 1]).write_bytes(b'original font')
            Path(command[command.index('-om') + 1]).write_bytes(b'original map')
        return subprocess.CompletedProcess(command, 0)

    def display(self, sizes=((53, 22),)):
        runner = Mock(side_effect=self.runner)
        return ConsoleDisplay(console='/dev/tty2', runner=runner, size_reader=Mock(side_effect=sizes),
                              font_dir=self.tmp.name, executable='/usr/bin/setfont'), runner

    def test_font_and_map_are_restored_and_snapshot_removed(self):
        display, runner = self.display()
        with display:
            self.assertTrue(display.active)
            backup = display.saved_font.parent
            self.assertTrue(display.saved_font.exists())
        self.assertFalse(backup.exists())
        restore = runner.call_args.args[0]
        self.assertIn('-m', restore)
        self.assertTrue(restore[-1].endswith('original.psfu'))

    def test_interrupt_restores_original_console(self):
        display, runner = self.display()
        with self.assertRaises(KeyboardInterrupt):
            with display:
                raise KeyboardInterrupt
        self.assertFalse(display.active)
        self.assertIn('-m', runner.call_args.args[0])

    def test_font_falls_back_when_screen_would_be_too_small(self):
        display, runner = self.display(((32, 15), (80, 22)))
        with display:
            self.assertTrue(display.active)
            self.assertTrue(any(str(call.args[0][-1]).endswith(FONTS[1]) for call in runner.call_args_list))

    def test_ssh_never_changes_the_tv_font(self):
        runner = Mock()
        with ConsoleDisplay(console='/dev/pts/0', runner=runner, executable='/usr/bin/setfont') as display:
            self.assertFalse(display.active)
        runner.assert_not_called()

    def test_switching_modes_preserves_one_original_snapshot(self):
        display, runner = self.display(((53, 22), (53, 22)))
        with display:
            snapshot = display.saved_font.read_bytes()
            display.set_mode('desktop')
            self.assertFalse(display.active)
            self.assertEqual(display.mode, 'desktop')
            display.set_mode('sofa')
            self.assertTrue(display.active)
            self.assertEqual(display.saved_font.read_bytes(), snapshot)
        self.assertEqual(sum('-O' in call.args[0] for call in runner.call_args_list), 1)
        self.assertIn('-m', runner.call_args.args[0])

    def test_desktop_start_never_changes_font_until_sofa_is_selected(self):
        display, runner = self.display()
        display.mode = 'desktop'
        with display:
            self.assertFalse(display.active)
            self.assertEqual(runner.call_count, 1)  # Save only; keep the existing font.
            display.set_mode('sofa')
            self.assertTrue(display.active)


if __name__ == '__main__':
    unittest.main()
