import asyncio
from types import SimpleNamespace

from src.agent.pipeline import TranslationDirection, _Fragment
from src.prompts import WAIT_TOKEN


class FakeLLMStream:
    def __init__(self, reply: str) -> None:
        self._chunks = [reply[i : i + 3] for i in range(0, len(reply), 3)]

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for text in self._chunks:
            await asyncio.sleep(0)
            yield SimpleNamespace(delta=SimpleNamespace(content=text))

    async def aclose(self) -> None:
        pass


class FakeLLM:
    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.prompts: list[str] = []

    def chat(self, *, chat_ctx):
        self.prompts.append(chat_ctx.items[-1].text_content)
        return FakeLLMStream(self.replies.pop(0))


class FakeTTSStream:
    def __init__(self, spoken: list[str]) -> None:
        self._spoken = spoken
        self._text = ""
        self._done = asyncio.Event()

    def push_text(self, text: str) -> None:
        self._text += text

    def end_input(self) -> None:
        self._done.set()

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        await self._done.wait()
        self._spoken.append(self._text.strip())
        yield SimpleNamespace(frame=self._text.strip())

    async def aclose(self) -> None:
        pass


class FakeTTS:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def stream(self):
        return FakeTTSStream(self.spoken)


class FakeSource:
    def __init__(self) -> None:
        self.frames: list[str] = []

    async def capture_frame(self, frame) -> None:
        self.frames.append(frame)


def _lane(llm: FakeLLM, tts: FakeTTS, source: FakeSource) -> TranslationDirection:
    return TranslationDirection(
        label="test",
        stt_engine=None,  # type: ignore[arg-type]
        stt_language="es",
        llm_engine=llm,  # type: ignore[arg-type]
        tts_engine=tts,  # type: ignore[arg-type]
        output=source,  # type: ignore[arg-type]
        from_language="Spanish",
        to_language="English",
        holdback_words=0,
        history_turns=2,
        log_transcripts=False,
    )


async def _run(lane: TranslationDirection, fragments: list[_Fragment], expected_frames: int, source):
    tasks = [asyncio.create_task(lane._translate_loop()), asyncio.create_task(lane._playout_loop())]
    lane._fragments.put_nowait(fragments[0])
    await asyncio.sleep(0)  # translator picks up the first word before the rest arrive
    for fragment in fragments[1:]:
        lane._fragments.put_nowait(fragment)
    for _ in range(200):
        if len(source.frames) >= expected_frames and lane._fragments.empty():
            break
        await asyncio.sleep(0.01)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def test_each_fragment_is_spoken_in_order_and_backlog_is_batched() -> None:
    llm = FakeLLM(["Hello", "how are you"])
    tts, source = FakeTTS(), FakeSource()
    lane = _lane(llm, tts, source)
    fragments = [_Fragment("Hola"), _Fragment("como"), _Fragment("estas")]

    asyncio.run(_run(lane, fragments, 2, source))

    assert source.frames == ["Hello", "how are you"]
    # "como" and "estas" arrived while "Hola" was being translated, so they share a request.
    assert "New words to translate now: <source>como estas</source>" in llm.prompts[1]
    assert "<spoken>Hello</spoken>" in llm.prompts[1]


def test_wait_token_speaks_nothing_and_carries_words_forward() -> None:
    llm = FakeLLM([WAIT_TOKEN, "The house"])
    tts, source = FakeTTS(), FakeSource()
    lane = _lane(llm, tts, source)

    async def scenario() -> None:
        tasks = [
            asyncio.create_task(lane._translate_loop()),
            asyncio.create_task(lane._playout_loop()),
        ]
        lane._fragments.put_nowait(_Fragment("La"))
        await asyncio.sleep(0.05)
        lane._fragments.put_nowait(_Fragment("casa", end_of_utterance=True))
        for _ in range(100):
            if source.frames:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.02)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())

    assert source.frames == ["The house"]
    assert "<source>La casa</source>" in llm.prompts[1]
    assert "<spoken></spoken>" in llm.prompts[1]
    assert list(lane._history) == [("La casa", "The house")]
    assert lane._utterance_source == ""
