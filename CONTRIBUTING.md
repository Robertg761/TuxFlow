# Contributing

TuxFlow is deliberately local-first. New features must work without a TuxFlow
account and must not upload microphone audio, transcript text, history, or
dictionary data by default.

## Development

```bash
make dev
make test
make lint
```

Run the service in one terminal with `.venv/bin/tuxflow daemon`, then open the
control center with `.venv/bin/tuxflow app`. Use `.venv/bin/tuxflow doctor` when
desktop integration is not behaving as expected.

Pull requests should include tests for text processing and state changes. Keep
desktop-specific behavior behind small adapters so KDE, GNOME, and X11
fallbacks can evolve independently.
