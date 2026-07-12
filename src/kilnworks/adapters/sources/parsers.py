from pathlib import Path

from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from pypdf import PdfReader

TEXT_SUFFIXES = {".md", ".txt"}
PARSED_SUFFIXES = {".pdf", ".docx", ".html", ".htm"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | PARSED_SUFFIXES
MAX_TEXT_CHARS = 10_000_000


def parse_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8")
    elif suffix == ".pdf":
        text = _parse_pdf(path)
    elif suffix == ".docx":
        text = _parse_docx(path)
    elif suffix in {".html", ".htm"}:
        text = _parse_html(path)
    else:
        raise ValueError(f"unsupported file type: {path.suffix}")

    if len(text) > MAX_TEXT_CHARS:
        msg = (
            f"document too large: extracted {len(text)} chars "
            f"exceeds {MAX_TEXT_CHARS}"
        )
        raise ValueError(msg)
    return text


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
