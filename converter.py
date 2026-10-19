"""Local desktop file converter. Run with Python 3.10 or newer."""
from __future__ import annotations

import io
import gzip
import bz2
import lzma
import math
import os
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
import tkinter as tk
from tkinter import messagebox, font

from PIL import Image, ImageOps
import pymupdf
from engines import find_pandoc
import pillow_heif
from tkinterdnd2 import TkinterDnD, DND_FILES
from tables import FORMATS as TABLES, convert_table

pillow_heif.register_heif_opener()

PANDOC = find_pandoc()


@dataclass(frozen=True)
class Options:
    quality: int = 90
    max_size: int = 0
    rotation: int = 0
    crop: tuple[int, int, int, int] | None = None
    trim_start: float = 0
    trim_end: float = 0
    pdf_dpi: int = 150


IMAGES = {'png', 'jpg', 'jpeg', 'webp', 'bmp', 'tiff', 'tif', 'gif', 'ico', 'heic', 'heif', 'avif'}
AUDIO = {'mp3', 'wav', 'flac', 'ogg', 'm4a', 'aac', 'opus', 'aiff', 'wma'}
VIDEO = {'mp4', 'mkv', 'mov', 'avi', 'webm', 'mpeg', 'm4v', 'flv', 'wmv', 'ts', 'mts', '3gp'}
DOCS = {'md', 'rst', 'org', 'docx', 'epub', 'odt', 'html', 'rtf', 'tex'}
DOC_OUT = {'md', 'txt', 'html', 'docx', 'epub', 'rst', 'odt', 'rtf', 'tex', 'pdf'}
TEXT = {'txt', 'md', 'rst', 'org', 'html', 'log'}


def targets(source: Path) -> list[str]:
    """Return only meaningful routes available on this machine."""
    ext = source.suffix.lower().lstrip('.')
    result = {'zip', 'tar', 'gz', 'bz2', 'xz'}
    if ext in IMAGES:
        result |= IMAGES | {'pdf'}
    if ext == 'pdf':
        result |= {'txt', 'png', 'jpg', 'pdf-pages', 'pdf'}
    if ext in AUDIO and shutil.which('ffmpeg'):
        result |= AUDIO
    if ext in VIDEO and shutil.which('ffmpeg'):
        result |= VIDEO | AUDIO | {'gif'}
    if ext in TABLES:
        result |= TABLES
    if ext in TEXT and ext not in DOCS:
        result.add('txt')
    if PANDOC and (ext in DOCS or ext == 'txt'):
        result |= DOC_OUT
    if ext in {'srt', 'vtt', 'ass', 'ssa'} and shutil.which('ffmpeg'):
        result |= {'srt', 'vtt', 'ass', 'ssa', 'txt'}
    if ext not in IMAGES | {'pdf'}:
        result.discard(ext)
    return sorted(result)


def run(command: list[str]) -> None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=300, shell=False,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Conversion exceeded the five minute time limit.') from exc
    if completed.returncode:
        raise ValueError(completed.stderr.strip()[-2000:] or 'Local engine failed.')


