# Contributing

TuxFlow is deliberately local-first. New features must work without a TuxFlow
account and must not upload microphone audio, transcript text, history, or
dictionary data by default.

## Getting set up

```bash
./scripts/dev-env.sh   # or: make dev
make test
make lint
```

`scripts/dev-env.sh` creates `.venv`, installs TuxFlow in editable mode with the
`speech` and `dev` extras, and rebuilds the environment if it no longer matches
the checkout — which is what happens when the project directory is moved. Pass
`--minimal` to skip faster-whisper, or `--recreate` to start clean. Set
`TUXFLOW_PYTHON` to choose the interpreter.

Run the service in one terminal with `.venv/bin/tuxflow daemon`, then open the
control center with `.venv/bin/tuxflow app`. Use `.venv/bin/tuxflow doctor` when
desktop integration is not behaving as expected.

## Supported platforms

Linux and macOS are both first-class. Anything platform-specific goes behind a
small adapter so the other platform keeps working:

| Concern | Module | Backends |
| --- | --- | --- |
| OS detection | `tuxflow/system.py` | — |
| Recording | `tuxflow/audio.py` | `pw-record`, `arecord`, `ffmpeg`, `sox` |
| Clipboard and paste | `tuxflow/insertion.py` | `_linux_*` and `_macos_*` helpers |
| Global hotkey | `tuxflow/shortcuts.py` | `portal.py`, `mac_hotkey.py` |
| Tray | `tuxflow/tray.py` | `tray_sni.py` (Linux only) |

Three rules keep this workable:

1. **Ask `tuxflow.system`, never `sys.platform`.** `current_os()` honours the
   `TUXFLOW_PLATFORM` environment variable, which is how the tests exercise the
   platform you are not on.
2. **Import platform bindings lazily.** `dbus-next` and PyObjC only exist on one
   platform each, so they are imported inside the function that needs them.
   Importing `tuxflow.daemon` must work on both.
3. **Degrade, do not crash.** A missing recorder, denied permission, or absent
   paste tool should surface as a message the user can act on — ideally as a
   check in `tuxflow/doctor.py`.

## Tests

```bash
make test
```

Pull requests should include tests for text processing and state changes.
Platform-specific code is tested everywhere by faking the platform:

```python
def test_something(monkeypatch):
    monkeypatch.setenv("TUXFLOW_PLATFORM", "macos")
```

`tests/test_mac_hotkey.py` shows the pattern for code that talks to a native
framework: drive it through a stand-in object rather than skipping the test.
Reserve `pytest.importorskip` for tests that genuinely need a platform-only
dependency to be installed.

`make lint` runs `ruff check`, `ruff format --check`, and ShellCheck over the
install scripts — the same checks CI runs on both Ubuntu and macOS, so a change
that only builds on one of them will fail there.

## Style

- `ruff format` and `ruff check` are the source of truth; the line length is 100.
- Comments explain why something is done, not what the line does. The ones worth
  writing are about platform behavior that is not obvious from the code —
  ydotool's device settle delay, or macOS disabling a slow event tap.
- User-facing strings should say what to do next, not just what failed.
