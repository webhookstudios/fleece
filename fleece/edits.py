"""Suggested code changes: finding them in a reply, and placing them in a file safely.

The model writes each change as:

    <<<<<<< SEARCH
    lines copied from the file
    =======
    the new lines
    >>>>>>> REPLACE
"""
import difflib
import re

_BLOCK = re.compile(
    r'^[ \t]*<{5,9} ?SEARCH[^\n]*\n(.*?)^[ \t]*={5,9}[ \t]*\n(.*?)^[ \t]*>{5,9} ?REPLACE[^\n]*$',
    re.M | re.S)
_OPEN_FENCE = re.compile(r'```[^\n`]*\n[ \t]*$')
_CLOSE_FENCE = re.compile(r'[ \t]*\n?[ \t]*```[ \t]*(?:\n|$)')


def _trim(part):
    return part[:-1] if part.endswith('\n') else part


def split(text, final=True):
    """Cuts a reply into ('text', str), ('change', search, replace) and, while streaming, ('pending',)."""
    segments = []
    pos = 0
    for match in _BLOCK.finditer(text):
        before = _OPEN_FENCE.sub('', text[pos:match.start()])
        if before.strip():
            segments.append(('text', before))
        segments.append(('change', _trim(match.group(1)), _trim(match.group(2))))
        pos = match.end()
        closing = _CLOSE_FENCE.match(text, pos)
        if closing:
            pos = closing.end()
    rest = text[pos:]
    if not final:
        start = rest.find('<<<<<<<')
        if start >= 0:
            head = _OPEN_FENCE.sub('', rest[:start])
            if head.strip():
                segments.append(('text', head))
            segments.append(('pending',))
            return segments
    if rest.strip():
        segments.append(('text', rest))
    return segments


def _indent(line):
    return line[:len(line) - len(line.lstrip())]


def locate(text, search):
    """Where `search` sits in `text`.

    Returns (start, end, reindent) on success, where reindent is a prefix to add to
    replacement lines when the model dropped the file's indentation, or an error string.
    """
    if search.strip() == '':
        if text.strip() == '':
            return (0, len(text), '')
        return 'The change does not say which lines to replace.'
    count = text.count(search)
    if count == 1:
        start = text.index(search)
        return (start, start + len(search), '')
    if count > 1:
        return 'Those lines appear more than once in the file, so the right place is unclear.'

    lines = text.split('\n')
    wanted = search.split('\n')
    while wanted and not wanted[0].strip():
        wanted.pop(0)
    while wanted and not wanted[-1].strip():
        wanted.pop()
    n = len(wanted)
    for norm in (str.rstrip, str.strip):
        target = [norm(l) for l in wanted]
        hits = [i for i in range(len(lines) - n + 1)
                if [norm(l) for l in lines[i:i + n]] == target]
        if len(hits) > 1:
            return 'Those lines appear more than once in the file, so the right place is unclear.'
        if len(hits) == 1:
            i = hits[0]
            start = sum(len(l) + 1 for l in lines[:i])
            end = start + len('\n'.join(lines[i:i + n]))
            reindent = ''
            if norm is str.strip:
                first_file = next((l for l in lines[i:i + n] if l.strip()), '')
                first_search = next((l for l in wanted if l.strip()), '')
                file_ind, search_ind = _indent(first_file), _indent(first_search)
                if len(file_ind) > len(search_ind) and file_ind.endswith(search_ind):
                    reindent = file_ind[:len(file_ind) - len(search_ind)]
            return (start, end, reindent)
    return "Those lines aren't in the file any more."


def reindent(replace, prefix):
    if not prefix:
        return replace
    return '\n'.join(prefix + l if l.strip() else l for l in replace.split('\n'))


def diff(search, replace):
    """A unified diff without file headers, for showing on a change card."""
    old = search.split('\n') if search else []
    new = replace.split('\n') if replace else []
    out = list(difflib.unified_diff(old, new, lineterm='', n=2))[2:]
    return '\n'.join(l for l in out if not l.startswith('@@'))
