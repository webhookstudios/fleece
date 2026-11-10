"""Turns markdown into GTK widgets, so replies look like text rather than raw symbols."""
from gi.repository import Adw, Gdk, GLib, Gtk, Pango
from markdown_it import MarkdownIt

try:
    from pygments import lex
    from pygments.lexers import get_lexer_by_name, guess_lexer
    from pygments.token import Comment, Keyword, Name, Number, String, Token
except ImportError:  # highlighting is optional
    lex = None

_MD = MarkdownIt('commonmark').enable(['table', 'strikethrough'])

# (dark, light) colors per token family.
_PALETTE = {
    Keyword: ('#c678dd', '#8250df'), String: ('#98c379', '#0a7d33'),
    Comment: ('#7f848e', '#6e7781'), Number: ('#d19a66', '#b25a00'),
    Name.Function: ('#61afef', '#0550ae'), Name.Class: ('#e5c07b', '#953800'),
    Name.Builtin: ('#56b6c2', '#0a6b78'), Name.Decorator: ('#e5c07b', '#953800'),
} if lex else {}

esc = GLib.markup_escape_text


def _inline_markup(token):
    out = []
    for child in token.children or []:
        t = child.type
        if t == 'text':
            out.append(esc(child.content))
        elif t == 'code_inline':
            out.append(f'<span font_family="monospace" background="#80808030"> {esc(child.content)} </span>')
        elif t == 'strong_open':
            out.append('<b>')
        elif t == 'strong_close':
            out.append('</b>')
        elif t == 'em_open':
            out.append('<i>')
        elif t == 'em_close':
            out.append('</i>')
        elif t == 's_open':
            out.append('<s>')
        elif t == 's_close':
            out.append('</s>')
        elif t == 'link_open':
            href = esc(child.attrGet('href') or '')
            out.append(f'<a href="{href}">')
        elif t == 'link_close':
            out.append('</a>')
        elif t == 'softbreak':
            out.append('\n')
        elif t == 'hardbreak':
            out.append('\n')
        elif t == 'image':
            out.append(esc(child.content or ''))
        else:
            out.append(esc(child.content or ''))
    return ''.join(out)


def _label(markup, css=None):
    label = Gtk.Label(use_markup=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0,
                      selectable=True, hexpand=True, halign=Gtk.Align.FILL)
    label.set_markup(markup)
    if css:
        label.add_css_class(css)
    return label


def _match(tokens, start):
    """Index of the token that closes tokens[start]."""
    depth = 0
    for i in range(start, len(tokens)):
        depth += tokens[i].nesting
        if depth == 0:
            return i
    return len(tokens) - 1


def _highlight(code, lang):
    if not lex:
        return esc(code)
    dark = Adw.StyleManager.get_default().get_dark()
    try:
        lexer = get_lexer_by_name(lang) if lang else None
    except Exception:
        lexer = None
    if lexer is None:
        return esc(code)
    parts = []
    for ttype, value in lex(code, lexer):
        t = ttype
        while t not in _PALETTE and t is not Token and t.parent is not None:
            t = t.parent
        color = _PALETTE.get(t)
        text = esc(value)
        if color:
            style = ' style="italic"' if t is Comment else ''
            parts.append(f'<span foreground="{color[0] if dark else color[1]}"{style}>{text}</span>')
        else:
            parts.append(text)
    return ''.join(parts)


def _code_block(code, lang):
    code = code.rstrip('\n')
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.add_css_class('code-block')

    head = Gtk.Box()
    head.add_css_class('code-head')
    name = Gtk.Label(label=lang or 'code', xalign=0, hexpand=True)
    name.add_css_class('dim-label')
    name.add_css_class('caption')
    head.append(name)
    copy = Gtk.Button(icon_name='edit-copy-symbolic', tooltip_text='Copy code')
    copy.add_css_class('flat')
    copy.add_css_class('circular')

    def on_copy(_button):
        Gdk.Display.get_default().get_clipboard().set(code)
        copy.set_icon_name('object-select-symbolic')
        GLib.timeout_add(1200, lambda: copy.set_icon_name('edit-copy-symbolic') or False)

    copy.connect('clicked', on_copy)
    head.append(copy)
    box.append(head)

    body = Gtk.Label(use_markup=True, xalign=0, selectable=True, wrap=False)
    body.set_markup(_highlight(code, lang))
    body.add_css_class('code-body')
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                  vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
    scroller.set_child(body)
    box.append(scroller)
    return box


def _table(tokens):
    rows = []
    row = None
    header = False
    for t in tokens:
        if t.type == 'thead_open':
            header = True
        elif t.type == 'tbody_open':
            header = False
        elif t.type == 'tr_open':
            row = []
        elif t.type == 'tr_close':
            rows.append((header, row))
        elif t.type == 'inline':
            row.append(_inline_markup(t))
    grid = Gtk.Grid(column_spacing=0, row_spacing=0, halign=Gtk.Align.START)
    grid.add_css_class('md-table')
    for r, (is_head, cells) in enumerate(rows):
        for c, markup in enumerate(cells):
            label = Gtk.Label(use_markup=True, xalign=0, selectable=True, wrap=True, max_width_chars=40)
            label.set_markup(f'<b>{markup}</b>' if is_head else markup)
            label.add_css_class('md-cell')
            if is_head:
                label.add_css_class('md-cell-head')
            grid.attach(label, c, r, 1, 1)
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                  vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
    scroller.set_child(grid)
    return scroller


def _blocks(tokens, box):
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == 'paragraph_open':
            box.append(_label(_inline_markup(tokens[i + 1]), 'md-p'))
            i += 3
        elif t.type == 'heading_open':
            box.append(_label(_inline_markup(tokens[i + 1]), 'md-' + t.tag))
            i += 3
        elif t.type in ('fence', 'code_block'):
            lang = (t.info or '').strip().split(' ')[0]
            box.append(_code_block(t.content, lang))
            i += 1
        elif t.type == 'hr':
            box.append(Gtk.Separator(margin_top=6, margin_bottom=6))
            i += 1
        elif t.type == 'blockquote_open':
            end = _match(tokens, i)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            inner.add_css_class('md-quote')
            _blocks(tokens[i + 1:end], inner)
            box.append(inner)
            i = end + 1
        elif t.type in ('bullet_list_open', 'ordered_list_open'):
            end = _match(tokens, i)
            ordered = t.type == 'ordered_list_open'
            number = int(t.attrGet('start') or 1)
            lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            lst.add_css_class('md-list')
            j = i + 1
            while j < end:
                if tokens[j].type == 'list_item_open':
                    item_end = _match(tokens, j)
                    row = Gtk.Box(spacing=8, valign=Gtk.Align.START)
                    marker = Gtk.Label(label=f'{number}.' if ordered else '•', xalign=1, valign=Gtk.Align.START)
                    marker.set_width_chars(2)
                    marker.add_css_class('md-marker')
                    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
                    _blocks(tokens[j + 1:item_end], content)
                    row.append(marker)
                    row.append(content)
                    lst.append(row)
                    number += 1
                    j = item_end + 1
                else:
                    j += 1
            box.append(lst)
            i = end + 1
        elif t.type == 'table_open':
            end = _match(tokens, i)
            box.append(_table(tokens[i:end]))
            i = end + 1
        else:
            i += 1


def render(text):
    """A vertical box with the rendered markdown."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    box.add_css_class('markdown')
    try:
        _blocks(_MD.parse(text), box)
    except Exception:
        box.append(_label(esc(text)))
    return box
