from kilnworks.adapters.media.fake import FakeTranscriber, FakeVisionExtractor


def test_vision_returns_default_reply_and_records_call():
    vision = FakeVisionExtractor()
    completion = vision.describe(b"\x89PNG", "image/png", "photo.png")
    assert completion.text == vision.reply
    assert completion.model == "fake"
    assert vision.calls == [("image/png", "photo.png", 4)]


def test_vision_custom_reply_overrides_default():
    vision = FakeVisionExtractor(reply="a glazed vase")
    completion = vision.describe(b"data", "image/jpeg", "vase.jpg")
    assert completion.text == "a glazed vase"
    assert completion.output_tokens >= 1


def test_transcriber_returns_default_reply_and_records_call():
    transcriber = FakeTranscriber()
    completion = transcriber.transcribe(b"RIFF", "audio/wav", "clip.wav")
    assert completion.text == transcriber.reply
    assert completion.model == "fake"
    assert transcriber.calls == [("audio/wav", "clip.wav", 4)]


def test_transcriber_custom_reply_overrides_default():
    transcriber = FakeTranscriber(reply="glaze the bisque ware")
    completion = transcriber.transcribe(b"data", "audio/mpeg", "note.mp3")
    assert completion.text == "glaze the bisque ware"
    assert completion.output_tokens >= 1
