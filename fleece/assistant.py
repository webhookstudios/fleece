"""The AI panel in Code mode: questions about the open code, and changes you approve."""
import difflib
import os
import re
import threading

import gi

gi.require_version('GtkSource', '5')

from gi.repository import Adw, Gdk, GLib, Gtk, GtkSource, Pango  # noqa: E402

from . import edits  # noqa: E402
from .ollama import OllamaError  # noqa: E402
from .render import render  # noqa: E402

RULES = """You are the coding assistant inside Fleece, a code editor. When it helps, the user's open file is \
included after their message.

Answer clearly and briefly. When the user asks you to change, fix, add to or improve the open file, show every \
change as an edit block in exactly this format:

<<<<<<< SEARCH
exact lines copied from the open file
=======
the lines that should replace them
>>>>>>> REPLACE

Rules for edit blocks:
- Copy the SEARCH lines exactly from the file, with the same indentation. Never invent lines that are not in it.
- Keep each block small: only the lines that change, plus one or two unchanged lines so the place is clear.
- To add code, put a nearby existing line in SEARCH and repeat it in REPLACE together with the new lines.
- To delete code, leave the part after ======= empty.
- Prefer several small blocks to one big one. Explain each change in one sentence before its block.
- If the file is empty, leave SEARCH empty and put the whole new content in REPLACE.
Do not write edit blocks when the user only asks a question."""

QUICK = {
    'explain': 'Explain what this code does, step by step.',
    'fix': 'Find bugs or problems in this code and fix them.',
    'comment': 'Add clear comments and docstrings to this code without changing what it does.',
    'improve': 'Make this code cleaner and easier to read without changing what it does.',
    'tests': 'Write tests for this code.',
}


WANTS_CHANGE = re.compile(r'\b(fix|change|add|remove|delete|rename|refactor|rewrite|update|improve|make|convert|'
                          r'implement|replace|clean|comment|format|optimi[sz]e|simplify|move|split|edit)\b', re.I)
CODE_FENCE = re.compile(r'```[^\n`]*\n(.*?)```', re.S)
CODE_HINTS = ('coder', 'code', 'codestral', 'devstral', 'starcoder', 'deepseek-r1', 'gpt-oss')


def coding_tip(name, details):
    """Why `name` may struggle in Code mode, or None when it should be fine."""
    low = name.lower()
    family = (details.get('family') or '').lower()
    if 'embed' in low or 'minilm' in low or 'bert' in family:
        return (f'{name} is an embedding model. It turns text into numbers for search and cannot write '
                'answers at all, so it will not work here.')
    if any(h in low for h in CODE_HINTS):
        return None
    size = details.get('parameter_size') or ''
    match = re.match(r'([\d.]+)\s*([MB])', size, re.I)
    billions = None
    if match:
        billions = float(match.group(1)) / (1000 if match.group(2).upper() == 'M' else 1)
    if billions is not None and billions >= 7:
        return None
    what = f'a small general chat model ({size} parameters)' if size else 'a general chat model'
    return (f'{name} is {what}, not one trained mainly on code. Small general models often write code that '
            'looks right but does not run, and they struggle to follow the exact format Fleece uses to '
            'suggest changes you can apply. A coding model such as qwen2.5-coder reads and edits code much '
            'more reliably.')


def _scheme():
    dark = Adw.StyleManager.get_default().get_dark()
    manager = GtkSource.StyleSchemeManager.get_default()
    return manager.get_scheme('Adwaita-dark' if dark else 'Adwaita')


