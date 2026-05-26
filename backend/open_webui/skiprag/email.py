"""Email parsing helpers for skip-rag `.eml` uploads.

This module intentionally handles RFC822 email containers before Docling.  The
public API returns Markdown and is fail-closed: malformed messages produce a
non-empty Markdown document with parse warnings rather than raising or returning
an empty string.
"""

from __future__ import annotations

import re
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from typing import cast

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"[ \t\r\n]+")
_MARKDOWN_LINK_CHARS_RE = re.compile(r"[\[\]()<>]")
_MAX_DISPLAY_CHARS = 300


def _sanitize_display(value: object, *, fallback: str = "") -> str:
    """Return a single-line, bounded string safe for Markdown metadata."""
    text = str(value or "")
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    # Avoid accidentally opening/closing Markdown fences in metadata.
    text = text.replace("```", "''' ")
    if len(text) > _MAX_DISPLAY_CHARS:
        text = text[: _MAX_DISPLAY_CHARS - 1].rstrip() + "…"
    return text or fallback


def _safe_filename(filename: str) -> str:
    """Sanitize a display filename across Unix and Windows path separators."""
    cleaned = _sanitize_display(filename, fallback="email.eml")
    cleaned = cleaned.replace("\\", "/").split("/")[-1].strip()
    return _sanitize_display(cleaned, fallback="email.eml")


def _safe_attachment_filename(filename: str | None, index: int) -> str:
    """Sanitize attachment display names and provide a stable fallback."""
    fallback = f"attachment-{index}.bin"
    cleaned = _sanitize_display(filename, fallback=fallback)
    cleaned = _MARKDOWN_LINK_CHARS_RE.sub("", cleaned)
    if "://" in cleaned:
        cleaned = cleaned.replace("://", "").replace("/", "")
    cleaned = cleaned.replace("\\", "/").split("/")[-1].strip()
    return _sanitize_display(cleaned, fallback=fallback)


def _message_header(msg: Message, name: str) -> str:
    try:
        return _sanitize_display(msg.get(name, ""))
    except Exception:  # pragma: no cover - defensive against malformed headers
        return ""


def _part_text(part: Message) -> str:
    """Decode a text MIME part to a normalized string."""
    try:
        if isinstance(part, EmailMessage):
            content = part.get_content()
            if isinstance(content, str):
                return content
    except Exception:
        pass

    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        if isinstance(raw_payload, str):
            return raw_payload
        return ""

    payload_bytes = cast(bytes, payload)
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload_bytes.decode(charset, errors="replace")
    except LookupError:
        return payload_bytes.decode("utf-8", errors="replace")


def _normalize_body(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS_RE.sub("", text)
    return text.strip()


def _html_to_text(html: str) -> str:
    """Convert an HTML email body to plain text without executable/noisy tags."""
    from bs4 import BeautifulSoup  # type: ignore[import-untyped]

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines()]
    compact_lines: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                compact_lines.append("")
            previous_blank = True
            continue
        compact_lines.append(line)
        previous_blank = False
    return _normalize_body("\n".join(compact_lines))


def _part_filename(part: Message) -> str | None:
    try:
        return part.get_filename()
    except Exception:  # pragma: no cover - defensive against malformed parts
        return None


def _is_body_part(part: Message, content_type: str) -> bool:
    if part.is_multipart():
        return False
    if part.get_content_disposition() == "attachment":
        return False
    if _part_filename(part):
        return False
    return part.get_content_type() == content_type


def _extract_plain_text_body(msg: Message) -> str:
    """Extract the preferred plain text body from an email message."""
    plain_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if _is_body_part(part, "text/plain"):
                text = _normalize_body(_part_text(part))
                if text:
                    plain_parts.append(text)
    elif _is_body_part(msg, "text/plain"):
        text = _normalize_body(_part_text(msg))
        if text:
            plain_parts.append(text)

    if plain_parts:
        return "\n\n".join(plain_parts)
    return ""


def _extract_html_body(msg: Message) -> str:
    """Extract and sanitize HTML body text when no plain text body exists."""
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if _is_body_part(part, "text/html"):
                text = _html_to_text(_part_text(part))
                if text:
                    html_parts.append(text)
    elif _is_body_part(msg, "text/html"):
        text = _html_to_text(_part_text(msg))
        if text:
            html_parts.append(text)

    if html_parts:
        return "\n\n".join(html_parts)
    return ""


