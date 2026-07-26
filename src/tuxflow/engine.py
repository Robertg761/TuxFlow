"""Lazy local Whisper inference."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class EngineUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    language: str
    language_probability: float


class WhisperEngine:
    def __init__(
        self,
        *,
        model_name: str,
        device: str,
        compute_type: str,
        download_root: Path,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.download_root = download_root
        self._model: Any = None

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise EngineUnavailableError(
                "The speech engine is not installed. Run: ./scripts/install.sh"
            ) from error
        self.download_root.mkdir(parents=True, exist_ok=True)
        try:
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                download_root=str(self.download_root),
            )
        except Exception as error:
            raise EngineUnavailableError(
                f"Could not load Whisper model '{self.model_name}': {error}"
            ) from error

    def transcribe(self, audio_path: Path, language: str = "auto") -> Transcript:
        self.load()
        selected_language = None if language == "auto" else language
        try:
            segments, info = self._model.transcribe(
                str(audio_path),
                language=selected_language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400},
                condition_on_previous_text=False,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as error:
            raise EngineUnavailableError(f"Transcription failed: {error}") from error
        return Transcript(
            text=text,
            language=str(info.language),
            language_probability=float(info.language_probability),
        )
