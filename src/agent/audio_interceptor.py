from __future__ import annotations

import logging

from livekit import rtc
from livekit.plugins import deepgram, elevenlabs, openai

from src.agent import languages
from src.agent.languages import Language
from src.agent.pipeline import TranslationDirection
from src.config import Settings
from src.services.twilio_livekit import TwilioLiveKitBridge

logger = logging.getLogger(__name__)

NUM_CHANNELS = 1
AGENT_WARMUP_MS = 1000
ELEVENLABS_ENCODING = "pcm_16000"


class AudioInterceptor:
    """Dual streaming STT -> LLM -> TTS bridge.

    Caller speech -> Deepgram -> OpenAI (caller_lang -> English) -> ElevenLabs -> ``to_agent``
    Agent speech  -> Deepgram -> OpenAI (English -> caller_lang) -> ElevenLabs -> ``to_caller``
    """

    def __init__(
        self,
        *,
        room: rtc.Room,
        bridge: TwilioLiveKitBridge,
        settings: Settings,
        caller_identity: str,
        agent_identity: str,
        caller_language: Language,
    ) -> None:
        self._room = room
        self._bridge = bridge
        self._settings = settings
        self._caller_identity = caller_identity
        self._agent_identity = agent_identity
        self._caller_language = caller_language

        self._started = False
        self._closed = False

        self._llm: openai.LLM | None = None
        self._caller_lane: TranslationDirection | None = None
        self._agent_lane: TranslationDirection | None = None
        self._to_agent_tts: elevenlabs.TTS | None = None
        self._to_caller_tts: elevenlabs.TTS | None = None

        self._to_caller_source: rtc.AudioSource | None = None
        self._to_agent_source: rtc.AudioSource | None = None
        self._to_caller_pub: rtc.LocalTrackPublication | None = None
        self._to_agent_pub: rtc.LocalTrackPublication | None = None

    @property
    def started(self) -> bool:
        return self._started

    @property
    def caller_identity(self) -> str:
        return self._caller_identity

    @property
    def agent_identity(self) -> str:
        return self._agent_identity

    async def start(self) -> None:
        if self._started or self._closed:
            return

        caller = self._room.remote_participants.get(self._caller_identity)
        agent = self._room.remote_participants.get(self._agent_identity)
        if caller is None or agent is None:
            logger.error(
                "Both participants required to start interception "
                "(caller=%s present=%s agent=%s present=%s)",
                self._caller_identity,
                caller is not None,
                self._agent_identity,
                agent is not None,
            )
            return

        logger.info(
            "Starting STT->LLM->TTS interception lang=%s",
            self._caller_language.name,
        )

        await self._isolate_original_tracks(caller, agent)
        self._create_engines()
        await self._publish_output_tracks()
        await self._apply_subscription_permissions()

        self._caller_lane = self._make_lane(
            label="caller->agent",
            speaker=caller,
            source_lang=self._caller_language,
            target_lang=languages.ENGLISH,
            stt_lang=self._caller_language,
            tts_engine=self._to_agent_tts,
            output=self._to_agent_source,
        )
        self._agent_lane = self._make_lane(
            label="agent->caller",
            speaker=agent,
            source_lang=languages.ENGLISH,
            target_lang=self._caller_language,
            stt_lang=languages.ENGLISH,
            tts_engine=self._to_caller_tts,
            output=self._to_caller_source,
            skip_initial_ms=AGENT_WARMUP_MS,
        )

        self._started = True
        logger.info("Interception active")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        logger.info("Closing AudioInterceptor")

        for lane in (self._caller_lane, self._agent_lane):
            if lane is not None:
                await lane.aclose()

        for engine in (self._to_agent_tts, self._to_caller_tts, self._llm):
            if engine is not None:
                try:
                    await engine.aclose()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error closing %s: %s", type(engine).__name__, exc)

        self._started = False

    def _create_engines(self) -> None:
        s = self._settings
        self._llm = openai.LLM(
            model=s.openai_llm_model,
            api_key=s.openai_api_key,
            max_completion_tokens=200,
        )
        self._to_agent_tts = self._make_tts(languages.ENGLISH, s.elevenlabs_voice_id)
        self._to_caller_tts = self._make_tts(
            self._caller_language,
            s.elevenlabs_caller_voice_id or s.elevenlabs_voice_id,
        )

    def _make_stt(self, language: Language) -> deepgram.STT:
        s = self._settings
        return deepgram.STT(
            model=s.deepgram_model,
            language=language.stt_code,
            api_key=s.deepgram_api_key,
            interim_results=True,
            no_delay=True,
            punctuate=True,
            smart_format=False,
            filler_words=False,
            endpointing_ms=s.deepgram_endpointing_ms,
            sample_rate=16000,
        )

    def _make_tts(self, language: Language, voice_id: str) -> elevenlabs.TTS:
        s = self._settings
        engine = elevenlabs.TTS(
            api_key=s.eleven_api_key,
            voice_id=voice_id,
            model=s.elevenlabs_model,
            encoding=ELEVENLABS_ENCODING,
            auto_mode=True,
            sync_alignment=False,
            language=language.tts_code,
        )
        engine.prewarm()
        return engine

    def _make_lane(
        self,
        *,
        label: str,
        speaker: rtc.RemoteParticipant,
        source_lang: Language,
        target_lang: Language,
        stt_lang: Language,
        tts_engine: elevenlabs.TTS | None,
        output: rtc.AudioSource | None,
        skip_initial_ms: int = 0,
    ) -> TranslationDirection:
        assert self._llm and tts_engine and output
        lane = TranslationDirection(
            label=label,
            stt_engine=self._make_stt(stt_lang),
            stt_language=stt_lang.stt_code,
            llm_engine=self._llm,
            tts_engine=tts_engine,
            output=output,
            from_language=source_lang.name,
            to_language=target_lang.name,
            holdback_words=self._settings.eager_holdback_words,
            history_turns=self._settings.translation_history_turns,
            log_transcripts=self._settings.log_transcripts,
        )
        lane.start(speaker, skip_initial_ms=skip_initial_ms)
        return lane

    async def _publish_output_tracks(self) -> None:
        assert self._to_agent_tts is not None
        sample_rate = self._to_agent_tts.sample_rate
        self._to_caller_source = rtc.AudioSource(sample_rate, NUM_CHANNELS)
        self._to_agent_source = rtc.AudioSource(sample_rate, NUM_CHANNELS)

        to_caller_track = rtc.LocalAudioTrack.create_audio_track(
            "to_caller", self._to_caller_source
        )
        to_agent_track = rtc.LocalAudioTrack.create_audio_track(
            "to_agent", self._to_agent_source
        )
        opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)

        self._to_caller_pub = await self._room.local_participant.publish_track(
            to_caller_track, opts
        )
        self._to_agent_pub = await self._room.local_participant.publish_track(
            to_agent_track, opts
        )
        logger.info(
            "Published translation tracks to_caller=%s to_agent=%s",
            self._to_caller_pub.sid if self._to_caller_pub else None,
            self._to_agent_pub.sid if self._to_agent_pub else None,
        )

    async def _apply_subscription_permissions(self) -> None:
        """Caller may only subscribe to to_caller; agent only to to_agent."""
        if not self._to_caller_pub or not self._to_agent_pub:
            return

        try:
            self._room.local_participant.set_track_subscription_permissions(
                allow_all_participants=False,
                participant_permissions=[
                    rtc.ParticipantTrackPermission(
                        participant_identity=self._caller_identity,
                        allow_all=False,
                        allowed_track_sids=[self._to_caller_pub.sid],
                    ),
                    rtc.ParticipantTrackPermission(
                        participant_identity=self._agent_identity,
                        allow_all=False,
                        allowed_track_sids=[self._to_agent_pub.sid],
                    ),
                ],
            )
            logger.info("Applied translation track subscription permissions")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not set track subscription permissions (%s) — "
                "participants may hear both translation tracks",
                exc,
            )

    async def _isolate_original_tracks(
        self,
        caller: rtc.RemoteParticipant,
        agent: rtc.RemoteParticipant,
    ) -> None:
        """Prevent humans from hearing each other's untranslated audio."""
        caller_audio_sids = [
            pub.sid
            for pub in caller.track_publications.values()
            if pub.kind == rtc.TrackKind.KIND_AUDIO and pub.sid
        ]
        agent_audio_sids = [
            pub.sid
            for pub in agent.track_publications.values()
            if pub.kind == rtc.TrackKind.KIND_AUDIO and pub.sid
        ]

        for sid in agent_audio_sids:
            await self._bridge.unsubscribe_track(
                room_name=self._room.name,
                identity=self._caller_identity,
                track_sid=sid,
            )
        for sid in caller_audio_sids:
            await self._bridge.unsubscribe_track(
                room_name=self._room.name,
                identity=self._agent_identity,
                track_sid=sid,
            )
        logger.info(
            "Unsubscribed original cross-audio (caller_tracks=%s agent_tracks=%s)",
            caller_audio_sids,
            agent_audio_sids,
        )

    async def mute_cross_audio(self, publisher_identity: str, track_sid: str) -> None:
        """Stop the other person hearing this original microphone track (late SIP publish)."""
        if self._closed or not track_sid:
            return
        if publisher_identity == self._caller_identity:
            listener = self._agent_identity
        elif publisher_identity == self._agent_identity:
            listener = self._caller_identity
        else:
            return
        await self._bridge.unsubscribe_track(
            room_name=self._room.name,
            identity=listener,
            track_sid=track_sid,
        )
        logger.info(
            "Muted original track %s from %s for %s",
            track_sid,
            publisher_identity,
            listener,
        )
