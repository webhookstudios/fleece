"""The project's files and folders, with right-click actions."""
import os

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

HIDDEN = {'.git', '__pycache__', '.venv', '.mypy_cache', '.pytest_cache', '.idea', '.DS_Store',
          '.ruff_cache', '.cache'}
ATTRS = 'standard::name,standard::display-name,standard::type,standard::icon,standard::symbolic-icon'


def _info_file(info):
    return info.get_attribute_object('standard::file')


class FileTree(Gtk.Box):
    __gsignals__ = {
        'open-file': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'renamed': (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        'deleted': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, workspace):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.ws = workspace
        self.root = None

        head = Gtk.Box(spacing=2)
        head.add_css_class('tree-head')
        self.name = Gtk.Label(xalign=0, hexpand=True, ellipsize=3)
        self.name.add_css_class('heading')
        head.append(self.name)
        for icon, tip, cb in (('document-new-symbolic', 'New file', lambda *_: self.new_item(self.root, False)),
                              ('folder-new-symbolic', 'New folder', lambda *_: self.new_item(self.root, True))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class('flat')
            b.connect('clicked', cb)
            head.append(b)
        self.append(head)
        self.path_label = Gtk.Label(xalign=0, ellipsize=1, selectable=True)
        self.path_label.add_css_class('tree-path')
        self.append(self.path_label)

        factory = Gtk.SignalListItemFactory()
        factory.connect('setup', self._setup)
        factory.connect('bind', self._bind)
        self.view = Gtk.ListView(factory=factory, single_click_activate=False)
        self.view.add_css_class('navigation-sidebar')
        self.view.add_css_class('file-tree')
        self.view.connect('activate', self._activate)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.view)
        self.append(scroller)

        self.actions = Gio.SimpleActionGroup()
        for name, cb in (('new-file', lambda p: self.new_item(p, False)),
                         ('new-folder', lambda p: self.new_item(p, True)),
                         ('rename', self.rename), ('trash', self.trash),
                         ('copy-path', self.copy_path), ('reveal', self.reveal),
                         ('set-run-folder', lambda p: self.ws.set_run_folder(p))):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new('s'))
            action.connect('activate', lambda _a, v, cb=cb: cb(v.get_string()))
            self.actions.add_action(action)
        self.insert_action_group('tree', self.actions)

    # ---- model ----------------------------------------------------------

    def _visible(self, info):
        return info.get_name() not in HIDDEN

    @staticmethod
    def _compare(a, b, _data=None):
        a_dir = a.get_file_type() == Gio.FileType.DIRECTORY
        b_dir = b.get_file_type() == Gio.FileType.DIRECTORY
        if a_dir != b_dir:
            return -1 if a_dir else 1
        x, y = a.get_display_name().casefold(), b.get_display_name().casefold()
        return (x > y) - (x < y)

    def _dir_model(self, gfile):
        listing = Gtk.DirectoryList.new(ATTRS, gfile)
        filtered = Gtk.FilterListModel(model=listing, filter=Gtk.CustomFilter.new(self._visible))
        return Gtk.SortListModel(model=filtered, sorter=Gtk.CustomSorter.new(self._compare))

    def _children(self, info):
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            return self._dir_model(_info_file(info))
        return None

    def set_root(self, path):
        self.root = path
        self.name.set_text(os.path.basename(path.rstrip(os.sep)) or path)
        home = os.path.expanduser('~')
        self.path_label.set_text(path.replace(home, '~', 1) if path.startswith(home) else path)
        self.path_label.set_tooltip_text(path)
        tree = Gtk.TreeListModel.new(self._dir_model(Gio.File.new_for_path(path)), False, False, self._children)
        self.tree = tree
        self.view.set_model(Gtk.SingleSelection(model=tree, autoselect=False, can_unselect=True))

    # ---- rows -----------------------------------------------------------

    def _setup(self, _factory, item):
        expander = Gtk.TreeExpander(indent_for_icon=True)
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Image())
        box.append(Gtk.Label(xalign=0, ellipsize=3))
        expander.set_child(box)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect('pressed', self._menu, expander)
        expander.add_controller(click)
        item.set_child(expander)

    def _bind(self, _factory, item):
        row = item.get_item()
        info = row.get_item()
        expander = item.get_child()
        expander.set_list_row(row)
        box = expander.get_child()
        icon = info.get_symbolic_icon() or info.get_icon()
        box.get_first_child().set_from_gicon(icon)
        box.get_last_child().set_text(info.get_display_name())
        expander.fleece_path = _info_file(info).get_path()
        expander.fleece_dir = info.get_file_type() == Gio.FileType.DIRECTORY

    def _activate(self, _view, position):
        row = self.tree.get_item(position)
        info = row.get_item()
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            row.set_expanded(not row.get_expanded())
        else:
            self.emit('open-file', _info_file(info).get_path())

    def _menu(self, gesture, _n, x, y, expander):
        path = getattr(expander, 'fleece_path', None)
        if not path:
            return
        folder = path if expander.fleece_dir else os.path.dirname(path)
        menu = Gio.Menu()
        create = Gio.Menu()
        create.append('New file here', f'tree.new-file::{folder}')
        create.append('New folder here', f'tree.new-folder::{folder}')
        menu.append_section(None, create)
        edit = Gio.Menu()
        edit.append('Rename…', f'tree.rename::{path}')
        edit.append('Move to trash', f'tree.trash::{path}')
        menu.append_section(None, edit)
        where = Gio.Menu()
        where.append('Copy full path', f'tree.copy-path::{path}')
        where.append('Show in Files', f'tree.reveal::{path}')
        if expander.fleece_dir:
            where.append('Run commands from this folder', f'tree.set-run-folder::{path}')
        menu.append_section(None, where)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(expander)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect('closed', lambda p: GLib.idle_add(p.unparent))
        popover.popup()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    # ---- actions --------------------------------------------------------

    def _ask_name(self, title, initial, button, done):
        dialog = Adw.AlertDialog(heading=title)
        entry = Gtk.Entry(text=initial, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response('cancel', 'Cancel')
        dialog.add_response('ok', button)
        dialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('ok')
        dialog.set_close_response('cancel')

        def on_response(_d, response):
            name = entry.get_text().strip()
            if response == 'ok' and name and '/' not in name and name not in ('.', '..'):
                done(name)

        dialog.connect('response', on_response)
        dialog.present(self.ws.win)
        stem = os.path.splitext(initial)[0]
        GLib.idle_add(lambda: entry.grab_focus() or entry.select_region(0, len(stem)) or False)

    def new_item(self, folder, is_dir):
        folder = folder or self.root
        if not folder:
            return

        def make(name):
            path = os.path.join(folder, name)
            if os.path.exists(path):
                self.ws.toast(f'{name} already exists here.')
                return
            try:
                if is_dir:
                    os.makedirs(path)
                else:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    open(path, 'x').close()
                    self.emit('open-file', path)
            except OSError as exc:
                self.ws.toast(str(exc))

        where = folder.replace(os.path.expanduser('~'), '~', 1)
        self._ask_name(f'New {"folder" if is_dir else "file"} in {where}',
                       'new-folder' if is_dir else 'untitled.txt', 'Create', make)

    def rename(self, path):
        def do(name):
            target = os.path.join(os.path.dirname(path), name)
            if os.path.exists(target):
                self.ws.toast(f'{name} already exists here.')
                return
            try:
                os.rename(path, target)
                self.emit('renamed', path, target)
            except OSError as exc:
                self.ws.toast(str(exc))

        self._ask_name('Rename', os.path.basename(path), 'Rename', do)

    def trash(self, path):
        try:
            Gio.File.new_for_path(path).trash(None)
            self.emit('deleted', path)
            self.ws.toast(f'Moved {os.path.basename(path)} to the trash')
        except GLib.Error as exc:
            self.ws.toast(exc.message)

    def copy_path(self, path):
        Gdk.Display.get_default().get_clipboard().set(path)
        self.ws.toast('Path copied')

    def reveal(self, path):
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).open_containing_folder(self.ws.win, None, None)
