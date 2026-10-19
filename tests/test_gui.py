"""Run on a desktop or a virtual X display: DISPLAY=:99 python -m unittest discover -s tests."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from converter import App, create_window


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt' or __import__('sys').platform == 'darwin',
                     'A desktop display is required')
class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = create_window()
        self.addCleanup(self.root.destroy)
        self.app = App(self.root)
        self.path = Path(self.temp.name) / 'image with spaces.png'
        Image.new('RGB', (32, 32), 'blue').save(self.path)
        self.app.folder.set(str(Path(self.temp.name) / 'out'))
        self.root.update()

    def test_drag_drop_with_spaces_and_duplicate_filter(self):
        self.assertTrue(self.root.dnd_ready)
        event = type('Event', (), {'data': self.root.tk.call('list', str(self.path))})()
        # Tcl may return a tuple instead of an encoded string for 'list'.
        event.data = self.root.tk.call('format', '%s', self.root.tk.call('list', str(self.path)))
        self.app.drop_files(event)
        self.app.add_paths([str(self.path)])
        self.assertEqual(self.app.files, [self.path])
        self.assertIn('jpg', self.app.format['values'])

    def test_background_conversion(self):
        self.app.add_paths([str(self.path)])
        self.app.format.set('jpg')
        self.app.start()
        self.assertTrue(self.app.busy)
        deadline = time.monotonic() + 10
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.assertFalse(self.app.busy)
        self.assertTrue((Path(self.app.folder.get()) / 'image with spaces.jpg').is_file())
        self.assertIn('1 converted', self.app.status.get())
        self.assertEqual(str(self.app.format['state']), 'readonly')

    def test_remove_selected(self):
        self.app.add_paths([str(self.path)])
        self.app.listbox.selection_set(0)
        self.app.remove_selected()
        self.assertEqual(self.app.files, [])
        self.assertEqual(self.app.format.get(), '')

    def test_invalid_settings_remain_editable(self):
        self.app.add_paths([str(self.path)])
        self.app.quality.set('not a number')
        with patch('converter.messagebox.showerror') as error:
            self.app.start()
        error.assert_called_once()
        self.assertFalse(self.app.busy)
