"""One open file: a GtkSourceView with line numbers, highlighting and a minimap."""
import os

import gi

gi.require_version('GtkSource', '5')

from gi.repository import Adw, GLib, GObject, Gtk, GtkSource, Pango  # noqa: E402

MAX_BYTES = 8 * 1024 * 1024


def _scheme():
    dark = Adw.StyleManager.get_default().get_dark()
    manager = GtkSource.StyleSchemeManager.get_default()
    return manager.get_scheme('Adwaita-dark' if dark else 'Adwaita') or manager.get_scheme('classic')


class EditorTab(Gtk.Box):
    __gsignals__ = {
        'changed-state': (GObject.SignalFlags.RUN_FIRST, None, ()),  # title, modified or path changed
        'cursor-moved': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, path=None, text='', language=None):
        super().__init__()
        self.path = path
        self.buffer = GtkSource.Buffer(highlight_matching_brackets=True)
        self.buffer.set_style_scheme(_scheme())
        Adw.StyleManager.get_default().connect('notify::dark', lambda *_: self.buffer.set_style_scheme(_scheme()))

        self.view = GtkSource.View(
            buffer=self.buffer, monospace=True, show_line_numbers=True, highlight_current_line=True,
            auto_indent=True, insert_spaces_instead_of_tabs=True, tab_width=4, indent_width=4,
            smart_backspace=True, smart_home_end=GtkSource.SmartHomeEndType.BEFORE,
            top_margin=8, bottom_margin=120, left_margin=6, hexpand=True, vexpand=True)
        self.view.add_css_class('code-editor')
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_child(self.view)
        self.append(scroller)
        self.minimap = GtkSource.Map(view=self.view)
        self.minimap.add_css_class('code-map')
        self.append(self.minimap)

        if path:
            self._load(path)
        else:
            self._set_text(text)
            if language:
                lang = GtkSource.LanguageManager.get_default().get_language(language)
                if lang:
                    self.buffer.set_language(lang)
        self.buffer.connect('modified-changed', lambda *_: self.emit('changed-state'))
        self.buffer.connect('notify::cursor-position', lambda *_: self.emit('cursor-moved'))
        self.search = GtkSource.SearchContext(buffer=self.buffer, highlight=True)
        self._flash_tag = self.buffer.create_tag('fleece-flash', background='#e0a52660',
                                                 paragraph_background='#e0a52630')

    # ---- files ----------------------------------------------------------

    def _set_text(self, text):
        self.buffer.begin_irreversible_action()
        self.buffer.set_text(text)
        self.buffer.end_irreversible_action()
        self.buffer.set_modified(False)
        self.buffer.place_cursor(self.buffer.get_start_iter())

    def _load(self, path):
        if os.path.getsize(path) > MAX_BYTES:
            raise ValueError('This file is too large to open (over 8 MB).')
        with open(path, 'rb') as f:
            data = f.read()
        if b'\0' in data[:8192]:
            raise ValueError("This looks like a binary file, so it can't be edited here.")
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('latin-1')
        self._set_text(text)
        self._guess_language()

    def _guess_language(self):
        content_type = None
        if self.path:
            content_type, _ = Gio_content_type(self.path)
        lang = GtkSource.LanguageManager.get_default().guess_language(
            os.path.basename(self.path) if self.path else None, content_type)
        self.buffer.set_language(lang)

    def save(self, path=None):
        if path:
            self.path = path
        text = self.get_text()
        tmp = self.path + '.fleece-tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        try:
            mode = os.stat(self.path).st_mode
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, self.path)
        self.buffer.set_modified(False)
        if path:
            self._guess_language()
        self.emit('changed-state')

    # ---- info -----------------------------------------------------------

    @property
    def title(self):
        return os.path.basename(self.path) if self.path else 'Untitled'

    @property
    def modified(self):
        return self.buffer.get_modified()

    @property
    def language_id(self):
        lang = self.buffer.get_language()
        return lang.get_id() if lang else ''

    @property
    def language_name(self):
        lang = self.buffer.get_language()
        return lang.get_name() if lang else 'Plain text'

    def get_text(self):
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)

    def cursor(self):
        it = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        return it.get_line() + 1, it.get_line_offset() + 1

    def selection(self):
        """(text, first line, last line) of the selection, or None."""
        bounds = self.buffer.get_selection_bounds()
        if not bounds:
            return None
        start, end = bounds
        text = self.buffer.get_text(start, end, True)
        if not text.strip():
            return None
        last = end.get_line() + (0 if end.get_line_offset() or end.get_line() == start.get_line() else -1)
        return text, start.get_line() + 1, max(start.get_line(), last) + 1

    # ---- edits ----------------------------------------------------------

    def replace_range(self, start, end, text):
        """Replaces characters start..end as one undoable step, then highlights the new text."""
        buf = self.buffer
        a = buf.get_iter_at_offset(start)
        b = buf.get_iter_at_offset(end)
        buf.begin_user_action()
        buf.delete(a, b)
        a = buf.get_iter_at_offset(start)
        buf.insert(a, text)
        buf.end_user_action()
        a = buf.get_iter_at_offset(start)
        b = buf.get_iter_at_offset(start + len(text))
        buf.place_cursor(a)
        self.view.scroll_to_iter(a, 0.2, False, 0, 0)
        buf.apply_tag(self._flash_tag, a, b)

        def clear():
            buf.remove_tag(self._flash_tag, buf.get_start_iter(), buf.get_end_iter())
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(1800, clear)

    def goto_line(self, line):
        ok, it = self.buffer.get_iter_at_line(max(0, line - 1))
        if ok:
            self.buffer.place_cursor(it)
            self.view.scroll_to_iter(it, 0.25, False, 0, 0)


