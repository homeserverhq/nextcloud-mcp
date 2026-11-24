"""Turn WebDAV file bytes into readable text.

`read_file` must never put raw binary into an MCP response: clients cap tool
output and then fail to parse the truncated JSON, and small binaries only
waste context on mojibake. This module extracts text from common document
formats and reports everything else as metadata.
"""

from __future__ import annotations

import io
import logging
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from email import policy
from email.parser import BytesParser
from html import unescape


MAX_PART_BYTES = 64 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024
MAX_LEGACY_BYTES = 64 * 1024 * 1024
LEGACY_TIMEOUT = 20

PDF_MAGIC = b"%PDF-"
OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")

TEXT_CONTENT_TYPES = {
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
    "application/x-javascript",
    "application/x-ndjson",
    "application/yaml",
    "application/x-yaml",
    "application/sql",
    "application/rtf",
}

TEXT_SUFFIXES = {
    ".txt", ".text", ".log", ".md", ".markdown", ".csv", ".tsv", ".json",
    ".jsonl", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".toml",
    ".html", ".htm", ".css", ".scss", ".js", ".mjs", ".ts", ".py", ".sh",
    ".bash", ".sql", ".ics", ".vcf", ".srt", ".vtt", ".rst", ".tex",
    ".diff", ".patch", ".env", ".properties",
}

BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".ico", ".heic", ".heif", ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z",
    ".rar", ".tar", ".mp3", ".wav", ".flac", ".ogg", ".mp4", ".mkv", ".mov",
    ".avi", ".webm", ".exe", ".dll", ".so", ".bin", ".iso", ".woff",
    ".woff2", ".ttf", ".otf", ".msg", ".pages", ".numbers", ".key",
}

LEGACY_TYPES = {
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
}

OOXML_SUFFIXES = {
    ".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".xltx", ".pptx", ".pptm",
    ".potx", ".ppsx",
}

EXTRACT_LABELS = {
    "pdf": "PDF",
    "docx": "DOCX",
    "xlsx": "XLSX",
    "pptx": "PPTX",
    "odf": "ODF",
    "epub": "EPUB",
    "rtf": "RTF",
    "eml": "EML",
    "doc": "DOC",
    "xls": "XLS",
    "ppt": "PPT",
}


def extract_content(
    data: bytes,
    content_type: str = "",
    filename: str = "",
    max_chars: int = 50_000,
    start_page: int | None = None,
    end_page: int | None = None,
) -> dict:
    """Return an MCP-friendly payload with extracted text or metadata."""
    raw_type = content_type or ""
    media_type = raw_type.split(";")[0].strip().lower()
    charset = _charset(raw_type)
    name = os.path.basename(filename or "") or "file"
    suffix = os.path.splitext(name)[1].lower()

    base = {
        "content": None,
        "content_type": media_type or "application/octet-stream",
        "size_bytes": len(data),
        "extracted": "binary",
        "truncated": False,
    }

    if data.startswith(PDF_MAGIC) or media_type == "application/pdf" or suffix == ".pdf":
        return _pdf(data, base, name, max_chars, start_page, end_page)

    if data.startswith(OLE_MAGIC):
        return _ole(data, base, name, media_type, suffix, max_chars)

    if data.startswith(b"PK\x03\x04") or media_type in {
        "application/zip", "application/x-zip-compressed",
    }:
        return _zipped(data, base, name, max_chars)

    if data.startswith(b"{\\rtf") or suffix == ".rtf":
        return _finish_text(
            base, name, _rtf_to_text(data), "rtf", max_chars, strip=True,
        )

    if media_type == "message/rfc822" or suffix == ".eml":
        return _finish_text(
            base, name, _eml_to_text(data), "eml", max_chars, strip=True,
        )

    if _is_text(data, media_type, suffix):
        text, encoding = _decode_text(data, charset)
        result = _finish_text(base, name, text, "text", max_chars, strip=False)
        result["encoding"] = encoding
        return result

    return _binary(base, name)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

