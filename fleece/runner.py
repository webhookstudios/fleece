"""What the Run button does for a file, and the live preview server for web pages."""
import functools
import http.server
import json
import os
import shlex
import shutil
import threading

WEB = ('.html', '.htm', '.css', '.js', '.mjs', '.svg')

_RELOAD = b"""<script>/* Fleece live preview */(function(){var v=null;setInterval(function(){
fetch('/__fleece/version',{cache:'no-store'}).then(function(r){return r.text()}).then(function(t){
if(v!==null&&t!==v){location.reload()}v=t}).catch(function(){})},600)})();</script>"""


class Action:
    def __init__(self, label, kind, command='', cwd='', page=''):
        self.label = label  # shown on the Run menu
        self.kind = kind  # 'terminal' or 'preview'
        self.command = command
        self.cwd = cwd
        self.page = page  # for previews: the page path relative to the project


def _q(path):
    return shlex.quote(path)


def _npm_scripts(folder):
    try:
        with open(os.path.join(folder, 'package.json'), encoding='utf-8') as f:
            return list((json.load(f).get('scripts') or {}).keys())
    except (OSError, ValueError, AttributeError):
        return []


def _find_up(start, root, name):
    """The nearest folder from `start` up to `root` that contains `name`."""
    root = os.path.abspath(root)
    folder = os.path.abspath(start)
    while folder == root or folder.startswith(root + os.sep):
        if os.path.exists(os.path.join(folder, name)):
            return folder
        folder = os.path.dirname(folder)
    return None


def detect(project, path):
    """The ways to run `path` (or the project when no file is open). The first is the default."""
    actions = []
    folder = os.path.dirname(path) if path else project
    ext = os.path.splitext(path)[1].lower() if path else ''
    name = os.path.basename(path) if path else ''

    if ext in WEB:
        page = path if ext in ('.html', '.htm') else None
        if page is None:
            for candidate in (os.path.join(folder, 'index.html'), os.path.join(project, 'index.html')):
                if os.path.exists(candidate):
                    page = candidate
                    break
        if page:
            actions.append(Action('Live preview', 'preview', page=os.path.relpath(page, project)))
    if ext == '.py':
        actions.append(Action(f'python3 {name}', 'terminal', f'python3 -u {_q(name)}', folder))
    if ext in ('.js', '.mjs', '.cjs') and shutil.which('node'):
        actions.append(Action(f'node {name}', 'terminal', f'node {_q(name)}', folder))
    if ext == '.ts' and shutil.which('npx'):
        actions.append(Action(f'npx tsx {name}', 'terminal', f'npx tsx {_q(name)}', folder))
    if ext == '.sh':
        actions.append(Action(f'bash {name}', 'terminal', f'bash {_q(name)}', folder))
    if ext == '.rb':
        actions.append(Action(f'ruby {name}', 'terminal', f'ruby {_q(name)}', folder))
    if ext == '.php':
        actions.append(Action(f'php {name}', 'terminal', f'php {_q(name)}', folder))
    if ext == '.go':
        actions.append(Action(f'go run {name}', 'terminal', f'go run {_q(name)}', folder))
    if ext in ('.c', '.cpp', '.cc'):
        compiler = 'gcc' if ext == '.c' else 'g++'
        out = os.path.splitext(name)[0]
        actions.append(Action(f'Compile and run {name}', 'terminal',
                              f'{compiler} {_q(name)} -o {_q(out)} && ./{_q(out)}', folder))

    start = folder if path else project
    node = _find_up(start, project, 'package.json')
    if node:
        scripts = _npm_scripts(node)
        for script in sorted(scripts, key=lambda s: (s not in ('dev', 'start', 'test'), s)):
            actions.append(Action(f'npm run {script}', 'terminal', f'npm run {_q(script)}', node))
    cargo = _find_up(start, project, 'Cargo.toml')
    if cargo:
        actions.append(Action('cargo run', 'terminal', 'cargo run', cargo))
        actions.append(Action('cargo test', 'terminal', 'cargo test', cargo))
    make = _find_up(start, project, 'Makefile')
    if make:
        actions.append(Action('make', 'terminal', 'make', make))
    if ext not in WEB and os.path.exists(os.path.join(project, 'index.html')) and not actions:
        actions.append(Action('Live preview', 'preview', page='index.html'))
    if ext == '.py' and (_find_up(start, project, 'pytest.ini') or _find_up(start, project, 'conftest.py')):
        actions.append(Action('pytest', 'terminal', 'python3 -m pytest', project))
    return actions


class _Handler(http.server.SimpleHTTPRequestHandler):
    server_version = 'FleecePreview'

    def __init__(self, *args, owner=None, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)

    def log_message(self, *_args):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def do_GET(self):
        if self.path.startswith('/__fleece/version'):
            body = str(self.owner.version).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            index = os.path.join(path, 'index.html')
            if os.path.exists(index):
                path = index
        if path.lower().endswith(('.html', '.htm')) and os.path.isfile(path):
            try:
                with open(path, 'rb') as f:
                    body = f.read()
            except OSError:
                self.send_error(404)
                return
            low = body.lower()
            at = low.rfind(b'</body>')
            body = body[:at] + _RELOAD + body[at:] if at >= 0 else body + _RELOAD
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


class PreviewServer:
    """Serves the project on 127.0.0.1 and tells open pages to reload after a save."""

    def __init__(self, root):
        self.root = root
        self.version = 0
        handler = functools.partial(_Handler, directory=root, owner=self)
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f'http://127.0.0.1:{self.httpd.server_address[1]}/'

    def url(self, page):
        return self.base + '/'.join(page.split(os.sep))

    def bump(self):
        self.version += 1

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
