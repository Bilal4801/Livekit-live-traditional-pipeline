from __future__ import annotations


class EagerWordCommitter:
    """Turns Deepgram interim/final results into an ordered stream of new words.

    Deepgram interim results cover the audio since the last final result, so the live
    hypothesis for an utterance is ``finalized words + current interim words``. Each
    word is released exactly once, as soon as it appears (minus ``holdback_words``),
    without waiting for the phrase to finish.
    """

    def __init__(self, holdback_words: int = 0) -> None:
        self._holdback = max(0, holdback_words)
        self._final_words: list[str] = []
        self._committed = 0

    def on_interim(self, words: list[str]) -> list[str]:
        hypothesis = self._final_words + words
        stable_end = len(hypothesis) - self._holdback
        return self._release(hypothesis, stable_end)

    def on_final(self, words: list[str]) -> list[str]:
        self._final_words.extend(words)
        # Deepgram can merge interim words in the final ("I want" -> "I wanna"); never
        # point past the finalized words or the next segment's first words would be lost.
        self._committed = min(self._committed, len(self._final_words))
        return self._release(self._final_words, len(self._final_words))

    def end_utterance(self) -> None:
        self._final_words = []
        self._committed = 0

    def _release(self, hypothesis: list[str], end: int) -> list[str]:
        if end <= self._committed:
            return []
        new_words = hypothesis[self._committed : end]
        self._committed = end
        return new_words
