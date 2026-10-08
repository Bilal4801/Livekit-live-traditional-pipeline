"""Phrases Twilio speaks to the caller before translation starts, in the caller's language."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CallerMessages:
    voice: str
    language: str
    please_wait: str
    no_agent: str


_ENGLISH = CallerMessages(
    voice="Polly.Joanna",
    language="en-US",
    please_wait="Please wait while we connect your call.",
    no_agent="Sorry, no agent is available right now. Please try again later.",
)

_BY_LANGUAGE: dict[str, CallerMessages] = {
    "spanish": CallerMessages(
        voice="Polly.Lupe",
        language="es-US",
        please_wait="Por favor, espere mientras conectamos su llamada.",
        no_agent=(
            "Lo sentimos, en este momento no hay agentes disponibles. "
            "Por favor, intente de nuevo más tarde."
        ),
    ),
}


def for_language(language_key: str) -> CallerMessages:
    return _BY_LANGUAGE.get(language_key, _ENGLISH)
