"""Modern, local desktop workflow built with CustomTkinter."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import math
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from tkinterdnd2 import DND_FILES

from previews import Preview, preview_file, size_label

BG = ('#f5f5f8', '#14151b')
CARD = ('#ffffff', '#1e2029')
TEXT = ('#202333', '#eef0fa')
MUTED = ('#73778b', '#a1a5b9')
LINE = ('#e8e9f1', '#303340')
SOFT = ('#f0edff', '#30284b')
ACCENT = '#7460ed'
HOVER = '#6250d7'
GREEN = ('#22816a', '#70d7b3')
RED = ('#ba4355', '#ff94a4')


class AutoScrollFrame(ctk.CTkScrollableFrame):
    """Keep the layout quiet until there is actually content to scroll."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scroll_visible = True
        self._parent_canvas.configure(yscrollcommand=self.update_scrollbar)

    def update_scrollbar(self, first, last):
        self._scrollbar.set(first, last)
        needed = float(first) > 0.001 or float(last) < 0.999
        if needed != self.scroll_visible:
            self.scroll_visible = needed
            if needed:
                self._scrollbar.grid()
            else:
                self._scrollbar.grid_remove()


class DesktopApp:
    def __init__(self, root, engine):
        self.root, self.engine = root, engine
        self.files = []
        self.selected = None
        self.rows = {}
        self.file_states = {}
        self.errors = []
        self.busy = False
        self.closed = False
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.preview_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='preview')
        self.preview_future = None
        self.preview_version = 0
        self.preview_data = None
        self.preview_image = None
        self.preview_loading = False
        self.options_open = False
        self.last_merge = False
        self.quality = tk.StringVar(value='90')
        self.size = tk.StringVar(value='0')
        self.folder = tk.StringVar(value=str(Path.home() / 'Converted Files'))
        self.status = tk.StringVar(value='Everything stays on your device.')
        self.tool_values = {key: tk.StringVar(value=value) for key, value in
                           {'rotation': '0', 'crop': '', 'trim_start': '0', 'trim_end': '0', 'pdf_dpi': '150'}.items()}
        self.format_map = {}
        root.title('Local — File Converter')
        root.geometry(f'{min(1240, max(1040, root.winfo_screenwidth() - 70))}x{min(850, max(700, root.winfo_screenheight() - 90))}')
        root.minsize(1040, 700)
        root.configure(fg_color=BG)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)
        self.build_header()
        self.build_workspace()
        self.build_footer()
        self.refresh()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Destroy>', self.on_destroy, add='+')
        root.bind('<Control-o>', lambda e: self.choose() if not self.busy else None)
        root.bind('<Delete>', lambda e: self.remove_selected() if not self.busy else None)
        root.bind('<Control-c>', lambda e: self.copy_paths() if root.focus_get() == root else None)
        if sys.platform == 'darwin':
            root.bind('<Command-o>', lambda e: self.choose() if not self.busy else None)
        if getattr(root, 'dnd_ready', False):
            for widget in (root, self.empty, self.drop_area, self.queue_frame):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind('<<Drop>>', self.drop_files)
        self.poll_id = root.after(50, self.poll)

    def label(self, parent, text='', size=13, color=TEXT, bold=False, **kwargs):
        return ctk.CTkLabel(parent, text=text, text_color=color,
                            font=ctk.CTkFont(size=size, weight='bold' if bold else 'normal'), **kwargs)

    def button(self, parent, text, command, primary=False, **kwargs):
        return ctk.CTkButton(parent, text=text, command=command, height=38, corner_radius=10,
            fg_color=ACCENT if primary else SOFT, hover_color=HOVER if primary else LINE,
            text_color='white' if primary else TEXT, font=ctk.CTkFont(size=13, weight='bold'), **kwargs)

    def build_header(self):
        header = ctk.CTkFrame(self.root, fg_color='transparent')
        header.grid(row=0, column=0, sticky='ew', padx=30, pady=(24, 20))
        header.grid_columnconfigure(1, weight=1)
        self.label(header, 'L', size=22, color='white', bold=True, width=38, height=38, fg_color=ACCENT, corner_radius=11).grid(row=0, column=0, rowspan=2, padx=(0, 10))
        self.label(header, 'local / converter', size=20, bold=True, anchor='w').grid(row=0, column=1, sticky='w')
        self.label(header, 'A new format. All on your device.', color=MUTED, anchor='w').grid(row=1, column=1, sticky='w')
        pill = ctk.CTkFrame(header, fg_color=SOFT, corner_radius=12)
        pill.grid(row=0, column=2, rowspan=2, padx=18)
        self.label(pill, '●  100% local', color=ACCENT, size=12).pack(padx=14, pady=8)
        self.theme = ctk.CTkOptionMenu(header, values=['System', 'Light', 'Dark'], width=100,
            fg_color=CARD, button_color=CARD, button_hover_color=LINE, text_color=TEXT,
            dropdown_fg_color=CARD, dropdown_text_color=TEXT, dropdown_hover_color=SOFT,
            command=ctk.set_appearance_mode, corner_radius=10)
        self.theme.set('System')
        self.theme.grid(row=0, column=3, rowspan=2)

    def build_workspace(self):
        self.body = ctk.CTkFrame(self.root, fg_color='transparent')
        self.body.grid(row=1, column=0, sticky='nsew', padx=30)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self.empty = ctk.CTkFrame(self.body, fg_color=CARD, corner_radius=22)
        self.empty.grid(row=0, column=0, sticky='nsew')
        center = ctk.CTkFrame(self.empty, fg_color='transparent')
        center.place(relx=.5, rely=.46, anchor='center')
        self.label(center, '↗', size=54, color=ACCENT).pack(pady=(0, 14))
        self.label(center, 'New possibilities for your files.', size=30, bold=True).pack()
        self.label(center, 'Images, documents, media, and more. No uploads. No waiting on a server.',
                   color=MUTED, size=14).pack(pady=(12, 30))
        self.drop_area = ctk.CTkFrame(center, fg_color=BG, corner_radius=18, border_width=1, border_color=LINE,
                                    width=530, height=150)
        self.drop_area.pack()
        self.drop_area.pack_propagate(False)
        self.label(self.drop_area, 'Drop files or a folder here', size=17, bold=True).pack(pady=(25, 5))
        self.label(self.drop_area, 'or choose something to get started', color=MUTED).pack()
        self.empty_add = self.button(self.drop_area, 'Choose files', self.choose, primary=True, width=150)
        self.empty_add.pack(pady=14)
        self.label(center, 'A preview and the right tools will appear when you add a file.', color=MUTED).pack(pady=24)
        self.workspace = ctk.CTkFrame(self.body, fg_color='transparent')
        self.workspace.grid_columnconfigure(1, weight=1, minsize=330)
        self.workspace.grid_columnconfigure(0, minsize=230)
        self.workspace.grid_columnconfigure(2, minsize=282)
        self.workspace.grid_rowconfigure(0, weight=1)
        self.build_queue()
        self.build_preview()
        self.build_settings()

    def build_queue(self):
        sidebar = ctk.CTkFrame(self.workspace, fg_color=CARD, corner_radius=18, width=230)
        sidebar.grid(row=0, column=0, sticky='nsew', padx=(0, 14))
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(2, weight=1)
        top = ctk.CTkFrame(sidebar, fg_color='transparent')
        top.grid(row=0, column=0, sticky='ew', padx=16, pady=(18, 12))
        self.label(top, 'Your files', size=17, bold=True).pack(side='left')
        self.count = self.label(top, '0', color=MUTED)
        self.count.pack(side='right')
        bar = ctk.CTkFrame(sidebar, fg_color='transparent')
        bar.grid(row=1, column=0, sticky='ew', padx=14, pady=(0, 8))
        self.add = self.button(bar, '+ Files', self.choose, width=95)
        self.add.pack(side='left', padx=(0, 6))
        self.add_folder = self.button(bar, '+ Folder', self.choose_batch_folder, width=95)
        self.add_folder.pack(side='left')
        self.queue_frame = AutoScrollFrame(sidebar, fg_color='transparent', width=200,
                                                scrollbar_button_color=LINE, corner_radius=0)
        self.queue_frame.grid(row=2, column=0, sticky='nsew', padx=8)
        self.clear = ctk.CTkButton(sidebar, text='Clear all files', fg_color='transparent', text_color=MUTED,
                                  hover_color=BG, command=self.reset, height=36)
        self.clear.grid(row=3, column=0, sticky='ew', padx=14, pady=12)

    def build_preview(self):
        panel = ctk.CTkFrame(self.workspace, fg_color=CARD, corner_radius=18)
        panel.grid(row=0, column=1, sticky='nsew', padx=(0, 14))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)
        self.label(panel, 'PREVIEW', size=11, color=MUTED, bold=True, anchor='w').grid(row=0, column=0, sticky='ew', padx=22, pady=(20, 9))
        self.preview_title = self.label(panel, '', size=17, bold=True, anchor='w')
        self.preview_title.grid(row=1, column=0, sticky='ew', padx=22, pady=(0, 16))
        self.viewer = ctk.CTkFrame(panel, fg_color=BG, corner_radius=12)
        self.viewer.grid(row=2, column=0, sticky='nsew', padx=18)
        self.viewer.grid_columnconfigure(0, weight=1)
        self.viewer.grid_rowconfigure(0, weight=1)
        self.image_label = ctk.CTkLabel(self.viewer, text='', fg_color='transparent')
        self.preview_text = ctk.CTkTextbox(self.viewer, fg_color=BG, text_color=TEXT,
                                         font=ctk.CTkFont(size=13), wrap='word', corner_radius=12)
        self.preview_message = self.label(self.viewer, 'Preparing preview…', color=MUTED, wraplength=280)
        self.viewer.bind('<Configure>', self.resize_preview)
        self.page_bar = ctk.CTkFrame(panel, fg_color='transparent')
        self.previous_page = self.button(self.page_bar, '←', lambda: self.change_page(-1), width=40)
        self.previous_page.pack(side='left')
        self.page_label = self.label(self.page_bar, 'Page 1 of 1', color=MUTED, width=130)
        self.page_label.pack(side='left', padx=8)
        self.next_page = self.button(self.page_bar, '→', lambda: self.change_page(1), width=40)
        self.next_page.pack(side='left')
        self.preview_meta = self.label(panel, '', color=MUTED, size=12, anchor='w', wraplength=330)
        self.preview_meta.grid(row=4, column=0, sticky='ew', padx=22, pady=(16, 8))
        bottom = ctk.CTkFrame(panel, fg_color='transparent')
        bottom.grid(row=5, column=0, sticky='ew', padx=18, pady=(0, 18))
        self.open_original = self.button(bottom, 'Open original ↗', self.open_selected, width=155)
        self.open_original.pack(side='left')
        self.copy = ctk.CTkButton(bottom, text='Copy path', command=self.copy_paths, fg_color='transparent',
                                  text_color=MUTED, hover_color=BG, width=90)
        self.copy.pack(side='right')

    def build_settings(self):
        self.settings = ctk.CTkFrame(self.workspace, fg_color=CARD, corner_radius=18, width=282)
        self.settings.grid(row=0, column=2, sticky='nsew')
        self.settings.grid_propagate(False)
        self.settings.grid_columnconfigure(0, weight=1)
        self.settings.grid_rowconfigure(0, weight=1)
        self.controls = AutoScrollFrame(self.settings, fg_color='transparent', width=250, corner_radius=0)
        self.controls.grid(row=0, column=0, sticky='nsew', padx=8, pady=(12, 0))
        self.label(self.controls, 'Make it yours', size=19, bold=True, anchor='w').pack(fill='x', padx=10, pady=(4, 6))
        self.settings_hint = self.label(self.controls, 'Choose a format for your queue.', color=MUTED, anchor='w', wraplength=230)
        self.settings_hint.pack(fill='x', padx=10, pady=(0, 20))
        self.label(self.controls, 'CONVERT TO', color=MUTED, size=11, bold=True, anchor='w').pack(fill='x', padx=10, pady=(0, 8))
        self.format = ctk.CTkComboBox(self.controls, values=[], state='readonly', command=lambda value: self.update_context(),
            height=44, corner_radius=10, fg_color=BG, border_color=LINE, button_color=SOFT,
            button_hover_color=LINE, text_color=TEXT, dropdown_fg_color=CARD, dropdown_text_color=TEXT,
            dropdown_hover_color=SOFT, font=ctk.CTkFont(size=14))
        self.format.pack(fill='x', padx=10)
        self.format.set('')
        self.route_hint = self.label(self.controls, '', color=MUTED, size=12, wraplength=225, anchor='w', justify='left')
        self.route_hint.pack(fill='x', padx=10, pady=(8, 14))
        self.options_button = ctk.CTkButton(self.controls, text='Customize conversion  +', command=self.toggle_options,
            fg_color='transparent', hover_color=BG, text_color=ACCENT, anchor='w', height=36)
        self.options_button.pack(fill='x', padx=4)
        self.options_frame = ctk.CTkFrame(self.controls, fg_color=BG, corner_radius=12)
        self.image_options = ctk.CTkFrame(self.options_frame, fg_color='transparent')
        self.quality_row = self.setting_entry(self.image_options, 'Image quality · 1–100', self.quality)
        self.setting_entry(self.image_options, 'Max edge in px · 0 = original', self.size)
        self.setting_entry(self.image_options, 'Rotate clockwise · 0 / 90 / 180 / 270', self.tool_values['rotation'])
        self.setting_entry(self.image_options, 'Crop · x, y, width, height', self.tool_values['crop'])
        self.media_options = ctk.CTkFrame(self.options_frame, fg_color='transparent')
        self.setting_entry(self.media_options, 'Start at · seconds', self.tool_values['trim_start'])
        self.setting_entry(self.media_options, 'End at · 0 = end of file', self.tool_values['trim_end'])
        self.pdf_options = ctk.CTkFrame(self.options_frame, fg_color='transparent')
        self.setting_entry(self.pdf_options, 'Page export DPI · 72–600', self.tool_values['pdf_dpi'])
        self.destination = ctk.CTkFrame(self.controls, fg_color='transparent')
        self.destination.pack(fill='x', padx=10, pady=(20, 8))
        self.label(self.destination, 'SAVE TO', size=11, color=MUTED, bold=True, anchor='w').pack(fill='x')
        self.folder_label = self.label(self.destination, '', wraplength=220, justify='left', anchor='w', size=12)
        self.folder_label.pack(fill='x', pady=(9, 4))
        self.browse = ctk.CTkButton(self.destination, text='Change folder', command=self.choose_folder,
            fg_color='transparent', text_color=ACCENT, hover_color=BG, width=110, anchor='w', height=30)
        self.browse.pack(anchor='w')
        self.folder.trace_add('write', lambda *args: self.update_folder_label())
        self.update_folder_label()
        self.engine_hint = self.label(self.controls, '', color=MUTED, size=12, wraplength=225, justify='left')
        self.engine_hint.pack(fill='x', padx=10, pady=10)
        self.actions = ctk.CTkFrame(self.settings, fg_color='transparent')
        self.actions.grid(row=1, column=0, sticky='ew', padx=18, pady=(12, 18))
        self.merge = self.button(self.actions, 'Combine as PDF', lambda: self.start(merge=True))
        self.go = self.button(self.actions, 'Convert file  ↗', self.start, primary=True)
        self.go.pack(fill='x', pady=(8, 0))
        self.cancel = self.button(self.actions, 'Stop after current file', self.request_stop)
        self.output = self.button(self.actions, 'Open output folder ↗', self.show_output)
        self.retry_button = self.button(self.actions, 'Retry unfinished files', lambda: self.start(merge=self.last_merge, retry=True))

    def setting_entry(self, parent, label, variable):
        row = ctk.CTkFrame(parent, fg_color='transparent')
        row.pack(fill='x', padx=12, pady=(10, 2))
        self.label(row, label, color=MUTED, size=11, anchor='w', wraplength=210).pack(fill='x')
        ctk.CTkEntry(row, textvariable=variable, height=32, fg_color=CARD, border_color=LINE,
                     corner_radius=8).pack(fill='x', pady=(4, 6))
        return row

    def build_footer(self):
        self.footer = ctk.CTkFrame(self.root, fg_color='transparent')
        self.footer.grid(row=2, column=0, sticky='ew', padx=32, pady=(14, 18))
        self.footer.grid_columnconfigure(0, weight=1)
        self.status_label = self.label(self.footer, '', color=MUTED, anchor='w', wraplength=850)
        self.status_label.configure(textvariable=self.status)
        self.status_label.grid(row=1, column=0, sticky='ew')
        self.progress = ctk.CTkProgressBar(self.footer, height=4, corner_radius=4, fg_color=LINE, progress_color=ACCENT)
        self.progress.set(0)
        self.details = ctk.CTkButton(self.footer, text='Show details', command=self.toggle_errors, width=100,
            fg_color='transparent', text_color=RED, hover_color=SOFT, height=28)
        self.error_box = ctk.CTkTextbox(self.footer, height=110, fg_color=CARD, text_color=RED, corner_radius=12, wrap='word')

    def update_folder_label(self):
        value = self.folder.get()
        home = str(Path.home())
        self.folder_label.configure(text='~' + value[len(home):] if value.startswith(home + os.sep) else value)

    def target(self):
        return self.format_map.get(self.format.get(), self.format.get().lower())

    def set_target(self, target):
        label = next((label for label, value in self.format_map.items() if value == target), target.upper())
        self.format.set(label)
        self.update_context()

    def refresh(self):
        if not self.files:
            for widget in self.queue_frame.winfo_children():
                widget.destroy()
            self.rows.clear()
            self.workspace.grid_remove()
            self.empty.grid(row=0, column=0, sticky='nsew')
            self.format.set('')
            self.format.configure(values=[])
            self.preview_version += 1
            self.selected = None
            self.preview_data = None
            self.preview_image = None
            return
        self.empty.grid_remove()
        self.workspace.grid(row=0, column=0, sticky='nsew')
        self.count.configure(text=str(len(self.files)))
        old = self.target()
        common = set.intersection(*(set(self.engine.targets(path)) for path in self.files))
        self.format_map = {('PDF · split pages' if fmt == 'pdf-pages' else fmt.upper()): fmt for fmt in sorted(common)}
        self.format.configure(values=list(self.format_map))
        preferred = ['png', 'mp4', 'mp3', 'docx', 'xlsx', 'txt', 'zip']
        source_exts = {path.suffix.lower().lstrip('.') for path in self.files}
        selected_target = old if old in common else next((fmt for fmt in preferred if fmt in common and fmt not in source_exts), next(iter(sorted(common)), ''))
        self.format.set(next((label for label, fmt in self.format_map.items() if fmt == selected_target), ''))
        self.render_queue()
        if self.selected not in self.files:
            self.select_file(self.files[0])
        self.update_context()

    def render_queue(self):
        for widget in self.queue_frame.winfo_children():
            widget.destroy()
        self.rows.clear()
        for path in self.files:
            active = path == self.selected
            card = ctk.CTkFrame(self.queue_frame, fg_color=SOFT if active else 'transparent', corner_radius=12,
                                border_width=1 if active else 0, border_color=('#d8d0ff', '#53457d'))
            card.pack(fill='x', pady=4)
            card.grid_columnconfigure(1, weight=1)
            ext = path.suffix[1:].upper() or 'FILE'
            icon = self.label(card, ext[:4], color=ACCENT, bold=True, size=10, width=38, height=38,
                               fg_color=BG, corner_radius=8)
            icon.grid(row=0, column=0, rowspan=2, padx=(8, 8), pady=10)
            name = self.label(card, self.shorten(path.name, 18), bold=True, size=12, anchor='w')
            name.grid(row=0, column=1, sticky='ew', pady=(9, 0))
            state, error = self.file_states.get(path, ('Ready', ''))
            color = GREEN if state == 'Converted' else RED if state == 'Failed' else MUTED
            try:
                meta = f'{size_label(path.stat().st_size)} · {state}'
            except OSError:
                meta = 'File unavailable'
            note = self.label(card, meta, color=color, size=10, anchor='w')
            note.grid(row=1, column=1, sticky='ew', pady=(0, 9))
            remove = ctk.CTkButton(card, text='×', width=24, height=26, corner_radius=6,
                fg_color='transparent', hover_color=LINE, text_color=MUTED,
                command=lambda p=path: self.remove_file(p), state='disabled' if self.busy else 'normal')
            remove.grid(row=0, column=2, rowspan=2, padx=4)
            for widget in (card, name, note, icon):
                widget.bind('<Button-1>', lambda event, p=path: self.select_file(p))
            self.rows[path] = card

    @staticmethod
    def shorten(text, maximum):
        return text if len(text) <= maximum else text[:maximum - 1] + '…'

    def select_file(self, path, page=0):
        if path not in self.files or self.closed:
            return
        self.selected = path
        self.render_queue()
        self.preview_version += 1
        version = self.preview_version
        self.preview_loading = True
        self.preview_data = None
        self.preview_title.configure(text=self.shorten(path.name, 42))
        self.preview_meta.configure(text='Reading a local preview…')
        self.image_label.grid_remove()
        self.preview_text.grid_remove()
        self.page_bar.grid_remove()
        self.preview_message.configure(text='Preparing preview…')
        self.preview_message.grid(row=0, column=0)
        if self.preview_future:
            self.preview_future.cancel()
        def load():
            try:
                data = preview_file(path, self.engine, page)
            except Exception:
                data = Preview(path.name, 'Preview unavailable', text='This file could not be previewed. Try opening the original. Other files in your queue are unaffected.')
            self.events.put(('preview', (version, data)))
        self.preview_future = self.preview_pool.submit(load)

    def show_preview(self, data):
        self.preview_loading = False
        self.preview_data = data
        self.preview_message.grid_remove()
        self.preview_meta.configure(text=data.detail + '  ·  Original file')
        if data.image is not None:
            self.preview_text.grid_remove()
            self.image_label.grid(row=0, column=0, sticky='nsew', padx=16, pady=16)
            self.resize_preview()
        else:
            self.image_label.grid_remove()
            self.preview_image = None
            self.preview_text.grid(row=0, column=0, sticky='nsew', padx=8, pady=8)
            self.preview_text.configure(state='normal')
            self.preview_text.delete('1.0', 'end')
            self.preview_text.insert('1.0', data.text or 'No content to preview.')
            self.preview_text.configure(state='disabled')
        if data.pages > 1:
            self.page_label.configure(text=f'Page {data.page + 1} of {data.pages}')
            self.previous_page.configure(state='normal' if data.page > 0 else 'disabled')
            self.next_page.configure(state='normal' if data.page < data.pages - 1 else 'disabled')
            self.page_bar.grid(row=3, column=0, pady=(12, 0))
        else:
            self.page_bar.grid_remove()

    def resize_preview(self, event=None):
        if self.preview_data and self.preview_data.image:
            image = self.preview_data.image
            width, height = max(80, self.viewer.winfo_width() - 40), max(80, self.viewer.winfo_height() - 40)
            scale = min(width / image.width, height / image.height, 1)
            display_size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
            if self.preview_image is None or self.preview_image.cget('size') != display_size:
                self.preview_image = ctk.CTkImage(light_image=image, dark_image=image, size=display_size)
                self.image_label.configure(image=self.preview_image, text='')

    def change_page(self, delta):
        if self.preview_data and self.selected:
            page = self.preview_data.page + delta
            if 0 <= page < self.preview_data.pages:
                self.select_file(self.selected, page)

    def update_context(self):
        target = self.target()
        extensions = {path.suffix.lower().lstrip('.') for path in self.files}
        self.image_context = bool(extensions & self.engine.IMAGES) and target in self.engine.IMAGES | {'pdf'}
        self.media_context = bool(extensions & (self.engine.AUDIO | self.engine.VIDEO)) and target in self.engine.AUDIO | self.engine.VIDEO | {'gif'}
        self.pdf_context = 'pdf' in extensions and target in {'png', 'jpg'}
        relevant = self.image_context or self.media_context or self.pdf_context
        self.image_options.pack_forget()
        self.media_options.pack_forget()
        self.pdf_options.pack_forget()
        self.options_frame.pack_forget()
        self.options_button.pack_forget()
        if relevant:
            self.options_button.pack(fill='x', padx=4, before=self.destination)
            if self.options_open:
                self.options_frame.pack(fill='x', padx=6, pady=(0, 8), before=self.destination)
                for frame, visible in ((self.image_options, self.image_context), (self.media_options, self.media_context), (self.pdf_options, self.pdf_context)):
                    if visible:
                        frame.pack(fill='x', pady=(0, 8))
                self.quality_row.pack_forget()
                if self.image_context and target in {'jpg', 'jpeg', 'webp', 'heic', 'heif', 'avif'}:
                    self.quality_row.pack(fill='x', padx=12, pady=(10, 2), before=self.image_options.winfo_children()[1])
        self.options_button.configure(text='Customize conversion  −' if self.options_open else 'Customize conversion  +')
        count = len(self.files)
        self.settings_hint.configure(text=f'One format for {count} file{"s" if count != 1 else ""}.' if count > 1 else 'A fresh format for this file.')
        notes = {'pdf-pages': 'Each page becomes its own PDF.', 'pdf': 'Images and documents become a PDF. Existing PDFs are optimized.',
                 'gif': 'Video exports at 12 fps, up to 640 px wide.', 'zip': 'One new archive per file. Contents are not extracted.',
                 'txt': 'PDF text export does not include OCR.' if 'pdf' in extensions else 'A plain text version of your file.'}
        self.route_hint.configure(text=notes.get(target, f'Your originals stay untouched. Output: .{target}' if target else 'No shared output format. Try a smaller selection.'))
        can_merge = len(self.files) > 1 and extensions <= (self.engine.IMAGES | {'pdf'})
        self.merge.pack_forget()
        if can_merge and not self.busy:
            self.merge.pack(fill='x', before=self.go, pady=(0, 2))
        self.go.configure(text=f'Convert {count} file{"s" if count != 1 else ""}  ↗',
                          state='disabled' if self.busy or not target else 'normal')
        missing = []
        if not self.engine.PANDOC and extensions & (self.engine.DOCS | {'txt'}):
            missing.append('Document conversion needs a native Pandoc installation.')
        if not self.engine.shutil.which('ffmpeg') and extensions & (self.engine.AUDIO | self.engine.VIDEO | {'srt', 'vtt', 'ass', 'ssa'}):
            missing.append('Install FFmpeg to enable media conversion and previews.')
        self.engine_hint.configure(text='\n'.join(missing))

    def toggle_options(self):
        self.options_open = not self.options_open
        self.update_context()

    def choose(self):
        if not self.busy:
            self.add_paths(filedialog.askopenfilenames(title='Choose files'))

    def choose_batch_folder(self):
        folder = filedialog.askdirectory(title='Add a folder’s files')
        if folder and not self.busy:
            self.add_paths(path for path in sorted(Path(folder).iterdir()) if path.is_file())

    def drop_files(self, event):
        if self.busy:
            return
        files = []
        try:
            for value in self.root.tk.splitlist(event.data):
                path = Path(value)
                if path.is_dir():
                    files.extend(item for item in sorted(path.iterdir()) if item.is_file())
                elif path.is_file():
                    files.append(path)
            self.add_paths(files)
        except OSError as exc:
            self.status.set(f'Could not read this folder: {exc}')

    def add_paths(self, names):
        if self.busy:
            return
        for name in names:
            path = Path(name).expanduser().resolve()
            if path.is_file() and path not in self.files:
                self.files.append(path)
                self.file_states[path] = ('Ready', '')
        self.refresh()
        self.status.set(f'{len(self.files)} file{"s" if len(self.files) != 1 else ""} ready. Select a file to preview it.')

    def remove_file(self, path):
        if not self.busy and path in self.files:
            self.files.remove(path)
            self.file_states.pop(path, None)
            self.refresh()
            self.status.set('Queue updated.' if self.files else 'Everything stays on your device.')

    def remove_selected(self):
        if self.selected:
            self.remove_file(self.selected)

    def reset(self):
        if self.busy:
            return
        self.files.clear()
        self.file_states.clear()
        self.errors.clear()
        self.progress.grid_remove()
        self.details.grid_remove()
        self.error_box.grid_remove()
        self.output.pack_forget()
        self.retry_button.pack_forget()
        self.status.set('Everything stays on your device.')
        self.refresh()

    def copy_paths(self):
        if self.selected:
            self.root.clipboard_clear()
            self.root.clipboard_append(str(self.selected))
            self.status.set('File path copied.')

    def open_path(self, path):
        try:
            if sys.platform == 'win32':
                os.startfile(str(path))
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.status.set(f'Could not open this file: {exc}')

    def open_selected(self):
        if self.selected:
            self.open_path(self.selected)

    def show_output(self):
        folder = Path(self.folder.get()).expanduser()
        if folder.is_dir():
            self.open_path(folder)
        else:
            self.status.set('The output folder will be created with your first conversion.')

    def choose_folder(self):
        folder = filedialog.askdirectory(title='Save converted files to')
        if folder and not self.busy:
            self.folder.set(folder)

    def read_options(self):
        # Hidden settings never leak into unrelated conversions.
        image, media, pdf = self.image_context, self.media_context, self.pdf_context
        values = {key: var.get().strip() for key, var in self.tool_values.items()}
        crop = tuple(int(part.strip()) for part in values['crop'].split(',')) if image and values['crop'] else None
        if crop is not None and (len(crop) != 4 or min(crop[:2]) < 0 or min(crop[2:]) <= 0):
            raise ValueError('Crop needs x, y, width, height; width and height must be positive.')
        options = self.engine.Options(quality=int(self.quality.get()) if image and self.target() in {'jpg', 'jpeg', 'webp', 'heic', 'heif', 'avif'} else 90,
            max_size=int(self.size.get()) if image else 0,
            rotation=int(values['rotation']) if image else 0, crop=crop,
            trim_start=float(values['trim_start']) if media else 0,
            trim_end=float(values['trim_end']) if media else 0,
            pdf_dpi=int(values['pdf_dpi']) if pdf else 150)
        if not 1 <= options.quality <= 100 or not 0 <= options.max_size <= 16384:
            raise ValueError('Quality must be 1–100. Maximum edge must be 0–16384 pixels.')
        if options.rotation not in {0, 90, 180, 270} or not 72 <= options.pdf_dpi <= 600:
            raise ValueError('Rotation must be 0, 90, 180, or 270. PDF DPI must be 72–600.')
        if not all(math.isfinite(value) and value >= 0 for value in (options.trim_start, options.trim_end)) or (options.trim_end and options.trim_end <= options.trim_start):
            raise ValueError('Trim times must be finite and nonnegative. End must be after start.')
        return options

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.add, self.add_folder, self.clear, self.browse, self.options_button):
            widget.configure(state='disabled' if busy else 'normal')
        self.format.configure(state='disabled' if busy else 'readonly')
        for frame in (self.image_options, self.media_options, self.pdf_options):
            for row in frame.winfo_children():
                for child in row.winfo_children():
                    if isinstance(child, ctk.CTkEntry):
                        child.configure(state='disabled' if busy else 'normal')
        self.cancel.pack_forget()
        self.go.pack_forget()
        self.merge.pack_forget()
        if busy:
            self.cancel.pack(fill='x', pady=(8, 0))
        else:
            self.go.pack(fill='x', pady=(8, 0))
        self.render_queue()
        self.update_context()

    def request_stop(self):
        self.stop.set()
        self.cancel.configure(state='disabled', text='Finishing current file…')
        self.status.set('Stopping safely after the current file. Completed files will be kept.')

    def start(self, merge=False, retry=False):
        if self.busy or not self.files:
            return
        try:
            self.update_context()
            options = self.engine.Options() if merge else self.read_options()
            if not merge and not self.target():
                raise ValueError('Choose an output format first.')
            if not self.folder.get().strip():
                raise ValueError('Choose a folder for the converted files.')
        except ValueError as exc:
            messagebox.showerror('Check conversion settings', str(exc))
            return
        batch = tuple(path for path in self.files if not retry or self.file_states.get(path, ('Ready', ''))[0] in {'Failed', 'Skipped'})
        if not batch:
            return
        self.last_merge = merge
        self.errors.clear()
        self.retry_button.pack_forget()
        self.details.grid_remove()
        self.error_box.grid_remove()
        self.output.pack_forget()
        self.stop.clear()
        self.cancel.configure(state='normal', text='Stop after current file')
        for path in batch:
            self.file_states[path] = ('Ready', '')
        self.progress.set(0)
        self.progress.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        self.set_busy(True)
        self.status.set('Starting local conversion…')
        threading.Thread(target=self.worker, args=(batch, '__merge__' if merge else self.target(),
                                                  Path(self.folder.get()), options), daemon=True).start()

    def worker(self, files, target, folder, options):
        if target == '__merge__':
            try:
                self.events.put(('status', 'Combining files as PDF…'))
                self.engine.merge_pdf(files, folder)
                for path in files:
                    self.events.put(('file', (path, 'Converted', '')))
                self.events.put(('progress', 1))
                self.events.put(('done', (len(files), 0, 0, [])))
            except Exception as exc:
                for path in files:
                    self.events.put(('file', (path, 'Failed', str(exc))))
                self.events.put(('done', (0, len(files), 0, [str(exc)])))
            return
        good = failed = 0
        errors = []
        for index, path in enumerate(files):
            if self.stop.is_set():
                for remaining in files[index:]:
                    self.events.put(('file', (remaining, 'Skipped', '')))
                break
            self.events.put(('file', (path, 'Converting', '')))
            self.events.put(('status', f'Converting {index + 1} of {len(files)} · {path.name}'))
            try:
                self.engine.convert(path, target, folder, options)
                good += 1
                self.events.put(('file', (path, 'Converted', '')))
            except Exception as exc:
                failed += 1
                error = f'{path.name}: {exc}'
                errors.append(error)
                self.events.put(('file', (path, 'Failed', str(exc))))
            self.events.put(('progress', (index + 1) / len(files)))
        self.events.put(('done', (good, failed, len(files) - good - failed, errors)))

    def toggle_errors(self):
        if self.error_box.winfo_ismapped():
            self.error_box.grid_remove()
            self.details.configure(text='Show details')
        else:
            self.error_box.configure(state='normal')
            self.error_box.delete('1.0', 'end')
            self.error_box.insert('1.0', '\n\n'.join(self.errors)[:20000])
            self.error_box.configure(state='disabled')
            self.error_box.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(10, 0))
            self.details.configure(text='Hide details')

    def poll(self):
        if self.closed:
            return
        changed = False
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'preview':
                    version, data = value
                    if version == self.preview_version:
                        self.show_preview(data)
                elif kind == 'status':
                    self.status.set(value)
                elif kind == 'progress':
                    self.progress.set(value)
                elif kind == 'file':
                    path, state, error = value
                    self.file_states[path] = (state, error)
                    changed = True
                elif kind == 'done':
                    good, failed, skipped, self.errors = value
                    self.set_busy(False)
                    self.status.set(f'{good} converted · {failed} failed · {skipped} skipped. Your originals are unchanged.')
                    if good:
                        self.output.pack(fill='x', pady=(8, 0))
                    if failed or skipped:
                        self.retry_button.pack(fill='x', pady=(8, 0))
                    if self.errors:
                        self.details.configure(text='Show details')
                        self.details.grid(row=1, column=1, padx=(16, 0))
        except queue.Empty:
            pass
        if changed:
            self.render_queue()
        self.poll_id = self.root.after(50, self.poll)

    def on_destroy(self, event):
        if event.widget is self.root and not self.closed:
            self.closed = True
            self.preview_version += 1
            self.preview_pool.shutdown(wait=False, cancel_futures=True)
            if hasattr(self, 'poll_id'):
                self.root.after_cancel(self.poll_id)

    def close(self):
        if self.busy:
            self.request_stop()
            self.status.set('Finishing the current file. You can close the window when conversion stops.')
        else:
            self.root.destroy()
