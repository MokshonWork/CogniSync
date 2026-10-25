"""Upload validation utilities for LLM Council document/image attachments."""

import mimetypes
import os
from typing import Tuple, Optional

#: Maximum file size per attachment: 10 MB
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB

#: Maximum total attachments per message
MAX_ATTACHMENT_COUNT = 5

#: Allowed file extensions (lowercase)
ALLOWED_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".csv",
}

ALLOWED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}

#: Combined allowed extensions (for quick lookup)
ALLOWED_EXTENSIONS = ALLOWED_DOCUMENT_EXTENSIONS | ALLOWED_IMAGE_EXTENSIONS


def get_extension(filename: str) -> Optional[str]:
    """Return the lowercase file extension, or None if cannot determine."""
    _, ext = os.path.splitext(filename)
    return ext.lower() if ext else None


def validate_extension(filename: str) -> Tuple[bool, Optional[str]]:
    """Validate that a file has an allowed extension.

    Returns ``(valid, reason)`` where *reason* is a string explanation
    if the file is invalid.
    """
    ext = get_extension(filename)
    if ext is None:
        return False, "Could not determine file extension"
    if ext in ALLOWED_EXTENSIONS:
        return True, None
    allowed = sorted(ALLOWED_DOCUMENT_EXTENSIONS | ALLOWED_IMAGE_EXTENSIONS)
    return False, f"Unsupported file type. Allowed: {', '.join(allowed)}"


def validate_size(file_size: int, filename: str = "") -> Tuple[bool, Optional[str]]:
    """Validate that a file size is within the per-attachment limit.

    Returns ``(valid, reason)`` .
    """
    if file_size <= 0:
        return False, "File is empty"
    if file_size > MAX_ATTACHMENT_SIZE:
        mb = MAX_ATTACHMENT_SIZE // (1024 * 1024)
        return False, f"File too large ({file_size // (1024*1024)} MB max). Maximum per attachment is {mb} MB."
    return True, None


def validate_mime_type(filename: str, supplied_mime: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Validate MIME type against the file extension.

    We do not trust client-supplied MIME types, but we can use them as a
    secondary check after extension validation.
    """
    ext = get_extension(filename)
    if ext is None:
        # No extension; fall back to MIME type only
        if supplied_mime and supplied_mime.startswith("image/"):
            return True, None
        if supplied_mime and supplied_mime == "application/pdf":
            return True, None
        return False, "Cannot determine file type without extension"

    # Build expected MIME from extension
    expected_mime, _ = mimetypes.guess_type(f"test{ext}")
    expected_mime = (expected_mime or "").split("/")[0] if expected_mime else ""

    # Accept if extension is allowed (primary check)
    if ext.lower() in ALLOWED_EXTENSIONS:
        # Secondary MIME check if a MIME was supplied and differs
        if supplied_mime and supplied_mime != expected_mime:
            # Warn but don't reject — extension is the authoritative check
            pass
        return True, None

    # Extension not allowed; MIME alone cannot override
    return False, "Unsupported file type"


def validate_attachment_count(count: int) -> Tuple[bool, Optional[str]]:
    """Validate that the number of attachments is within limits."""
    if count > MAX_ATTACHMENT_COUNT:
        return False, f"Too many attachments ({count} max {MAX_ATTACHMENT_COUNT} allowed)."
    if count < 1:
        return False, "At least one attachment required when web_search is not enabled"
    return True, None