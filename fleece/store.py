"""Chats and settings, kept as plain JSON files in the user's folders."""
import json
import os
import time
import uuid

DATA = os.path.join(os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share')), 'fleece')
CONFIG = os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')), 'fleece')
CHATS = os.path.join(DATA, 'chats')

DEFAULTS = {
    'host': 'http://127.0.0.1:11434',
    'model': '',
    'system_prompt': '',
    'temperature': 0.7,
    'num_ctx': 8192,
    'show_thinking': True,
    'mode': 'chat',
    'code_model': '',
    'last_project': '',
    'recent_projects': [],
    'projects': {},
    'code_tip_shown': [],
}


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Config:
    def __init__(self):
        self.path = os.path.join(CONFIG, 'config.json')
        self.data = dict(DEFAULTS)
        try:
            with open(self.path, encoding='utf-8') as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value
        _write(self.path, self.data)


def new_chat(model=''):
    now = time.time()
    return {'id': uuid.uuid4().hex, 'title': 'New chat', 'model': model,
            'created': now, 'updated': now, 'messages': []}


class ChatStore:
    def list(self):
        chats = []
        try:
            names = os.listdir(CHATS)
        except OSError:
            return chats
        for name in names:
            if not name.endswith('.json'):
                continue
            try:
                with open(os.path.join(CHATS, name), encoding='utf-8') as f:
                    chat = json.load(f)
                chats.append({k: chat[k] for k in ('id', 'title', 'model', 'updated')})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(chats, key=lambda c: c['updated'], reverse=True)

    def load(self, chat_id):
        with open(os.path.join(CHATS, chat_id + '.json'), encoding='utf-8') as f:
            return json.load(f)

    def save(self, chat):
        chat['updated'] = time.time()
        _write(os.path.join(CHATS, chat['id'] + '.json'), chat)

    def delete(self, chat_id):
        try:
            os.remove(os.path.join(CHATS, chat_id + '.json'))
        except OSError:
            pass
