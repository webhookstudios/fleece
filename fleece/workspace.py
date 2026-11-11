"""Code mode: a project folder, tabs of files, the AI panel, a terminal and live preview."""
import os
import shlex

import gi

gi.require_version('GtkSource', '5')
gi.require_version('Vte', '3.91')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, Vte  # noqa: E402

from .assistant import AssistantPanel  # noqa: E402
from .editor import EditorTab, FindBar  # noqa: E402
from .filetree import HIDDEN, FileTree  # noqa: E402
from . import runner  # noqa: E402

try:
    gi.require_version('WebKit', '6.0')
    from gi.repository import WebKit
except (ValueError, ImportError):
    WebKit = None

HOME = os.path.expanduser('~')


def short(path):
    return path.replace(HOME, '~', 1) if path and path.startswith(HOME) else (path or '')


class CodeWorkspace(Gtk.Box):
    def __init__(self, win, mode_switch):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.view = Adw.ToolbarView(vexpand=True)
        self.append(self.view)
        self.win = win
        self.project = None
        self.server = None
        self._terminal_ready = False
        self.add_css_class('code-workspace')

        # ---- header ----------------------------------------------------
        header = Adw.HeaderBar()
        self.tree_toggle = Gtk.ToggleButton(icon_name='sidebar-show-symbolic', active=True,
                                            tooltip_text='Show or hide files')
        header.pack_start(self.tree_toggle)
        self.open_btn = Adw.SplitButton(icon_name='folder-open-symbolic', tooltip_text='Open a project folder (Ctrl+O)')
        self.open_btn.connect('clicked', lambda *_: self.choose_project())
        header.pack_start(self.open_btn)
        header.set_title_widget(mode_switch)

        self.menu = Gio.Menu()
        files = Gio.Menu()
        files.append('New file', 'code.new-file')
        files.append('Go to file…', 'code.quick-open')
        files.append('Save', 'code.save')
        files.append('Save as…', 'code.save-as')
        files.append('Save all', 'code.save-all')
        self.menu.append_section(None, files)
        more = Gio.Menu()
        more.append('Find and replace', 'code.replace')
        more.append('Run settings…', 'code.run-settings')
        more.append('Close project', 'code.close-project')
        self.menu.append_section(None, more)
        other = Gio.Menu()
        other.append('Get models…', 'win.models')
        other.append('Settings', 'win.settings')
        other.append('About Fleece', 'app.about')
        self.menu.append_section(None, other)
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=self.menu, primary=True))

        self.ai_toggle = Gtk.ToggleButton(icon_name='chat-bubble-text-symbolic', active=True,
                                          tooltip_text='Show or hide the assistant (Ctrl+L focuses it)')
        header.pack_end(self.ai_toggle)
        self.term_toggle = Gtk.ToggleButton(icon_name='utilities-terminal-symbolic', tooltip_text='Terminal (Ctrl+`)')
        header.pack_end(self.term_toggle)
        self.run_btn = Adw.SplitButton(tooltip_text='Run (F5)')
        self.run_btn.set_child(Adw.ButtonContent(icon_name='media-playback-start-symbolic', label='Run'))
        self.run_btn.add_css_class('suggested-action')
        self.run_btn.connect('clicked', lambda *_: self.run())
        self.run_menu = Gio.Menu()
        self.run_btn.set_menu_model(self.run_menu)
        self.run_btn.connect('notify::active', lambda *_: self._fill_run_menu())
        header.pack_end(self.run_btn)
        self.view.add_top_bar(header)
        self.header = header

        # ---- body ------------------------------------------------------
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._build_welcome(), 'welcome')
        self.stack.add_named(self._build_main(), 'main')
        self.view.set_content(self.stack)
        self.status = self._build_status()
        self.view.add_bottom_bar(self.status)

        self._actions()
        self._shortcuts()
        self._fill_recent()
        last = win.config['last_project']
        if last and os.path.isdir(last):
            self.open_project(last)
        else:
            self._show_welcome()

    # ---- building ----------------------------------------------------------

    def _build_welcome(self):
        page = Adw.StatusPage(icon_name='folder-open-symbolic', title='Open a project',
                              description='Pick the folder you want to work in. Fleece shows its files, '
                                          'saves where you choose and runs your code from it.')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.CENTER)
        open_btn = Gtk.Button(label='Open folder…', halign=Gtk.Align.CENTER)
        open_btn.add_css_class('pill')
        open_btn.add_css_class('suggested-action')
        open_btn.connect('clicked', lambda *_: self.choose_project())
        box.append(open_btn)
        self.recent_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, width_request=420)
        self.recent_list.add_css_class('boxed-list')
        self.recent_list.connect('row-activated', lambda _l, row: self.open_project(row.path))
        self.recent_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        heading = Gtk.Label(label='Recent projects', xalign=0)
        heading.add_css_class('heading')
        self.recent_group.append(heading)
        self.recent_group.append(self.recent_list)
        box.append(self.recent_group)
        page.set_child(box)
        return page

    def _build_main(self):
        self.tree = FileTree(self)
        self.tree.add_css_class('tree-pane')
        self.tree.connect('open-file', lambda _t, p: self.open_file(p))
        self.tree.connect('renamed', self._on_renamed)
        self.tree.connect('deleted', self._on_deleted)

        # editor area: find bar, tabs, editor | preview
        self.tab_view = Adw.TabView()
        self.tab_view.connect('close-page', self._on_close_page)
        self.tab_view.connect('notify::selected-page', lambda *_: self._on_switch())
        self.tab_bar = Adw.TabBar(view=self.tab_view, autohide=False)
        self.tab_bar.add_css_class('code-tabs')
        self.find_bar = FindBar(self)
        self.no_tabs = Adw.StatusPage(icon_name='text-x-generic-symbolic', title='No file open',
                                      description='Pick a file on the left, or press Ctrl+P to find one by name.')
        self.no_tabs.add_css_class('compact')
        self.editor_stack = Gtk.Stack()
        self.editor_stack.add_named(self.no_tabs, 'empty')
        self.editor_stack.add_named(self.tab_view, 'tabs')
        editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        editor_box.append(self.tab_bar)
        editor_box.append(self.find_bar)
        editor_box.append(self.editor_stack)
        self.editor_stack.set_vexpand(True)

        self.preview = self._build_preview()
        self.preview_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=False,
                                       shrink_start_child=False, shrink_end_child=False)
        self.preview_paned.set_start_child(editor_box)
        self.preview_paned.set_end_child(self.preview)
        self.preview.set_visible(False)

        # terminal below the editor
        self.bottom = self._build_terminal()
        self.bottom.set_visible(False)
        center = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, shrink_end_child=False, resize_end_child=False)
        center.set_start_child(self.preview_paned)
        center.set_end_child(self.bottom)
        self.center_paned = center
        self.term_toggle.connect('toggled', lambda b: self.show_terminal(b.get_active()))

        self.assistant = AssistantPanel(self)
        right = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, shrink_end_child=False, resize_end_child=False,
                          shrink_start_child=False)
        right.set_start_child(center)
        right.set_end_child(self.assistant)
        self.ai_toggle.bind_property('active', self.assistant, 'visible', 1 | 2)

        outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, shrink_start_child=False,
                          resize_start_child=False)
        outer.set_start_child(self.tree)
        outer.set_end_child(right)
        outer.set_position(250)
        self.tree_toggle.bind_property('active', self.tree, 'visible', 1 | 2)
        self.right_paned = right
        GLib.idle_add(self._size_panes)
        return outer

    def _size_panes(self):
        width = self.win.get_width() or 1300
        self.right_paned.set_position(max(400, width - 250 - 400))
        self.center_paned.set_position(max(240, (self.win.get_height() or 820) - 330))
        return GLib.SOURCE_REMOVE

    def _build_status(self):
        bar = Gtk.Box(spacing=12)
        bar.add_css_class('status-bar')
        self.path_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.START, selectable=True,
                                    width_chars=30)
        bar.append(self.path_label)
        copy = Gtk.Button(icon_name='edit-copy-symbolic', tooltip_text='Copy full path')
        copy.add_css_class('flat')
        copy.connect('clicked', lambda *_: self._copy_current_path())
        self.copy_path_btn = copy
        bar.append(copy)
        self.pos_label = Gtk.Label()
        bar.append(self.pos_label)
        self.lang_label = Gtk.Label()
        bar.append(self.lang_label)
        self.runs_btn = Gtk.Button(tooltip_text='Where Run starts. Click to change.')
        self.runs_label = Gtk.Label(ellipsize=Pango.EllipsizeMode.START, max_width_chars=38)
        self.runs_btn.set_child(self.runs_label)
        self.runs_btn.add_css_class('flat')
        self.runs_btn.connect('clicked', lambda *_: self.run_settings())
        bar.append(self.runs_btn)
        return bar

    def _build_terminal(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class('bottom-panel')
        head = Gtk.Box(spacing=6)
        head.add_css_class('panel-head')
        title = Gtk.Label(label='Terminal', xalign=0)
        title.add_css_class('heading')
        head.append(title)
        self.term_status = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.term_status.add_css_class('dim-label')
        self.term_status.add_css_class('caption')
        head.append(self.term_status)
        ask = Gtk.Button(label='Ask AI about this output')
        ask.add_css_class('flat')
        ask.connect('clicked', lambda *_: self.ask_about_output())
        head.append(ask)
        for icon, tip, cb in (('media-playback-stop-symbolic', 'Stop the running program (Ctrl+C)',
                               lambda *_: self.terminal.feed_child(b'\x03')),
                              ('edit-clear-all-symbolic', 'Clear', lambda *_: self.terminal.reset(True, True)),
                              ('window-close-symbolic', 'Hide', lambda *_: self.term_toggle.set_active(False))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class('flat')
            b.connect('clicked', cb)
            head.append(b)
        box.append(head)
        self.terminal = Vte.Terminal(vexpand=True, hexpand=True, scrollback_lines=10000)
        self.terminal.set_font(Pango.FontDescription.from_string('Monospace 10.5'))
        self._terminal_colors()
        Adw.StyleManager.get_default().connect('notify::dark', lambda *_: self._terminal_colors())
        self.terminal.connect('child-exited', lambda *_: self._respawn())
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(self.terminal)
        box.append(scroll)
        return box

    def _terminal_colors(self):
        dark = Adw.StyleManager.get_default().get_dark()
        fg, bg = Gdk.RGBA(), Gdk.RGBA()
        fg.parse('#e6e6e6' if dark else '#2e3436')
        bg.parse('#1d1d20' if dark else '#fbfbfb')
        self.terminal.set_colors(fg, bg, None)

    def _build_preview(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=260)
        box.add_css_class('preview-pane')
        head = Gtk.Box(spacing=4)
        head.add_css_class('panel-head')
        title = Gtk.Label(label='Live preview', xalign=0)
        title.add_css_class('heading')
        head.append(title)
        self.preview_url = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.START, selectable=True)
        self.preview_url.add_css_class('dim-label')
        self.preview_url.add_css_class('caption')
        head.append(self.preview_url)
        for icon, tip, cb in (('view-refresh-symbolic', 'Reload', lambda *_: self.webview and self.webview.reload()),
                              ('web-browser-symbolic', 'Open in your browser', lambda *_: self._open_browser()),
                              ('window-close-symbolic', 'Close preview', lambda *_: self.preview.set_visible(False))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class('flat')
            b.connect('clicked', cb)
            head.append(b)
        box.append(head)
        self.webview = None
        if WebKit:
            self.webview = WebKit.WebView(vexpand=True, hexpand=True)
            box.append(self.webview)
        return box

    # ---- actions and shortcuts ---------------------------------------------

    def _actions(self):
        group = Gio.SimpleActionGroup()
        for name, cb in (('new-file', self.new_file), ('quick-open', self.quick_open), ('save', self.save),
                         ('save-as', self.save_as), ('save-all', self.save_all),
                         ('replace', lambda: self.find_bar.show_bar(True)), ('run-settings', self.run_settings),
                         ('close-project', self.close_project)):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', lambda *_a, cb=cb: cb())
            group.add_action(action)
        run = Gio.SimpleAction.new('run-action', GLib.VariantType.new('i'))
        run.connect('activate', lambda _a, v: self.run(v.get_int32()))
        group.add_action(run)
        recent = Gio.SimpleAction.new('open-recent', GLib.VariantType.new('s'))
        recent.connect('activate', lambda _a, v: self.open_project(v.get_string()))
        group.add_action(recent)
        for key in ('explain', 'fix', 'comment', 'improve', 'tests'):
            action = Gio.SimpleAction.new('ai-' + key, None)
            action.connect('activate', lambda *_a, k=key: self._quick(k))
            group.add_action(action)
        ask = Gio.SimpleAction.new('ai-ask', None)
        ask.connect('activate', lambda *_: self._focus_ai())
        group.add_action(ask)
        self.insert_action_group('code', group)
        self.actions = group

    def _shortcuts(self):
        ctrl = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        for accel, cb in (('<Control>s', self.save), ('<Control><Shift>s', self.save_as),
                          ('<Control>o', self.choose_project), ('<Control>p', self.quick_open),
                          ('<Control>f', lambda: self.find_bar.show_bar(False)),
                          ('<Control>h', lambda: self.find_bar.show_bar(True)),
                          ('<Control>w', self.close_current), ('F5', self.run), ('<Control>r', self.run),
                          ('<Control>grave', lambda: self.term_toggle.set_active(not self.term_toggle.get_active())),
                          ('<Control>l', self._focus_ai), ('<Control>t', self.new_file)):
            ctrl.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(accel),
                                           action=Gtk.CallbackAction.new(lambda *_a, cb=cb: cb() or True)))
        self.add_controller(ctrl)

    def _focus_ai(self):
        self.ai_toggle.set_active(True)
        self.assistant.refresh_context()
        self.assistant.focus()

    def _quick(self, key):
        self.ai_toggle.set_active(True)
        self.assistant.quick(key)

    # ---- projects ------------------------------------------------------------

    def _fill_recent(self):
        recent = [p for p in self.win.config['recent_projects'] if os.path.isdir(p)]
        menu = Gio.Menu()
        child = self.recent_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.recent_list.remove(child)
            child = nxt
        for path in recent[:8]:
            menu.append(short(path), f"code.open-recent::{path}")
            row = Adw.ActionRow(title=GLib.markup_escape_text(os.path.basename(path)),
                                subtitle=GLib.markup_escape_text(short(path)), activatable=True)
            row.add_prefix(Gtk.Image(icon_name='folder-symbolic'))
            row.path = path
            self.recent_list.append(row)
        self.recent_group.set_visible(bool(recent))
        self.open_btn.set_menu_model(menu if recent else None)

    def choose_project(self):
        dialog = Gtk.FileDialog(title='Open a project folder')
        if self.project:
            dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.project)))

        def done(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return
            if folder and folder.get_path():
                self.open_project(folder.get_path())

        dialog.select_folder(self.win, None, done)

    def open_project(self, path):
        path = os.path.abspath(path)
        if path == self.project:
            return

        def go():
            self._close_all_tabs()
            if self.server:
                self.server.stop()
                self.server = None
            self.project = path
            config = self.win.config
            recent = [path] + [p for p in config['recent_projects'] if p != path]
            config['recent_projects'] = recent[:12]
            config['last_project'] = path
            self._fill_recent()
            self.tree.set_root(path)
            self.stack.set_visible_child_name('main')
            self.preview.set_visible(False)
            self._update_status()
            if self._terminal_ready:
                self._feed(f'\x15export FLEECE_PROJECT={shlex.quote(path)}; cd "$FLEECE_PROJECT"; clear\n')
            self.assistant.reset()

        self._confirm_unsaved(go)

    def close_project(self):
        def go():
            self._close_all_tabs()
            self.project = None
            self.win.config['last_project'] = ''
            self._show_welcome()

        self._confirm_unsaved(go)

    def _show_welcome(self):
        self.stack.set_visible_child_name('welcome')
        self._update_status()

    # ---- tabs ----------------------------------------------------------------

    def tabs(self):
        return [self.tab_view.get_nth_page(i).get_child() for i in range(self.tab_view.get_n_pages())]

    def current_tab(self):
        page = self.tab_view.get_selected_page()
        return page.get_child() if page else None

    def find_tab(self, path):
        if path is None:
            tab = self.current_tab()
            return tab if tab and tab.path is None else None
        for tab in self.tabs():
            if tab.path and os.path.abspath(tab.path) == os.path.abspath(path):
                return tab
        return None

    def show_tab(self, tab):
        page = self.tab_view.get_page(tab)
        if page:
            self.tab_view.set_selected_page(page)

    def _add_tab(self, tab):
        page = self.tab_view.append(tab)
        tab.connect('changed-state', lambda t: self._tab_state(t))
        tab.connect('cursor-moved', lambda t: self._cursor_moved(t))
        tab.buffer.connect('changed', lambda *_: self._buffer_changed(tab))
        self._extra_menu(tab)
        self._tab_state(tab)
        self.tab_view.set_selected_page(page)
        self.editor_stack.set_visible_child_name('tabs')
        tab.view.grab_focus()
        return tab

    def _extra_menu(self, tab):
        menu = Gio.Menu()
        ai = Gio.Menu()
        ai.append('Ask the assistant about this', 'code.ai-ask')
        ai.append('Explain', 'code.ai-explain')
        ai.append('Find and fix bugs', 'code.ai-fix')
        ai.append('Add comments', 'code.ai-comment')
        ai.append('Make it cleaner', 'code.ai-improve')
        ai.append('Write tests', 'code.ai-tests')
        menu.append_section('Assistant', ai)
        tab.view.set_extra_menu(menu)

    def open_file(self, path):
        existing = self.find_tab(path)
        if existing:
            self.show_tab(existing)
            return existing
        try:
            tab = EditorTab(path=path)
        except (OSError, ValueError) as exc:
            self.toast(str(exc))
            return None
        return self._add_tab(tab)

    def open_snippet(self, code, lang):
        """Code from a chat answer, in a new unsaved tab."""
        tab = EditorTab(text=code, language=lang or None)
        tab.buffer.set_modified(True)
        if not self.project:
            self.toast('Open a project folder first, so Fleece knows where to save it.')
        else:
            self.stack.set_visible_child_name('main')
        return self._add_tab(tab)

    def new_file(self):
        if not self.project:
            self.choose_project()
            return
        self._add_tab(EditorTab(text=''))

    def _tab_state(self, tab):
        page = self.tab_view.get_page(tab)
        if not page:
            return
        page.set_title(('● ' if tab.modified else '') + tab.title)
        page.set_tooltip(GLib.markup_escape_text(tab.path or 'Not saved yet'))
        if tab is self.current_tab():
            self._update_status()

    def _cursor_moved(self, tab):
        if tab is self.current_tab():
            line, col = tab.cursor()
            self.pos_label.set_text(f'Ln {line}, Col {col}')
            self.assistant.refresh_context()

    def _buffer_changed(self, tab):
        if getattr(self, '_recheck', 0):
            GLib.source_remove(self._recheck)

        def go():
            self._recheck = 0
            self.assistant.recheck_cards()
            return GLib.SOURCE_REMOVE

        self._recheck = GLib.timeout_add(400, go)

    def _on_switch(self):
        tab = self.current_tab()
        self.editor_stack.set_visible_child_name('tabs' if tab else 'empty')
        self._update_status()
        self.assistant.refresh_context()

    def _on_close_page(self, view, page):
        tab = page.get_child()
        if not tab.modified:
            view.close_page_finish(page, True)
            GLib.idle_add(self._after_close)
            return True
        dialog = Adw.AlertDialog(heading=f'Save changes to {tab.title}?',
                                 body='Your changes will be lost if you close without saving.')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('discard', "Don't save")
        dialog.add_response('save', 'Save')
        dialog.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('save')
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response == 'save':
                self._save_tab(tab, lambda ok: view.close_page_finish(page, ok))
            else:
                view.close_page_finish(page, response == 'discard')
            GLib.idle_add(self._after_close)

        dialog.connect('response', on_response)
        dialog.present(self.win)
        return True

    def _after_close(self):
        if self.tab_view.get_n_pages() == 0:
            self.editor_stack.set_visible_child_name('empty')
            self._update_status()
            self.assistant.refresh_context()
        return GLib.SOURCE_REMOVE

    def close_current(self):
        page = self.tab_view.get_selected_page()
        if page:
            self.tab_view.close_page(page)

    def _close_all_tabs(self):
        for tab in self.tabs():
            tab.buffer.set_modified(False)
            page = self.tab_view.get_page(tab)
            self.tab_view.close_page(page)
        self.editor_stack.set_visible_child_name('empty')

    def unsaved(self):
        return [t for t in self.tabs() if t.modified]

    def _confirm_unsaved(self, then):
        dirty = self.unsaved()
        if not dirty:
            then()
            return
        names = ', '.join(t.title for t in dirty[:4]) + ('…' if len(dirty) > 4 else '')
        dialog = Adw.AlertDialog(heading='Save your changes?', body=f'Unsaved: {names}')
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('discard', "Don't save")
        dialog.add_response('save', 'Save all')
        dialog.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            if response == 'discard':
                then()
            elif response == 'save':
                self.save_all(then)

        dialog.connect('response', on_response)
        dialog.present(self.win)

    # ---- saving --------------------------------------------------------------

    def save(self):
        tab = self.current_tab()
        if tab:
            self._save_tab(tab)

    def save_as(self):
        tab = self.current_tab()
        if tab:
            self._save_tab(tab, ask=True)

    def save_all(self, then=None):
        pending = [t for t in self.tabs() if t.modified]

        def next_one(_ok=True):
            if pending:
                self._save_tab(pending.pop(0), next_one)
            elif then:
                then()

        next_one()

    def _save_tab(self, tab, done=None, ask=False):
        if tab.path and not ask:
            try:
                tab.save()
            except OSError as exc:
                self.toast(f'Could not save: {exc.strerror}')
                done and done(False)
                return
            self._saved(tab)
            done and done(True)
            return
        dialog = Gtk.FileDialog(title='Save file as', accept_label='Save')
        folder = os.path.dirname(tab.path) if tab.path else (self.project or HOME)
        dialog.set_initial_folder(Gio.File.new_for_path(folder))
        dialog.set_initial_name(os.path.basename(tab.path) if tab.path else 'untitled.txt')

        def finish(d, res):
            try:
                gfile = d.save_finish(res)
            except GLib.Error:
                done and done(False)
                return
            try:
                tab.save(gfile.get_path())
            except OSError as exc:
                self.toast(f'Could not save: {exc.strerror}')
                done and done(False)
                return
            self._saved(tab)
            done and done(True)

        dialog.save(self.win, None, finish)

    def _saved(self, tab):
        self.toast(f'Saved to {short(tab.path)}')
        if self.server:
            self.server.bump()

    # ---- file events from the tree --------------------------------------------

    def _on_renamed(self, _tree, old, new):
        for tab in self.tabs():
            if tab.path and (tab.path == old or tab.path.startswith(old + os.sep)):
                tab.path = new + tab.path[len(old):]
                self._tab_state(tab)

    def _on_deleted(self, _tree, path):
        for tab in self.tabs():
            if tab.path and (tab.path == path or tab.path.startswith(path + os.sep)):
                tab.buffer.set_modified(True)
                tab.path = None
                self._tab_state(tab)

    # ---- status --------------------------------------------------------------

    def _update_status(self):
        tab = self.current_tab() if self.project else None
        if tab:
            self.path_label.set_text(short(tab.path) if tab.path else 'Not saved yet. Ctrl+S picks where it goes.')
            self.path_label.set_tooltip_text(tab.path or None)
            line, col = tab.cursor()
            self.pos_label.set_text(f'Ln {line}, Col {col}')
            self.lang_label.set_text(tab.language_name)
        else:
            self.path_label.set_text(short(self.project) if self.project else 'No project open')
            self.pos_label.set_text('')
            self.lang_label.set_text('')
        self.copy_path_btn.set_visible(bool(tab and tab.path))
        self.runs_btn.set_visible(bool(self.project))
        if self.project:
            settings = self._project_settings()
            where = settings.get('cwd') or self._default_cwd()
            self.runs_label.set_text(f'Runs in {short(where)}')
            self.runs_btn.set_tooltip_text(f'Run starts in {where}. Click to change.')

    def _copy_current_path(self):
        tab = self.current_tab()
        if tab and tab.path:
            Gdk.Display.get_default().get_clipboard().set(tab.path)
            self.toast('Path copied')

    # ---- running ---------------------------------------------------------------

    def _project_settings(self):
        return dict((self.win.config['projects'] or {}).get(self.project, {}))

    def _save_project_settings(self, settings):
        projects = dict(self.win.config['projects'] or {})
        projects[self.project] = settings
        self.win.config['projects'] = projects
        self._update_status()

    def _default_cwd(self):
        tab = self.current_tab()
        actions = runner.detect(self.project, tab.path if tab else None) if self.project else []
        return actions[0].cwd if actions and actions[0].cwd else (self.project or HOME)

    def set_run_folder(self, path):
        settings = self._project_settings()
        settings['cwd'] = path
        self._save_project_settings(settings)
        self.toast(f'Commands now run from {short(path)}')

    def _actions_for_run(self):
        tab = self.current_tab()
        actions = runner.detect(self.project, tab.path if tab else None)
        settings = self._project_settings()
        if settings.get('command'):
            actions.insert(0, runner.Action(settings['command'], 'terminal', settings['command'],
                                            settings.get('cwd') or self.project))
        elif settings.get('cwd'):
            for a in actions:
                if a.kind == 'terminal':
                    a.cwd = settings['cwd']
        return actions

    def _fill_run_menu(self):
        self.run_menu.remove_all()
        if not self.project:
            return
        section = Gio.Menu()
        for i, action in enumerate(self._actions_for_run()):
            section.append(action.label, f'code.run-action({i})')
        self.run_menu.append_section(None, section)
        tail = Gio.Menu()
        tail.append('Run settings…', 'code.run-settings')
        self.run_menu.append_section(None, tail)

    def run(self, index=0):
        if not self.project:
            self.toast('Open a project folder first.')
            return
        tab = self.current_tab()
        if tab and tab.modified and not tab.path:
            self._save_tab(tab, lambda ok: ok and self.run(index))
            return

        def go():
            actions = self._actions_for_run()
            if not actions:
                self.toast("Fleece doesn't know how to run this yet. Set a command in Run settings.")
                self.run_settings()
                return
            action = actions[min(index, len(actions) - 1)]
            if action.kind == 'preview':
                self._preview(action.page)
            else:
                self.show_terminal(True)
                cwd = action.cwd or self.project
                self.term_status.set_text(f'{action.command}  ·  in {short(cwd)}')
                self._feed(f'\x15clear && cd {self._cd_target(cwd)} && {action.command}\n')

        dirty = [t for t in self.tabs() if t.modified and t.path]
        if dirty:
            for t in dirty:
                try:
                    t.save()
                except OSError:
                    pass
            if self.server:
                self.server.bump()
        go()

    def _preview(self, page):
        if not self.server:
            self.server = runner.PreviewServer(self.project)
        url = self.server.url(page)
        self.preview_url.set_text(url)
        self._preview_target = url
        if self.webview:
            if (self.win.get_width() or 0) < 1500:
                self.tree_toggle.set_active(False)
            self.preview.set_visible(True)
            width = self.preview_paned.get_width() or 900
            if self.preview_paned.get_position() <= 0 or self.preview_paned.get_position() > width - 200:
                self.preview_paned.set_position(width // 2)
            self.webview.load_uri(url)
        else:
            self._open_browser()

    def _open_browser(self):
        url = getattr(self, '_preview_target', None)
        if url:
            Gtk.UriLauncher.new(url).launch(self.win, None, None)

    def show_terminal(self, show):
        if self.term_toggle.get_active() != show:
            self.term_toggle.set_active(show)
            return
        self.bottom.set_visible(show)
        if show:
            self._spawn()

    def _spawn(self):
        if self._terminal_ready:
            return
        self._terminal_ready = True
        shell = os.environ.get('SHELL') or '/bin/bash'
        env = [f'{k}={v}' for k, v in os.environ.items()] + [f'FLEECE_PROJECT={self.project or HOME}']
        self.terminal.spawn_async(Vte.PtyFlags.DEFAULT, self.project or HOME, [shell], env,
                                  GLib.SpawnFlags.DEFAULT, None, None, -1, None, None, None)

    def _respawn(self):
        self._terminal_ready = False
        if self.bottom.get_visible():
            self._spawn()

    def _feed(self, text):
        self._spawn()
        GLib.timeout_add(150, lambda: self.terminal.feed_child(text.encode()) or False)

    def _cd_target(self, cwd):
        """A short way to type `cwd` in the terminal, through $FLEECE_PROJECT when possible."""
        rel = os.path.relpath(cwd, self.project) if self.project else None
        if rel and not rel.startswith('..'):
            return '"$FLEECE_PROJECT"' if rel == '.' else f'"$FLEECE_PROJECT"/{shlex.quote(rel)}'
        return shlex.quote(cwd)

    def ask_about_output(self):
        try:
            text = self.terminal.get_text_format(Vte.Format.TEXT) or ''
        except (AttributeError, TypeError):
            text = ''
        lines = [l for l in text.rstrip().split('\n')][-120:]
        if not ''.join(lines).strip():
            self.toast('The terminal is empty.')
            return
        self.ai_toggle.set_active(True)
        output = '\n'.join(lines)
        self.assistant.ask('Here is the output from running my code. What went wrong, and how do I fix it?',
                           extra=f'\n\nTerminal output:\n```\n{output}\n```')

    def run_settings(self):
        if not self.project:
            return
        settings = self._project_settings()
        dialog = Adw.PreferencesDialog(title='Run settings')
        page = Adw.PreferencesPage()
        where = Adw.PreferencesGroup(title='This project',
                                     description='New files are saved where you choose in the save window, '
                                                 'starting in the project folder.')
        project_row = Adw.ActionRow(title='Project folder', subtitle=GLib.markup_escape_text(self.project),
                                    subtitle_selectable=True)
        where.add(project_row)
        page.add(where)

        run = Adw.PreferencesGroup(title='Run button',
                                   description='Leave the command empty to let Fleece pick one for the open file.')
        detected = self._actions_for_run()
        command = Adw.EntryRow(title='Command, for example npm start or python3 main.py',
                               text=settings.get('command', ''))
        run.add(command)
        if detected and not settings.get('command'):
            hint = Adw.ActionRow(title='Right now Run does', subtitle=GLib.markup_escape_text(detected[0].label))
            run.add(hint)
        folder_row = Adw.ActionRow(title='Run from folder')
        current = settings.get('cwd') or ''
        folder_row.set_subtitle(GLib.markup_escape_text(current or "The file's own folder (automatic)"))
        pick = Gtk.Button(label='Choose…', valign=Gtk.Align.CENTER)
        reset = Gtk.Button(icon_name='edit-undo-symbolic', valign=Gtk.Align.CENTER, tooltip_text='Reset to automatic')
        reset.add_css_class('flat')
        folder_row.add_suffix(reset)
        folder_row.add_suffix(pick)
        run.add(folder_row)
        page.add(run)
        dialog.add(page)
        state = {'cwd': current}

        def choose(*_):
            fd = Gtk.FileDialog(title='Run commands from this folder')
            fd.set_initial_folder(Gio.File.new_for_path(state['cwd'] or self.project))

            def done(d, res):
                try:
                    f = d.select_folder_finish(res)
                except GLib.Error:
                    return
                state['cwd'] = f.get_path()
                folder_row.set_subtitle(GLib.markup_escape_text(state['cwd']))

            fd.select_folder(self.win, None, done)

        def clear(*_):
            state['cwd'] = ''
            folder_row.set_subtitle("The file's own folder (automatic)")

        pick.connect('clicked', choose)
        reset.connect('clicked', clear)

        def closed(*_):
            new = {}
            if command.get_text().strip():
                new['command'] = command.get_text().strip()
            if state['cwd']:
                new['cwd'] = state['cwd']
            self._save_project_settings(new)

        dialog.connect('closed', closed)
        dialog.present(self.win)

    # ---- quick open ------------------------------------------------------------

    def quick_open(self):
        if not self.project:
            return
        files = []
        for root, dirs, names in os.walk(self.project):
            dirs[:] = sorted(d for d in dirs if d not in HIDDEN and d != 'node_modules')
            for n in sorted(names):
                files.append(os.path.relpath(os.path.join(root, n), self.project))
                if len(files) >= 8000:
                    break
        dialog = Adw.Dialog(title='Go to file', content_width=560, content_height=460)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12,
                      margin_top=12, margin_bottom=12)
        entry = Gtk.SearchEntry(placeholder_text='Type part of a file name')
        box.append(entry)
        listbox = Gtk.ListBox()
        listbox.add_css_class('navigation-sidebar')
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(listbox)
        box.append(scroll)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(box)
        dialog.set_child(view)

        def matches(query):
            q = query.lower().replace(' ', '')
            out = []
            for f in files:
                low = f.lower()
                pos = 0
                ok = True
                for ch in q:
                    pos = low.find(ch, pos) + 1
                    if pos == 0:
                        ok = False
                        break
                if ok:
                    name = os.path.basename(low)
                    out.append((0 if q in name else 1, len(f), f))
            return [f for *_k, f in sorted(out)[:60]]

        def fill(*_):
            child = listbox.get_first_child()
            while child:
                nxt = child.get_next_sibling()
                listbox.remove(child)
                child = nxt
            for f in matches(entry.get_text()):
                row = Gtk.ListBoxRow()
                row.rel = f
                line = Gtk.Box(spacing=8)
                name = Gtk.Label(label=os.path.basename(f), xalign=0)
                where = Gtk.Label(label=os.path.dirname(f), xalign=0, ellipsize=Pango.EllipsizeMode.START,
                                  hexpand=True)
                where.add_css_class('dim-label')
                line.append(name)
                line.append(where)
                row.set_child(line)
                listbox.append(row)
            first = listbox.get_row_at_index(0)
            if first:
                listbox.select_row(first)

        def open_row(row):
            if row:
                dialog.close()
                self.open_file(os.path.join(self.project, row.rel))

        entry.connect('search-changed', fill)
        entry.connect('activate', lambda *_: open_row(listbox.get_selected_row()))
        listbox.connect('row-activated', lambda _l, row: open_row(row))
        keys = Gtk.EventControllerKey()

        def on_key(_c, keyval, _code, _state):
            if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
                row = listbox.get_selected_row()
                i = row.get_index() if row else -1
                target = listbox.get_row_at_index(i + (1 if keyval == Gdk.KEY_Down else -1))
                if target:
                    listbox.select_row(target)
                return True
            return False

        keys.connect('key-pressed', on_key)
        entry.add_controller(keys)
        fill()
        dialog.present(self.win)
        entry.grab_focus()

    # ---- misc ----------------------------------------------------------------

    def toast(self, text):
        self.win.toast(text)

    def set_models(self, models):
        self.assistant.set_models(models)

    def shutdown(self):
        if self.server:
            self.server.stop()
            self.server = None
