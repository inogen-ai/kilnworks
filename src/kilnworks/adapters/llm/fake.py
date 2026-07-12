from kilnworks.core.models import Completion


class FakeLLM:
    """Canned LLM for tests; records every call."""

    def __init__(self, reply: str = "Based on the context, yes. [1]"):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> Completion:
        self.calls.append((system, user))
        return Completion(
            text=self.reply,
            model="fake",
            input_tokens=len(system.split()) + len(user.split()),
            output_tokens=max(1, len(self.reply.split())),
        )

    def stream(self, system: str, user: str):
        self.calls.append((system, user))
        words = self.reply.split(" ")
        for i, word in enumerate(words):
            yield word if i == len(words) - 1 else word + " "
        yield Completion(
            text=self.reply,
            model="fake",
            input_tokens=len(system.split()) + len(user.split()),
            output_tokens=max(1, len(self.reply.split())),
        )