def _decoded_payload_size(part: Message) -> int:
    payload = part.get_payload(decode=True) or b""
    return len(cast(bytes, payload))


def _is_attachment_part(part: Message) -> bool:
    if part.is_multipart():
        return False
    disposition = part.get_content_disposition()
    return disposition == "attachment" or bool(_part_filename(part))


def _extract_attachment_metadata(msg: Message, warnings: list[str]) -> list[list[str]]:
    """Return Markdown lines for metadata-only attachment entries."""
    attachment_lines: list[list[str]] = []
    parts = msg.walk() if msg.is_multipart() else [msg]

    for part in parts:
        if not _is_attachment_part(part):
            continue

        index = len(attachment_lines) + 1
        filename = _safe_attachment_filename(_part_filename(part), index)
        content_type = _sanitize_display(
            part.get_content_type(), fallback="application/octet-stream"
        )
        disposition = _sanitize_display(part.get_content_disposition(), fallback="unspecified")

        try:
            size = _decoded_payload_size(part)
        except Exception as exc:  # noqa: BLE001 - keep malformed attachments fail-closed
            size = 0
            reason = _sanitize_display(exc, fallback=exc.__class__.__name__)
            warnings.append(f"Failed to decode attachment payload for {filename}: {reason}")

        attachment_lines.append([
            f"### Attachment {index}: {filename}",
            "",
            f"- Content-Type: {content_type}",
            f"- Size: {size} bytes",
            f"- Disposition: {disposition}",
            "- Status: metadata-only",
        ])

    return attachment_lines


def _minimal_failure_markdown(filename: str, error: Exception) -> str:
    safe_name = _safe_filename(filename)
    reason = _sanitize_display(error, fallback=error.__class__.__name__)
    return (
        f"# Email: {safe_name}\n\n"
        "## Body\n\n"
        "[No body extracted]\n\n"
        "## Parse Warnings\n\n"
        f"- Failed to parse email: {reason}\n"
    )


def eml_to_markdown(
    raw_bytes: bytes,
    filename: str = "email.eml",
    request=None,
    attachment_converter=None,
) -> str:
    """Convert a single `.eml` message to Markdown for skip-rag injection.

    `request` and `attachment_converter` are accepted for the later attachment
    extraction phase.  They are intentionally unused in the basic metadata/body
    parser implemented here.
    """
    del request, attachment_converter

    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        warnings: list[str] = []

        safe_name = _safe_filename(filename)
        subject = _message_header(msg, "Subject") or safe_name
        body = _extract_plain_text_body(msg)
        if not body:
            body = _extract_html_body(msg)
            if body:
                warnings.append("HTML body converted to text because text/plain was missing.")
            else:
                warnings.append("No text/plain or text/html body found.")
                body = "[No body extracted]"

        metadata_fields = [
            ("From", _message_header(msg, "From")),
            ("To", _message_header(msg, "To")),
            ("Cc", _message_header(msg, "Cc")),
            ("Date", _message_header(msg, "Date")),
            ("Message-ID", _message_header(msg, "Message-ID")),
            ("In-Reply-To", _message_header(msg, "In-Reply-To")),
        ]

        lines = [
            f"# Email: {subject}",
            "",
            "## Metadata",
            "",
        ]
        for label, value in metadata_fields:
            if value:
                lines.append(f"- {label}: {value}")

        lines.extend([
            "",
            "## Body",
            "",
            body,
        ])

        attachments = _extract_attachment_metadata(msg, warnings)
        if attachments:
            lines.extend(["", "## Attachments", ""])
            for attachment in attachments:
                lines.extend(attachment)
                lines.append("")
            lines.pop()

        if warnings:
            lines.extend(["", "## Parse Warnings", ""])
            lines.extend(f"- {_sanitize_display(warning)}" for warning in warnings)

        return "\n".join(lines).rstrip() + "\n"
    except Exception as exc:  # noqa: BLE001 - fail closed for upload pipeline
        return _minimal_failure_markdown(filename, exc)
