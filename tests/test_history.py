from tuxflow.history import HistoryStore


def test_history_is_newest_first_and_clearable(tmp_path):
    history = HistoryStore(tmp_path / "history.sqlite3")
    history.add(
        text="First",
        raw_text="first",
        language="en",
        duration_seconds=1.0,
        model="tiny",
    )
    history.add(
        text="Second",
        raw_text="second",
        language="en",
        duration_seconds=2.0,
        model="base",
    )

    assert [item.text for item in history.recent()] == ["Second", "First"]
    history.clear()
    assert history.recent() == []
