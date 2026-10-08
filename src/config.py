from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.agent import languages


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_transcripts: bool = Field(default=False, alias="LOG_TRANSCRIPTS")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=5050, alias="API_PORT")
    ngrok_domain: str = Field(alias="NGROK_DOMAIN")

    twilio_account_sid: str = Field(alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field(alias="TWILIO_AUTH_TOKEN")
    twilio_caller_number: str = Field(alias="TWILIO_CALLER_NUMBER")
    twilio_agent_number: str = Field(alias="TWILIO_AGENT_NUMBER")
    twilio_flex_workflow_sid: str = Field(default="", alias="TWILIO_FLEX_WORKFLOW_SID")
    twilio_validate_signature: bool = Field(default=True, alias="TWILIO_VALIDATE_SIGNATURE")

    livekit_url: str = Field(alias="LIVEKIT_URL")
    livekit_api_key: str = Field(alias="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field(alias="LIVEKIT_API_SECRET")

    # STT — Deepgram
    deepgram_api_key: str = Field(alias="DEEPGRAM_API_KEY")
    deepgram_model: str = Field(default="nova-3", alias="DEEPGRAM_MODEL")
    deepgram_endpointing_ms: int = Field(default=25, alias="DEEPGRAM_ENDPOINTING_MS")

    # LLM — OpenAI
    openai_api_key: str = Field(alias="OPENAI_API_KEY")
    openai_llm_model: str = Field(default="gpt-5.4-mini", alias="OPENAI_LLM_MODEL")

    # TTS — ElevenLabs
    eleven_api_key: str = Field(alias="ELEVEN_API_KEY")
    elevenlabs_model: str = Field(default="eleven_flash_v2_5", alias="ELEVENLABS_MODEL")
    # Voice the agent hears (English translation of the caller).
    elevenlabs_voice_id: str = Field(default="hpp4J3VqNfWAUOO0d1Us", alias="ELEVENLABS_VOICE_ID")
    # Voice the caller hears (Spanish translation of the agent); empty = ELEVENLABS_VOICE_ID.
    elevenlabs_caller_voice_id: str = Field(default="", alias="ELEVENLABS_CALLER_VOICE_ID")

    # Language the callers speak; the agent always speaks English.
    caller_language: str = Field(default="spanish", alias="CALLER_LANGUAGE")

    # Eagerness: 0 = translate every word the moment Deepgram reports it.
    # 1+ = hold back the last N interim words, which Deepgram may still revise.
    eager_holdback_words: int = Field(default=0, ge=0, le=5, alias="EAGER_HOLDBACK_WORDS")
    translation_history_turns: int = Field(default=3, ge=0, le=20, alias="TRANSLATION_HISTORY_TURNS")

    agent_name: str = Field(default="live-translator", alias="AGENT_NAME")
    skip_flex: bool = Field(default=False, alias="SKIP_FLEX")

    @field_validator("caller_language")
    @classmethod
    def _known_caller_language(cls, value: str) -> str:
        key = value.strip().lower()
        if languages.from_key(key) is None:
            raise ValueError("CALLER_LANGUAGE must be a supported language (english or spanish)")
        return key

    @property
    def public_https_base(self) -> str:
        return f"https://{self.ngrok_domain}"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
