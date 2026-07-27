"""Tests for the Whisper wrapper.

faster-whisper is a heavy optional extra that CI deliberately does not install,
so a stand-in module is put into ``sys.modules`` instead. Everything here runs
the same way whether or not the real speech engine is present.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import ClassVar

import pytest

from tuxflow.engine import EngineUnavailableError, WhisperEngine


class FakeSegment:
    """One segment of a faster-whisper result, whitespace and all."""

    def __init__(self, text: str) -> None:
        self.text = text


class FakeInfo:
    def __init__(self, language: str = "en", language_probability: float = 0.97) -> None:
        self.language = language
        self.language_probability = language_probability


class FakeWhisperModel:
    """Stand-in for ``faster_whisper.WhisperModel``.

    Instances record how they were built and how they were called so the tests
    can assert on the options TuxFlow chose.
    """

    built: ClassVar[list[dict[str, object]]] = []
    calls: ClassVar[list[dict[str, object]]] = []
    segments: ClassVar[list[FakeSegment]] = []
    info: ClassVar[FakeInfo] = FakeInfo()
    failure: ClassVar[Exception | None] = None

    def __init__(self, model_name: str, **options: object) -> None:
        type(self).built.append({"model_name": model_name, **options})

    def transcribe(self, audio_path: str, **options: object):
        type(self).calls.append({"audio_path": audio_path, **options})
        if type(self).failure is not None:
            raise type(self).failure
        return iter(type(self).segments), type(self).info


@pytest.fixture
def speech_engine_installed(monkeypatch):
    """Pretend faster-whisper is installed, with a model that answers on demand."""
    FakeWhisperModel.built = []
    FakeWhisperModel.calls = []
    FakeWhisperModel.segments = [FakeSegment("  Hello there. "), FakeSegment(" Second line ")]
    FakeWhisperModel.info = FakeInfo()
    FakeWhisperModel.failure = None
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return FakeWhisperModel


@pytest.fixture
def speech_engine_missing(monkeypatch):
    """Pretend faster-whisper is not installed, even when it is."""
    # A None entry is what the import system itself leaves behind for a module
    # that cannot be loaded, and `import faster_whisper` raises ImportError on it.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)


def _engine(tmp_path: Path, **overrides) -> WhisperEngine:
    options = {
        "model_name": "tiny",
        "device": "cpu",
        "compute_type": "int8",
        "download_root": tmp_path / "models",
    }
    options.update(overrides)
    return WhisperEngine(**options)


def test_a_missing_speech_extra_points_at_the_installer(tmp_path, speech_engine_missing):
    with pytest.raises(EngineUnavailableError, match="install.sh"):
        _engine(tmp_path).load()


def test_transcribing_without_the_speech_extra_fails_the_same_way(tmp_path, speech_engine_missing):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")

    with pytest.raises(EngineUnavailableError, match="install.sh"):
        _engine(tmp_path).transcribe(audio)


def test_the_model_is_built_once_and_reused(tmp_path, speech_engine_installed):
    engine = _engine(tmp_path)
    engine.load()
    engine.load()

    assert speech_engine_installed.built == [
        {
            "model_name": "tiny",
            "device": "cpu",
            "compute_type": "int8",
            "download_root": str(tmp_path / "models"),
        }
    ]
    # The download directory is TuxFlow's to create; faster-whisper only writes into it.
    assert (tmp_path / "models").is_dir()


def test_a_model_that_cannot_be_built_is_reported_with_its_reason(tmp_path, monkeypatch):
    class BrokenModel:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("no CUDA device")

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = BrokenModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    with pytest.raises(EngineUnavailableError) as caught:
        _engine(tmp_path, model_name="large-v3").load()

    assert "large-v3" in str(caught.value)
    assert "no CUDA device" in str(caught.value)


def test_segments_are_joined_and_trimmed_into_one_transcript(tmp_path, speech_engine_installed):
    speech_engine_installed.info = FakeInfo("de", 0.42)
    audio = tmp_path / "clip.wav"

    transcript = _engine(tmp_path).transcribe(audio)

    assert transcript.text == "Hello there. Second line"
    assert transcript.language == "de"
    assert transcript.language_probability == pytest.approx(0.42)


def test_an_automatic_language_is_left_for_whisper_to_detect(tmp_path, speech_engine_installed):
    engine = _engine(tmp_path)
    audio = tmp_path / "clip.wav"

    engine.transcribe(audio, "auto")
    engine.transcribe(audio, "fr")

    languages = [call["language"] for call in speech_engine_installed.calls]
    assert languages == [None, "fr"]
    # Silence detection matters more than any other option here: without it a
    # long pause is transcribed as invented words.
    assert speech_engine_installed.calls[0]["vad_filter"] is True
    assert speech_engine_installed.calls[0]["audio_path"] == str(audio)


def test_a_failure_inside_the_model_is_reported_as_a_transcription_failure(
    tmp_path, speech_engine_installed
):
    speech_engine_installed.failure = RuntimeError("the audio file is truncated")

    with pytest.raises(EngineUnavailableError, match="Transcription failed: the audio"):
        _engine(tmp_path).transcribe(tmp_path / "clip.wav")
