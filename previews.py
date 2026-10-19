"""Bounded, read-only file previews. No networking or persistent thumbnails."""
from dataclasses import dataclass
import csv
import io
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

from PIL import Image, ImageDraw, ImageOps
import openpyxl
import pymupdf


@dataclass
class Preview:
    title: str
    detail: str
    image: Image.Image | None = None
    text: str = ''
    pages: int = 1
    page: int = 0


def size_label(size: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024


def local_process(command: list[str], timeout=10) -> bytes:
    result = subprocess.run(command, capture_output=True, timeout=timeout, shell=False,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('This file could not be decoded by the local preview engine.')
    return result.stdout


def preview_file(path: Path, engine, page: int = 0) -> Preview:
    size = path.stat().st_size
    ext = path.suffix.lower().lstrip('.')
    basic = f'{ext.upper() or "FILE"}  ·  {size_label(size)}'
    if ext in engine.IMAGES:
        with Image.open(path) as original:
            if original.width * original.height > 60_000_000:
                return Preview(path.name, basic, text='This image is too large for a quick preview. You can still convert it.')
            dimensions = original.size
            original.thumbnail((1000, 800), Image.Resampling.LANCZOS)
            image = ImageOps.exif_transpose(original).convert('RGBA').copy()
        return Preview(path.name, f'{dimensions[0]} × {dimensions[1]} px  ·  {basic}', image)
    if ext == 'pdf':
        with pymupdf.open(path) as document:
            if document.needs_pass:
                return Preview(path.name, basic, text='This PDF is password protected. Open the original to view it.')
            if not len(document):
                return Preview(path.name, basic, text='This PDF has no pages.')
            page = max(0, min(page, len(document) - 1))
            pdf_page = document[page]
            scale = min(900 / max(1, pdf_page.rect.width), 750 / max(1, pdf_page.rect.height))
            pixmap = pdf_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            image = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)
            return Preview(path.name, f'{len(document)} pages  ·  {basic}', image=image, pages=len(document), page=page)
    if ext in engine.VIDEO | engine.AUDIO:
        if not shutil.which('ffmpeg'):
            return Preview(path.name, basic, text='Install FFmpeg to preview and convert audio and video. Open the original to play it now.')
        detail = basic
        if shutil.which('ffprobe'):
            try:
                metadata = json.loads(local_process(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                    '-show_entries', 'format=duration:stream=codec_name,width,height,sample_rate', '-of', 'json', str(path)]))
                duration = float(metadata.get('format', {}).get('duration', 0))
                detail = f'{int(duration // 60)}:{int(duration % 60):02} duration  ·  {basic}'
            except (ValueError, OSError, subprocess.SubprocessError):
                pass
        command = ['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', str(path)]
        if ext in engine.VIDEO:
            raw = local_process(command + ['-frames:v', '1', '-vf', 'scale=640:-2', '-f', 'image2pipe', '-vcodec', 'png', '-'])
            with Image.open(io.BytesIO(raw)) as image:
                return Preview(path.name, detail, image=image.convert('RGB').copy())
        raw = local_process(command + ['-t', '30', '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'])
        import array
        import sys
        samples = array.array('h', raw)
        if sys.byteorder != 'little':
            samples.byteswap()
        image = Image.new('RGB', (720, 260), '#f0edff')
        draw = ImageDraw.Draw(image)
        chunk = max(1, len(samples) // 144)
        for index in range(144):
            segment = samples[index * chunk:(index + 1) * chunk]
            peak = max((abs(value) for value in segment), default=0) / 32768
            height = max(3, int(peak * 105))
            draw.rounded_rectangle((index * 5, 130 - height, index * 5 + 3, 130 + height), radius=2, fill='#7661ef')
        return Preview(path.name, detail + '  ·  waveform: first 30s', image=image)
    # Avoid parsing large documents or structured data just to paint a preview.
    if size > 20 * 1024 * 1024:
        return Preview(path.name, basic, text='Preview is limited to files under 20 MB for this format. Your file can still be converted.')
    if ext == 'xlsx':
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            sheet = workbook.active
            rows = list(itertools.islice(sheet.iter_rows(values_only=True), 35))
            text = '\n'.join('  |  '.join(str(value)[:80] if value is not None else '—' for value in row[:8]) for row in rows)
            return Preview(path.name, f'{len(workbook.sheetnames)} sheets  ·  {basic}', text=f'{sheet.title}\n\n{text}\n\nPreview: first 35 rows / 8 columns. Formula values may be cached.')
        finally:
            workbook.close()
    if ext in {'csv', 'tsv'}:
        with path.open(encoding='utf-8-sig', errors='replace', newline='') as handle:
            rows = itertools.islice(csv.reader(handle, delimiter='\t' if ext == 'tsv' else ','), 35)
            text = '\n'.join('  |  '.join(value[:80] for value in row[:8]) for row in rows)
        return Preview(path.name, basic, text=text + '\n\nPreview: first 35 rows / 8 columns.')
    if ext == 'zip':
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            return Preview(path.name, f'{len(names)} items  ·  {basic}', text='Archive contents\n\n' + '\n'.join(names[:80]) + ('\n…' if len(names) > 80 else ''))
    if ext == 'tar':
        with tarfile.open(path) as archive:
            names = [item.name for item in itertools.islice(archive, 81)]
        return Preview(path.name, basic, text='Archive contents\n\n' + '\n'.join(names[:80]) + ('\n…' if len(names) > 80 else ''))
    if ext in {'docx', 'epub', 'odt', 'rtf'} and engine.PANDOC:
        data = local_process([engine.PANDOC, '--sandbox', str(path), '-t', 'plain'], timeout=10)
        return Preview(path.name, basic, text=data[:32000].decode('utf-8', errors='replace') + ('\n\n… Preview shortened.' if len(data) > 32000 else ''))
    if ext in engine.TEXT | {'json', 'srt', 'vtt', 'ass', 'ssa', 'tex', 'py', 'xml', 'yaml', 'yml'}:
        with path.open('rb') as handle:
            data = handle.read(32001)
        text = data[:32000].decode('utf-8-sig', errors='replace')
        if ext == 'json' and len(data) <= 32000:
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except ValueError:
                pass
        return Preview(path.name, basic, text=text + ('\n\n… Preview shortened.' if len(data) > 32000 else ''))
    return Preview(path.name, basic, text='No inline preview for this format. You can open the original file in its default app or choose an available conversion.')
