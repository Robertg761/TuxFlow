"""Fast, deterministic dictation cleanup that never sends text off-device."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tuxflow.config import Settings

_FILLERS = re.compile(
    r"(?<![\w'-])(?:um+|uh+|erm+|hmm+)(?:,\s*|\s+)",
    flags=re.IGNORECASE,
)

_PUNCTUATION = (
    (re.compile(r"\s+\bnew paragraph\b\s*", re.IGNORECASE), "\n\n"),
    (re.compile(r"\s+\bnew line\b\s*", re.IGNORECASE), "\n"),
    (re.compile(r"\s+\bquestion mark\b", re.IGNORECASE), "?"),
    (re.compile(r"\s+\bexclamation (?:mark|point)\b", re.IGNORECASE), "!"),
    (re.compile(r"\s+\bcomma\b", re.IGNORECASE), ","),
    (re.compile(r"\s+\bcolon\b", re.IGNORECASE), ":"),
    (re.compile(r"\s+\bsemicolon\b", re.IGNORECASE), ";"),
    (re.compile(r"\s+\bperiod\b", re.IGNORECASE), "."),
)

_PRESS_ENTER = re.compile(r"(?:[\s,.!?]+)?press enter[.!?]?\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ProcessedText:
    text: str
    press_enter: bool = False


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    escaped = re.escape(phrase.strip())
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def process_text(raw_text: str, settings: Settings) -> ProcessedText:
    text = re.sub(r"\s+", " ", raw_text).strip()
    press_enter = False

    if settings.press_enter_command and _PRESS_ENTER.search(text):
        press_enter = True
        text = _PRESS_ENTER.sub("", text).rstrip(" ,")

    if settings.remove_fillers:
        text = _FILLERS.sub("", text).strip()

    if settings.spoken_punctuation:
        for pattern, replacement in _PUNCTUATION:
            text = pattern.sub(replacement, text)

    for item in sorted(settings.snippets, key=lambda value: len(value.trigger), reverse=True):
        if item.trigger.strip():
            text = _phrase_pattern(item.trigger).sub(
                lambda _match, expansion=item.expansion: expansion, text
            )

    for item in sorted(settings.dictionary, key=lambda value: len(value.spoken), reverse=True):
        if item.spoken.strip():
            text = _phrase_pattern(item.spoken).sub(
                lambda _match, written=item.written: written, text
            )

    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if text:
        text = re.sub(
            r"(^|[.!?]\s+|\n+)([a-z])",
            lambda match: match.group(1) + match.group(2).upper(),
            text,
        )
    return ProcessedText(text=text, press_enter=press_enter)
