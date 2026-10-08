from src.agent.eager_committer import EagerWordCommitter


def test_releases_each_word_as_soon_as_it_is_heard() -> None:
    c = EagerWordCommitter()
    assert c.on_interim(["Hola"]) == ["Hola"]
    assert c.on_interim(["Hola", "como"]) == ["como"]
    assert c.on_interim(["Hola", "como", "estas"]) == ["estas"]
    assert c.on_final(["Hola", "como", "estas"]) == []


def test_finals_accumulate_across_segments() -> None:
    c = EagerWordCommitter()
    assert c.on_final(["I", "need"]) == ["I", "need"]
    assert c.on_interim(["help"]) == ["help"]
    assert c.on_final(["help", "today"]) == ["today"]


def test_holdback_waits_for_following_word_until_final() -> None:
    c = EagerWordCommitter(holdback_words=1)
    assert c.on_interim(["I"]) == []
    assert c.on_interim(["I", "want"]) == ["I"]
    assert c.on_final(["I", "want", "coffee"]) == ["want", "coffee"]


def test_shorter_final_does_not_drop_next_segment_words() -> None:
    c = EagerWordCommitter()
    assert c.on_interim(["I", "want", "to", "go"]) == ["I", "want", "to", "go"]
    assert c.on_final(["I", "wanna", "go"]) == []
    assert c.on_interim(["home"]) == ["home"]


def test_end_utterance_resets_state() -> None:
    c = EagerWordCommitter()
    c.on_final(["yes"])
    c.end_utterance()
    assert c.on_interim(["no"]) == ["no"]
