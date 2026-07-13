import csv
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from openpyxl import load_workbook
from pypdf import PdfReader

from kilnworks.adapters.media.audio import VIDEO_SUFFIXES, extract_audio
from kilnworks.core.models import Completion
from kilnworks.core.ports import MediaExtractor

TEXT_SUFFIXES = {".md", ".txt"}
PARSED_SUFFIXES = {".pdf", ".docx", ".html", ".htm"}
TABLE_SUFFIXES = {".csv", ".tsv", ".xlsx"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MEDIA_SUFFIXES = {".mp3", ".wav", ".m4a", ".mp4", ".mov"}
SUPPORTED_SUFFIXES = (
    TEXT_SUFFIXES | PARSED_SUFFIXES | TABLE_SUFFIXES | IMAGE_SUFFIXES | MEDIA_SUFFIXES
)
MAX_TEXT_CHARS = 10_000_000

_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_MEDIA_MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


class MediaProviderRequired(Exception):
    """Raised by `parse_file` when an image/audio/video file needs a vision or
    transcription provider that isn't configured. Sources catch this the same way
    as any other per-file parse error and surface it as a `SourceFailure`, so one
    unconfigured media file never stops the rest of a batch from ingesting."""

    def __init__(self, suffix: str, kind: str):
        env_var = (
            "KILNWORKS_VISION_PROVIDER" if kind == "vision" else "KILNWORKS_TRANSCRIPTION_PROVIDER"
        )
        super().__init__(
            f"ingesting {suffix} files requires {env_var} to be configured"
        )
        self.suffix = suffix
        self.kind = kind


@dataclass
class ParsedContent:
    """`parse_file`'s return value: the extracted `text` plus any Completions
    spent producing it (vision/transcription calls). Empty `usage` for
    text/tables/pdf/etc. — those never call a paid provider."""

    text: str
    usage: list[Completion] = field(default_factory=list)


def parse_file(path: Path, media: MediaExtractor | None = None) -> ParsedContent:
    suffix = path.suffix.lower()
    usage: list[Completion] = []
    if suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8")
    elif suffix == ".pdf":
        text = _parse_pdf(path)
    elif suffix == ".docx":
        text = _parse_docx(path)
    elif suffix in {".html", ".htm"}:
        text = _parse_html(path)
    elif suffix == ".csv":
        text = _parse_csv(path)
    elif suffix == ".tsv":
        text = _parse_tsv(path)
    elif suffix == ".xlsx":
        text = _parse_xlsx(path)
    elif suffix in IMAGE_SUFFIXES:
        text, completion = _extract_image(path, suffix, media)
        usage.append(completion)
    elif suffix in MEDIA_SUFFIXES:
        text, completion = _extract_media(path, suffix, media)
        usage.append(completion)
    else:
        raise ValueError(f"unsupported file type: {path.suffix}")

    if len(text) > MAX_TEXT_CHARS:
        msg = (
            f"document too large: extracted {len(text)} chars "
            f"exceeds {MAX_TEXT_CHARS}"
        )
        raise ValueError(msg)
    return ParsedContent(text=text, usage=usage)


def _check_media_size(path: Path, media: MediaExtractor) -> None:
    size = path.stat().st_size
    if size > media.max_bytes:
        raise ValueError(
            f"media file too large: {path.name} is {size} bytes, exceeding "
            f"KILNWORKS_MAX_MEDIA_BYTES ({media.max_bytes})"
        )


def _extract_image(
    path: Path, suffix: str, media: MediaExtractor | None
) -> tuple[str, Completion]:
    if media is None or media.vision is None:
        raise MediaProviderRequired(suffix, "vision")
    _check_media_size(path, media)
    mime = _IMAGE_MIME_TYPES.get(suffix, "application/octet-stream")
    completion = media.vision.describe(path.read_bytes(), mime, path.name)
    tagged = completion.model_copy(update={"context": "vision"})
    return tagged.text, tagged


def _extract_media(
    path: Path, suffix: str, media: MediaExtractor | None
) -> tuple[str, Completion]:
    if media is None or media.transcription is None:
        raise MediaProviderRequired(suffix, "transcription")
    _check_media_size(path, media)
    raw = path.read_bytes()
    if suffix in VIDEO_SUFFIXES:
        # Whisper-family APIs/models expect audio, not a video container; pull the
        # audio track out with ffmpeg first (extract_audio is a no-op passthrough
        # for suffixes that are already audio). The extracted bytes are WAV, so the
        # name handed to the transcriber must end in .wav too: OpenAI's transcription
        # endpoint validates the multipart filename's EXTENSION against its allowed
        # set (which excludes .mov), and would 400 a valid WAV body carrying a .mov name.
        audio_bytes = extract_audio(raw, suffix)
        mime = "audio/wav"
        transcribe_name = f"{path.stem}.wav"
    else:
        audio_bytes = raw
        mime = _MEDIA_MIME_TYPES.get(suffix, "application/octet-stream")
        transcribe_name = path.name
    completion = media.transcription.transcribe(audio_bytes, mime, transcribe_name)
    tagged = completion.model_copy(update={"context": "transcription"})
    return tagged.text, tagged


def _parse_pdf(path: Path) -> str:
    reader = PdfReader(path)
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()


def _parse_docx(path: Path) -> str:
    document = DocxDocument(str(path))
    return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())


def _parse_html(path: Path) -> str:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return "\n\n".join(soup.stripped_strings)


def _parse_csv(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
        rows = list(csv.reader(handle, delimiter=delimiter))
    return _rows_to_text(rows)


def _parse_tsv(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle, delimiter="\t"))
    return _rows_to_text(rows)


def _parse_xlsx(path: Path) -> str:
    # read_only streams sheet XML row-by-row instead of loading the whole
    # workbook into memory; data_only surfaces cached formula results.
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sections = []
        for sheet in workbook.worksheets:
            rows = [[_cell_to_str(v) for v in row] for row in sheet.iter_rows(values_only=True)]
            sheet_text = _rows_to_text(rows)
            if sheet_text:
                sections.append(f"# Sheet: {sheet.title}\n{sheet_text}")
        return "\n\n".join(sections)
    finally:
        workbook.close()


def _cell_to_str(value: object) -> str:
    return "" if value is None else str(value)


def _rows_to_text(rows: list[list[str]]) -> str:
    """Render rows as `label: value | label: value ...` using the first
    non-empty row as the header; falls back to positional `colN` labels
    wherever a header cell is blank or missing. Blank cells and fully-empty
    rows are skipped.
    """
    non_empty_rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not non_empty_rows:
        return ""

    header, data_rows = non_empty_rows[0], non_empty_rows[1:]
    lines = []
    for row in data_rows:
        parts = [
            f"{_column_label(header, index)}: {value}"
            for index, cell in enumerate(row)
            if (value := cell.strip())
        ]
        if parts:
            lines.append(" | ".join(parts))
    return "\n".join(lines)


def _column_label(header: list[str], index: int) -> str:
    if index < len(header) and header[index].strip():
        return header[index].strip()
    return f"col{index + 1}"