def _finish_text(base: dict, name: str, text: str, extracted: str,
                 max_chars: int, strip: bool) -> dict:
    if strip:
        text = text.strip("\n")
    total = len(text)
    truncated = bool(max_chars and max_chars > 0 and total > max_chars)
    content = text[:max_chars] if truncated else text

    out = dict(base)
    out.update(
        extracted=extracted,
        content=content,
        truncated=truncated,
        status="success" if content.strip() else "no_text",
    )
    if truncated:
        out["returned_chars"] = len(content)
        out["total_chars"] = total

    details = []
    if extracted != "text" and content.strip():
        details.append(f"Extracted text from {EXTRACT_LABELS.get(extracted, extracted.upper())}.")
    if truncated:
        details.append(
            f"Truncated to {len(content):,} of {total:,} characters."
        )
    if out["status"] == "no_text":
        out["content"] = None
        out["truncated"] = False
        out.pop("returned_chars", None)
        out.pop("total_chars", None)
        out["message"] = f"No text content found in '{name}'."
    else:
        out["message"] = " ".join(details)
    return out


def _binary(base: dict, name: str) -> dict:
    out = dict(base)
    out.update(
        extracted="binary",
        status="binary",
        message=(
            f"Read file '{name}' ({base['content_type']}, "
            f"{base['size_bytes']:,} bytes). Binary content is not returned."
        ),
        next_steps=(
            "Use search_files to get the file id, then get_direct_download_url "
            "to download and inspect it."
        ),
    )
    return out


def _unsupported(base: dict, name: str, reason: str) -> dict:
    out = dict(base)
    out.update(
        extracted="unsupported",
        status="unsupported",
        message=f"Could not extract text from '{name}': {reason}",
        next_steps=(
            "Use get_direct_download_url with the file id to download it, or "
            "check whether the document is also available in Paperless."
        ),
    )
    return out


def _charset(content_type: str) -> str:
    match = re.search(r"charset\s*=\s*\"?([^\";]+)", content_type or "", re.I)
    return match.group(1).strip().lower() if match else ""


def _decode_text(data: bytes, charset: str) -> tuple[str, str]:
    if charset:
        try:
            return data.decode(charset, errors="replace"), charset
        except LookupError:
            pass
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), "utf-8"


def _is_text(data: bytes, media_type: str, suffix: str) -> bool:
    if media_type.startswith("text/") or media_type.endswith(("+xml", "+json")):
        return True
    if media_type in TEXT_CONTENT_TYPES:
        return True
    if media_type.startswith(("image/", "audio/", "video/")):
        return False
    if suffix in BINARY_SUFFIXES:
        return False
    if suffix in TEXT_SUFFIXES:
        return True
    if media_type not in ("", "application/octet-stream", "binary/octet-stream"):
        return False
    return _decodes_as_text(data)


def _decodes_as_text(data: bytes) -> bool:
    sample = data[:65536]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _paperless_query(name: str) -> str:
    stem = os.path.splitext(name)[0]
    stem = re.sub(r"^\d+[\s_.-]*", "", stem).replace("_", " ").strip()
    return stem or os.path.splitext(name)[0]


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _pdf(data: bytes, base: dict, name: str, max_chars: int,
         start_page: int | None, end_page: int | None) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        return _unsupported(base, name, "PDF support is not installed (pypdf is missing).")

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Could not read PDF '{name}': {exc}") from exc

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise ValueError(
                f"PDF '{name}' is password-protected and cannot be read."
            )

    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise ValueError(f"Could not read PDF '{name}': {exc}") from exc

    if page_count == 0:
        result = _finish_text(base, name, "", "pdf", max_chars, strip=True)
        result["pages"] = 0
        return result

    start = 1 if start_page is None else start_page
    end = page_count if end_page is None else end_page
    if start < 1 or end < 1 or start > page_count or end > page_count or end < start:
        raise ValueError(
            f"Invalid page range {start}-{end} for PDF '{name}', which has "
            f"{page_count} page(s)."
        )

    chunks = []
    for page in reader.pages[start - 1:end]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            chunks.append("")
    text = "\n\n".join(chunks)

    result = _finish_text(base, name, text, "pdf", max_chars, strip=True)
    result["pages"] = page_count
    result["page_range"] = f"{start}-{end}" if (start, end) != (1, page_count) else f"1-{page_count}"

    if result["status"] == "no_text":
        query = _paperless_query(name)
        result["extracted"] = "none"
        result["message"] = (
            f"No text layer found in '{name}'. It is probably a scanned or "
            "image-only PDF."
        )
        result["paperless_query"] = query
        result["next_steps"] = (
            f"This document may already be OCR'd in Paperless. Search Paperless "
            f"for '{query}' (or the exact filename) and read the matching "
            "document there."
        )
    return result


