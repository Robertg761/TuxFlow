# TuxFlow

TuxFlow is a free, open-source voice-dictation app for **Linux and macOS**. Hold
a global shortcut, speak naturally, then release it, and TuxFlow transcribes with
a local [OpenAI Whisper](https://github.com/openai/whisper) model before pasting
into the app you were already using.

No account. No subscription. No paid API key. No uploaded recordings.

> TuxFlow is an independent project and is not affiliated with Wispr Flow or
> OpenAI.

## What works

- Hold-to-dictate global shortcut: the XDG Desktop Portal on Linux, a Quartz
  event tap (hold 🌐 fn by default) on macOS
- Microphone recording through PipeWire, ALSA, or FFmpeg on Linux, and
  AVFoundation or SoX on macOS
- Local transcription with `tiny`, `base`, `small`, `medium`, `large-v3`, or
  `turbo` Whisper models
- Automatic language detection or a pinned language
- Clipboard and automatic paste on Wayland, X11, and macOS
- Local transcription history with one-click copy
- Personal dictionary replacements
- Voice snippets for signatures, links, addresses, and canned replies
- Optional filler-word cleanup and spoken punctuation
- Optional “press enter” voice command
- A native GTK 4 / Libadwaita control center on both platforms
- A CLI for scripts, mouse bindings, and custom desktop shortcuts

TuxFlow uses
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), an MIT-licensed
CTranslate2 implementation of Whisper. It downloads compatible converted model
weights on first use and then runs inference locally. OpenAI releases the
original Whisper code and model weights under the MIT license.

## Install

One command on either platform, from a clone of this repository:

```bash
./scripts/install.sh
```

The installer never touches system Python. It:

1. installs the desktop packages with `dnf`/`apt`/`pacman`/`zypper` on Linux, or
   Homebrew on macOS;
2. creates an isolated Python environment under `~/.local/share/tuxflow/venv`;
3. installs TuxFlow and faster-whisper into it;
4. links `~/.local/bin/tuxflow`;
5. adds the launcher entry — a `.desktop` file on Linux,
   `~/Applications/TuxFlow.app` on macOS;
6. starts TuxFlow at login with a systemd user service or a launchd agent; and
7. finishes by running `tuxflow doctor`.

Useful flags:

```bash
./scripts/install.sh --skip-system-packages   # dependencies already installed
./scripts/install.sh --no-service             # do not start TuxFlow at login
./scripts/install.sh --with-uinput            # Linux: enable automatic paste via ydotool
```

Requirements: Python 3.11 or newer, and a desktop session. Set `TUXFLOW_PYTHON`
to pin a specific interpreter. Remove everything again with
`./scripts/uninstall.sh` (add `--purge` to delete settings, models, and history).

### Linux

Fedora, Ubuntu/Debian, Arch, and openSUSE are handled directly; other
distributions work with `--skip-system-packages` once GTK 4, Libadwaita,
PyGObject, a recorder (`pw-record`, `arecord`, or `ffmpeg`), and `wl-clipboard`
are installed.

The first time the background service starts, your desktop asks you to approve
the global shortcut. The suggested trigger is `Ctrl` + `Super` + `Space`.

### macOS

Homebrew is required; the installer uses it for `ffmpeg` and for the GTK stack.
macOS 12 or newer, on Apple silicon or Intel.

Two permissions have to be granted, and macOS only prompts for them in a
foreground process — so run the daemon once from Terminal after installing:

```bash
tuxflow daemon
```

- **Microphone** — prompted the first time you dictate.
- **Accessibility** — needed to watch the hotkey and to send ⌘V. Approve the app
  that is asking (Terminal, iTerm, or TuxFlow) in System Settings › Privacy &
  Security › Accessibility, then restart the service.

Finally, set System Settings › Keyboard › “Press 🌐 key to” to **Do Nothing**.
The event tap is listen-only, so it never swallows the key — without this
setting, holding fn also opens the emoji picker. Prefer a different key? Pick
`right_command`, `right_option`, or `right_control` under Settings in the
control center.

## Use

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
tuxflow doctor                 # check this machine's integration
```

You can bind `tuxflow toggle` to a mouse button or a desktop shortcut if the
built-in hotkey is unavailable.

## Privacy and storage

TuxFlow has no telemetry or account system. After the selected model has been
downloaded, normal dictation requires no network connection.

| Data | Default location | Default behavior |
| --- | --- | --- |
| Settings | `~/.config/tuxflow/config.json` | Kept locally |
| History | `~/.local/share/tuxflow/history.sqlite3` | Kept locally |
| Models | `~/.local/share/tuxflow/models/` | Kept locally |
| Temporary audio | `~/.cache/tuxflow/recordings/` | Deleted after transcription |

The same XDG paths are used on macOS, so everything TuxFlow stores stays in
those three directories on both platforms. Use the Privacy page to clear history
or opt into retaining audio.

## How it integrates with each platform

TuxFlow deliberately uses standard components instead of bundling its own.

| | Linux | macOS |
| --- | --- | --- |
| Hotkey | XDG GlobalShortcuts portal | Quartz `CGEventTap` (listen-only) |
| Recording | `pw-record`, `arecord`, `ffmpeg` | `ffmpeg` (AVFoundation), `sox` |
| Clipboard | `wl-copy`, `xclip`, `xsel` | `pbcopy` |
| Paste | `ydotool`, `wtype`, `xdotool` | System Events (`osascript`) |
| Status | Tray icon (StatusNotifierItem) | Notifications |
| Autostart | systemd user service | launchd agent |

Every one of these is optional at import time, so the daemon starts even when a
piece is missing and reports the gap instead of crashing.

## Troubleshooting

Start with `tuxflow doctor`; it names the platform and checks each integration
point individually.

- **"Copied, but automatic paste is unavailable" (Linux)** — a locked-down
  Wayland session will not let any app synthesise keystrokes. Run
  `./scripts/install.sh --with-uinput` and log out and back in, or press
  `Ctrl+V` yourself.
- **"Copied, but automatic paste is unavailable" (macOS)** — Accessibility has
  not been granted to the process running the daemon.
- **The hotkey does nothing on macOS** — run `tuxflow daemon` in Terminal and
  watch for the Accessibility prompt; a launchd agent cannot show one.
- **No microphone recorder found** — install `pipewire-utils`/`pipewire-bin` on
  Linux, or `brew install ffmpeg` on macOS.
- **A specific microphone is needed** — set it under Settings in the control
  center; it maps to a `pw-record --target`, `arecord -D`, FFmpeg AVFoundation
  index, or sox `AUDIODEV`.
- **`gi` module not found on macOS** — the environment must be built from the
  same Homebrew Python that `pygobject3` was built for. The installer does this
  automatically; if you built the venv by hand, delete it and rerun the script.

## Development

```bash
make dev     # create or repair .venv (also fixes one moved to a new path)
make test
make lint
```

Or run the processes directly:

```bash
.venv/bin/tuxflow daemon
.venv/bin/tuxflow app
```

The core modules do not import GTK, dbus-next, or PyObjC, which keeps text
processing, history, and configuration easy to test headlessly. Set
`TUXFLOW_PLATFORM=macos` or `TUXFLOW_PLATFORM=linux` to exercise the other
platform's code paths from either machine.

## Roadmap

- Flatpak packaging and portal-native clipboard insertion
- A macOS menu-bar status item to match the Linux tray
- Optional local-LLM transforms through Ollama
- Context-aware capitalization without sending text off-device
- Packaging for distro repositories and a Homebrew tap

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

TuxFlow is available under the [MIT License](LICENSE).
