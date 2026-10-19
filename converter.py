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
import queue
import shutil
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, font

from PIL import Image, ImageOps
import pymupdf
import pypandoc
import pillow_heif
from tkinterdnd2 import TkinterDnD, DND_FILES
from tables import FORMATS as TABLES, convert_table

pillow_heif.register_heif_opener()

PANDOC = pypandoc.get_pandoc_path()


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
    if ext in TEXT:
        result.add('txt')
    if ext in DOCS or ext == 'txt':
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


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.files: list[Path] = []
        self.events = queue.Queue()
        self.stop = threading.Event()
        families = set(font.families(root))
        self.family = next((name for name in ('Segoe UI', 'SF Pro Text', 'Helvetica', 'helvetica', 'DejaVu Sans')
                            if name in families), 'sans-serif')
        font.nametofont('TkDefaultFont').configure(family=self.family, size=10)
        root.title('Local - File Converter')
        root.geometry('820x640')
        root.minsize(680, 540)
        root.configure(bg='#f4f6fa')
        style = ttk.Style(root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('TFrame', background='#f4f6fa')
        style.configure('TLabel', background='#f4f6fa', font=(self.family, 11))
        style.configure('TButton', padding=(14, 9), font=(self.family, 10))
        style.configure('Accent.TButton', background='#2563eb', foreground='white')
        style.map('Accent.TButton', background=[('active', '#1d4ed8'), ('disabled', '#b7c4d8')])
        style.configure('Title.TLabel', font=(self.family, 26, 'bold'), foreground='#182338')
        panel = ttk.Frame(root, padding=28)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Make your files work for you.', style='Title.TLabel').pack(anchor='w')
        ttk.Label(panel, text='Convert on your device. Your files stay here.').pack(anchor='w', pady=(6, 18))
        toolbar = ttk.Frame(panel)
        toolbar.pack(fill='x')
        self.add = ttk.Button(toolbar, text='+  Add files', command=self.choose)
        self.add.pack(side='left')
        self.clear = ttk.Button(toolbar, text='Clear queue', command=self.reset)
        self.clear.pack(side='left', padx=8)
        self.add_folder = ttk.Button(toolbar, text='Add folder', command=self.choose_batch_folder)
        self.add_folder.pack(side='left')
        self.tool_values = {key: tk.StringVar(value=value) for key, value in
                            {'rotation': '0', 'crop': '', 'trim_start': '0', 'trim_end': '0', 'pdf_dpi': '150'}.items()}
        self.tools = ttk.Button(toolbar, text='Tools...', command=self.edit_tools)
        self.tools.pack(side='left', padx=8)
        self.remove = ttk.Button(toolbar, text='Remove selected', command=self.remove_selected)
        self.remove.pack(side='left')
        self.listbox = tk.Listbox(panel, height=8, relief='flat', bg='white', fg='#27364c',
                                  font=(self.family, 11), selectbackground='#dbeafe')
        self.listbox.pack(fill='both', expand=True, pady=14)
        self.listbox.bind('<Delete>', lambda event: self.remove_selected() if not self.busy else None)
        self.listbox.bind('<Control-c>', lambda event: self.copy_paths())
        root.bind('<Control-o>', lambda event: self.choose() if not self.busy else None)
        if getattr(root, 'dnd_ready', hasattr(root, 'TkdndVersion')):
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind('<<Drop>>', self.drop_files)
        options = ttk.Frame(panel)
        options.pack(fill='x')
        ttk.Label(options, text='Convert to').pack(side='left')
        self.format = ttk.Combobox(options, state='readonly', width=12)
        self.format.pack(side='left', padx=12)
        self.quality = tk.StringVar(value='90')
        self.size = tk.StringVar(value='0')
        ttk.Label(options, text='Image quality').pack(side='left')
        self.quality_entry = ttk.Spinbox(options, from_=1, to=100, width=4, textvariable=self.quality)
        self.quality_entry.pack(side='left', padx=8)
        ttk.Label(options, text='Max pixels (0 = original)').pack(side='left')
        self.size_entry = ttk.Entry(options, textvariable=self.size, width=6)
        self.size_entry.pack(side='left', padx=8)
        self.folder = tk.StringVar(value=str(Path.home() / 'Converted Files'))
        ttk.Label(panel, text='Save files to').pack(anchor='w', pady=(16, 5))
        row = ttk.Frame(panel)
        row.pack(fill='x')
        self.entry = ttk.Entry(row, textvariable=self.folder)
        self.entry.pack(side='left', fill='x', expand=True)
        self.browse = ttk.Button(row, text='Browse...', command=self.choose_folder)
        self.browse.pack(side='left', padx=(8, 0))
        actions = ttk.Frame(panel)
        actions.pack(fill='x', pady=(18, 10))
        self.go = ttk.Button(actions, text='Convert files', style='Accent.TButton', command=self.start)
        self.go.pack(side='left')
        self.merge = ttk.Button(actions, text='Combine as PDF', command=lambda: self.start(merge=True))
        self.merge.pack(side='left', padx=8)
        self.cancel = ttk.Button(actions, text='Stop after current file', command=self.stop.set, state='disabled')
        self.cancel.pack(side='left', padx=8)
        ttk.Button(actions, text='Show output', command=self.show_output).pack(side='right')
        self.progress = ttk.Progressbar(panel, mode='determinate')
        self.progress.pack(fill='x')
        self.status = tk.StringVar(value='Add files to see compatible output formats.')
        ttk.Label(panel, textvariable=self.status, wraplength=720).pack(anchor='w', pady=8)
        engines = ', '.join(f'{name}: {"available" if shutil.which(name) else "not installed"}'
                            for name in ('ffmpeg',)) + ' · Pandoc: bundled' + ('' if getattr(root, 'dnd_ready', False) else ' · Use Add files if drag/drop is unavailable')
        ttk.Label(panel, text=engines, foreground='#58677e').pack(anchor='w')
        self.busy = False
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def remove_selected(self):
        for index in reversed(self.listbox.curselection()):
            self.files.pop(index)
            self.listbox.delete(index)
        self.add_paths(())

    def copy_paths(self):
        paths = [str(self.files[index]) for index in self.listbox.curselection()]
        if paths:
            self.root.clipboard_clear()
            self.root.clipboard_append('\n'.join(paths))

    def show_output(self):
        folder = Path(self.folder.get()).expanduser().resolve()
        if not folder.is_dir():
            messagebox.showinfo('Output folder', 'The output folder will be created when you convert.')
            return
        try:
            if sys.platform == 'win32':
                os.startfile(folder)
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(folder)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            messagebox.showerror('Could not open folder', str(exc))

    def edit_tools(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('Conversion tools')
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill='both', expand=True)
        labels = {'rotation': 'Rotate image clockwise (0/90/180/270)',
                  'crop': 'Crop image: x,y,width,height (blank = full image)',
                  'trim_start': 'Audio/video start in seconds (0 = beginning)',
                  'trim_end': 'Audio/video end in seconds (0 = end)',
                  'pdf_dpi': 'PDF image export DPI (72–600)'}
        for index, (key, label) in enumerate(labels.items()):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky='w', padx=8, pady=8)
            ttk.Entry(frame, textvariable=self.tool_values[key], width=18).grid(row=index, column=1)
        ttk.Label(frame, text='Settings apply to matching file types only. Crop precedes rotation and resize.').grid(
            row=5, column=0, columnspan=2, pady=12)
        ttk.Button(frame, text='Done', command=dialog.destroy).grid(row=6, column=1, sticky='e')

    def drop_files(self, event):
        if not self.busy:
            paths = [Path(path) for path in self.root.tk.splitlist(event.data)]
            files = []
            for path in paths:
                if path.is_dir():
                    files.extend(str(item) for item in sorted(path.iterdir()) if item.is_file())
                elif path.is_file():
                    files.append(str(path))
            self.add_paths(files)

    def choose(self):
        self.add_paths(filedialog.askopenfilenames(title='Select files to convert'))

    def choose_batch_folder(self):
        folder = filedialog.askdirectory(title='Add files in folder (not recursive)')
        if folder:
            self.add_paths(str(path) for path in sorted(Path(folder).iterdir()) if path.is_file())

    def add_paths(self, names):
        for name in names:
            path = Path(name)
            if path not in self.files:
                self.files.append(path)
                self.listbox.insert('end', f'{path.name}   ·   {path.parent}')
        common = set.intersection(*(set(targets(path)) for path in self.files)) if self.files else set()
        self.format['values'] = sorted(common)
        preferred = ['png', 'mp4', 'mp3', 'docx', 'xlsx', 'txt', 'zip']
        self.format.set(next((fmt for fmt in preferred if fmt in common), sorted(common)[0] if common else ''))
        self.status.set(f'{len(self.files)} file(s) selected.' if common else
                        'No shared output format. Clear the queue and select supported files of a similar type.')

    def reset(self):
        self.files.clear()
        self.listbox.delete(0, 'end')
        self.format.set('')
        self.format['values'] = []
        self.status.set('Add files to begin.')

    def choose_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder.set(folder)

    def start(self, merge=False):
        if self.busy or not self.files or (not merge and not self.format.get()) or not self.folder.get().strip():
            messagebox.showinfo('Choose files and format', 'Select supported files, an output format, and a destination.')
            return
        try:
            values = {key: var.get().strip() for key, var in self.tool_values.items()}
            crop = tuple(int(value.strip()) for value in values['crop'].split(',')) if values['crop'] else None
            if crop is not None and len(crop) != 4:
                raise ValueError
            options = Options(int(self.quality.get()), int(self.size.get()), int(values['rotation']), crop,
                              float(values['trim_start']), float(values['trim_end']), int(values['pdf_dpi']))
            if not 1 <= options.quality <= 100 or not 0 <= options.max_size <= 16384:
                raise ValueError
        except ValueError:
            messagebox.showerror('Invalid settings', 'Check quality (1–100), size (0–16384), and numeric values in Tools.')
            return
        self.busy = True
        self.stop.clear()
        self.progress.configure(maximum=len(self.files), value=0)
        for widget in (self.add, self.add_folder, self.tools, self.remove, self.clear, self.go, self.merge, self.browse, self.entry, self.format, self.quality_entry, self.size_entry):
            widget.configure(state='disabled')
        self.cancel.configure(state='normal')
        threading.Thread(target=self.worker, args=(tuple(self.files), '__merge__' if merge else self.format.get(), Path(self.folder.get()), options),
                         daemon=True).start()

    def worker(self, files, target, folder, options):
        if target == '__merge__':
            self.events.put(('status', 'Combining files as PDF...'))
            try:
                merge_pdf(files, folder)
                self.events.put(('progress', len(files)))
                self.events.put(('done', (len(files), 0, 0, [])))
            except Exception as exc:
                self.events.put(('done', (0, len(files), 0, [str(exc)])))
            return
        succeeded = failed = 0
        errors = []
        for index, source in enumerate(files):
            if self.stop.is_set():
                break
            self.events.put(('status', f'Converting {source.name}…'))
            try:
                convert(source, target, folder, options)
                succeeded += 1
            except Exception as exc:
                failed += 1
                errors.append(f'{source.name}: {exc}')
            self.events.put(('progress', index + 1))
        self.events.put(('done', (succeeded, failed, len(files) - succeeded - failed, errors)))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'status':
                    self.status.set(value)
                elif kind == 'progress':
                    self.progress['value'] = value
                else:
                    good, bad, skipped, errors = value
                    self.status.set(f'{good} converted · {bad} failed · {skipped} skipped. Existing files are never replaced.')
                    self.busy = False
                    for widget in (self.add, self.add_folder, self.tools, self.remove, self.clear, self.go, self.merge, self.browse, self.entry, self.quality_entry, self.size_entry):
                        widget.configure(state='normal')
                    self.format.configure(state='readonly')
                    self.cancel.configure(state='disabled')
                    if errors:
                        messagebox.showwarning('Some files could not be converted', '\n\n'.join(errors)[:8000])
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo('Conversion running', 'Stop after the current file, then close the app.')
        else:
            self.root.destroy()


def create_window():
    window = tk.Tk()
    try:
        window.TkdndVersion = TkinterDnD._require(window)
        window.dnd_ready = True
    except (RuntimeError, tk.TclError):
        window.dnd_ready = False
    return window


if __name__ == '__main__':
    window = create_window()
    App(window)
    window.mainloop()
