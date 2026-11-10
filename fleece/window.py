import datetime
import threading
import time

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from . import APP_ID
from .ollama import Client, OllamaError
from . import render as render_module
from .render import render
from .store import new_chat

SUGGESTIONS = [
    'Explain how a neural network learns',
    'Write a Python script that renames files by date',
    'Give me ideas for a weekend project',
    'Help me plan a week of simple meals',
]


def _copy(text):
    Gdk.Display.get_default().get_clipboard().set(text)


class UserMessage(Gtk.Box):
    def __init__(self, text):
        super().__init__(halign=Gtk.Align.END)
        bubble = Gtk.Box()
        bubble.add_css_class('user-bubble')
        label = Gtk.Label(label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0,
                          selectable=True, max_width_chars=70)
        bubble.append(label)
        self.append(bubble)


class AssistantMessage(Gtk.Box):
    def __init__(self, win, msg):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.win = win
        self.msg = msg
        self._timer = 0
        self._shown = None
        self._t0 = time.time()
        self._think_secs = None

        self.spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.START, margin_top=4)
        self.append(self.spinner)

        self.think_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.think_box.add_css_class('thinking')
        self.think_exp = Gtk.Expander(label='Thinking…')
        self.think_label = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0,
                                     selectable=True, margin_top=6)
        self.think_label.add_css_class('thinking-text')
        self.think_exp.set_child(self.think_label)
        self.think_box.append(self.think_exp)
        self.append(self.think_box)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.append(self.body)

        self.actions = Gtk.Box(spacing=2, visible=False)
        self.actions.add_css_class('msg-actions')
        copy = Gtk.Button(icon_name='edit-copy-symbolic', tooltip_text='Copy')
        copy.add_css_class('flat')
        copy.connect('clicked', lambda _b: _copy(self.msg['content']))
        self.regen = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Try again')
        self.regen.add_css_class('flat')
        self.regen.connect('clicked', lambda _b: self.win.regenerate())
        self.stats = Gtk.Label(margin_start=8)
        self.stats.add_css_class('msg-stats')
        self.actions.append(copy)
        self.actions.append(self.regen)
        self.actions.append(self.stats)
        self.append(self.actions)

    def update(self):
        if not self._timer:
            self._timer = GLib.timeout_add(60, self._flush)

    def _flush(self):
        self._timer = 0
        self._render(streaming=True)
        return GLib.SOURCE_REMOVE

    def _render(self, streaming):
        content, thinking = self.msg['content'], self.msg.get('thinking', '')
        self.spinner.set_visible(streaming and not content and not thinking)
        self.spinner.set_spinning(self.spinner.get_visible())
        if thinking:
            self.think_box.set_visible(True)
            self.think_label.set_text(thinking.strip())
            if content and self._think_secs is None:
                self._think_secs = max(1, round(time.time() - self._t0))
                self.think_exp.set_expanded(False)
            if self._think_secs is not None:
                self.think_exp.set_label(f'Thought for {self._think_secs} s')
            elif streaming:
                self.think_exp.set_label('Thinking…')
                self.think_exp.set_expanded(True)
            else:
                self.think_exp.set_label('Thinking')
        if content != self._shown:
            self._shown = content
            child = self.body.get_first_child()
            while child:
                nxt = child.get_next_sibling()
                self.body.remove(child)
                child = nxt
            if content:
                self.body.append(render(content))

    def finish(self, stats=None, error=None):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self._render(streaming=False)
        self.spinner.set_visible(False)
        self.spinner.set_spinning(False)
        if error:
            label = Gtk.Label(label=error, wrap=True, xalign=0, selectable=True, margin_top=6)
            label.add_css_class('error-msg')
            self.body.append(label)
        self.stats.set_text(stats or '')
        self.actions.set_visible(bool(self.msg['content']))

    def set_regen_visible(self, visible):
        self.regen.set_visible(visible)


