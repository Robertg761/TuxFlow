# TuxFlow

TuxFlow is a free, open-source voice-dictation app built for Linux. Hold a
global shortcut, speak naturally, then release it, and TuxFlow transcribes with a local
[OpenAI Whisper](https://github.com/openai/whisper) model before pasting into
the app you were already using.

No account. No subscription. No paid API key. No uploaded recordings.

> TuxFlow is an independent project and is not affiliated with Wispr Flow or
> OpenAI.

## What works

- Global dictation shortcut through the XDG Desktop Portal on KDE and GNOME
- PipeWire microphone recording
- Local transcription with `tiny`, `base`, `small`, `medium`, `large-v3`, or
  `turbo` Whisper models
- Automatic language detection or a pinned language
- Wayland and X11 clipboard support with automatic paste when available
- Local transcription history with one-click copy
- Personal dictionary replacements
- Voice snippets for signatures, links, addresses, and canned replies
- Optional filler-word cleanup and spoken punctuation
- Optional “press enter” voice command
- A native GTK 4 / Libadwaita control center
- A CLI for scripts, mouse bindings, and custom desktop shortcuts

TuxFlow uses
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), an MIT-licensed
CTranslate2 implementation of Whisper. It downloads compatible converted model
weights on first use and then runs inference locally. OpenAI releases the
original Whisper code and model weights under the MIT license.

## Install

TuxFlow targets recent Fedora, Ubuntu, Arch, and related desktop distributions.
Run:

```bash
chmod +x scripts/install.sh scripts/uninstall.sh
./scripts/install.sh
```

The installer:

1. installs GTK, PipeWire, clipboard, and input-injection packages;
2. creates an isolated Python environment under
   `~/.local/share/tuxflow/venv`;
3. installs TuxFlow and faster-whisper;
4. enables the `tuxflow.service` user service; and
5. installs the application-launcher entry and icon.

If the system packages are already installed:

```bash
./scripts/install.sh --skip-system-packages
```

Open **TuxFlow** from the application launcher. Your desktop will ask you to
approve a global shortcut the first time the background service starts.

## Use

The default suggested shortcut is `Ctrl` + `Super` + `Space`:

1. Put the cursor in any text field.
2. Hold the shortcut and speak.
3. Release the shortcut.
4. TuxFlow transcribes locally, cleans the text, and pastes it.

The first dictation downloads the selected model. The default `small` model is
a practical CPU balance and is roughly a 500 MB download. Choose `base` on
slower machines, or `turbo` when you have capable hardware and want higher
accuracy.

Useful commands:

```bash
tuxflow app                    # open the control center
tuxflow toggle                 # start or stop dictation
tuxflow cancel                 # discard the current recording
tuxflow status                 # inspect service state
tuxflow transcribe audio.wav   # transcribe an existing file
tuxflow doctor                 # check Linux integration
```

You can bind `tuxflow toggle` to a mouse button or a desktop shortcut if your
desktop does not implement the GlobalShortcuts portal.

## Privacy and storage

TuxFlow has no telemetry or account system. After the selected model has been
downloaded, normal dictation requires no network connection.

| Data | Default location | Default behavior |
| --- | --- | --- |
| Settings | `~/.config/tuxflow/config.json` | Kept locally |
| History | `~/.local/share/tuxflow/history.sqlite3` | Kept locally |
| Models | `~/.local/share/tuxflow/models/` | Kept locally |
| Temporary audio | `~/.cache/tuxflow/recordings/` | Deleted after transcription |

Use the Privacy page to clear history or opt into retaining audio.

## Linux integration

TuxFlow intentionally uses standard desktop components:

- [XDG GlobalShortcuts](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html)
  for desktop-approved shortcuts;
- `pw-record` for PipeWire audio capture;
- `wl-copy` on Wayland, with `xclip`/`xsel` fallbacks on X11; and
- `ydotool`, `wtype`, or `xdotool` to send the paste keystroke.

On a locked-down Wayland session, TuxFlow may only copy the transcript. Press
`Ctrl+V` manually, or configure `ydotool`/`wtype` for automatic paste. Run
`tuxflow doctor` for an exact status report.

## Development

```bash
make dev
make test
make lint
```

Or run the processes directly:

```bash
.venv/bin/tuxflow daemon
.venv/bin/tuxflow app
```

The core modules do not import GTK, which keeps text processing, history, and
configuration easy to test headlessly.

## Roadmap

- Flatpak packaging and portal-native clipboard insertion
- Optional local-LLM transforms through Ollama
- Context-aware capitalization without sending text off-device
- Microphone and audio-level selection
- Packaging for distro repositories

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

TuxFlow is available under the [MIT License](LICENSE).
