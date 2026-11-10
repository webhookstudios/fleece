"""A small client for the Ollama HTTP API. Standard library only."""
import json
import urllib.error
import urllib.request

DEFAULT_HOST = 'http://127.0.0.1:11434'


class OllamaError(Exception):
    pass


class Stream:
    """Newline-delimited JSON from Ollama. close() may be called from another thread."""

    def __init__(self, response):
        self._response = response
        self.closed = False

    def __iter__(self):
        try:
            for line in self._response:
                if self.closed:
                    return
                line = line.strip()
                if not line:
                    continue
                chunk = json.loads(line)
                if 'error' in chunk:
                    raise OllamaError(chunk['error'])
                yield chunk
        except (OSError, ValueError):
            if not self.closed:
                raise OllamaError('The connection to Ollama was lost.') from None
        finally:
            self.close()

    def close(self):
        self.closed = True
        try:
            self._response.close()
        except Exception:
            pass


class Client:
    def __init__(self, host=DEFAULT_HOST):
        host = (host or DEFAULT_HOST).strip()
        if '://' not in host:
            host = 'http://' + host
        self.host = host.rstrip('/')

    def _open(self, path, payload=None, method=None, timeout=30):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self.host + path, data=data, method=method or ('POST' if data is not None else 'GET'),
            headers={'Content-Type': 'application/json'})
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read()).get('error')
            except Exception:
                message = None
            raise OllamaError(message or f'Ollama answered with an error ({error.code}).') from None
        except (urllib.error.URLError, OSError, ValueError):
            raise OllamaError(f"Can't reach Ollama at {self.host}.") from None

    def _json(self, path, payload=None, method=None, timeout=30):
        with self._open(path, payload, method, timeout) as response:
            body = response.read()
        return json.loads(body) if body else {}

    def version(self):
        return self._json('/api/version', timeout=5).get('version', '')

    def models(self):
        models = self._json('/api/tags', timeout=10).get('models', [])
        return sorted(models, key=lambda m: m['name'].lower())

    def capabilities(self, model):
        try:
            return self._json('/api/show', {'model': model}, timeout=15).get('capabilities') or []
        except OllamaError:
            return []

    def chat(self, payload):
        # The first reply can take a while: the model may still have to load.
        return Stream(self._open('/api/chat', payload, timeout=600))

    def pull(self, name):
        return Stream(self._open('/api/pull', {'model': name, 'stream': True}, timeout=600))

    def delete(self, name):
        self._json('/api/delete', {'model': name}, method='DELETE')
