import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__  # noqa: E402
from .store import ChatStore, Config  # noqa: E402
from .window import FleeceWindow  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


class FleeceApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.prompt = None
        self.add_main_option('send', ord('s'), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             'Open a new chat and send this message', 'TEXT')
        self.add_main_option('settings', 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             'Open the settings window', None)
        self.add_main_option('models', 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             'Open the window for downloading models', None)
        self.add_main_option('code', 0, GLib.OptionFlags.NONE, GLib.OptionArg.FILENAME,
                             'Open a folder in Code mode', 'FOLDER')
        self.open_settings = False
        self.code_folder = None
        self.open_models = False
        self.connect('handle-local-options', self._on_options)

    def _on_options(self, _app, options):
        value = options.lookup_value('send')
        if value:
            self.prompt = value.get_string()
        self.open_settings = options.contains('settings')
        self.open_models = options.contains('models')
        folder = options.lookup_value('code')
        if folder:
            import os
            raw = bytes(folder.get_bytestring()).rstrip(b'\0').decode()
            self.code_folder = os.path.abspath(os.path.expanduser(raw))
        return -1

    def do_startup(self):
        Adw.Application.do_startup(self)
        from . import sedge
        sedge.apply()
        provider = Gtk.CssProvider()
        provider.load_from_path(os.path.join(HERE, 'style.css'))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        icons = os.path.join(os.path.dirname(HERE), 'data', 'icons')
        if os.path.isdir(icons):
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            theme.set_search_path([icons, *theme.get_search_path()])

        about = Gio.SimpleAction.new('about', None)
        about.connect('activate', self._about)
        self.add_action(about)
        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action('app.quit', ['<Control>q'])
        self.set_accels_for_action('win.new-chat', ['<Control>n'])
        self.set_accels_for_action('win.settings', ['<Control>comma'])

    def do_activate(self):
        win = self.props.active_window
        if not win:
            if self.code_folder:
                config = Config()
                config['last_project'] = self.code_folder
                config['mode'] = 'code'
            win = FleeceWindow(self, Config(), ChatStore(), self.prompt, self.open_settings, self.open_models)
        win.present()

    def _about(self, *_args):
        Adw.AboutDialog(application_name='Fleece', application_icon=APP_ID, version=__version__,
                        developer_name='webhookstudios', license_type=Gtk.License.GPL_2_0,
                        website='https://github.com/webhookstudios/fleece',
                        comments='A chat window for your local Ollama models.\n\nPart of the Sedge family of apps.').present(self.props.active_window)


def main(argv):
    GLib.set_prgname(APP_ID)
    return FleeceApp().run(argv)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
