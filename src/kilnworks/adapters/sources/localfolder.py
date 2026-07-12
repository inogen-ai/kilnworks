from collections.abc import Iterator, Sequence
from pathlib import Path

from kilnworks.adapters.sources.parsers import SUPPORTED_SUFFIXES, parse_file
from kilnworks.core.models import Document, SourceFailure


class LocalFolderSource:
    """Yields documents from supported files under a folder; bad files become failures."""

    def __init__(self, root: Path, acl_tags: Sequence[str] = ("public",)):
        self._root = root
        self._acl_tags = list(acl_tags)

    def documents(self) -> Iterator[Document | SourceFailure]:
        for path in sorted(self._root.rglob("*")):
            if not (path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES):
                continue
            uri = path.resolve().as_uri()
            try:
                text = parse_file(path)
            except Exception as exc:  # one bad file never stops the walk
                yield SourceFailure(source_uri=uri, error=str(exc))
                continue
            yield Document(
                source_uri=uri, title=path.stem, text=text, acl_tags=self._acl_tags
            )
