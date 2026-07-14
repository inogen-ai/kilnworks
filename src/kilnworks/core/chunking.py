import re
from dataclasses import dataclass

# A page-boundary marker line emitted by the PDF parser (`_parse_pdf`) at the START of
# each page, e.g. `[[page:3]]`. The chunker treats it as a flush point (so a chunk never
# spans a page boundary), tags the following section with the page, and strips the marker
# so it never pollutes chunk text. Non-paginated formats never emit these lines, so their
# chunk output is unchanged.
_PAGE_MARKER_RE = re.compile(r"^\[\[page:(\d+)\]\]$")


@dataclass(frozen=True)
class ChunkSpan:
    text: str
    heading_path: tuple[str, ...]
    page: int | None = None


class HeadingAwareChunker:
    """Split markdown/plain text into chunks, tracking the markdown heading hierarchy."""

    def __init__(self, max_chars: int = 1200, overlap_chars: int = 150):
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        if overlap_chars < 0:
            raise ValueError("overlap_chars must be non-negative")
        if overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def chunk(self, text: str) -> list[ChunkSpan]:
        spans: list[ChunkSpan] = []
        for heading_path, body, page in self._split_by_headings(text):
            for piece in self._window(body):
                spans.append(ChunkSpan(text=piece, heading_path=heading_path, page=page))
        return spans

    def _split_by_headings(
        self, text: str
    ) -> list[tuple[tuple[str, ...], str, int | None]]:
        sections: list[tuple[tuple[str, ...], str, int | None]] = []
        stack: list[tuple[int, str]] = []
        lines: list[str] = []
        in_fence = False
        # The page in effect for the content currently accumulating in `lines`. Stays
        # None when the text carries no page markers (all non-paginated formats), which
        # keeps their chunk output byte-identical to the pre-page behavior.
        current_page: int | None = None

        def flush() -> None:
            body = "\n".join(lines).strip()
            if body:
                sections.append((tuple(title for _, title in stack), body, current_page))
            lines.clear()

        for line in text.splitlines():
            stripped = line.strip()
            # A page-boundary marker forces a flush (so no section spans two pages),
            # then updates the current page for the section that follows it. The marker
            # line itself is dropped, never appended to `lines`.
            marker = _PAGE_MARKER_RE.match(stripped)
            if marker:
                flush()
                current_page = int(marker.group(1))
                continue
            # Toggle fence state if line is a fence marker (``` or ~~~)
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = not in_fence
                lines.append(line)
                continue
            # Only treat as heading if not inside a fence
            if not in_fence and stripped.startswith("#"):
                level = len(stripped) - len(stripped.lstrip("#"))
                title = stripped[level:].strip()
                if 1 <= level <= 6 and title:
                    flush()
                    while stack and stack[-1][0] >= level:
                        stack.pop()
                    stack.append((level, title))
                    continue
            lines.append(line)
        flush()
        return sections

    def _window(self, body: str) -> list[str]:
        if len(body) <= self.max_chars:
            return [body]
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
        split_paragraphs: list[str] = []
        for para in paragraphs:
            while len(para) > self.max_chars:
                split_paragraphs.append(para[: self.max_chars])
                para = para[self.max_chars - self.overlap_chars :]
            split_paragraphs.append(para)
        pieces: list[str] = []
        current = ""
        for para in split_paragraphs:
            candidate = f"{current}\n\n{para}".strip()
            if len(candidate) > self.max_chars and current:
                pieces.append(current)
                tail = current[-self.overlap_chars :] if self.overlap_chars else ""
                current = (tail + "\n\n" + para).strip()
            else:
                current = candidate
        if current:
            pieces.append(current)
        return pieces
