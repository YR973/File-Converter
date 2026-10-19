import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import wave
import zipfile

from PIL import Image
import pymupdf

from converter import App, Options, convert, targets, merge_pdf


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'output'
        self.image = self.root / 'sample.png'
        Image.new('RGBA', (64, 48), (255, 0, 0, 128)).save(self.image)

    def test_image_formats_and_resize(self):
        for fmt in ('jpg', 'webp', 'bmp', 'tiff', 'gif', 'ico', 'heic', 'heif', 'avif'):
            with self.subTest(fmt=fmt):
                result = convert(self.image, fmt, self.out, Options(max_size=32))[0]
                with Image.open(result) as image:
                    self.assertEqual(image.size, (32, 24) if fmt != 'ico' else (32, 32))

    def test_pdf_roundtrip(self):
        pdf = convert(self.image, 'pdf', self.out)[0]
        with pymupdf.open(pdf) as doc:
            self.assertEqual(len(doc), 1)
        result = convert(pdf, 'png', self.root / 'pages')
        self.assertEqual(len(result), 1)
        with Image.open(result[0]) as image:
            self.assertGreater(image.width, 0)

    def test_pdf_text_and_multiple_pages(self):
        path = self.root / 'document.pdf'
        with pymupdf.open() as document:
            for text in ('First page', 'Second page'):
                document.new_page().insert_text((50, 50), text)
            document.save(path)
        self.assertIn('Second page', convert(path, 'txt', self.out)[0].read_text())
        self.assertEqual(len(convert(path, 'jpg', self.out)), 2)

    def test_table_roundtrip(self):
        source = self.root / 'table.json'
        source.write_text(json.dumps([{'name': 'Café, test', 'number': 3}]))
        csv = convert(source, 'csv', self.out)[0]
        result = convert(csv, 'json', self.root / 'roundtrip')[0]
        self.assertEqual(json.loads(result.read_text()), [{'name': 'Café, test', 'number': '3'}])
        self.assertIn('\t', convert(csv, 'tsv', self.out)[0].read_text())

    def test_nested_json_rejected(self):
        source = self.root / 'nested.json'
        source.write_text('[{"value": {"nested": true}}]')
        with self.assertRaisesRegex(ValueError, 'Nested'):
            convert(source, 'csv', self.out)
        self.assertFalse(any(self.out.iterdir()))

    def test_malformed_table_rejected(self):
        source = self.root / 'bad.csv'
        source.write_text('one,two\na,b,c\n')
        with self.assertRaises(ValueError):
            convert(source, 'json', self.out)

    def test_overwrite_protection(self):
        result = convert(self.image, 'jpg', self.out)[0]
        result.write_bytes(b'keep me')
        with self.assertRaises(FileExistsError):
            convert(self.image, 'jpg', self.out)
        self.assertEqual(result.read_bytes(), b'keep me')
        self.assertEqual(len(list(self.out.iterdir())), 1)

    def test_multipage_collision_rolls_back(self):
        path = self.root / 'multi.pdf'
        with pymupdf.open() as document:
            document.new_page(); document.new_page(); document.save(path)
        self.out.mkdir()
        existing = self.out / 'multi-page-002.png'
        existing.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            convert(path, 'png', self.out)
        self.assertEqual(list(self.out.iterdir()), [existing])
        self.assertEqual(existing.read_bytes(), b'keep')

    def test_archive_and_compression(self):
        import gzip, bz2, lzma, tarfile
        for fmt in ('zip', 'tar', 'gz', 'bz2', 'xz'):
            with self.subTest(fmt=fmt):
                result = convert(self.image, fmt, self.out)[0]
                if fmt == 'zip':
                    with zipfile.ZipFile(result) as archive:
                        content = archive.read(self.image.name)
                elif fmt == 'tar':
                    with tarfile.open(result) as archive:
                        content = archive.extractfile(self.image.name).read()
                else:
                    with {'gz': gzip.open, 'bz2': bz2.open, 'xz': lzma.open}[fmt](result) as archive:
                        content = archive.read()
                self.assertEqual(content, self.image.read_bytes())

    def test_document_formats(self):
        source = self.root / 'note.md'
        source.write_text('# Local document\n\nHello from the converter.\n')
        for fmt in ('txt', 'html', 'docx', 'epub', 'rst', 'odt', 'rtf', 'tex', 'pdf'):
            with self.subTest(fmt=fmt):
                result = convert(source, fmt, self.out)[0]
                self.assertGreater(result.stat().st_size, 0)
                if fmt == 'pdf':
                    with pymupdf.open(result) as document:
                        self.assertIn('Hello from the converter', ''.join(page.get_text() for page in document))
                if fmt in ('docx', 'epub', 'odt'):
                    with zipfile.ZipFile(result) as archive:
                        self.assertIsNone(archive.testzip())
                if fmt in ('docx', 'epub', 'odt', 'rtf', 'tex'):
                    text = convert(result, 'txt', self.root / ('from-' + fmt))[0].read_text()
                    self.assertIn('Hello from the converter', text)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg not installed')
    def test_audio_formats(self):
        source = self.root / 'sound.wav'
        with wave.open(str(source), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(b'\x00\x00' * 1600)
        for fmt in ('mp3', 'flac', 'ogg', 'm4a', 'aac', 'opus', 'aiff', 'wma'):
            with self.subTest(fmt=fmt):
                result = convert(source, fmt, self.out)[0]
                self.assertGreater(result.stat().st_size, 0)
                check = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(result), '-f', 'null', '-'], capture_output=True)
                self.assertEqual(check.returncode, 0, check.stderr)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg not installed')
    def test_video_and_audio_extraction(self):
        source = self.root / 'clip.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=64x64:d=0.2',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.2',
                        '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
        for fmt in ('webm', 'mkv', 'mov', 'avi', 'mp3', 'mpeg', 'm4v', 'flv', 'wmv', 'ts', 'mts', '3gp', 'gif'):
            with self.subTest(fmt=fmt):
                result = convert(source, fmt, self.out)[0]
                self.assertGreater(result.stat().st_size, 0)
                check = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(result), '-f', 'null', '-'], capture_output=True)
                self.assertEqual(check.returncode, 0, check.stderr)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg not installed')
    def test_subtitles(self):
        source = self.root / 'captions.srt'
        source.write_text('1\n00:00:00,000 --> 00:00:01,000\nHello\n')
        for fmt in ('vtt', 'ass', 'ssa', 'txt'):
            with self.subTest(fmt=fmt):
                self.assertIn('Hello', convert(source, fmt, self.out)[0].read_text())

    def test_combine_pdf_and_image(self):
        pdf = convert(self.image, 'pdf', self.root / 'pdf')[0]
        result = merge_pdf((pdf, self.image), self.out)[0]
        with pymupdf.open(result) as doc:
            self.assertEqual(len(doc), 2)
        before = result.read_bytes()
        with self.assertRaises(FileExistsError):
            merge_pdf((pdf,), self.out)
        self.assertEqual(result.read_bytes(), before)

    def test_image_crop_and_rotation(self):
        result = convert(self.image, 'png', self.out, Options(crop=(4, 8, 20, 30), rotation=90))[0]
        with Image.open(result) as image:
            self.assertEqual(image.size, (30, 20))
        with self.assertRaises(ValueError):
            convert(self.image, 'png', self.root / 'bad', Options(crop=(0, 0, 100, 100)))

    def test_pdf_split(self):
        source = self.root / 'pages.pdf'
        with pymupdf.open() as document:
            document.new_page(); document.new_page(); document.save(source)
        results = convert(source, 'pdf-pages', self.out)
        self.assertEqual(len(results), 2)
        for path in results:
            with pymupdf.open(path) as document:
                self.assertEqual(len(document), 1)
        optimized = convert(source, 'pdf', self.root / 'optimized')[0]
        with pymupdf.open(optimized) as document:
            self.assertEqual(len(document), 2)

    def test_spreadsheet_roundtrip(self):
        source = self.root / 'sheet.json'
        source.write_text('[{"name": "=literal", "value": 42, "enabled": true}]')
        xlsx = convert(source, 'xlsx', self.out)[0]
        ods = convert(xlsx, 'ods', self.out)[0]
        result = convert(ods, 'json', self.root / 'back')[0]
        self.assertEqual(json.loads(result.read_text()), json.loads(source.read_text()))

    def test_spreadsheet_multisheet_and_formula_protection(self):
        import openpyxl
        source = self.root / 'sheets.xlsx'
        workbook = openpyxl.Workbook()
        workbook.active.append(['header']); workbook.active.append([42])
        workbook.create_sheet('Other').append(['value'])
        workbook.save(source)
        with self.assertRaisesRegex(ValueError, 'exactly one sheet'):
            convert(source, 'csv', self.out)
        ods = convert(source, 'ods', self.out)[0]
        back = convert(ods, 'xlsx', self.root / 'back')[0]
        result = openpyxl.load_workbook(back)
        self.assertEqual(len(result.sheetnames), 2)
        result.close()
        workbook.active['A2'] = '=1+1'; workbook.save(source)
        with self.assertRaisesRegex(ValueError, 'Formula'):
            convert(source, 'json', self.out)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg not installed')
    def test_audio_trim(self):
        source = self.root / 'trim.wav'
        with wave.open(str(source), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(b'\x00\x00' * 32000)
        result = convert(source, 'flac', self.out, Options(trim_start=0.5, trim_end=1.0))[0]
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                '-of', 'default=noprint_wrappers=1:nokey=1', str(result)],
                               capture_output=True, text=True, check=True)
        self.assertAlmostEqual(float(probe.stdout), 0.5, places=1)

    def test_invalid_route_and_options(self):
        with self.assertRaises(ValueError):
            convert(self.image, 'mp3', self.out)
        with self.assertRaises(ValueError):
            convert(self.image, 'jpg', self.out, Options(quality=101))


if __name__ == '__main__':
    unittest.main()
