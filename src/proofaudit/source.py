from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .models import ConfigError, ItemSpec, SourceDocument


_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
    }
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".tex", ".latex"}:
        return "tex"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    return "text"


def load_document(path: Path, kind: str | None = None) -> SourceDocument:
    path = path.resolve()
    if not path.is_file():
        raise ConfigError(f"source not found: {path}")
    kind = (kind or detect_kind(path)).lower()
    if kind == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ConfigError(
                "PDF input requires pypdf; install with "
                "'pip install danus-based-proof-audit[pdf]'"
            ) from exc
        reader = PdfReader(str(path))
        pages = [((page.extract_text() or "").translate(_LIGATURES)) for page in reader.pages]
        if not pages:
            raise ConfigError(f"PDF has no pages: {path}")
        return SourceDocument(path, kind, pages, "pages")

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines:
        lines = [""]
    return SourceDocument(path, kind, lines, "lines")


def item_hash(document: SourceDocument, item: ItemSpec) -> str:
    return sha256_text(document.extract(item))


_MD_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_TEX_SECTION = re.compile(
    r"^\s*\\(?P<kind>section|subsection|subsubsection)\*?"
    r"\{(?P<title>.+?)\}\s*$"
)
_TEX_THEOREM = re.compile(
    r"^\s*\\begin\{(?P<kind>definition|theorem|lemma|proposition|corollary|remark)\}"
    r"(?:\[(?P<title>.*?)\])?"
)
_PDF_SECTION = re.compile(r"^(?P<number>\d+)\.\s+(?P<title>[A-Z][^\n]{2,})$")


def _clean_tex_title(value: str) -> str:
    value = re.sub(r"\\texorpdfstring\{([^{}]*)\}\{[^{}]*\}", r"\1", value)
    value = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", "", value)
    return re.sub(r"[{}$]", "", value).strip() or "Untitled item"


def suggest_items(document: SourceDocument) -> tuple[ItemSpec, ...]:
    if document.unit_name == "pages":
        anchors: list[tuple[int, str]] = []
        for page_number, page in enumerate(document.units, 1):
            for line in page.splitlines():
                match = _PDF_SECTION.match(" ".join(line.split()))
                if match:
                    anchors.append(
                        (
                            page_number,
                            f"{match.group('number')}. {match.group('title').strip()}",
                        )
                    )
                    break
        if not anchors:
            return tuple(
                ItemSpec(f"{page:03d}", f"PDF page {page}", "pages", page, page)
                for page in range(1, len(document.units) + 1)
            )
        if anchors[0][0] > 1:
            anchors.insert(0, (1, "Front matter"))
        items: list[ItemSpec] = []
        for index, (start, title) in enumerate(anchors):
            next_start = anchors[index + 1][0] if index + 1 < len(anchors) else len(document.units) + 1
            end = max(start, next_start - 1)
            items.append(ItemSpec(f"{index + 1:03d}", title, "pages", start, end))
        return tuple(items)

    anchors: list[tuple[int, str]] = []
    for line_number, line in enumerate(document.units, 1):
        if document.kind == "markdown":
            match = _MD_HEADING.match(line)
            if match:
                anchors.append((line_number, match.group(2).strip()))
        elif document.kind == "tex":
            match = _TEX_SECTION.match(line)
            if match:
                anchors.append(
                    (
                        line_number,
                        f"{match.group('kind').title()}: "
                        f"{_clean_tex_title(match.group('title'))}",
                    )
                )
                continue
            match = _TEX_THEOREM.match(line)
            if match:
                title = match.group("kind").title()
                if match.group("title"):
                    title += f": {_clean_tex_title(match.group('title'))}"
                anchors.append((line_number, title))
    if not anchors:
        return (ItemSpec("001", "Complete proof", "lines", 1, len(document.units)),)
    if anchors[0][0] > 1:
        anchors.insert(0, (1, "Preamble"))
    return tuple(
        ItemSpec(
            f"{index + 1:03d}",
            title,
            "lines",
            start,
            (anchors[index + 1][0] - 1 if index + 1 < len(anchors) else len(document.units)),
        )
        for index, (start, title) in enumerate(anchors)
    )
