"""The "Get models" window: download, list and remove the models Ollama keeps on this computer."""
import threading

from gi.repository import Adw, GLib, Gtk

from .ollama import Client, OllamaError

# name, short description, approximate download size
RECOMMENDED = [
    ('llama3.2:3b', 'Small and quick. A good first model for everyday questions.', '2.0 GB'),
    ('gemma3:4b', "Google's all-rounder. Good writing, and it can read images.", '3.3 GB'),
    ('phi4-mini', "Microsoft's compact model. Strong at reasoning for its size.", '2.5 GB'),
    ('qwen3:8b', 'Thinks before it answers. Strong at maths, code and planning.', '5.2 GB'),
    ('llama3.1:8b', 'A larger, more capable general model. Wants about 8 GB of RAM.', '4.9 GB'),
    ('qwen2.5-coder:7b', 'Tuned for writing and explaining code.', '4.7 GB'),
    ('deepseek-r1:8b', 'Shows its reasoning step by step on harder problems.', '5.2 GB'),
    ('mistral', 'Fast and capable. A long-time favorite.', '4.1 GB'),
]


def human(size):
    size = float(size or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit in ('B', 'KB') else f'{size:.1f} {unit}'
        size /= 1024


def _base(name):
    return name[:-7] if name.endswith(':latest') else name


class ModelManager:
    def __init__(self, win):
        self.win = win
        self.config = win.config
        self.downloads = {}  # name -> row
        self.installed = set()
        self.dialog = Adw.Dialog(title='Get models', content_width=580, content_height=700)

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.toasts = Adw.ToastOverlay()
        self.page = Adw.PreferencesPage()
        self.toasts.set_child(self.page)
        view.set_content(self.toasts)
        self.dialog.set_child(view)

        intro = Adw.PreferencesGroup(
            description='Models run on this computer, so the first download takes a few minutes. '
                        'Bigger models answer better but need more memory.')
        self.page.add(intro)

        self.rec_group = Adw.PreferencesGroup(title='Recommended')
        self.page.add(self.rec_group)

        custom = Adw.PreferencesGroup(
            title='Any other model', description='Type a name from ollama.com/library, such as phi3:mini.')
        self.custom_row = Adw.EntryRow(title='Model name', show_apply_button=True)
        self.custom_row.connect('apply', lambda r: self.download(r.get_text().strip(), r))
        custom.add(self.custom_row)
        self.page.add(custom)

        self.inst_group = Adw.PreferencesGroup(title='On this computer')
        self.page.add(self.inst_group)

        self.rec_rows = {}
        for name, blurb, size in RECOMMENDED:
            row = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle=f'{blurb} · {size}')
            row.set_subtitle_lines(2)
            button = Gtk.Button(label='Download', valign=Gtk.Align.CENTER)
            button.add_css_class('pill')
            button.connect('clicked', lambda _b, n=name: self.download(n))
            row.add_suffix(button)
            row.button = button
            self.rec_group.add(row)
            self.rec_rows[name] = row
        self.inst_rows = []
        self.refresh()

    # ---- list -------------------------------------------------------------

    def refresh(self):
        def work():
            try:
                items = Client(self.config['host']).models()
                error = None
            except OllamaError as exc:
                items, error = [], str(exc)
            GLib.idle_add(self._show, items, error)

        threading.Thread(target=work, daemon=True).start()

    def _show(self, items, error):
        for row in self.inst_rows:
            self.inst_group.remove(row)
        self.inst_rows = []
        self.installed = {m['name'] for m in items} | {_base(m['name']) for m in items}
        if error:
            row = Adw.ActionRow(title='Ollama is not running', subtitle=error)
            self.inst_group.add(row)
            self.inst_rows.append(row)
        for item in items:
            row = Adw.ActionRow(title=GLib.markup_escape_text(item['name']), subtitle=human(item.get('size')))
            delete = Gtk.Button(icon_name='user-trash-symbolic', valign=Gtk.Align.CENTER,
                                tooltip_text='Remove from this computer')
            delete.add_css_class('flat')
            delete.connect('clicked', lambda _b, n=item['name']: self.remove(n))
            row.add_suffix(delete)
            self.inst_group.add(row)
            self.inst_rows.append(row)
        if not items and not error:
            row = Adw.ActionRow(title='No models yet', subtitle='Pick one above to get started.')
            self.inst_group.add(row)
            self.inst_rows.append(row)
        for name, row in self.rec_rows.items():
            have = name in self.installed
            if name not in self.downloads:
                row.button.set_label('Installed' if have else 'Download')
                row.button.set_sensitive(not have)
                row.button.remove_css_class('suggested-action')
                if not have:
                    row.button.add_css_class('suggested-action')
        return GLib.SOURCE_REMOVE

    def remove(self, name):
        def work():
            try:
                Client(self.config['host']).delete(name)
            except OllamaError as exc:
                GLib.idle_add(lambda: self.toasts.add_toast(Adw.Toast(title=str(exc))) or False)
            GLib.idle_add(self._changed)

        threading.Thread(target=work, daemon=True).start()

    def _changed(self):
        self.refresh()
        self.win.refresh_models()
        return GLib.SOURCE_REMOVE

    # ---- downloading ------------------------------------------------------

    def download(self, name, entry=None):
        if not name or name in self.downloads:
            return
        row = self.rec_rows.get(name)
        if row is None:
            # A model typed by hand gets a temporary row of its own.
            row = Adw.ActionRow(title=GLib.markup_escape_text(name))
            button = Gtk.Button(label='Download')
            row.button = button
            self.rec_group.add(row)
            self.rec_rows[name] = row
            row.temporary = True
        row.button.set_visible(False)

        bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, width_request=130)
        stop = Gtk.Button(icon_name='process-stop-symbolic', valign=Gtk.Align.CENTER, tooltip_text='Cancel')
        stop.add_css_class('flat')
        stop.add_css_class('circular')
        row.add_suffix(bar)
        row.add_suffix(stop)
        row.set_subtitle('Starting…')
        state = {'stream': None, 'cancelled': False}
        self.downloads[name] = row
        if entry is not None:
            entry.set_text('')

        def cancel(_b):
            state['cancelled'] = True
            if state['stream']:
                state['stream'].close()

        stop.connect('clicked', cancel)

        def progress(status, done, total):
            if total and done is not None:
                bar.set_fraction(done / total)
                row.set_subtitle(f'{human(done)} of {human(total)}')
            else:
                bar.pulse()
                row.set_subtitle(status.capitalize())
            return GLib.SOURCE_REMOVE

        def finish(error):
            row.remove(bar)
            row.remove(stop)
            del self.downloads[name]
            if getattr(row, 'temporary', False):
                self.rec_group.remove(row)
                del self.rec_rows[name]
            else:
                row.button.set_visible(True)
                for n, blurb, size in RECOMMENDED:
                    if n == name:
                        row.set_subtitle(f'{blurb} · {size}')
            if error and not state['cancelled']:
                self.toasts.add_toast(Adw.Toast(title=error))
            elif not error:
                self.toasts.add_toast(Adw.Toast(title=f'{name} is ready'))
                if not self.config['model']:
                    self.config['model'] = name
            self.refresh()
            self.win.refresh_models()
            return GLib.SOURCE_REMOVE

        def work():
            error = None
            try:
                stream = Client(self.config['host']).pull(name)
                state['stream'] = stream
                if state['cancelled']:
                    stream.close()
                for chunk in stream:
                    GLib.idle_add(progress, chunk.get('status', ''), chunk.get('completed'), chunk.get('total'))
            except OllamaError as exc:
                error = str(exc)
            GLib.idle_add(finish, error)

        threading.Thread(target=work, daemon=True).start()

    def present(self):
        self.dialog.present(self.win)


def show_model_manager(win):
    ModelManager(win).present()
