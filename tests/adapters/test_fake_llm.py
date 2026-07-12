from kilnworks.adapters.llm.fake import FakeLLM


def test_default_reply_contains_a_citation_marker():
    """The smoke citation metric checks the answer for a [N] citation marker;
    the default canned reply must keep satisfying it."""
    llm = FakeLLM()
    completion = llm.complete("system", "user")
    assert "[1]" in completion.text


def test_default_reply_is_recorded_verbatim():
    llm = FakeLLM()
    completion = llm.complete("system", "user")
    assert completion.text == llm.reply


def test_custom_reply_overrides_default():
    llm = FakeLLM(reply="custom answer")
    completion = llm.complete("system", "user")
    assert completion.text == "custom answer"


def test_complete_records_calls():
    llm = FakeLLM()
    llm.complete("sys", "usr")
    assert llm.calls == [("sys", "usr")]


def test_stream_yields_words_then_final_completion():
    llm = FakeLLM(reply="hello world")
    chunks = list(llm.stream("sys", "usr"))
    *words, final = chunks
    assert words == ["hello ", "world"]
    assert final.text == "hello world"