class FleeceWindow(Adw.ApplicationWindow):
    def __init__(self, app, config, store, first_prompt=None, open_settings=False, open_models=False):
        super().__init__(application=app, title='Fleece', default_width=1320, default_height=840)
        self.config = config
        self.store = store
        self.client = Client(config['host'])
        self.models = []
        self.model_details = {}
        self.caps = {}
        self.chat = None
        self.streaming = False
        self.current = None
        self._gen = 0
        self._stream = None
        self._stick = True
        self._populating = False
        self._first_prompt = first_prompt

        self._mode_groups = []
        self.code = None
        self._build()
        self._actions()
        self.reload_list()
        self.new_chat()
        self.refresh_models()
        if open_settings:
            GLib.idle_add(lambda: self.show_settings() or False)
        if open_models:
            GLib.idle_add(lambda: self.show_models() or False)
        if self.config['mode'] == 'code':
            GLib.idle_add(lambda: self.set_mode('code') or False)

    # ---- layout ---------------------------------------------------------

    def _build(self):
        self.toasts = Adw.ToastOverlay()
        self.split = Adw.OverlaySplitView(min_sidebar_width=250, max_sidebar_width=330, sidebar_width_fraction=0.24)
        self.modes = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        self.modes.add_named(self.split, 'chat')
        self.toasts.set_child(self.modes)
        self.set_content(self.toasts)
        render_module.open_in_code = self.open_in_code
        self.connect('close-request', self._on_close_request)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 760sp'))
        bp.add_setter(self.split, 'collapsed', True)
        self.add_breakpoint(bp)

        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())

    def _build_sidebar(self):
        view = Adw.ToolbarView()
        view.add_css_class('sidebar-pane')
        header = Adw.HeaderBar(show_end_title_buttons=False)
        title = Gtk.Box(spacing=8)
        title.append(Gtk.Image(icon_name=APP_ID, pixel_size=20))
        name = Gtk.Label(label='Fleece')
        name.add_css_class('heading')
        title.append(name)
        header.set_title_widget(title)
        view.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        new = Gtk.Button()
        new_content = Adw.ButtonContent(icon_name='list-add-symbolic', label='New chat')
        new.set_child(new_content)
        new.add_css_class('new-chat')
        new.set_margin_start(10)
        new.set_margin_end(10)
        new.set_margin_top(6)
        new.connect('clicked', lambda _b: self.new_chat())
        box.append(new)

        self.search = Gtk.SearchEntry(placeholder_text='Search chats', margin_start=10, margin_end=10)
        self.search.connect('search-changed', lambda _s: self.chat_list.invalidate_filter())
        box.append(self.search)

        self.chat_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.chat_list.add_css_class('chat-list')
        self.chat_list.set_header_func(self._row_header)
        self.chat_list.set_filter_func(self._row_filter)
        self.chat_list.connect('row-activated', lambda _l, row: self.open_chat(row.chat_id))
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.chat_list)
        box.append(scroller)

        view.set_content(box)
        return view

    def _build_content(self):
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toggle = Gtk.ToggleButton(icon_name='sidebar-show-symbolic', tooltip_text='Show or hide the sidebar')
        self.split.bind_property('show-sidebar', toggle, 'active',
                                 GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        header.pack_start(toggle)
        self.title_widget = Adw.WindowTitle(title='Fleece', subtitle='')
        header.set_title_widget(self.mode_switch())
        menu = Gio.Menu()
        menu.append('New chat', 'win.new-chat')
        menu.append('Get models…', 'win.models')
        menu.append('Settings', 'win.settings')
        menu.append('About Fleece', 'app.about')
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu, primary=True))
        view.add_top_bar(header)

        self.banner = Adw.Banner(title="Can't reach Ollama", button_label='Try again')
        self._banner_mode = 'retry'
        self.banner.connect('button-clicked', self._on_banner)
        view.add_top_bar(self.banner)

        self.composer = self._build_composer()

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)

        # Welcome page: the composer sits in the middle, like a search box.
        welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=14)
        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=560, margin_start=16, margin_end=16)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        inner.append(Gtk.Image(icon_name=APP_ID, pixel_size=72, margin_bottom=4))
        self.greeting = Gtk.Label()
        self.greeting.add_css_class('greeting')
        inner.append(self.greeting)
        sub = Gtk.Label(label='Your models run on this computer. Nothing leaves it.')
        sub.add_css_class('sub-greeting')
        inner.append(sub)
        self.welcome_holder = Gtk.Box(margin_top=14)
        inner.append(self.welcome_holder)
        chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=2,
                            column_spacing=8, row_spacing=8, homogeneous=False, halign=Gtk.Align.CENTER)
        for text in SUGGESTIONS:
            chip = Gtk.Button(label=text)
            chip.add_css_class('chip')
            chip.add_css_class('flat')
            chip.connect('clicked', lambda _b, t=text: self._send_text(t))
            chips.append(chip)
        inner.append(chips)
        clamp.set_child(inner)
        welcome.append(clamp)
        self.stack.add_named(welcome, 'welcome')

        # Chat page: messages scroll, the composer is docked at the bottom.
        chat = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        mclamp = Adw.Clamp(maximum_size=780, tightening_threshold=600, margin_start=16, margin_end=16)
        self.msgs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=24, margin_bottom=64)
        mclamp.set_child(self.msgs)
        self.scroller.set_child(mclamp)
        adj = self.scroller.get_vadjustment()
        adj.connect('notify::upper', self._follow)
        adj.connect('value-changed', self._on_scrolled)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect('scroll', self._on_wheel)
        self.scroller.add_controller(wheel)
        chat.append(self.scroller)
        dock_clamp = Adw.Clamp(maximum_size=780, tightening_threshold=600, margin_start=16, margin_end=16,
                               margin_bottom=14, margin_top=4)
        self.dock_holder = Gtk.Box()
        dock_clamp.set_child(self.dock_holder)
        chat.append(dock_clamp)
        self.stack.add_named(chat, 'chat')

        view.set_content(self.stack)
        return view

    def _build_composer(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        card.add_css_class('composer')

        overlay = Gtk.Overlay()
        self.entry = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=6, accepts_tab=False)
        self.entry_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=34,
                                               max_content_height=220, propagate_natural_height=True)
        self.entry_scroll.set_child(self.entry)
        overlay.set_child(self.entry_scroll)
        self.hint = Gtk.Label(label='Message Fleece…', xalign=0, valign=Gtk.Align.START, margin_top=6,
                              can_target=False)
        self.hint.add_css_class('hint')
        overlay.add_overlay(self.hint)
        card.append(overlay)

        buf = self.entry.get_buffer()
        buf.connect('changed', lambda b: self.hint.set_visible(b.get_char_count() == 0))
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._on_key)
        self.entry.add_controller(keys)

        row = Gtk.Box(spacing=6)
        self.model_drop = Gtk.DropDown(model=Gtk.StringList())
        self.model_drop.add_css_class('flat')
        self.model_drop.add_css_class('model-pick')
        self.model_drop.set_tooltip_text('Choose a model')
        self.model_drop.connect('notify::selected', self._on_model_changed)
        row.append(self.model_drop)
        row.append(Gtk.Box(hexpand=True))
        self.send_btn = Gtk.Button(icon_name='go-up-symbolic', valign=Gtk.Align.CENTER)
        self.send_btn.add_css_class('circular')
        self.send_btn.add_css_class('suggested-action')
        self.send_btn.add_css_class('send')
        self.send_btn.connect('clicked', self._on_send_clicked)
        row.append(self.send_btn)
        card.append(row)
        return card

    def _place_composer(self, holder):
        parent = self.composer.get_parent()
        if parent is holder:
            return
        if parent:
            parent.remove(self.composer)
        holder.append(self.composer)

    def _actions(self):
        for name, cb in (('new-chat', lambda *_: self.code.new_file() if self._mode() == 'code' else self.new_chat()),
                         ('models', lambda *_: self.show_models()),
                         ('settings', lambda *_: self.show_settings())):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', cb)
            self.add_action(action)

    # ---- sidebar --------------------------------------------------------

    @staticmethod
    def _bucket(ts):
        day = datetime.datetime.fromtimestamp(ts).date()
        delta = (datetime.date.today() - day).days
        if delta <= 0:
            return 'Today'
        if delta == 1:
            return 'Yesterday'
        if delta < 8:
            return 'Previous 7 days'
        return 'Older'

    def reload_list(self):
        child = self.chat_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.chat_list.remove(child)
            child = nxt
        for item in self.store.list():
            row = Gtk.ListBoxRow()
            row.chat_id = item['id']
            row.bucket = self._bucket(item['updated'])
            row.title = item['title']
            box = Gtk.Box()
            label = Gtk.Label(label=item['title'], xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            label.add_css_class('chat-title')
            box.append(label)
            delete = Gtk.Button(icon_name='user-trash-symbolic', tooltip_text='Delete chat',
                                valign=Gtk.Align.CENTER, margin_end=4)
            delete.add_css_class('flat')
            delete.add_css_class('circular')
            delete.add_css_class('del')
            delete.connect('clicked', lambda _b, cid=item['id']: self.delete_chat(cid))
            box.append(delete)
            row.set_child(box)
            self.chat_list.append(row)
            if self.chat and item['id'] == self.chat['id']:
                self.chat_list.select_row(row)

    def _row_header(self, row, before):
        if before is None or before.bucket != row.bucket:
            label = Gtk.Label(label=row.bucket.upper(), xalign=0)
            label.add_css_class('group-title')
            row.set_header(label)
        else:
            row.set_header(None)

    def _row_filter(self, row):
        query = self.search.get_text().strip().lower()
        return not query or query in row.title.lower()

    def delete_chat(self, chat_id):
        self.store.delete(chat_id)
        if self.chat and self.chat['id'] == chat_id:
            self.new_chat()
        self.reload_list()

    # ---- models ---------------------------------------------------------

    def refresh_models(self):
        def work():
            try:
                models = self.client.models()
                GLib.idle_add(self._models_loaded, models, None)
            except OllamaError as error:
                GLib.idle_add(self._models_loaded, [], str(error))

        threading.Thread(target=work, daemon=True).start()

    def _models_loaded(self, models, error):
        self.models = [m['name'] for m in models]
        self.model_details = {m['name']: m.get('details') or {} for m in models}
        self.banner.set_revealed(error is not None or not self.models)
        if error:
            self._banner_mode = 'retry'
            self.banner.set_title(error + ' Start Ollama, or install it from ollama.com/download.')
            self.banner.set_button_label('Try again')
        elif not self.models:
            self._banner_mode = 'models'
            self.banner.set_title('You have no models yet. Download one to start chatting.')
            self.banner.set_button_label('Get a model')
        self._populate_models()
        if self.code:
            self.code.set_models(self.models)
        if self._first_prompt and self.models:
            text, self._first_prompt = self._first_prompt, None
            self._send_text(text)
        return GLib.SOURCE_REMOVE

    def _populate_models(self):
        self._populating = True
        store = Gtk.StringList()
        for name in self.models:
            store.append(name)
        self.model_drop.set_model(store)
        wanted = (self.chat or {}).get('model') or self.config['model']
        if wanted in self.models:
            self.model_drop.set_selected(self.models.index(wanted))
        self._populating = False
        self._show_model()

    def _current_model(self):
        index = self.model_drop.get_selected()
        if self.models and index < len(self.models):
            return self.models[index]
        return ''

    def _on_model_changed(self, *_args):
        if self._populating:
            return
        model = self._current_model()
        if model:
            self.config['model'] = model
            if self.chat:
                self.chat['model'] = model
        self._show_model()

    def _show_model(self):
        self.title_widget.set_subtitle(self._current_model())

    # ---- chats ----------------------------------------------------------

    def _cancel_for_switch(self):
        """Stop a running answer before another chat is shown, keeping what was written so far."""
        if not self.streaming:
            return
        self._gen += 1
        self._stop_stream()
        self._set_streaming(False)
        if self.current and not self.current.msg['content']:
            self.chat['messages'].pop()
        elif self.current:
            self.current.finish()
        if any(m['role'] == 'user' for m in self.chat['messages']):
            self.store.save(self.chat)
        self.current = None
        self.reload_list()

    def new_chat(self):
        self._cancel_for_switch()
        self.chat = new_chat(self._current_model() or self.config['model'])
        self._clear_messages()
        hour = datetime.datetime.now().hour
        part = 'morning' if hour < 12 else 'afternoon' if hour < 18 else 'evening'
        self.greeting.set_text(f'Good {part}')
        self.stack.set_visible_child_name('welcome')
        self._place_composer(self.welcome_holder)
        self.chat_list.unselect_all()
        self.entry.grab_focus()

    def open_chat(self, chat_id):
        try:
            chat = self.store.load(chat_id)
        except (OSError, ValueError):
            self.toast('That chat could not be opened.')
            return
        self._cancel_for_switch()
        self.chat = chat
        self._clear_messages()
        for message in chat['messages']:
            self._add_message_widget(message, final=True)
        self._update_regen()
        self._populate_models()
        self.stack.set_visible_child_name('chat')
        self._place_composer(self.dock_holder)
        self._stick = True
        GLib.idle_add(self._scroll_bottom)
        self.entry.grab_focus()

    def _clear_messages(self):
        child = self.msgs.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.msgs.remove(child)
            child = nxt
        self.current = None

    def _add_message_widget(self, message, final):
        if message['role'] == 'user':
            widget = UserMessage(message['content'])
        else:
            widget = AssistantMessage(self, message)
            if final:
                widget.finish(message.get('stats'))
        self.msgs.append(widget)
        return widget

    def _update_regen(self):
        rows = []
        child = self.msgs.get_first_child()
        while child:
            if isinstance(child, AssistantMessage):
                rows.append(child)
            child = child.get_next_sibling()
        for i, row in enumerate(rows):
            row.set_regen_visible(i == len(rows) - 1 and not self.streaming)

    # ---- sending --------------------------------------------------------

    def _on_key(self, _ctrl, keyval, _code, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.SHIFT_MASK:
            self._on_send_clicked(None)
            return True
        return False

    def _on_send_clicked(self, _button):
        if self.streaming:
            self._stop_stream()
            return
        buf = self.entry.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if text:
            buf.set_text('')
            self._send_text(text)

    def _send_text(self, text):
        if self.streaming:
            return
        if not self._current_model():
            self.toast('No model is available. Check that Ollama is running.')
            return
        if self.stack.get_visible_child_name() == 'welcome':
            self.stack.set_visible_child_name('chat')
            self._place_composer(self.dock_holder)
        message = {'role': 'user', 'content': text}
        self.chat['messages'].append(message)
        if self.chat['title'] == 'New chat':
            self.chat['title'] = ' '.join(text.split())[:48]
        self.chat['model'] = self._current_model()
        self._add_message_widget(message, final=True)
        self._stick = True
        self._generate()

    def regenerate(self):
        if self.streaming or not self.chat['messages'] or self.chat['messages'][-1]['role'] != 'assistant':
            return
        self.chat['messages'].pop()
        last = self.msgs.get_last_child()
        if last:
            self.msgs.remove(last)
        self._stick = True
        self._generate()

    def _generate(self):
        message = {'role': 'assistant', 'content': '', 'thinking': ''}
        self.chat['messages'].append(message)
        self.current = self._add_message_widget(message, final=False)
        self._update_regen()
        self._gen += 1
        token = self._gen
        self._set_streaming(True)

        history = [{'role': m['role'], 'content': m['content']} for m in self.chat['messages'][:-1]]
        system = self.config['system_prompt'].strip()
        if system:
            history.insert(0, {'role': 'system', 'content': system})
        model = self._current_model()
        threading.Thread(target=self._worker, args=(token, model, history), daemon=True).start()

    def _worker(self, token, model, messages):
        stats = None
        error = None
        try:
            if model not in self.caps:
                self.caps[model] = self.client.capabilities(model)
            payload = {'model': model, 'messages': messages, 'stream': True,
                       'options': {'temperature': self.config['temperature'], 'num_ctx': int(self.config['num_ctx'])}}
            if 'thinking' in self.caps[model]:
                payload['think'] = bool(self.config['show_thinking'])
            stream = self.client.chat(payload)
            if token != self._gen:
                stream.close()
                return
            self._stream = stream
            for chunk in stream:
                msg = chunk.get('message') or {}
                GLib.idle_add(self._on_chunk, token, msg.get('content', ''), msg.get('thinking', ''))
                if chunk.get('done') and chunk.get('eval_duration'):
                    rate = chunk['eval_count'] / (chunk['eval_duration'] / 1e9)
                    stats = f'{model} · {rate:.0f} tokens/s'
        except OllamaError as exc:
            error = str(exc)
        GLib.idle_add(self._on_done, token, stats, error)

    def _on_chunk(self, token, content, thinking):
        if token == self._gen and self.current:
            msg = self.current.msg
            msg['content'] += content
            msg['thinking'] += thinking
            self.current.update()
        return GLib.SOURCE_REMOVE

    def _on_done(self, token, stats, error):
        if token != self._gen:
            return GLib.SOURCE_REMOVE
        row = self.current
        msg = row.msg
        if stats:
            msg['stats'] = stats
        if error and not msg['content']:
            # Keep the history clean: a failed answer is not part of the conversation.
            self.chat['messages'].pop()
        row.finish(stats, error)
        if not msg.get('thinking'):
            msg.pop('thinking', None)
        self._set_streaming(False)
        self._stream = None
        if error and not msg['content']:
            self.current = None
            self.msgs.remove(row)
            self.toast(error)
        if any(m['role'] == 'user' for m in self.chat['messages']):
            self.store.save(self.chat)
            self.reload_list()
        self._update_regen()
        self.entry.grab_focus()
        if self._stick:
            GLib.timeout_add(80, self._scroll_bottom)
            GLib.timeout_add(400, self._scroll_bottom)
        return GLib.SOURCE_REMOVE

    def _stop_stream(self):
        stream = self._stream
        if stream:
            stream.close()

    def _set_streaming(self, streaming):
        self.streaming = streaming
        self.send_btn.set_icon_name('media-playback-stop-symbolic' if streaming else 'go-up-symbolic')
        self.send_btn.set_tooltip_text('Stop' if streaming else 'Send')
        self.model_drop.set_sensitive(not streaming)

    # ---- scrolling ------------------------------------------------------

    def _follow(self, adj, _p):
        if self._stick:
            adj.set_value(adj.get_upper() - adj.get_page_size())

    def _on_scrolled(self, adj):
        # Following the answer stops only when the reader scrolls up, and resumes at the bottom.
        if adj.get_value() >= adj.get_upper() - adj.get_page_size() - 4:
            self._stick = True

    def _on_wheel(self, _ctrl, _dx, dy):
        if dy < 0:
            self._stick = False
        return False

    def _scroll_bottom(self):
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return GLib.SOURCE_REMOVE

    # ---- modes ----------------------------------------------------------

    def mode_switch(self):
        group = Adw.ToggleGroup()
        group.add_css_class('mode-switch')
        group.add(Adw.Toggle(name='chat', label='Chat', tooltip='Chat with a model'))
        group.add(Adw.Toggle(name='code', label='Code', tooltip='Edit and run code with the assistant beside you'))
        group.set_active_name(self.config['mode'] if self.config['mode'] in ('chat', 'code') else 'chat')
        group.connect('notify::active-name', lambda g, _p: self.set_mode(g.get_active_name()))
        self._mode_groups.append(group)
        return group

    def _mode(self):
        return self.modes.get_visible_child_name()

    def set_mode(self, mode):
        if mode not in ('chat', 'code'):
            return
        for group in self._mode_groups:
            if group.get_active_name() != mode:
                group.set_active_name(mode)
        if mode == 'code' and not self.modes.get_child_by_name('code'):
            self.modes.add_named(self._build_code(), 'code')
        self.modes.set_visible_child_name(mode)
        self.config['mode'] = mode

    def _build_code(self):
        try:
            from .workspace import CodeWorkspace
            self.code = CodeWorkspace(self, self.mode_switch())
            self.code.set_models(self.models)
            return self.code
        except (ImportError, ValueError) as exc:
            view = Adw.ToolbarView()
            header = Adw.HeaderBar()
            header.set_title_widget(self.mode_switch())
            view.add_top_bar(header)
            view.set_content(Adw.StatusPage(
                icon_name='dialog-warning-symbolic', title='Code mode needs a few more parts',
                description='Install them with:\n\nsudo apt install gir1.2-gtksource-5 gir1.2-vte-3.91 '
                            'gir1.2-webkit-6.0\n\nthen open Fleece again.\n\n(' + str(exc) + ')'))
            return view

    def open_in_code(self, code, lang):
        self.set_mode('code')
        if self.code:
            self.code.open_snippet(code, lang)

    def _on_close_request(self, _win):
        if self.code and self.code.unsaved():
            def close():
                for tab in self.code.tabs():
                    tab.buffer.set_modified(False)
                self.code.shutdown()
                self.destroy()

            self.code._confirm_unsaved(close)
            return True
        if self.code:
            self.code.shutdown()
        return False

    # ---- misc -----------------------------------------------------------

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def _on_banner(self, _banner):
        if self._banner_mode == 'models':
            self.show_models()
        else:
            self.refresh_models()

    def show_models(self, coding=False):
        from .models import show_model_manager
        show_model_manager(self, coding)

    def show_settings(self):
        from .prefs import show_preferences
        show_preferences(self)

    def settings_changed(self):
        self.client = Client(self.config['host'])
        self.caps.clear()
        self.refresh_models()