class ChangeCard(Gtk.Box):
    """One suggested change: a diff, and Apply / Reject."""

    def __init__(self, panel, path, search, replace, label='Suggested change'):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.label = label
        self.panel = panel
        self.path = path
        self.search = search
        self.replace = replace
        self.state = 'waiting'
        self.add_css_class('change-card')

        head = Gtk.Box(spacing=8)
        head.add_css_class('change-head')
        head.append(Gtk.Image(icon_name='document-edit-symbolic'))
        name = Gtk.Label(label=os.path.basename(path) if path else 'Untitled', xalign=0, hexpand=True,
                         ellipsize=Pango.EllipsizeMode.MIDDLE)
        name.add_css_class('heading')
        head.append(name)
        self.status = Gtk.Label(label=label)
        self.status.add_css_class('caption')
        self.status.add_css_class('dim-label')
        head.append(self.status)
        self.append(head)

        buf = GtkSource.Buffer(language=GtkSource.LanguageManager.get_default().get_language('diff'))
        buf.set_style_scheme(_scheme())
        shown = edits.diff(search, replace) or replace
        buf.set_text(shown)
        view = GtkSource.View(buffer=buf, editable=False, monospace=True, cursor_visible=False,
                              top_margin=6, bottom_margin=6, left_margin=10, right_margin=10)
        view.add_css_class('diff-view')
        lines = shown.count('\n') + 1
        scroller = Gtk.ScrolledWindow(min_content_height=min(340, lines * 19 + 16), max_content_height=340,
                                      propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(view)
        self.append(scroller)

        self.buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.buttons.add_css_class('change-buttons')
        self.reject_btn = Gtk.Button(label='Reject')
        self.reject_btn.add_css_class('flat')
        self.reject_btn.connect('clicked', lambda *_: self.reject())
        self.apply_btn = Gtk.Button(label='Apply')
        self.apply_btn.add_css_class('suggested-action')
        self.apply_btn.set_tooltip_text('Change the file. Ctrl+Z in the editor undoes it.')
        self.apply_btn.connect('clicked', lambda *_: self.apply())
        self.retry_btn = Gtk.Button(label='Ask again', visible=False)
        self.retry_btn.connect('clicked', lambda *_: self.panel.retry_change(self))
        self.buttons.append(self.retry_btn)
        self.buttons.append(self.reject_btn)
        self.buttons.append(self.apply_btn)
        self.append(self.buttons)
        self.check()

    def check(self):
        """Disables Apply when the lines are no longer in the file."""
        if self.state != 'waiting':
            return
        tab = self.panel.ws.find_tab(self.path)
        text = tab.get_text() if tab else self._read()
        if text is None:
            self._fail("That file isn't open any more.")
            return
        found = edits.locate(text, self.search)
        if isinstance(found, str):
            self._fail(found)
        else:
            self.status.set_text(self.label)
            self.apply_btn.set_sensitive(True)
            self.retry_btn.set_visible(False)

    def _read(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                return f.read()
        except (OSError, TypeError, UnicodeDecodeError):
            return None

    def _fail(self, message):
        self.status.set_text("Can't apply cleanly")
        self.status.set_tooltip_text(message)
        self.apply_btn.set_sensitive(False)
        self.retry_btn.set_visible(True)
        self.set_tooltip_text(message)

    def apply(self):
        if self.state != 'waiting':
            return False
        tab = self.panel.ws.find_tab(self.path) or self.panel.ws.open_file(self.path)
        if not tab:
            self._fail("That file couldn't be opened.")
            return False
        text = tab.get_text()
        found = edits.locate(text, self.search)
        if isinstance(found, str):
            self._fail(found)
            return False
        start, end, prefix = found
        new = edits.reindent(self.replace, prefix)
        if new == '' and end < len(text) and text[end] == '\n':
            end += 1  # a deletion takes its line break with it
        self.panel.ws.show_tab(tab)
        tab.replace_range(start, end, new)
        self._done('applied', 'Applied · Ctrl+Z to undo')
        self.panel.ws.toast(f'Change applied to {tab.title}. It is not saved yet.')
        self.panel.cards_changed()
        return True

    def reject(self):
        self._done('rejected', 'Rejected')
        self.panel.cards_changed()

    def _done(self, state, label):
        self.state = state
        self.status.set_text(label)
        self.status.set_tooltip_text(None)
        self.buttons.set_visible(False)
        self.add_css_class(state)


class CodeReply(Gtk.Box):
    """An answer in the AI panel, with change cards in place of edit blocks."""

    def __init__(self, panel, msg, path, target=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.target = target  # [(original text, label), …] to offer a rewrite against, for change requests
        self.panel = panel
        self.msg = msg
        self.path = path
        self.cards = []
        self._timer = 0
        self._shown = None
        self.spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.START)
        self.append(self.spinner)
        self.think = Gtk.Expander(label='Thinking…', visible=False)
        self.think_label = Gtk.Label(wrap=True, xalign=0, selectable=True)
        self.think_label.add_css_class('thinking-text')
        self.think.set_child(self.think_label)
        self.think.add_css_class('thinking')
        self.append(self.think)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.append(self.body)
        self.footer = Gtk.Box(spacing=6, visible=False)
        self.apply_all = Gtk.Button(label='Apply all changes')
        self.apply_all.add_css_class('suggested-action')
        self.apply_all.connect('clicked', lambda *_: [c.apply() for c in list(self.cards)])
        self.footer.append(self.apply_all)
        self.append(self.footer)
        self.stats = Gtk.Label(xalign=0, visible=False)
        self.stats.add_css_class('msg-stats')
        self.append(self.stats)

    def update(self):
        if not self._timer:
            self._timer = GLib.timeout_add(80, self._flush)

    def _flush(self):
        self._timer = 0
        self._render(final=False)
        return GLib.SOURCE_REMOVE

    def _render(self, final):
        content, thinking = self.msg['content'], self.msg.get('thinking', '')
        self.spinner.set_visible(not final and not content and not thinking)
        if thinking:
            self.think.set_visible(True)
            self.think_label.set_text(thinking.strip())
            self.think.set_label('Thinking…' if not content and not final else 'Thinking')
        key = (content, final)
        if key == self._shown:
            return
        self._shown = key
        child = self.body.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.body.remove(child)
            child = nxt
        self.cards = []
        segments = edits.split(content, final)
        if final and self.target and not any(seg[0] == 'change' for seg in segments):
            segments = self._rewrite_fallback(content, segments)
        for seg in segments:
            if seg[0] == 'text':
                self.body.append(render(seg[1]))
            elif seg[0] == 'pending':
                row = Gtk.Box(spacing=8)
                row.add_css_class('pending-change')
                row.append(Gtk.Spinner(spinning=True))
                row.append(Gtk.Label(label='Writing a change…'))
                self.body.append(row)
            elif final:
                card = ChangeCard(self.panel, self.path, seg[1], seg[2], *seg[3:4])
                self.cards.append(card)
                self.body.append(card)
            else:
                label = Gtk.Label(label='Change ready, shown when the answer finishes', xalign=0)
                label.add_css_class('dim-label')
                self.body.append(label)
        self.cards_changed()

    def _rewrite_fallback(self, content, segments):
        """The model rewrote the code in a plain code block: offer that as a change too."""
        blocks = [b.rstrip('\n') for b in CODE_FENCE.findall(content)]
        if not blocks:
            return segments
        code = max(blocks, key=len)
        choice = None
        for original, label in self.target:
            if not original.strip() or code.strip() == original.strip():
                continue
            ratio = difflib.SequenceMatcher(None, original, code, autojunk=False).ratio()
            if ratio >= 0.45 and (choice is None or ratio > choice[0]):
                choice = (ratio, original, label)
        if choice is None:
            return segments
        # The card shows this code as a diff, so the full copy in the text is left out.
        trimmed = []
        for seg in segments:
            if seg[0] == 'text':
                text = CODE_FENCE.sub(lambda m: '' if m.group(1).rstrip('\n') == code else m.group(0), seg[1])
                if text.strip():
                    trimmed.append(('text', text))
            else:
                trimmed.append(seg)
        return trimmed + [('change', choice[1].rstrip('\n'), code, choice[2])]

    def cards_changed(self):
        waiting = [c for c in self.cards if c.state == 'waiting' and c.apply_btn.get_sensitive()]
        self.footer.set_visible(len(waiting) > 1)

    def finish(self, stats=None, error=None):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self._render(final=True)
        self.spinner.set_visible(False)
        if error:
            label = Gtk.Label(label=error, wrap=True, xalign=0)
            label.add_css_class('error-msg')
            self.body.append(label)
        if stats:
            self.stats.set_text(stats)
            self.stats.set_visible(True)


class AssistantPanel(Gtk.Box):
    def __init__(self, workspace):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.ws = workspace
        self.win = workspace.win
        self.history = []  # [{'role', 'content'}] without file contents
        self.streaming = False
        self.current = None
        self._gen = 0
        self._stream = None
        self.include_file = True
        self.add_css_class('assistant-panel')

        head = Gtk.Box(spacing=6)
        head.add_css_class('assistant-head')
        title = Gtk.Label(label='Assistant', xalign=0, hexpand=True)
        title.add_css_class('heading')
        head.append(title)
        self.model_drop = Gtk.DropDown(model=Gtk.StringList())
        self.model_drop.add_css_class('flat')
        self.model_drop.set_tooltip_text('Model for coding help')
        self.model_drop.connect('notify::selected', self._on_model)
        head.append(self.model_drop)
        clear = Gtk.Button(icon_name='edit-clear-all-symbolic', tooltip_text='Start a new conversation')
        clear.add_css_class('flat')
        clear.connect('clicked', lambda *_: self.reset())
        head.append(clear)
        self.append(head)

        self.scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.msgs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=14, margin_end=14,
                            margin_top=14, margin_bottom=40)
        self.scroller.set_child(self.msgs)
        adj = self.scroller.get_vadjustment()
        self._stick = True
        adj.connect('notify::upper', lambda a, _p: self._stick and a.set_value(a.get_upper() - a.get_page_size()))
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect('scroll', lambda _c, _x, dy: setattr(self, '_stick', False) if dy < 0 else None)
        self.scroller.add_controller(wheel)
        adj.connect('value-changed', lambda a: a.get_value() >= a.get_upper() - a.get_page_size() - 4
                    and setattr(self, '_stick', True))
        self.append(self.scroller)
        self._empty()

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=10, margin_end=10,
                         margin_bottom=10)
        self.chip = Gtk.Button(halign=Gtk.Align.START)
        self.chip.add_css_class('context-chip')
        self.chip.set_tooltip_text('What the assistant can see. Click to include or leave out the file.')
        self.chip.connect('clicked', self._toggle_file)
        bottom.append(self.chip)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class('composer')
        card.add_css_class('composer-small')
        overlay = Gtk.Overlay()
        self.entry = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=4, bottom_margin=4, accepts_tab=False)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=24,
                                    max_content_height=180, propagate_natural_height=True)
        scroll.set_child(self.entry)
        overlay.set_child(scroll)
        self.hint = Gtk.Label(label='Ask about this code…', xalign=0, valign=Gtk.Align.START, margin_top=4,
                              can_target=False)
        self.hint.add_css_class('hint')
        overlay.add_overlay(self.hint)
        card.append(overlay)
        self.entry.get_buffer().connect('changed', lambda b: self.hint.set_visible(b.get_char_count() == 0))
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._on_key)
        self.entry.add_controller(keys)
        row = Gtk.Box()
        row.append(Gtk.Box(hexpand=True))
        self.send_btn = Gtk.Button(icon_name='go-up-symbolic')
        self.send_btn.add_css_class('circular')
        self.send_btn.add_css_class('suggested-action')
        self.send_btn.add_css_class('send')
        self.send_btn.connect('clicked', self._on_send)
        row.append(self.send_btn)
        card.append(row)
        bottom.append(card)
        self.append(bottom)
        self.refresh_context()

    # ---- models ---------------------------------------------------------

    def set_models(self, models):
        self._models = list(models)
        self._populating = True
        store = Gtk.StringList()
        for m in self._models:
            store.append(m)
        self.model_drop.set_model(store)
        wanted = self.win.config['code_model'] or self.win.config['model']
        if wanted in self._models:
            self.model_drop.set_selected(self._models.index(wanted))
        self._populating = False

    def _on_model(self, *_args):
        if getattr(self, '_populating', False):
            return
        model = self.model()
        if model:
            self.win.config['code_model'] = model

    def model(self):
        models = getattr(self, '_models', [])
        i = self.model_drop.get_selected()
        return models[i] if models and i < len(models) else ''

    # ---- context --------------------------------------------------------

    def refresh_context(self):
        tab = self.ws.current_tab()
        if not tab:
            self.chip.set_label('No file open')
            self.chip.set_sensitive(False)
            return
        self.chip.set_sensitive(True)
        if not self.include_file:
            self.chip.set_label(f'＋ Include {tab.title}')
            return
        sel = tab.selection()
        if sel:
            _text, a, b = sel
            where = f'line {a}' if a == b else f'lines {a}–{b}'
            self.chip.set_label(f'{tab.title} · {where}  ✕')
        else:
            self.chip.set_label(f'{tab.title}  ✕')

    def _toggle_file(self, *_args):
        self.include_file = not self.include_file
        self.refresh_context()

    def _context(self, tab):
        """The text sent with a question, and a short note on what it covered."""
        text = tab.get_text()
        lang = tab.language_id or 'text'
        rel = os.path.relpath(tab.path, self.ws.project) if tab.path and self.ws.project else tab.title
        lines = text.split('\n')
        budget = max(4000, int(self.win.config['num_ctx']) * 3 - 6000)
        sel = tab.selection()
        head = f'Open file: {rel} ({tab.language_name}, {len(lines)} lines)'
        if len(text) > budget:
            centre = sel[1] if sel else tab.cursor()[0]
            span = max(40, budget // 120)
            a = max(1, centre - span)
            b = min(len(lines), centre + span)
            text = '\n'.join(lines[a - 1:b])
            head += f'. The file is long, so only lines {a}–{b} are shown'
        block = f'\n\n---\n{head}:\n```{lang}\n{text}\n```'
        if sel:
            block += f'\nThe user selected lines {sel[1]}–{sel[2]}:\n```{lang}\n{sel[0]}\n```'
        return block

    # ---- conversation ---------------------------------------------------

    def _empty(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER, vexpand=True,
                      margin_top=40)
        box.add_css_class('assistant-empty')
        box.append(Gtk.Image(icon_name='io.github.webhookstudios.Fleece', pixel_size=48))
        label = Gtk.Label(label='Ask about the open file, or select code and right-click for quick actions. '
                                'Suggested changes wait for you to apply them.',
                          wrap=True, justify=Gtk.Justification.CENTER)
        label.add_css_class('dim-label')
        box.append(label)
        for key, text in (('explain', 'Explain this file'), ('fix', 'Find and fix bugs'),
                          ('comment', 'Add comments')):
            b = Gtk.Button(label=text, halign=Gtk.Align.CENTER)
            b.add_css_class('chip')
            b.add_css_class('flat')
            b.connect('clicked', lambda *_a, k=key: self.quick(k))
            box.append(b)
        self.msgs.append(box)
        self._empty_box = box

    def reset(self):
        self._cancel()
        self.history = []
        child = self.msgs.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.msgs.remove(child)
            child = nxt
        self._empty()

    def _on_key(self, _c, keyval, _code, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.SHIFT_MASK:
            self._on_send(None)
            return True
        return False

    def _on_send(self, _b):
        if self.streaming:
            self._cancel(keep=True)
            return
        buf = self.entry.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if text:
            buf.set_text('')
            self.ask(text)

    def focus(self):
        self.entry.grab_focus()

    def quick(self, key):
        self.ask(QUICK[key], change=key in ('fix', 'comment', 'improve'))

    def _show_tip(self, model, tip, question, extra, change):
        """Shown once per model: why it may struggle with code, and where to get a better one."""
        shown = list(self.win.config['code_tip_shown'] or [])
        shown.append(model)
        self.win.config['code_tip_shown'] = shown
        dialog = Adw.AlertDialog(heading='This model may struggle with code', body=tip)
        dialog.add_response('use', f'Use {model} anyway')
        dialog.add_response('get', 'Get a coding model')
        dialog.set_response_appearance('get', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('get')
        dialog.set_close_response('use')
        if self._coding_installed():
            dialog.set_body(tip + f'\n\nYou already have {self._coding_installed()}. Pick it from the menu at the '
                            'top of the assistant.')
            dialog.add_response('switch', f'Switch to {self._coding_installed()}')
            dialog.set_response_appearance('switch', Adw.ResponseAppearance.SUGGESTED)
            dialog.set_response_appearance('get', Adw.ResponseAppearance.DEFAULT)
            dialog.set_default_response('switch')

        def on_response(_d, response):
            if response == 'use':
                self.ask(question, extra, change)
            elif response == 'switch':
                better = self._coding_installed()
                self.model_drop.set_selected(self._models.index(better))
                self.ask(question, extra, change)
            else:
                self.entry.get_buffer().set_text(question)
                self.win.show_models(coding=True)

        dialog.connect('response', on_response)
        dialog.present(self.win)

    def _coding_installed(self):
        for name in getattr(self, '_models', []):
            if not coding_tip(name, self.win.model_details.get(name, {})):
                return name
        return None

    def ask(self, question, extra='', change=None):
        if self.streaming:
            return
        model = self.model()
        if not model:
            self.ws.toast('No model is available. Check that Ollama is running.')
            return
        if model not in (self.win.config['code_tip_shown'] or []):
            tip = coding_tip(model, self.win.model_details.get(model, {}))
            if tip:
                self._show_tip(model, tip, question, extra, change)
                return
        if getattr(self, '_empty_box', None):
            self.msgs.remove(self._empty_box)
            self._empty_box = None
        tab = self.ws.current_tab()
        path = tab.path if tab else None
        context = self._context(tab) if tab and self.include_file else ''
        shown = question
        if tab and self.include_file:
            sel = tab.selection()
            note = f'{tab.title}' + (f' · lines {sel[1]}–{sel[2]}' if sel else '')
        else:
            note = None
        self.msgs.append(self._user_bubble(shown, note))
        self._stick = True

        messages = [{'role': 'system', 'content': RULES}] + self.history[-12:]
        messages.append({'role': 'user', 'content': question + extra + context})
        self.history.append({'role': 'user', 'content': question + extra})
        reply = {'role': 'assistant', 'content': '', 'thinking': ''}
        target = None
        wants = change if change is not None else bool(WANTS_CHANGE.search(question))
        if tab and self.include_file and wants:
            sel = tab.selection()
            target = [(tab.get_text(), 'Replace whole file')]
            if sel:
                where = f'line {sel[1]}' if sel[1] == sel[2] else f'lines {sel[1]}–{sel[2]}'
                target.insert(0, (sel[0], f'Replace {where}'))
        self.current = CodeReply(self, reply, path, target)
        self.msgs.append(self.current)
        self._gen += 1
        token = self._gen
        self._set_streaming(True)
        threading.Thread(target=self._worker, args=(token, model, messages), daemon=True).start()

    def retry_change(self, card):
        self.ask("Your change didn't match the current file, so it couldn't be applied. Look at the file again "
                 "and write the edit block with SEARCH lines copied exactly from it.")

    def _user_bubble(self, text, note):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.END)
        bubble = Gtk.Box()
        bubble.add_css_class('user-bubble')
        bubble.append(Gtk.Label(label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0, selectable=True,
                                max_width_chars=48))
        outer.append(bubble)
        if note:
            n = Gtk.Label(label=note, xalign=1)
            n.add_css_class('caption')
            n.add_css_class('dim-label')
            outer.append(n)
        return outer

    def _worker(self, token, model, messages):
        stats = error = None
        win = self.win
        try:
            if model not in win.caps:
                win.caps[model] = win.client.capabilities(model)
            payload = {'model': model, 'messages': messages, 'stream': True,
                       'options': {'temperature': min(0.4, float(win.config['temperature'])),
                                   'num_ctx': int(win.config['num_ctx'])}}
            if 'thinking' in win.caps[model]:
                payload['think'] = bool(win.config['show_thinking'])
            stream = win.client.chat(payload)
            if token != self._gen:
                stream.close()
                return
            self._stream = stream
            for chunk in stream:
                msg = chunk.get('message') or {}
                GLib.idle_add(self._on_chunk, token, msg.get('content', ''), msg.get('thinking', ''))
                if chunk.get('done') and chunk.get('eval_duration'):
                    stats = f"{model} · {chunk['eval_count'] / (chunk['eval_duration'] / 1e9):.0f} tokens/s"
        except OllamaError as exc:
            error = str(exc)
        GLib.idle_add(self._on_done, token, stats, error)

    def _on_chunk(self, token, content, thinking):
        if token == self._gen and self.current:
            self.current.msg['content'] += content
            self.current.msg['thinking'] += thinking
            self.current.update()
        return GLib.SOURCE_REMOVE

    def _on_done(self, token, stats, error):
        if token != self._gen:
            return GLib.SOURCE_REMOVE
        reply = self.current
        reply.finish(stats, error)
        if reply.msg['content']:
            self.history.append({'role': 'assistant', 'content': reply.msg['content']})
        self._set_streaming(False)
        self._stream = None
        return GLib.SOURCE_REMOVE

    def _cancel(self, keep=False):
        if self._stream:
            self._stream.close()
        if not keep:
            self._gen += 1
            self._set_streaming(False)

    def _set_streaming(self, on):
        self.streaming = on
        self.send_btn.set_icon_name('media-playback-stop-symbolic' if on else 'go-up-symbolic')
        self.send_btn.set_tooltip_text('Stop' if on else 'Send')

    def cards_changed(self):
        child = self.msgs.get_first_child()
        while child:
            if isinstance(child, CodeReply):
                child.cards_changed()
            child = child.get_next_sibling()

    def recheck_cards(self):
        """After the file changes, cards whose lines are gone stop offering Apply."""
        child = self.msgs.get_first_child()
        while child:
            if isinstance(child, CodeReply):
                for card in child.cards:
                    card.check()
                child.cards_changed()
            child = child.get_next_sibling()
