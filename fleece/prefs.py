from gi.repository import Adw, Gtk


def show_preferences(win):
    config = win.config
    dialog = Adw.PreferencesDialog(title='Settings')

    # ---- General ----------------------------------------------------------
    general = Adw.PreferencesPage(title='General', icon_name='preferences-system-symbolic')

    connection = Adw.PreferencesGroup(title='Ollama', description='Where Fleece finds your models.')
    host = Adw.EntryRow(title='Address', text=config['host'], show_apply_button=True)

    def on_host(row):
        config['host'] = row.get_text().strip() or 'http://127.0.0.1:11434'
        win.settings_changed()

    host.connect('apply', on_host)
    connection.add(host)
    general.add(connection)

    chat = Adw.PreferencesGroup(title='Answers')
    temp = Adw.SpinRow(title='Creativity', subtitle='Temperature: lower is more focused, higher is more varied',
                       adjustment=Gtk.Adjustment(value=config['temperature'], lower=0, upper=2,
                                                 step_increment=0.1, page_increment=0.5), digits=1)
    temp.connect('notify::value', lambda r, _p: config.__setitem__('temperature', round(r.get_value(), 2)))
    chat.add(temp)
    ctx = Adw.SpinRow(title='Memory length', subtitle='Context size in tokens. Larger remembers more and uses more RAM',
                      adjustment=Gtk.Adjustment(value=config['num_ctx'], lower=1024, upper=131072,
                                                step_increment=1024, page_increment=8192))
    ctx.connect('notify::value', lambda r, _p: config.__setitem__('num_ctx', int(r.get_value())))
    chat.add(ctx)
    think = Adw.SwitchRow(title='Let thinking models think', subtitle='Shows their reasoning above the answer',
                          active=bool(config['show_thinking']))
    think.connect('notify::active', lambda r, _p: config.__setitem__('show_thinking', r.get_active()))
    chat.add(think)
    general.add(chat)

    prompt_group = Adw.PreferencesGroup(title='Instructions for every chat',
                                        description='Sent first in every conversation, for example "Answer briefly".')
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10, left_margin=12,
                        right_margin=12, accepts_tab=False)
    view.get_buffer().set_text(config['system_prompt'])
    view.get_buffer().connect('changed', lambda b: config.__setitem__(
        'system_prompt', b.get_text(b.get_start_iter(), b.get_end_iter(), False)))
    frame = Gtk.ScrolledWindow(min_content_height=90, max_content_height=200, propagate_natural_height=True,
                               hscrollbar_policy=Gtk.PolicyType.NEVER)
    frame.set_child(view)
    frame.add_css_class('card')
    prompt_group.add(frame)
    general.add(prompt_group)

    # ---- Models -----------------------------------------------------------
    models = Adw.PreferencesGroup(title='Models', description='Download new models or remove ones you no longer use.')
    manage = Adw.ActionRow(title='Get and manage models', subtitle='Recommended models, custom names, free up space',
                           activatable=True)
    manage.add_prefix(Gtk.Image(icon_name='folder-download-symbolic'))
    manage.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))

    def open_manager(_row):
        dialog.close()
        from .models import show_model_manager
        show_model_manager(win)

    manage.connect('activated', open_manager)
    models.add(manage)
    general.add(models)
    dialog.add(general)

    dialog.present(win)