def Gio_content_type(path):
    from gi.repository import Gio
    return Gio.content_type_guess(path, None)


class FindBar(Gtk.Revealer):
    """Find and replace for the current tab (Ctrl+F, Ctrl+H)."""

    def __init__(self, workspace):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.ws = workspace
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class('find-bar')
        row = Gtk.Box(spacing=6)
        self.find = Gtk.SearchEntry(placeholder_text='Find', hexpand=True)
        self.find.connect('search-changed', lambda *_: self._update())
        self.find.connect('activate', lambda *_: self.step(True))
        self.find.connect('stop-search', lambda *_: self.hide_bar())
        self.count = Gtk.Label(width_chars=10)
        self.count.add_css_class('dim-label')
        self.case = Gtk.ToggleButton(label='Aa', tooltip_text='Match case')
        self.case.add_css_class('flat')
        self.case.connect('toggled', lambda *_: self._update())
        prev = Gtk.Button(icon_name='go-up-symbolic', tooltip_text='Previous (Shift+Enter)')
        prev.connect('clicked', lambda *_: self.step(False))
        nxt = Gtk.Button(icon_name='go-down-symbolic', tooltip_text='Next (Enter)')
        nxt.connect('clicked', lambda *_: self.step(True))
        close = Gtk.Button(icon_name='window-close-symbolic', tooltip_text='Close (Esc)')
        close.connect('clicked', lambda *_: self.hide_bar())
        for w in (self.find, self.count, self.case, prev, nxt, close):
            if isinstance(w, Gtk.Button):
                w.add_css_class('flat')
            row.append(w)
        box.append(row)
        self.replace_row = Gtk.Box(spacing=6, visible=False)
        self.replace = Gtk.Entry(placeholder_text='Replace with', hexpand=True)
        self.replace.connect('activate', lambda *_: self.replace_one())
        one = Gtk.Button(label='Replace')
        one.connect('clicked', lambda *_: self.replace_one())
        every = Gtk.Button(label='Replace all')
        every.connect('clicked', lambda *_: self.replace_all())
        for w in (self.replace, one, every):
            self.replace_row.append(w)
        box.append(self.replace_row)
        self.set_child(box)

        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._on_key)
        self.find.add_controller(keys)

    def _on_key(self, _c, keyval, _code, state):
        from gi.repository import Gdk
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK:
            self.step(False)
            return True
        return False

    def _ctx(self):
        tab = self.ws.current_tab()
        return tab.search if tab else None

    def _update(self):
        ctx = self._ctx()
        if not ctx:
            return
        settings = ctx.get_settings()
        settings.set_search_text(self.find.get_text() or None)
        settings.set_case_sensitive(self.case.get_active())
        settings.set_wrap_around(True)
        GLib.timeout_add(60, self._show_count)
        if self.find.get_text():
            self.step(True, from_start=True)

    def _show_count(self):
        ctx = self._ctx()
        if ctx and self.find.get_text():
            n = ctx.get_occurrences_count()
            self.count.set_text('No results' if n == 0 else f'{n} found' if n > 0 else '')
        else:
            self.count.set_text('')
        return GLib.SOURCE_REMOVE

    def step(self, forward, from_start=False):
        tab = self.ws.current_tab()
        if not tab or not self.find.get_text():
            return
        buf = tab.buffer
        ins = buf.get_iter_at_mark(buf.get_insert())
        if forward:
            if not from_start:
                bounds = buf.get_selection_bounds()
                if bounds:
                    ins = bounds[1]
            found, start, end, _wrapped = tab.search.forward(ins)
        else:
            bounds = buf.get_selection_bounds()
            if bounds:
                ins = bounds[0]
            found, start, end, _wrapped = tab.search.backward(ins)
        if found:
            buf.select_range(start, end)
            tab.view.scroll_to_iter(start, 0.25, False, 0, 0)

    def replace_one(self):
        tab = self.ws.current_tab()
        if not tab:
            return
        bounds = tab.buffer.get_selection_bounds()
        if bounds:
            try:
                tab.search.replace(bounds[0], bounds[1], self.replace.get_text(), -1)
            except GLib.Error:
                pass
        self.step(True)
        GLib.timeout_add(60, self._show_count)

    def replace_all(self):
        tab = self.ws.current_tab()
        if tab and self.find.get_text():
            tab.buffer.begin_user_action()
            n = tab.search.replace_all(self.replace.get_text(), -1)
            tab.buffer.end_user_action()
            self.count.set_text(f'Replaced {n}')

    def show_bar(self, replace=False):
        tab = self.ws.current_tab()
        if tab:
            sel = tab.selection()
            if sel and '\n' not in sel[0]:
                self.find.set_text(sel[0])
        self.replace_row.set_visible(replace)
        self.set_reveal_child(True)
        self.find.grab_focus()
        self._update()

    def hide_bar(self):
        self.set_reveal_child(False)
        ctx = self._ctx()
        if ctx:
            ctx.get_settings().set_search_text(None)
        tab = self.ws.current_tab()
        if tab:
            tab.view.grab_focus()