# ---------------------------------------------------------------------------
# OLE (legacy Office and encrypted OOXML)
# ---------------------------------------------------------------------------

def _ole(data: bytes, base: dict, name: str, media_type: str, suffix: str,
         max_chars: int) -> dict:
    fmt = suffix if suffix in {".doc", ".xls", ".ppt"} else LEGACY_TYPES.get(media_type, "")
    if not fmt:
        if suffix in OOXML_SUFFIXES:
            return _unsupported(
                base, name,
                "the file is password-protected (encrypted Office container).",
            )
        return _unsupported(base, name, "unsupported legacy binary document.")

    if fmt == ".xls":
        return _xls(data, base, name, max_chars)

    if len(data) > MAX_LEGACY_BYTES:
        return _unsupported(base, name, "the document is too large to extract.")

    tool = {"doc": "catdoc", "ppt": "catppt"}[fmt.lstrip(".")]
    text = _run_legacy(tool, data, fmt)
    if text is None:
        return _unsupported(
            base, name,
            f"{fmt.lstrip('.').upper()} support is not installed in this image.",
        )
    return _finish_text(base, name, text, fmt.lstrip("."), max_chars, strip=True)


def _run_legacy(tool: str, data: bytes, suffix: str) -> str | None:
    if shutil.which(tool) is None:
        return None
    with tempfile.NamedTemporaryFile(prefix="nc-mcp-", suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        for args in ([tool, "-d", "utf-8", path], [tool, path]):
            try:
                proc = subprocess.run(
                    args, capture_output=True, timeout=LEGACY_TIMEOUT,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout.decode("utf-8", errors="replace")
        return ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _xls(data: bytes, base: dict, name: str, max_chars: int) -> dict:
    try:
        import xlrd
    except ImportError:
        return _unsupported(base, name, "XLS support is not installed (xlrd is missing).")
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as exc:
        raise ValueError(f"Could not read XLS '{name}': {exc}") from exc

    sections = []
    for sheet in book.sheets():
        sections.append(f"## Sheet: {sheet.name}")
        for row_index in range(sheet.nrows):
            values = [
                _xls_value(book, sheet.cell(row_index, col))
                for col in range(sheet.ncols)
            ]
            line = "\t".join(values).rstrip("\t")
            if line:
                sections.append(line)
        sections.append("")
    return _finish_text(base, name, "\n".join(sections), "xls", max_chars, strip=True)


def _xls_value(book, cell) -> str:
    import xlrd

    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return ""
    if cell.ctype == xlrd.XL_CELL_TEXT:
        return str(cell.value)
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate.xldate_as_datetime(cell.value, book.datemode).isoformat(sep=" ")
        except Exception:
            return str(cell.value)
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if cell.value else "FALSE"
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return ""
    if isinstance(cell.value, float) and cell.value.is_integer():
        return str(int(cell.value))
    return str(cell.value)


# ---------------------------------------------------------------------------
# ZIP containers (OOXML, ODF, EPUB)
# ---------------------------------------------------------------------------

def _zipped(data: bytes, base: dict, name: str, max_chars: int) -> dict:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        _check_zip(archive)
        names = set(archive.namelist())
    except (zipfile.BadZipFile, ValueError) as exc:
        return _unsupported(base, name, f"invalid or unsafe archive ({exc}).")

    try:
        if "word/document.xml" in names:
            return _finish_text(
                base, name, _docx_text(archive), "docx", max_chars, strip=True,
            )
        if "xl/workbook.xml" in names:
            return _finish_text(
                base, name, _xlsx_text(archive), "xlsx", max_chars, strip=True,
            )
        if "ppt/presentation.xml" in names:
            return _finish_text(
                base, name, _pptx_text(archive), "pptx", max_chars, strip=True,
            )
        if "content.xml" in names and "mimetype" in names:
            return _finish_text(
                base, name, _odf_text(archive), "odf", max_chars, strip=True,
            )
        if "META-INF/container.xml" in names:
            return _finish_text(
                base, name, _epub_text(archive), "epub", max_chars, strip=True,
            )
    except ET.ParseError as exc:
        return _unsupported(base, name, f"malformed document XML ({exc}).")
    except ValueError as exc:
        return _unsupported(base, name, str(exc))

    return _binary(base, name)


def _check_zip(archive: zipfile.ZipFile) -> None:
    total = 0
    for info in archive.infolist():
        if info.file_size > MAX_PART_BYTES:
            raise ValueError("archive part is too large")
        total += info.file_size
    if total > MAX_ZIP_TOTAL_BYTES:
        raise ValueError("archive expands to too much data")


def _read_part(archive: zipfile.ZipFile, part: str) -> str:
    info = archive.getinfo(part)
    if info.file_size > MAX_PART_BYTES:
        raise ValueError(f"document part {part} is too large")
    data = archive.read(part)
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError(f"document part {part} contains a DTD")
    return data.decode("utf-8", errors="replace")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _element_text(element: ET.Element) -> str:
    parts = []
    for node in element.iter():
        tag = _local(node.tag)
        if tag == "t":
            parts.append(node.text or "")
        elif tag == "tab":
            parts.append("\t")
        elif tag in ("br", "cr"):
            parts.append("\n")
    return "".join(parts)


def _docx_text(archive: zipfile.ZipFile) -> str:
    root = ET.fromstring(_read_part(archive, "word/document.xml"))
    lines = []
    for element in root.iter():
        if _local(element.tag) == "p":
            lines.append(_element_text(element))
    return "\n".join(lines)


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(_read_part(archive, "xl/sharedStrings.xml"))
    strings = []
    for element in root.iter():
        if _local(element.tag) == "si":
            strings.append(_element_text(element))
    return strings


def _xlsx_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    root = ET.fromstring(_read_part(archive, "xl/workbook.xml"))
    rels = {}
    if "xl/_rels/workbook.xml.rels" in archive.namelist():
        rel_root = ET.fromstring(_read_part(archive, "xl/_rels/workbook.xml.rels"))
        for rel in rel_root:
            rels[rel.get("Id")] = rel.get("Target", "")

    sheets = []
    for element in root.iter():
        if _local(element.tag) != "sheet":
            continue
        title = element.get("name") or "Sheet"
        rel_id = next(
            (value for key, value in element.attrib.items()
             if _local(key) == "id"),
            "",
        )
        target = rels.get(rel_id, "")
        if target:
            part = target.lstrip("/")
            if not part.startswith("xl/"):
                part = posixpath.normpath(posixpath.join("xl", part))
        else:
            part = f"xl/worksheets/sheet{len(sheets) + 1}.xml"
        if part in archive.namelist():
            sheets.append((title, part))
    return sheets


def _column_index(ref: str) -> int:
    match = re.match(r"([A-Z]+)", ref or "")
    if not match:
        return 0
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _xlsx_text(archive: zipfile.ZipFile) -> str:
    shared = _xlsx_shared_strings(archive)
    sections = []
    for title, part in _xlsx_sheets(archive):
        sections.append(f"## Sheet: {title}")
        root = ET.fromstring(_read_part(archive, part))
        rows = 0
        for row in root.iter():
            if _local(row.tag) != "row":
                continue
            values: list[str] = []
            for cell in row:
                if _local(cell.tag) != "c":
                    continue
                index = _column_index(cell.get("r", ""))
                value = ""
                kind = cell.get("t")
                if kind == "inlineStr":
                    value = _element_text(cell)
                else:
                    raw = next(
                        (node.text for node in cell
                         if _local(node.tag) == "v"),
                        None,
                    )
                    if kind == "s" and raw is not None:
                        try:
                            value = shared[int(raw)]
                        except (ValueError, IndexError):
                            value = ""
                    elif kind == "b":
                        value = "TRUE" if raw == "1" else "FALSE"
                    else:
                        value = raw or ""
                while len(values) < index:
                    values.append("")
                if len(values) == index:
                    values.append(value)
                else:
                    values[index] = value
            line = "\t".join(values).rstrip("\t")
            if line:
                sections.append(line)
            rows += 1
            if rows >= 5000:
                sections.append("[... rows truncated ...]")
                break
        sections.append("")
    return "\n".join(sections)


def _pptx_text(archive: zipfile.ZipFile) -> str:
    slides = []
    for part in archive.namelist():
        match = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", part)
        if match:
            slides.append((int(match.group(1)), part))
    slides.sort()
    sections = []
    for number, part in slides:
        root = ET.fromstring(_read_part(archive, part))
        lines = []
        for element in root.iter():
            if _local(element.tag) == "p":
                text = _element_text(element)
                if text.strip():
                    lines.append(text)
        sections.append(f"## Slide {number}\n" + "\n".join(lines))
    return "\n\n".join(sections)


def _odf_text(archive: zipfile.ZipFile) -> str:
    mimetype = archive.read("mimetype").decode("utf-8", errors="replace").strip()
    root = ET.fromstring(_read_part(archive, "content.xml"))

    if mimetype.endswith("spreadsheet"):
        sections = []
        current_sheet = None
        for element in root.iter():
            tag = _local(element.tag)
            if tag == "table":
                current_sheet = element.get(
                    "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}name"
                ) or "Sheet"
                sections.append(f"## Sheet: {current_sheet}")
            elif tag == "table-row" and current_sheet is not None:
                cells = [
                    "".join(child.itertext()).strip()
                    for child in element
                    if _local(child.tag) in ("table-cell", "covered-table-cell")
                ]
                line = "\t".join(cells).rstrip("\t")
                if line:
                    sections.append(line)
        return "\n".join(sections)

    if mimetype.endswith("presentation"):
        sections = []
        for element in root.iter():
            if _local(element.tag) == "page":
                text = "".join(element.itertext()).strip()
                if text:
                    title = element.get(
                        "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}name"
                    )
                    sections.append(f"## {title or 'Slide'}\n{text}")
        return "\n\n".join(sections)

    lines = []
    for element in root.iter():
        if _local(element.tag) in ("p", "h"):
            lines.append("".join(element.itertext()).strip())
    return "\n".join(lines)


def _epub_text(archive: zipfile.ZipFile) -> str:
    container = ET.fromstring(_read_part(archive, "META-INF/container.xml"))
    opf_path = ""
    for element in container.iter():
        if _local(element.tag) == "rootfile":
            opf_path = element.get("full-path", "")
            break
    if not opf_path:
        raise ValueError("EPUB container has no rootfile")

    opf = ET.fromstring(_read_part(archive, opf_path))
    base_dir = posixpath.dirname(opf_path)
    manifest = {}
    spine = []
    for element in opf.iter():
        tag = _local(element.tag)
        if tag == "item":
            manifest[element.get("id", "")] = element.get("href", "")
        elif tag == "itemref":
            spine.append(element.get("idref", ""))

    chapters = []
    for ref in spine:
        href = manifest.get(ref)
        if not href:
            continue
        part = posixpath.normpath(posixpath.join(base_dir, href))
        if part in archive.namelist():
            chapters.append(_html_to_text(_read_part(archive, part)))
    if not chapters:
        for part in sorted(archive.namelist()):
            if part.lower().endswith((".xhtml", ".html", ".htm")):
                chapters.append(_html_to_text(_read_part(archive, part)))
    return "\n\n".join(chapter for chapter in chapters if chapter)


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style|head|title)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?s)<[^>]+>", " ", markup)
    text = unescape(markup)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# RTF and EML
