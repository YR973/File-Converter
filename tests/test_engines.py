import errno
import subprocess
import unittest
from unittest.mock import patch

from engines import find_pandoc


class PandocDiscoveryTests(unittest.TestCase):
    @patch('engines.subprocess.run', side_effect=OSError(errno.ENOEXEC, 'Bad CPU type in executable'))
    def test_wrong_cpu_returns_unavailable(self, run):
        self.assertIsNone(find_pandoc())

    @patch('engines.sys.platform', 'darwin')
    @patch('engines.subprocess.run')
    def test_mac_native_homebrew_works_without_shell_path(self, run):
        def probe(command, **kwargs):
            if command[0] == '/opt/homebrew/bin/pandoc':
                return subprocess.CompletedProcess(command, 0, 'pandoc 3.6\n', '')
            raise OSError(errno.ENOEXEC, 'Bad CPU type in executable')
        run.side_effect = probe
        with patch.dict('engines.os.environ', {}, clear=True):
            self.assertEqual(find_pandoc(), '/opt/homebrew/bin/pandoc')

    @patch('engines.subprocess.run', side_effect=subprocess.TimeoutExpired('pandoc', 5))
    def test_unresponsive_engine_returns_unavailable(self, run):
        self.assertIsNone(find_pandoc())

    def test_unavailable_pandoc_does_not_break_other_routes(self):
        from pathlib import Path
        import converter
        with patch.object(converter, 'PANDOC', None):
            self.assertNotIn('docx', converter.targets(Path('file.md')))
            self.assertNotIn('txt', converter.targets(Path('file.md')))
            self.assertIn('jpg', converter.targets(Path('file.png')))
            self.assertIn('txt', converter.targets(Path('file.pdf')))
            self.assertIn('csv', converter.targets(Path('file.xlsx')))
