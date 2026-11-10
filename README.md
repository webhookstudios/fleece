# Fleece

A chat window for your local [Ollama](https://ollama.com) models. Pick a model, type, and get streamed answers in a clean desktop app, with no terminal needed. Built with GTK 4 and libadwaita, so it fits Ubuntu.

![The Fleece start screen](docs/screenshots/welcome.png)

![A conversation with a code block and highlighting](docs/screenshots/chat.png)

## What it does

- **Chats like a modern assistant:** streamed answers, formatted text, lists, tables and highlighted code blocks with a copy button.
- **Switch models in one click** from the box where you type.
- **Download models without the terminal:** a recommended list with progress bars, or any name from the Ollama library.
- **Thinking models:** shows the reasoning above the answer, collapsed once the answer starts.
- **Keeps your chats** in a sidebar, grouped by day, searchable. They are plain files on your computer.
- **Settings:** creativity, memory length, instructions for every chat, and the Ollama address.
- Stop, copy and try-again buttons, and keyboard shortcuts: <kbd>Enter</kbd> sends, <kbd>Shift</kbd>+<kbd>Enter</kbd> adds a line, <kbd>Ctrl</kbd>+<kbd>N</kbd> starts a new chat.

![Downloading models](docs/screenshots/models.png)

![Settings](docs/screenshots/settings.png)

## Requirements

- Ubuntu 24.04 or newer, or another GNOME desktop with libadwaita 1.5+
- [Ollama](https://ollama.com/download), installed and running. Fleece does not include it. If it is not found, Fleece tells you.

You do not need any models yet: open **Get models** and download one from inside Fleece.

## Install

### With the deb package (recommended)

Download `fleece_1.0.0_all.deb` from the [latest release](https://github.com/webhookstudios/fleece/releases/latest), then:

```bash
sudo apt install ./fleece_1.0.0_all.deb
```

Open **Fleece** from the apps menu. To remove it:

```bash
sudo apt remove fleece
```

### From source

```bash
git clone https://github.com/webhookstudios/fleece.git
```

```bash
sudo apt install python3-gi gir1.2-adw-1 python3-markdown-it python3-pygments
```

```bash
./fleece/install.sh
```

## Using it

1. Open Fleece. The model you last used is selected in the box where you type.
2. No models yet? Click **Get a model** in the banner, or open the menu → **Get models…**, and download one. Small ones such as `llama3.2:3b` need about 4 GB of RAM.
3. Type a message and press <kbd>Enter</kbd>.

From a terminal, `fleece --send "your question"` opens a new chat and sends it, and `fleece --models` opens the download window.

## Where things are kept

| What | Where |
|---|---|
| Chats | `~/.local/share/fleece/chats/` |
| Settings | `~/.config/fleece/config.json` |
| Models | Wherever Ollama keeps them. Remove them in Fleece or with `ollama rm` |

Fleece only talks to the Ollama address in Settings, `http://127.0.0.1:11434` by default. Nothing is sent anywhere else.

## Development

```bash
./bin/fleece
```

Build the deb with `./build-deb.sh` (needs only `dpkg-deb`).

| File | Purpose |
|---|---|
| `fleece/window.py` | The main window, sidebar, composer and chat flow |
| `fleece/render.py` | Markdown to GTK widgets, with code highlighting |
| `fleece/models.py` | The model download window |
| `fleece/ollama.py` | A small Ollama API client |
| `fleece/store.py` | Saved chats and settings |
| `fleece/prefs.py` | The settings window |

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).
