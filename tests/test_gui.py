"""Desktop integration tests; run with a desktop or virtual X display."""
import os
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

from PIL import Image
import pymupdf

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
        Image.new('RGB', (120, 80), 'blue').save(self.path)
        self.app.folder.set(str(Path(self.temp.name) / 'out'))
        self.root.update()

    def wait_until(self, condition):
        deadline = time.monotonic() + 10
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.root.update()
        self.assertTrue(condition(), 'Desktop operation did not complete')

    def test_empty_state_and_progressive_controls(self):
        self.assertTrue(self.app.empty.winfo_ismapped())
        self.assertFalse(self.app.workspace.winfo_ismapped())
        self.app.add_paths([self.path])
        self.root.update()
        self.assertFalse(self.app.empty.winfo_ismapped())
        self.assertTrue(self.app.workspace.winfo_ismapped())
        self.assertFalse(self.app.options_frame.winfo_ismapped())
        self.assertFalse(self.app.cancel.winfo_ismapped())
        self.assertFalse(self.app.merge.winfo_ismapped())

    def test_drag_drop_with_spaces_and_duplicate_filter(self):
        self.assertTrue(self.root.dnd_ready)
        data = self.root.tk.call('format', '%s', self.root.tk.call('list', str(self.path)))
        self.app.drop_files(type('Event', (), {'data': data})())
        self.app.add_paths([self.path])
        self.assertEqual(self.app.files, [self.path])
        self.assertIn('JPG', self.app.format.cget('values'))

    def test_background_conversion_and_inline_success(self):
        self.app.add_paths([self.path])
        self.app.set_target('jpg')
        self.app.start()
        self.assertTrue(self.app.busy)
        self.wait_until(lambda: not self.app.busy)
        self.assertTrue((Path(self.app.folder.get()) / 'image with spaces.jpg').is_file())
        self.assertIn('1 converted', self.app.status.get())
        self.assertEqual(self.app.format.cget('state'), 'readonly')
        self.assertEqual(self.app.file_states[self.path][0], 'Converted')
        self.assertTrue(self.app.output.winfo_ismapped())
        self.assertFalse(self.app.cancel.winfo_ismapped())

    def test_image_preview_is_loaded_without_changing_source(self):
        before = self.path.read_bytes()
        self.app.add_paths([self.path])
        self.wait_until(lambda: not self.app.preview_loading)
        self.assertEqual(self.app.preview_data.image.size, (120, 80))
        self.assertIn('120 × 80', self.app.preview_data.detail)
        self.assertIsNotNone(self.app.preview_image)
        self.assertEqual(self.path.read_bytes(), before)

    def test_remove_selected_returns_to_empty_state(self):
        self.app.add_paths([self.path])
        self.app.remove_selected()
        self.root.update()
        self.assertEqual(self.app.files, [])
        self.assertEqual(self.app.format.get(), '')
        self.assertTrue(self.app.empty.winfo_ismapped())

    def test_invalid_settings_remain_editable(self):
        self.app.add_paths([self.path])
        self.app.set_target('jpg')
        self.app.quality.set('not a number')
        with patch('converter.messagebox.showerror') as error:
            self.app.start()
        error.assert_called_once()
        self.assertFalse(self.app.busy)

    def test_contextual_tools_and_hidden_values(self):
        self.app.add_paths([self.path])
        self.app.set_target('jpg')
        self.app.toggle_options()
        self.root.update()
        self.assertTrue(self.app.image_options.winfo_ismapped())
        self.assertFalse(self.app.media_options.winfo_ismapped())
        self.app.quality.set('invalid')
        self.app.tool_values['rotation'].set('invalid')
        self.app.set_target('zip')
        self.root.update()
        self.assertFalse(self.app.options_button.winfo_ismapped())
        self.assertFalse(self.app.options_frame.winfo_ismapped())
        self.assertEqual(self.app.read_options().rotation, 0)
        self.assertEqual(self.app.read_options().quality, 90)

    def test_pdf_preview_pagination_and_merge_visibility(self):
        path = Path(self.temp.name) / 'document.pdf'
        with pymupdf.open() as document:
            document.new_page(); document.new_page(); document.save(path)
        self.app.add_paths([self.path, path])
        self.app.select_file(path)
        self.wait_until(lambda: not self.app.preview_loading)
        self.assertEqual(self.app.preview_data.pages, 2)
        self.assertTrue(self.app.page_bar.winfo_ismapped())
        self.assertTrue(self.app.merge.winfo_ismapped())
        self.app.change_page(1)
        self.wait_until(lambda: not self.app.preview_loading)
        self.assertEqual(self.app.preview_data.page, 1)
        self.assertEqual(self.app.next_page.cget('state'), 'disabled')

    def test_collision_appears_inline_and_original_is_kept(self):
        self.app.add_paths([self.path])
        self.app.set_target('jpg')
        folder = Path(self.app.folder.get())
        folder.mkdir()
        existing = folder / 'image with spaces.jpg'
        existing.write_bytes(b'keep')
        self.app.start()
        self.wait_until(lambda: not self.app.busy)
        self.assertEqual(existing.read_bytes(), b'keep')
        self.assertEqual(self.app.file_states[self.path][0], 'Failed')
        self.assertTrue(self.app.details.winfo_ismapped())
        self.app.toggle_errors()
        self.root.update()
        self.assertTrue(self.app.error_box.winfo_ismapped())
        self.assertIn('File exists', self.app.error_box.get('1.0', 'end'))

    def test_stale_preview_does_not_replace_current_selection(self):
        other = Path(self.temp.name) / 'notes.txt'
        other.write_text('The current selection')
        self.app.add_paths([self.path, other])
        self.app.select_file(other)
        self.wait_until(lambda: not self.app.preview_loading)
        self.assertEqual(self.app.preview_data.title, other.name)
        self.assertIn('current selection', self.app.preview_data.text)

    def test_retry_only_processes_unfinished_files(self):
        other = Path(self.temp.name) / 'second.png'
        Image.new('RGB', (40, 40), 'red').save(other)
        self.app.add_paths([self.path, other])
        self.app.set_target('jpg')
        folder = Path(self.app.folder.get())
        folder.mkdir()
        (folder / 'second.jpg').write_bytes(b'existing')
        self.app.start()
        self.wait_until(lambda: not self.app.busy)
        self.assertEqual(self.app.file_states[self.path][0], 'Converted')
        self.assertEqual(self.app.file_states[other][0], 'Failed')
        retry_folder = Path(self.temp.name) / 'retry'
        self.app.folder.set(str(retry_folder))
        self.app.start(retry=True)
        self.wait_until(lambda: not self.app.busy)
        self.assertTrue((retry_folder / 'second.jpg').is_file())
        self.assertFalse((retry_folder / 'image with spaces.jpg').exists())
        self.assertEqual(self.app.file_states[self.path][0], 'Converted')
        self.assertEqual(self.app.file_states[other][0], 'Converted')

    def test_stop_keeps_current_output_and_skips_next_file(self):
        import converter
        original_convert = converter.convert
        entered, release = threading.Event(), threading.Event()
        other = Path(self.temp.name) / 'second.png'
        Image.new('RGB', (40, 40), 'red').save(other)
        self.app.add_paths([self.path, other])
        self.app.set_target('jpg')
        def delayed(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test worker was not released')
            return original_convert(*args)
        with patch('converter.convert', side_effect=delayed):
            self.app.start()
            self.wait_until(entered.is_set)
            self.app.request_stop()
            release.set()
            self.wait_until(lambda: not self.app.busy)
        self.assertEqual(self.app.file_states[self.path][0], 'Converted')
        self.assertEqual(self.app.file_states[other][0], 'Skipped')
        self.assertTrue((Path(self.app.folder.get()) / 'image with spaces.jpg').is_file())
        self.assertFalse((Path(self.app.folder.get()) / 'second.jpg').exists())

    def test_small_window_and_theme_switch_keep_primary_action_visible(self):
        import customtkinter as ctk
        self.root.geometry('1040x700')
        self.app.add_paths([self.path])
        self.app.toggle_options()
        ctk.set_appearance_mode('Dark')
        self.root.update()
        self.assertTrue(self.app.go.winfo_ismapped())
        self.assertLess(self.app.go.winfo_rooty() + self.app.go.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        self.assertLessEqual(self.app.settings.winfo_rootx() + self.app.settings.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
        ctk.set_appearance_mode('Light')
        self.root.update()
        self.assertTrue(self.app.go.winfo_ismapped())
