import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import wave
import zipfile

from PIL import Image
import openpyxl
import pymupdf

import converter
from previews import preview_file, size_label


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_large_image_is_downsampled_for_preview(self):
        path = self.root / 'photo.png'
        Image.new('RGB', (2400, 1600), 'green').save(path)
        result = preview_file(path, converter)
        self.assertLessEqual(result.image.width, 1000)
        self.assertLessEqual(result.image.height, 800)
        self.assertIn('2400 × 1600', result.detail)
        with Image.open(path) as original:
            self.assertEqual(original.size, (2400, 1600))

    def test_pdf_page_navigation_and_text_is_unchanged(self):
        path = self.root / 'pages.pdf'
        with pymupdf.open() as document:
            document.new_page().insert_text((30, 30), 'Page one')
            document.new_page().insert_text((30, 30), 'Page two')
            document.save(path)
        before = path.read_bytes()
        result = preview_file(path, converter, page=1)
        self.assertEqual((result.pages, result.page), (2, 1))
        self.assertIsNotNone(result.image)
        self.assertEqual(path.read_bytes(), before)

    def test_text_preview_is_bounded(self):
        path = self.root / 'long.txt'
        path.write_text('A' * 100000)
        result = preview_file(path, converter)
        self.assertLess(len(result.text), 32100)
        self.assertIn('shortened', result.text)

    def test_structured_previews(self):
        path = self.root / 'data.json'
        path.write_text('{"name":"Local","count":42}')
        self.assertEqual(json.loads(preview_file(path, converter).text)['count'], 42)
        csv = self.root / 'table.csv'
        csv.write_text('name,value\nLocal,42\n')
        self.assertIn('Local  |  42', preview_file(csv, converter).text)

    def test_spreadsheet_first_rows(self):
        path = self.root / 'sheet.xlsx'
        workbook = openpyxl.Workbook()
        workbook.active.append(['Name', 'Value'])
        workbook.active.append(['Preview row', 42])
        workbook.save(path)
        result = preview_file(path, converter)
        self.assertIn('Preview row', result.text)
        self.assertIn('1 sheets', result.detail)

    def test_archive_contents_are_listed_without_extraction(self):
        path = self.root / 'archive.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('../../not-extracted.txt', 'test')
        result = preview_file(path, converter)
        self.assertIn('../../not-extracted.txt', result.text)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_unknown_file_has_honest_fallback(self):
        path = self.root / 'unknown.bin'
        path.write_bytes(b'\x00\x01\x02')
        result = preview_file(path, converter)
        self.assertIsNone(result.image)
        self.assertIn('No inline preview', result.text)
        self.assertEqual(size_label(2048), '2.0 KB')

    @unittest.skipUnless(converter.PANDOC, 'Pandoc not available')
    def test_document_preview(self):
        source = self.root / 'source.md'
        source.write_text('# A local preview\n\nA little document.')
        path = converter.convert(source, 'docx', self.root / 'out')[0]
        self.assertIn('A little document', preview_file(path, converter).text)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg not installed')
    def test_audio_waveform_and_video_thumbnail(self):
        audio = self.root / 'audio.wav'
        with wave.open(str(audio), 'wb') as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(8000)
            handle.writeframes(b'\x00\x01' * 8000)
        result = preview_file(audio, converter)
        self.assertEqual(result.image.size, (720, 260))
        self.assertIn('waveform', result.detail)
        video = self.root / 'video.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=64x64:d=0.2',
                        '-c:v', 'libx264', str(video)], check=True)
        frame = preview_file(video, converter)
        self.assertEqual(frame.image.width, 640)
        r, g, b = frame.image.getpixel((100, 100))
        self.assertGreater(r, g + 100)
        self.assertGreater(r, b + 100)
