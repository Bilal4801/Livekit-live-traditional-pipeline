from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from livekit import rtc
from livekit.agents import AutoSubscribe, JobContext

from src.agent import languages
from src.agent.audio_interceptor import AudioInterceptor
from src.config import Settings
from src.services.twilio_livekit import TwilioLiveKitBridge

logger = logging.getLogger(__name__)


def _parse_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class TranslationJob:
    """Owns one room's dual STT -> LLM -> TTS interception lifecycle."""

    def __init__(self, ctx: JobContext, settings: Settings) -> None:
        self._ctx = ctx
        self._settings = settings
        self._bridge = TwilioLiveKitBridge(settings)
        self._interceptor: AudioInterceptor | None = None
        self._meta: dict[str, Any] = {}
        self._start_lock = asyncio.Lock()
        self._closed = False

    async def run(self) -> None:
        dispatch_meta = _parse_json(self._ctx.job.metadata if self._ctx.job else None)
        room_meta = _parse_json(self._ctx.room.metadata)
        self._meta = {**room_meta, **dispatch_meta}

        caller_identity = str(
            self._meta.get("caller_identity")
            or f"caller-{self._meta.get('from', 'unknown')}"
        )
        agent_identity = str(
            self._meta.get("agent_identity")
            or f"agent-{self._meta.get('from', 'unknown')}"
        )
        lang_key = str(self._meta.get("lang") or self._settings.caller_language)
        caller_language = languages.from_key(lang_key) or languages.from_key(
            self._settings.caller_language
        )
        assert caller_language is not None
        skip_flex = _truthy(self._meta.get("skip_flex", self._settings.skip_flex))

        logger.info(
            "Translation job room=%s lang=%s caller=%s agent=%s skip_flex=%s",
            self._ctx.room.name,
            caller_language.name,
            caller_identity,
            agent_identity,
            skip_flex,
        )

        await self._ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

        self._interceptor = AudioInterceptor(
            room=self._ctx.room,
            bridge=self._bridge,
            settings=self._settings,
            caller_identity=caller_identity,
            agent_identity=agent_identity,
            caller_language=caller_language,
        )

        @self._ctx.room.on("participant_connected")
        def _on_participant_connected(participant: rtc.RemoteParticipant) -> None:
            logger.info("Participant connected: %s", participant.identity)
            asyncio.create_task(self._maybe_start(skip_flex=skip_flex))

        @self._ctx.room.on("participant_disconnected")
        def _on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
            logger.info("Participant disconnected: %s", participant.identity)
            if participant.identity in {caller_identity, agent_identity}:
                asyncio.create_task(self._shutdown(reason=f"{participant.identity} left"))

        @self._ctx.room.on("room_metadata_changed")
        def _on_metadata_changed(*_args: Any) -> None:
            # Prefer live room.metadata — callback arity varies by SDK version
            self._meta.update(_parse_json(self._ctx.room.metadata))
            logger.info(
                "Room metadata updated start_requested=%s",
                self._meta.get("start_requested"),
            )
            asyncio.create_task(self._maybe_start(skip_flex=skip_flex))

        @self._ctx.room.on("track_published")
        def _on_track_published(
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            # Mute as soon as the track exists, even while the pipeline is still starting.
            if (
                self._interceptor
                and publication.kind == rtc.TrackKind.KIND_AUDIO
                and publication.sid
            ):
                asyncio.create_task(
                    self._interceptor.mute_cross_audio(participant.identity, publication.sid)
                )

        # Participants may already be present when the agent joins
        await self._maybe_start(skip_flex=skip_flex)

        shutdown_event = asyncio.Event()

        async def _on_shutdown() -> None:
            await self._shutdown(reason="job shutdown")
            shutdown_event.set()

        self._ctx.add_shutdown_callback(_on_shutdown)
        await shutdown_event.wait()

    async def _maybe_start(self, *, skip_flex: bool) -> None:
        if self._closed or self._interceptor is None or self._interceptor.started:
            return

        participants = self._ctx.room.remote_participants
        caller = participants.get(self._interceptor.caller_identity)
        agent = participants.get(self._interceptor.agent_identity)
        if caller is None or agent is None:
            logger.debug(
                "Waiting for both participants (caller=%s agent=%s)",
                caller is not None,
                agent is not None,
            )
            return

        start_requested = skip_flex or _truthy(self._meta.get("start_requested", False))
        if not start_requested:
            logger.info(
                "Both participants present — waiting for Flex reservation-accepted "
                "(start_requested=false)"
            )
            return

        async with self._start_lock:
            if self._interceptor.started or self._closed:
                return
            await self._interceptor.start()

    async def _shutdown(self, *, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        logger.info("Shutting down translation job: %s", reason)
        if self._interceptor is not None:
            await self._interceptor.close()
        try:
            await self._ctx.room.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Room disconnect: %s", exc)
