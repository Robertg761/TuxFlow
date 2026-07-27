import pytest

from tuxflow.engine import EngineUnavailableError, WhisperEngine


def test_engine_wraps_model_load_failure(monkeypatch, tmp_path):
    class BrokenModel:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    faster_whisper = pytest.importorskip(
        "faster_whisper", reason="the speech engine extra is not installed"
    )

    monkeypatch.setattr(faster_whisper, "WhisperModel", BrokenModel)
    engine = WhisperEngine(
        model_name="tiny",
        device="cpu",
        compute_type="int8",
        download_root=tmp_path,
    )

    with pytest.raises(EngineUnavailableError, match="Could not load"):
        engine.load()
