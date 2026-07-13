from kilnworks.core.models import Completion


class FakeVisionExtractor:
    """Canned vision extractor for tests/`KILNWORKS_FAKE_PROVIDERS`; records every call."""

    def __init__(self, reply: str = "A fake image description."):
        self.reply = reply
        self.calls: list[tuple[str, str, int]] = []

    def describe(self, image: bytes, mime: str, name: str) -> Completion:
        self.calls.append((mime, name, len(image)))
        return Completion(
            text=self.reply,
            model="fake",
            input_tokens=1,
            output_tokens=max(1, len(self.reply.split())),
        )


class FakeTranscriber:
    """Canned transcriber for tests/`KILNWORKS_FAKE_PROVIDERS`; records every call."""

    def __init__(self, reply: str = "This is a fake transcript."):
        self.reply = reply
        self.calls: list[tuple[str, str, int]] = []

    def transcribe(self, media: bytes, mime: str, name: str) -> Completion:
        self.calls.append((mime, name, len(media)))
        return Completion(
            text=self.reply,
            model="fake",
            input_tokens=1,
            output_tokens=max(1, len(self.reply.split())),
        )