def convert(source: Path, target: str, output_dir: Path, options: Options = Options()) -> list[Path]:
    """Stage each conversion locally, then publish without overwriting any file."""
    if not 1 <= options.quality <= 100 or not 0 <= options.max_size <= 16384:
        raise ValueError('Quality must be 1–100; maximum image size must be 0–16384.')
    if options.rotation not in {0, 90, 180, 270} or not 72 <= options.pdf_dpi <= 600:
        raise ValueError('Rotation must be 0, 90, 180, or 270; PDF DPI must be 72–600.')
    if not all(math.isfinite(value) for value in (options.trim_start, options.trim_end)):
        raise ValueError('Trim times must be finite numbers.')
    if options.trim_start < 0 or options.trim_end < 0 or (options.trim_end and options.trim_end <= options.trim_start):
        raise ValueError('Trim times must be positive and end must be later than start.')
    source = source.expanduser().resolve(strict=True)
    target = target.lower().lstrip('.')
    if not source.is_file() or target not in targets(source):
        raise ValueError(f'Unsupported conversion: {source.suffix} → {target}')
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = source.suffix.lower().lstrip('.')
    published = []
    # A private staging directory also keeps failed engine output out of the destination.
    with tempfile.TemporaryDirectory(prefix='file-converter-', dir=output_dir) as tmp:
        staging = Path(tmp)
        dest = staging / (f'{source.name}.{target}' if target in {'gz', 'bz2', 'xz'} else f'{source.stem}.{target}')
        if target in {'zip', 'tar', 'gz', 'bz2', 'xz'}:
            if target == 'zip':
                with zipfile.ZipFile(dest, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.write(source, arcname=source.name)
            elif target == 'tar':
                with tarfile.open(dest, 'w') as archive:
                    archive.add(source, arcname=source.name)
            else:
                opener = {'gz': gzip.open, 'bz2': bz2.open, 'xz': lzma.open}[target]
                with source.open('rb') as handle, opener(dest, 'wb') as compressed:
                    shutil.copyfileobj(handle, compressed)
        elif ext in IMAGES:
            with Image.open(source) as original:
                picture = ImageOps.exif_transpose(original)
                if options.crop:
                    x, y, width, height = options.crop
                    if min(x, y) < 0 or min(width, height) <= 0 or x + width > picture.width or y + height > picture.height:
                        raise ValueError('Crop must fit within the image: x,y,width,height.')
                    picture = picture.crop((x, y, x + width, y + height))
                if options.rotation:
                    picture = picture.rotate(-options.rotation, expand=True)
                if options.max_size:
                    picture.thumbnail((options.max_size, options.max_size), Image.Resampling.LANCZOS)
                if target in {'jpg', 'jpeg', 'pdf'}:
                    rgba = picture.convert('RGBA')
                    picture = Image.new('RGB', rgba.size, 'white')
                    picture.paste(rgba, mask=rgba.getchannel('A'))
                if target == 'ico':
                    edge = min(256, max(picture.size))
                    picture = ImageOps.pad(picture.convert('RGBA'), (edge, edge), color=(0, 0, 0, 0))
                picture.save(dest, **({'quality': options.quality} if target in {'jpg', 'jpeg', 'webp', 'avif', 'heic', 'heif'} else {}))
        elif ext == 'pdf':
            with pymupdf.open(source) as document:
                if document.needs_pass:
                    raise ValueError('Password-protected PDFs are not supported.')
                if target == 'pdf':
                    document.save(dest, garbage=4, deflate=True)
                elif target == 'pdf-pages':
                    for index in range(len(document)):
                        with pymupdf.open() as single:
                            single.insert_pdf(document, from_page=index, to_page=index)
                            single.save(staging / f'{source.stem}-page-{index + 1:03}.pdf')
                elif target == 'txt':
                    dest.write_text('\n'.join(page.get_text() for page in document), encoding='utf-8')
                else:
                    for index, page in enumerate(document, 1):
                        page.get_pixmap(matrix=pymupdf.Matrix(options.pdf_dpi / 72, options.pdf_dpi / 72), alpha=False).save(
                            staging / f'{source.stem}-page-{index:03}.{target}')
        elif ext in AUDIO | VIDEO | {'srt', 'vtt', 'ass', 'ssa'}:
            command = ['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                       '-i', str(source)]
            if ext in AUDIO | VIDEO:
                if options.trim_start:
                    command += ['-ss', str(options.trim_start)]
                if options.trim_end:
                    command += ['-t', str(options.trim_end - options.trim_start)]
            if target == 'gif':
                command += ['-an', '-vf', 'fps=12,scale=min(640\\,iw):-2']
            if target in AUDIO:
                command += ['-vn']
            if target == 'webm':
                command += ['-c:v', 'libvpx-vp9', '-c:a', 'libopus']
            if target in {'mp4', 'mov', 'm4v', '3gp'}:
                command += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac']
            if target == 'm4v':
                command += ['-f', 'mp4']
            if target == 'txt':
                subtitle = staging / 'intermediate.srt'
                run(command + [str(subtitle)])
                lines = subtitle.read_text(encoding='utf-8-sig').splitlines()
                dest.write_text('\n'.join(line for index, line in enumerate(lines)
                    if '-->' not in line and not (line.strip().isdigit() and index + 1 < len(lines)
                    and '-->' in lines[index + 1])), encoding='utf-8')
                subtitle.unlink()
            else:
                run(command + [str(dest)])
        elif ext in TABLES:
            convert_table(source, dest)
        elif ext in DOCS or ext == 'txt':
            if not PANDOC:
                raise ValueError('Document conversion needs a compatible local Pandoc installation.')
            # Pandoc's sandbox denies network and filesystem access outside input files.
            input_format = {'md': 'markdown', 'txt': 'markdown', 'tex': 'latex'}.get(ext, ext)
            output_format = {'md': 'markdown', 'txt': 'plain', 'tex': 'latex'}.get(target, target)
            intermediate = staging / 'render.html' if target == 'pdf' else dest
            run([PANDOC, '--sandbox'] + ([] if target == 'pdf' else ['--standalone']) +
                ['-f', input_format, '-t', 'html' if target == 'pdf' else output_format,
                 str(source), '-o', str(intermediate)])
            if target == 'pdf':
                html_to_pdf(intermediate.read_text(encoding='utf-8'), dest)
                intermediate.unlink()
        else:
            dest.write_text(source.read_text(encoding='utf-8-sig'), encoding='utf-8')
        outputs = sorted(staging.iterdir())
        if not outputs:
            raise ValueError('No output was produced.')
        try:
            for item in outputs:
                final = output_dir / item.name
                # Exclusive creation is race-safe and preserves existing user files.
                with final.open('xb') as handle:
                    published.append(final)
                    with item.open('rb') as data:
                        shutil.copyfileobj(data, handle)
        except Exception:
            for item in published:
                item.unlink(missing_ok=True)
            raise
    return published


def html_to_pdf(html: str, destination: Path) -> None:
    # Story uses only provided data; no URL fetcher or external resource archive is installed.
    story = pymupdf.Story(html=html)
    page = pymupdf.Rect(0, 0, 595, 842)
    content = page + (40, 40, -40, -40)
    with pymupdf.DocumentWriter(str(destination)) as writer:
        more = True
        while more:
            device = writer.begin_page(page)
            more, _ = story.place(content)
            story.draw(device)
            writer.end_page()


def merge_pdf(sources: tuple[Path, ...], output_dir: Path) -> list[Path]:
    """Combine PDFs and images in queue order; never replace an existing PDF."""
    if not sources:
        raise ValueError('Select at least one PDF or image.')
    with pymupdf.open() as combined:
        for source in sources:
            if source.suffix.lower() == '.pdf':
                with pymupdf.open(source) as document:
                    if document.needs_pass:
                        raise ValueError('Password-protected PDFs are not supported.')
                    combined.insert_pdf(document)
            elif source.suffix.lower().lstrip('.') in IMAGES:
                with Image.open(source) as original:
                    image = ImageOps.exif_transpose(original).convert('RGBA')
                    background = Image.new('RGB', image.size, 'white')
                    background.paste(image, mask=image.getchannel('A'))
                    buffer = io.BytesIO()
                    background.save(buffer, format='PDF')
                    with pymupdf.open(stream=buffer.getvalue(), filetype='pdf') as page:
                        combined.insert_pdf(page)
            else:
                raise ValueError('Combine as PDF accepts only PDFs and images.')
        data = combined.tobytes(garbage=4, deflate=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    result = output_dir / 'combined.pdf'
    with result.open('xb') as handle:
        try:
            handle.write(data)
        except Exception:
            result.unlink(missing_ok=True)
            raise
    return [result]


from desktop import DesktopApp


class App(DesktopApp):
    def __init__(self, root):
        super().__init__(root, sys.modules[__name__])


def create_window():
    import customtkinter as ctk
    ctk.set_appearance_mode('System')
    class DropWindow(ctk.CTk, TkinterDnD.DnDWrapper):
        def destroy(self):
            # CustomTkinter schedules theme/scrollbar callbacks. Cancel this
            # interpreter's timers before widgets disappear, including in tests.
            for callback in self.tk.splitlist(self.tk.call('after', 'info')):
                self.tk.call("after", "cancel", callback)
            super().destroy()
    window = DropWindow()
    if sys.platform.startswith('linux'):
        families = set(font.families(window))
        if 'Roboto' not in families:
            ctk.ThemeManager.theme['CTkFont']['family'] = next((name for name in ('Helvetica', 'helvetica', 'DejaVu Sans') if name in families), 'sans-serif')
    try:
        window.TkdndVersion = TkinterDnD.require(window)
        window.dnd_ready = True
    except (RuntimeError, tk.TclError):
        window.dnd_ready = False
    return window


if __name__ == '__main__':
    window = create_window()
    App(window)
    window.mainloop()
