"""Document text extraction module for LLM Council attachments.

Extracts text from PDF, DOCX, TXT, Markdown, and CSV files.
Preserves page/section references and handles edge cases safely.
"""

import csv
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import docx
import pypdf

# ---------------------------------------------------------------------------
# Extraction quality configuration
# ---------------------------------------------------------------------------

MAX_EXTRACTED_CHARS = 8000
MAX_CSV_ROWS = 100
MAX_CSV_CELL_CHARS = 200


# ---------------------------------------------------------------------------
# Extraction result metadata
# ---------------------------------------------------------------------------

@dataclass
class ExtractionResult:
    attachment_id: str
    original_filename: str
    media_type: str
    file_size: int
    text: str = ""
    image_reference: Optional[str] = None
    status: str = "success"
    error: Optional[str] = None
    page_references: List[str] = field(default_factory=list)
    csv_headers: Optional[List[str]] = field(default=None)
    csv_rows_included: int = 0


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_pdf_text(file_path: str, attachment_id: str, original_filename: str) -> ExtractionResult:
    result = ExtractionResult(
        attachment_id=attachment_id,
        original_filename=original_filename,
        media_type="application/pdf",
        file_size=0,
        status="error",
    )
    try:
        with open(file_path, "rb") as f:
            pdf = pypdf.PdfReader(f)
    except pypdf.PdfReadError as e:
        result.status = "error"
        result.error = f"Could not read PDF: {e}"
        return result
    except Exception as e:
        result.status = "error"
        result.error = f"Unexpected error opening PDF: {e}"
        return result

    if pdf.is_encrypted:
        result.status = "error"
        result.error = "PDF is encrypted and cannot be read without a password"
        return result

    text_parts: List[str] = []
    page_refs: List[str] = []

    for page_num, page in enumerate(pdf.pages, start=1):
        try:
            page_text = page.extract_text()
        except Exception:
            page_text = ""

        if page_text:
            normalized = " ".join(page_text.split())
            text_parts.append(f"[Page {page_num}] {normalized}")
            page_refs.append(f"[Page {page_num}]")

    result.text = "\n\n".join(text_parts) if text_parts else ""
    result.page_references = page_refs

    if not result.text.strip():
        result.status = "empty"
        result.error = "No extractable text found — this may be a scanned/image-only PDF"
    else:
        result.status = "success"

    return result


# ---------------------------------------------------------------------------
# DOCX extraction
# ---------------------------------------------------------------------------

def extract_docx_text(file_path: str, attachment_id: str, original_filename: str) -> ExtractionResult:
    result = ExtractionResult(
        attachment_id=attachment_id,
        original_filename=original_filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file_size=0,
        status="error",
    )
    try:
        doc = docx.Document(file_path)
    except Exception as e:
        result.status = "error"
        result.error = f"Could not open DOCX: {e}"
        return result

    result.file_size = sum(len(paragraph.text) for paragraph in doc.paragraphs)

    text_parts: List[str] = []
    page_refs: List[str] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            text_parts.append("")
            continue
        if len(text) < 100 and text[0].isupper():
            text_parts.append(f"【Heading】{text}")
        else:
            text_parts.append(text)

    table_text_parts: List[str] = []
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                table_text_parts.append(" | ".join(cells))

    if table_text_parts:
        text_parts.append("")
        text_parts.append("--- Tables ---")
        text_parts.extend(table_text_parts)
        page_refs.append("[Table extracted]")

    result.text = "\n\n".join(text_parts) if text_parts else ""
    result.page_references = page_refs
    result.status = "success" if result.text.strip() else "empty"
    return result


# ---------------------------------------------------------------------------
# TXT and Markdown extraction
# ---------------------------------------------------------------------------

def extract_txt_markdown_text(file_path: str, attachment_id: str, original_filename: str) -> ExtractionResult:
    result = ExtractionResult(
        attachment_id=attachment_id,
        original_filename=original_filename,
        media_type="text/plain",
        file_size=0,
        status="error",
    )
    texts: List[str] = []
    for encoding in ["utf-8", "latin-1", "cp1252", "utf-16"]:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                content = f.read()
            texts.append(content)
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception:
            continue

    if not texts:
        result.status = "error"
        result.error = "Could not decode file with any supported encoding"
        return result

    content = texts[0]
    result.file_size = len(content.encode("utf-8"))

    lines = content.splitlines()
    text_parts: List[str] = []
    page_refs: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            text_parts.append("")
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            text_parts.append(stripped)
            continue
        if stripped.startswith("#"):
            level = 0
            for ch in stripped:
                if ch == "#":
                    level += 1
                else:
                    break
            text_parts.append(f"[Heading level {level}] {stripped[level:].strip()}")
            page_refs.append(f"[Heading level {level}]")
            continue
        text_parts.append(stripped)

    result.text = "\n".join(text_parts) if text_parts else ""
    result.page_references = page_refs
    result.status = "success" if result.text.strip() else "empty"
    return result


