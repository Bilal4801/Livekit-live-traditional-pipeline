from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass

from livekit import rtc
from livekit.agents import llm, stt, tts

from src.agent.eager_committer import EagerWordCommitter
from src.prompts import WAIT_TOKEN, build_system_prompt, build_turn_prompt

logger = logging.getLogger(__name__)

STT_SAMPLE_RATE = 16000
MAX_UTTERANCE_CHARS = 2000


@dataclass
class _Fragment:
    text: str
    end_of_utterance: bool = False


class TranslationDirection:
    """One streaming STT -> LLM -> TTS lane: speaker audio in, translated audio out.

    Words are translated as soon as Deepgram reports them. While a translation request
    is in flight, newly arrived words queue up and are sent together in the next request,
    so latency stays at roughly one LLM round trip regardless of how fast people talk.
    Every translated fragment gets its own ElevenLabs context, which is flushed
    immediately, so even a single word is spoken without waiting for a full sentence.
    """

    def __init__(
        self,
        *,
        label: str,
        stt_engine: stt.STT,
        stt_language: str,
        llm_engine: llm.LLM,
        tts_engine: tts.TTS,
        output: rtc.AudioSource,
        from_language: str,
        to_language: str,
        holdback_words: int,
        history_turns: int,
        log_transcripts: bool,
    ) -> None:
        self._label = label
        self._stt = stt_engine
        self._stt_language = stt_language
        self._llm = llm_engine
        self._tts = tts_engine
        self._output = output
        self.from_language = from_language
        self.to_language = to_language
        self._committer = EagerWordCommitter(holdback_words)
        self._history: deque[tuple[str, str]] = deque(maxlen=history_turns or None)
        self._history_enabled = history_turns > 0
        self._log_transcripts = log_transcripts

        self._fragments: asyncio.Queue[_Fragment] = asyncio.Queue()
        self._playout: asyncio.Queue[tts.SynthesizeStream] = asyncio.Queue()
        self._utterance_source = ""
        self._utterance_spoken = ""
        self._tasks: list[asyncio.Task[None]] = []
        self._stt_stream: stt.RecognizeStream | None = None
        self._closed = False

    def start(self, participant: rtc.RemoteParticipant, *, skip_initial_ms: int = 0) -> None:
        self._stt_stream = self._stt.stream(language=self._stt_language)
        self._tasks = [
            asyncio.create_task(
                self._forward_audio(participant, skip_initial_ms), name=f"{self._label}-audio"
            ),
            asyncio.create_task(self._consume_stt(), name=f"{self._label}-stt"),
            asyncio.create_task(self._translate_loop(), name=f"{self._label}-llm"),
            asyncio.create_task(self._playout_loop(), name=f"{self._label}-tts"),
        ]
        logger.info(
            "[%s] streaming %s -> %s started", self._label, self.from_language, self.to_language
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._stt_stream is not None:
            await self._stt_stream.aclose()
        while not self._playout.empty():
            await self._playout.get_nowait().aclose()

    async def _forward_audio(self, participant: rtc.RemoteParticipant, skip_initial_ms: int) -> None:
        assert self._stt_stream is not None
        stream = rtc.AudioStream.from_participant(
            participant=participant,
            track_source=rtc.TrackSource.SOURCE_MICROPHONE,
            sample_rate=STT_SAMPLE_RATE,
            num_channels=1,
        )
        first_frame_at: float | None = None
        try:
            async for event in stream:
                if skip_initial_ms:
                    now = time.monotonic()
                    first_frame_at = first_frame_at or now
                    if (now - first_frame_at) * 1000 < skip_initial_ms:
                        continue
                self._stt_stream.push_frame(event.frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] audio feed ended: %s", self._label, exc)
        finally:
            await stream.aclose()

    async def _consume_stt(self) -> None:
        assert self._stt_stream is not None
        try:
            async for event in self._stt_stream:
                if event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT:
                    self._queue_words(self._committer.on_interim(_words(event)))
                elif event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                    self._queue_words(self._committer.on_final(_words(event)))
                    if self._log_transcripts and event.alternatives:
                        text = event.alternatives[0].text.strip()
                        if text:
                            logger.info('[%s] SAID: "%s"', self._label, text)
                elif event.type == stt.SpeechEventType.END_OF_SPEECH:
                    self._committer.end_utterance()
                    self._fragments.put_nowait(_Fragment("", end_of_utterance=True))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] STT stream ended: %s", self._label, exc)

    def _queue_words(self, words: list[str]) -> None:
        if words:
            self._fragments.put_nowait(_Fragment(" ".join(words)))

    async def _translate_loop(self) -> None:
        while True:
            fragment = await self._fragments.get()
            # Batch whatever arrived while the previous request was running.
            while not fragment.end_of_utterance and not self._fragments.empty():
                nxt = self._fragments.get_nowait()
                fragment = _Fragment(
                    f"{fragment.text} {nxt.text}".strip(), end_of_utterance=nxt.end_of_utterance
                )

            if fragment.text:
                try:
                    await self._translate(fragment.text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.error("[%s] translation failed: %s", self._label, exc)
            if fragment.end_of_utterance:
                self._finish_utterance()

    async def _translate(self, new_words: str) -> None:
        self._utterance_source = f"{self._utterance_source} {new_words}".strip()[
            -MAX_UTTERANCE_CHARS:
        ]
        chat_ctx = llm.ChatContext.empty()
        chat_ctx.add_message(
            role="system", content=build_system_prompt(self.from_language, self.to_language)
        )
        chat_ctx.add_message(
            role="user",
            content=build_turn_prompt(
                history=list(self._history),
                utterance_source=self._utterance_source,
                utterance_spoken=self._utterance_spoken,
                new_words=new_words,
            ),
        )

        output = ""
        tts_stream: tts.SynthesizeStream | None = None
        llm_stream = self._llm.chat(chat_ctx=chat_ctx)
        try:
            async for chunk in llm_stream:
                if not chunk.delta or not chunk.delta.content:
                    continue
                output += chunk.delta.content
                if tts_stream is None:
                    head = output.lstrip()
                    if not head or WAIT_TOKEN.startswith(head):
                        continue
                    tts_stream = self._tts.stream()
                    self._playout.put_nowait(tts_stream)
                    tts_stream.push_text(head)
                else:
                    tts_stream.push_text(chunk.delta.content)
        finally:
            await llm_stream.aclose()
            if tts_stream is not None:
                tts_stream.end_input()

        spoken = output.replace(WAIT_TOKEN, "").strip()
        if spoken:
            self._utterance_spoken = f"{self._utterance_spoken} {spoken}".strip()[
                -MAX_UTTERANCE_CHARS:
            ]
            if self._log_transcripts:
                logger.info('[%s] "%s" -> "%s"', self._label, new_words, spoken)

    def _finish_utterance(self) -> None:
        if self._history_enabled and self._utterance_source and self._utterance_spoken:
            self._history.append((self._utterance_source, self._utterance_spoken))
        self._utterance_source = ""
        self._utterance_spoken = ""

    async def _playout_loop(self) -> None:
        while True:
            tts_stream = await self._playout.get()
            try:
                async for audio in tts_stream:
                    await self._output.capture_frame(audio.frame)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("[%s] TTS playout failed: %s", self._label, exc)
            finally:
                await tts_stream.aclose()


def _words(event: stt.SpeechEvent) -> list[str]:
    if not event.alternatives:
        return []
    alt = event.alternatives[0]
    if alt.words:
        return [str(w).strip() for w in alt.words if str(w).strip()]
    return alt.text.split()
