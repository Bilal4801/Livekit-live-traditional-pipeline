from src import caller_messages
from src.agent import languages
from src.prompts import build_system_prompt
from src.routes.incoming_call import _normalize_language


def test_missing_or_unknown_lang_falls_back_to_spanish() -> None:
    assert _normalize_language(None, "spanish") == "spanish"
    assert _normalize_language("klingon", "spanish") == "spanish"
    assert _normalize_language("Spanish", "spanish") == "spanish"
    assert _normalize_language("french", "spanish") == "spanish"


def test_spanish_language_codes() -> None:
    spanish = languages.from_key("spanish")
    assert spanish is not None
    assert (spanish.name, spanish.stt_code, spanish.tts_code) == ("Spanish", "es", "es")


def test_both_directions_get_spanish_hints() -> None:
    assert "Spanish -> English specifics" in build_system_prompt("Spanish", "English")
    assert "usted" in build_system_prompt("English", "Spanish")


def test_caller_hears_spanish_phone_messages() -> None:
    messages = caller_messages.for_language("spanish")
    assert messages.language == "es-US"
    assert messages.please_wait.startswith("Por favor")
