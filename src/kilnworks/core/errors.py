class ProviderError(Exception):
    """A provider call failed for good. Message always names the provider."""

    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(f"{provider}: {message}")


class TransientProviderError(ProviderError):
    """A provider failure worth retrying (rate limit, timeout, 5xx)."""
