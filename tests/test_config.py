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


def test_malformed_dictionary_and_snippet_entries_are_dropped():
    settings = Settings.from_dict(
        {
            "model": "medium",
            "written_by_a_future_version": "ignored",
            "dictionary": [
                {"spoken": "tux flow", "written": "TuxFlow"},
                {"spoken": "half an entry"},
                {"written": "the other half"},
                {"spoken": 5, "written": 7},
                "not a mapping at all",
                None,
            ],
            "snippets": [
                {"trigger": "my sign off", "expansion": "Best,\nRobert"},
                {"expansion": "orphan"},
                [],
            ],
        }
    )

    assert settings.model == "medium"
    # Everything survives as text, whatever the file said it was.
    assert settings.dictionary == [Replacement("tux flow", "TuxFlow"), Replacement("5", "7")]
    assert settings.snippets == [Snippet("my sign off", "Best,\nRobert")]


def test_a_config_file_that_is_not_an_object_uses_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('["not", "a", "settings", "object"]', encoding="utf-8")

    assert ConfigStore(path).load() == Settings()
