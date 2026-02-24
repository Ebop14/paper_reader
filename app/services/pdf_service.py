import re
import fitz  # PyMuPDF
from pathlib import Path
from app.models import PaperSection

SECTION_PATTERNS = [
    r"^(?:(?:\d+\.?\s+)?(?:abstract|introduction|background|related\s+work|methodology|methods?|"
    r"approach|experiments?|results?|discussion|conclusion|acknowledgments?|references|appendix))",
]
SECTION_RE = re.compile("|".join(SECTION_PATTERNS), re.IGNORECASE | re.MULTILINE)

CHUNK_SIZE = 2000  # chars target per chunk


def extract_text(pdf_path: Path) -> tuple[str, int]:
    """Extract all text from a PDF. Returns (full_text, num_pages)."""
    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n".join(pages), len(pages)


def detect_sections(text: str) -> list[tuple[str, str]]:
    """Split text into (title, body) sections based on common headings."""
    matches = list(SECTION_RE.finditer(text))
    if not matches:
        return [("Full Text", text)]

    sections = []
    for i, m in enumerate(matches):
        title = m.group().strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append((title, body))

    # Include any text before the first section heading
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.insert(0, ("Preamble", preamble))

    return sections


def chunk_text(text: str, max_chars: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for s in sentences:
        if len(current) + len(s) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = s
        else:
            current = f"{current} {s}" if current else s
    if current.strip():
        chunks.append(current.strip())
    return chunks


def process_pdf(pdf_path: Path) -> tuple[list[PaperSection], int]:
    """Extract text, detect sections, chunk into PaperSections."""
    full_text, num_pages = extract_text(pdf_path)
    sections = detect_sections(full_text)

    paper_sections: list[PaperSection] = []
    idx = 0
    for title, body in sections:
        for chunk in chunk_text(body):
            paper_sections.append(
                PaperSection(title=title, text=chunk, chunk_index=idx)
            )
            idx += 1

    return paper_sections, num_pages
