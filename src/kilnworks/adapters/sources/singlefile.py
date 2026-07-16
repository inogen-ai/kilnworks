from collections.abc import Iterator, Sequence
from pathlib import Path

from kilnworks.adapters.sources.parsers import parse_file
from kilnworks.core.models import Document, SourceFailure
from kilnworks.core.ports import MediaExtractor


class SingleFileSource:
    """Yields exactly one document (or one failure) for a stored upload."""

    def __init__(self, path: Path, acl_tags: Sequence[str] = ("public",),
                 title: str | None = None, media: MediaExtractor | None = None):
        self._path = path
        self._acl_tags = list(acl_tags)
        self._title = title
        self._media = media

    def documents(self) -> Iterator[Document | SourceFailure]:
        uri = self._path.resolve().as_uri()
        try:
            parsed = parse_file(self._path, media=self._media)
        except Exception as exc:
            yield SourceFailure(source_uri=uri, error=str(exc))
            return
        yield Document(
            source_uri=uri,
            title=self._title or self._path.stem,
            text=parsed.text,
            acl_tags=self._acl_tags,
            extraction_usage=parsed.usage,
            metadata=parsed.metadata,
        )