# ---------------------------------------------------------------------------
# CSV extraction
# ---------------------------------------------------------------------------

def extract_csv_text(file_path: str, attachment_id: str, original_filename: str) -> ExtractionResult:
    result = ExtractionResult(
        attachment_id=attachment_id,
        original_filename=original_filename,
        media_type="text/csv",
        file_size=0,
        status="error",
    )
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            rows = list(reader)
    except Exception as e:
        result.status = "error"
        result.error = f"Could not open CSV: {e}"
        return result

    result.file_size = sum(len(row) for row in rows) if rows else 0

    if not rows:
        result.status = "empty"
        result.error = "CSV file is empty"
        return result

    headers = [h.strip() if h else "" for h in rows[0]]
    csv_headers = headers

    text_parts: List[str] = []
    page_refs: List[str] = []
    header_line = ", ".join(headers)
    text_parts.append(f"Headers: {header_line}")
    page_refs.append("[CSV headers]")

    data_rows = rows[1:]
    included_rows = 0

    for row in data_rows:
        if included_rows >= MAX_CSV_ROWS:
            remaining = len(data_rows) - MAX_CSV_ROWS
            text_parts.append(f"... and {remaining} more rows truncated")
            break
        truncated_cells = []
        for cell in row:
            if len(cell) > MAX_CSV_CELL_CHARS:
                truncated_cells.append(cell[:MAX_CSV_CELL_CHARS] + "...")
            else:
                truncated_cells.append(cell)
        row_line = ", ".join(truncated_cells)
        text_parts.append(row_line)
        included_rows += 1

    result.csv_headers = csv_headers
    result.csv_rows_included = included_rows

    result.text = "\n".join(text_parts) if text_parts else ""
    result.status = "success" if result.text.strip() else "empty"
    return result


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------

def extract_attachment(
    file_path: str,
    attachment_id: str,
    original_filename: str,
    media_type: str,
) -> ExtractionResult:
    """Extract text from an attachment based on its media type."""
    # Check for fully-specified media types first (before splitting on /)
    full_mt = media_type.lower()

    if full_mt == "application/pdf":
        return extract_pdf_text(file_path, attachment_id, original_filename)
    elif full_mt == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_docx_text(file_path, attachment_id, original_filename)
    elif full_mt == "text/csv":
        return extract_csv_text(file_path, attachment_id, original_filename)

    # Fall back to prefix-based dispatch (splitting on /)
    mt = full_mt.split("/")[0] if "/" in full_mt else ""

    if mt == "application":
        # Could be PDF or DOCX — check extension
        ext = original_filename.lower().split(".")[-1] if "." in original_filename else ""
        if ext == "pdf":
            return extract_pdf_text(file_path, attachment_id, original_filename)
        elif ext == "docx":
            return extract_docx_text(file_path, attachment_id, original_filename)
        else:
            result = ExtractionResult(
                attachment_id=attachment_id,
                original_filename=original_filename,
                media_type=media_type,
                file_size=0,
                status="error",
                error=f"Unsupported application type: {media_type}",
            )
            return result

    elif mt == "image":
        # Image — return a reference, no text extraction
        result = ExtractionResult(
            attachment_id=attachment_id,
            original_filename=original_filename,
            media_type=media_type,
            file_size=os.path.getsize(file_path) if os.path.exists(file_path) else 0,
            status="success",
            image_reference=f"image:{attachment_id}",
        )
        return result

    elif mt == "text":
        # TXT or Markdown — check extension
        base_ext = original_filename.lower().split(".")[-1] if "." in original_filename else ""
        if base_ext in ("md", "markdown"):
            return extract_txt_markdown_text(file_path, attachment_id, original_filename)
        else:
            return extract_txt_markdown_text(file_path, attachment_id, original_filename)

    else:
        result = ExtractionResult(
            attachment_id=attachment_id,
            original_filename=original_filename,
            media_type=media_type,
            file_size=0,
            status="error",
            error=f"Unsupported media type: {media_type}",
        )
        return result