# ---------------------------------------------------------------------------

_RTF_SKIP = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "objdata",
    "header", "footer", "headerl", "headerr", "footerl", "footerr",
    "footnote", "field", "fldinst", "listtable", "listoverridetable",
    "rsidtbl", "generator", "xmlnstbl", "themedata", "colorschememapping",
    "datastore", "latentstyles", "filetbl", "revtbl", "pgdsctbl",
}


def _rtf_to_text(data: bytes) -> str:
    raw = data.decode("latin-1", errors="replace")
    out: list[str] = []
    i = 0
    while i < len(raw):
        char = raw[i]
        if char == "{":
            match = re.match(r"\{\\\*?\\?([a-zA-Z]+)", raw[i:])
            if match and match.group(1).lower() in _RTF_SKIP:
                depth = 0
                end = i
                while end < len(raw):
                    if raw[end] == "{":
                        depth += 1
                    elif raw[end] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    end += 1
                i = end + 1
                continue
            i += 1
            continue
        if char == "}":
            i += 1
            continue
        if char == "\\":
            match = re.match(r"\\'([0-9a-fA-F]{2})", raw[i:])
            if match:
                out.append(bytes([int(match.group(1), 16)]).decode(
                    "cp1252", errors="replace"))
                i += match.end()
                continue
            match = re.match(r"\\u(-?\d+)\??", raw[i:])
            if match:
                code = int(match.group(1))
                if code < 0:
                    code += 65536
                out.append(chr(code))
                i += match.end()
                continue
            match = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", raw[i:])
            if match:
                word = match.group(1).lower()
                i += match.end()
                if word == "par":
                    out.append("\n\n")
                elif word in ("line", "row"):
                    out.append("\n")
                elif word == "tab":
                    out.append("\t")
                elif word == "emdash":
                    out.append("\u2014")
                elif word == "endash":
                    out.append("\u2013")
                elif word == "bullet":
                    out.append("\u2022")
                elif word in ("lquote", "rquote"):
                    out.append("'")
                elif word in ("ldblquote", "rdblquote"):
                    out.append('"')
                continue
            if i + 1 < len(raw) and raw[i + 1] in "\\{}~-":
                escaped = raw[i + 1]
                if escaped == "~":
                    out.append(" ")
                elif escaped != "-":
                    out.append(escaped)
                i += 2
                continue
            i += 1
            continue
        if char in "\r\n":
            i += 1
            continue
        out.append(char)
        i += 1

    text = "".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _eml_to_text(data: bytes) -> str:
    message = BytesParser(policy=policy.default).parsebytes(data)
    headers = [
        f"Subject: {message.get('Subject', '')}",
        f"From: {message.get('From', '')}",
        f"To: {message.get('To', '')}",
        f"Date: {message.get('Date', '')}",
    ]
    bodies = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() != "text/plain":
                continue
            bodies.append(_as_text(part.get_content()))
        if not any(body.strip() for body in bodies):
            for part in message.walk():
                if part.get_content_type() != "text/html":
                    continue
                bodies.append(_html_to_text(_as_text(part.get_content())))
    elif message.get_content_type() == "text/html":
        bodies.append(_html_to_text(_as_text(message.get_content())))
    else:
        bodies.append(_as_text(message.get_content()))
    return "\n".join(headers) + "\n\n" + "\n\n".join(bodies)


def _as_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
