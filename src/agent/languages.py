from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    name: str
    """English name used in translator prompts."""
    stt_code: str
    """Deepgram language code."""
    tts_code: str
    """ElevenLabs language code."""


_BY_KEY: dict[str, Language] = {
    "english": Language("English", "en-US", "en"),
    "spanish": Language("Spanish", "es", "es"),
}

ENGLISH = _BY_KEY["english"]


def from_key(key: str) -> Language | None:
    """Map a Studio/IVR language key (e.g. ``spanish``) to a Language."""
    return _BY_KEY.get(key.strip().lower())
