from __future__ import annotations

import json

from tuxflow.config import ConfigStore, Replacement, Settings, Snippet


def test_round_trip(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    settings = Settings(
        model="base",
        language="en",
        dictionary=[Replacement("tux flow", "TuxFlow")],
        snippets=[Snippet("my sign off", "Best,\nRobert")],
    )
    store.save(settings)

    loaded = store.load()
    assert loaded.model == "base"
    assert loaded.language == "en"
    assert loaded.dictionary == [Replacement("tux flow", "TuxFlow")]
    assert loaded.snippets == [Snippet("my sign off", "Best,\nRobert")]
    assert json.loads(path.read_text())["model"] == "base"


def test_invalid_config_uses_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{broken", encoding="utf-8")
    assert ConfigStore(path).load() == Settings()
