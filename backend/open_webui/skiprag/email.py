"""Email parsing helpers for skip-rag `.eml` uploads.

This module intentionally handles RFC822 email containers before Docling.  The
public API returns Markdown and is fail-closed: malformed messages produce a
non-empty Markdown document with parse warnings rather than raising or returning
an empty string.
"""

from __future__ import annotations

import base64
import io
import os
import re
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from typing import cast

import requests

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069\u200f\u061c]")
_WHITESPACE_RE = re.compile(r"[ \t\r\n]+")
_MARKDOWN_LINK_CHARS_RE = re.compile(r"[\[\]()<>]")
_MAX_DISPLAY_CHARS = 300

_CONVERTIBLE_ATTACHMENT_EXTS = frozenset(
    {
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "txt",
        "md",
    }
)
_IMAGE_ATTACHMENT_EXTS = frozenset(
    {
        "png",
        "jpg",
        "jpeg",
        "webp",
        "gif",
        "bmp",
        "tif",
        "tiff",
    }
)
_IMAGE_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}

_DEFAULT_MAX_ATTACHMENTS = 10
_DEFAULT_MAX_ATTACHMENT_BYTES = 25_000_000
_DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES = 50_000_000
_DEFAULT_MAX_ATTACHMENT_CHARS = 50_000
_DEFAULT_MAX_TOTAL_ATTACHMENT_CHARS = 150_000


def _sanitize_display(value: object, *, fallback: str = "") -> str:
    """Return a single-line, bounded string safe for Markdown metadata."""
    text = str(value or "")
    text = _BIDI_RE.sub("", text)
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = text.replace("`", "")
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


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return max(0, value)


def _attachment_limits() -> dict[str, int]:
    return {
        "max_attachments": _env_int(
            "SKIP_RAG_EMAIL_MAX_ATTACHMENTS",
            _DEFAULT_MAX_ATTACHMENTS,
        ),
        "max_attachment_bytes": _env_int(
            "SKIP_RAG_EMAIL_MAX_ATTACHMENT_BYTES",
            _DEFAULT_MAX_ATTACHMENT_BYTES,
        ),
        "max_total_attachment_bytes": _env_int(
            "SKIP_RAG_EMAIL_MAX_TOTAL_ATTACHMENT_BYTES",
            _DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES,
        ),
        "max_attachment_chars": _env_int(
            "SKIP_RAG_EMAIL_MAX_ATTACHMENT_CHARS",
            _DEFAULT_MAX_ATTACHMENT_CHARS,
        ),
        "max_total_attachment_chars": _env_int(
            "SKIP_RAG_EMAIL_MAX_TOTAL_ATTACHMENT_CHARS",
            _DEFAULT_MAX_TOTAL_ATTACHMENT_CHARS,
        ),
    }


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


def _decoded_attachment_bytes(part: Message) -> bytes:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        if isinstance(raw_payload, str):
            return raw_payload.encode("utf-8", errors="replace")
        return b""
    if isinstance(payload, bytes):
        return payload
    return bytes(cast(bytearray, payload))


def _is_attachment_part(part: Message) -> bool:
    if part.is_multipart():
        return False
    disposition = part.get_content_disposition()
    return disposition == "attachment" or bool(_part_filename(part))


def _attachment_ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _attachment_metadata_lines(
    *,
    index: int,
    filename: str,
    content_type: str,
    size: int,
    disposition: str,
    status: str,
    extractor: str | None = None,
    warning: str | None = None,
) -> list[str]:
    lines = [
        f"### Attachment {index}: {filename}",
        "",
        f"- Content-Type: {content_type}",
        f"- Size: {size} bytes",
        f"- Disposition: {disposition}",
        f"- Status: {status}",
    ]
    if extractor:
        lines.append(f"- Extractor: {extractor}")
    if warning:
        lines.append(f"- Warning: {_sanitize_display(warning)}")
    return lines


def _truncate_extracted_content(
    text: str,
    *,
    per_attachment_limit: int,
    total_remaining: int,
) -> tuple[str, int]:
    if not text:
        return "", 0

    limit = min(per_attachment_limit, total_remaining)
    if limit <= 0:
        return (
            "[...truncated: total email attachment extraction limit reached...]",
            0,
        )

    original_length = len(text)
    truncated = text[:limit]
    notices: list[str] = []
    if original_length > per_attachment_limit and limit == per_attachment_limit:
        notices.append(f"[...truncated: attachment content exceeded {per_attachment_limit} characters...]")
    if original_length > total_remaining:
        notices.append(
            f"[...truncated: total email attachment extraction limit exceeded "
            f"{total_remaining} remaining characters...]"
        )

    if notices:
        truncated = truncated.rstrip() + "\n\n" + "\n".join(notices)

    return truncated, min(original_length, limit)


def _first_gif_frame_as_png(file_bytes: bytes) -> bytes:
    from PIL import Image  # type: ignore[import-untyped]

    with Image.open(io.BytesIO(file_bytes)) as image:
        image.seek(0)
        frame = image.convert("RGBA")
        output = io.BytesIO()
        frame.save(output, format="PNG")
        return output.getvalue()


def _image_payload_and_mime(file_bytes: bytes, filename: str) -> tuple[bytes, str]:
    ext = _attachment_ext(filename)
    if ext == "gif":
        return _first_gif_frame_as_png(file_bytes), "image/png"
    return file_bytes, _IMAGE_MIME_BY_EXT.get(ext, "application/octet-stream")


def _extract_image_via_task_model(file_bytes: bytes, filename: str) -> tuple[bool, str]:
    """Return (success, extracted content or warning) for an image attachment."""
    try:
        api_base = os.environ.get(
            "SKIP_RAG_EXTRACTOR_API_BASE",
            "http://host.docker.internal:11234/v1",
        ).rstrip("/")
        model = os.environ.get("TASK_MODEL", "qwen3.5-4b")

        image_bytes, mime = _image_payload_and_mime(file_bytes, filename)
        b64 = base64.b64encode(image_bytes).decode()

        system_prompt = (
            "You are analyzing an email attachment image for skip-RAG context. "
            "First transcribe any visible text exactly (OCR). "
            "Then describe tables, receipts, charts, UI screenshots, stamps, "
            "signatures, and important visual details. "
            "Do not invent fields that are not visible. "
            "If unreadable, say unreadable."
        )

        resp = requests.post(
            f"{api_base}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": ("Analyze this email attachment image: " f"{_sanitize_display(filename)}"),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime};base64,{b64}",
                                },
                            },
                        ],
                    },
                ],
                "max_tokens": 2000,
            },
            timeout=120,
        )
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        if isinstance(content, str) and content.strip():
            return True, content.strip()
        return True, "unreadable"
    except Exception as exc:  # noqa: BLE001 - image extraction must fail closed
        reason = _sanitize_display(exc, fallback=exc.__class__.__name__)
        return False, f"Warning: task model image extraction failed: {reason}"


def _convert_attachment_to_markdown(
    *,
    part_bytes: bytes,
    filename: str,
    request,
    attachment_converter,
) -> tuple[bool, str]:
    """Return (success, extracted markdown or warning) for a document attachment."""
    if attachment_converter is None:
        return False, "Warning: attachment converter unavailable"

    try:
        extracted = attachment_converter(
            file_bytes=part_bytes,
            filename=filename,
            request=request,
        )
        if isinstance(extracted, str):
            return True, extracted
        return True, str(extracted or "")
    except Exception as exc:  # noqa: BLE001 - attachment conversion must fail closed
        reason = _sanitize_display(exc, fallback=exc.__class__.__name__)
        return False, f"Warning: attachment extraction failed: {reason}"


def _attachment_part_lines(
    *,
    index: int,
    part: Message,
    part_bytes: bytes,
    request,
    attachment_converter,
    total_chars_used: int,
    limits: dict[str, int],
) -> tuple[list[str], int]:
    filename = _safe_attachment_filename(_part_filename(part), index)
    content_type = _sanitize_display(
        part.get_content_type(),
        fallback="application/octet-stream",
    )
    disposition = _sanitize_display(
        part.get_content_disposition(),
        fallback="unspecified",
    )
    size = len(part_bytes)

    base_kwargs = {
        "index": index,
        "filename": filename,
        "content_type": content_type,
        "size": size,
        "disposition": disposition,
    }

    if size > limits["max_attachment_bytes"]:
        warning = f"attachment size {size} bytes exceeds " f"{limits['max_attachment_bytes']} byte limit"
        return (
            _attachment_metadata_lines(
                **base_kwargs,
                status="skipped-oversized",
                warning=warning,
            ),
            0,
        )

    ext = _attachment_ext(filename)
    if ext not in _CONVERTIBLE_ATTACHMENT_EXTS and ext not in _IMAGE_ATTACHMENT_EXTS:
        return (
            _attachment_metadata_lines(**base_kwargs, status="unsupported-type"),
            0,
        )

    # Note: Degraded/failed extraction results are intentionally NOT cached.
    # This ensures that a transient sidecar/model outage does not permanently
    # poison future skip-rag turns for the same .eml file. The cache write
    # happens at the convert.py level for the entire .eml output, which only
    # occurs when the full conversion succeeds without degraded fallback.
    if ext in _IMAGE_ATTACHMENT_EXTS:
        success, extracted = _extract_image_via_task_model(part_bytes, filename)
        status = "extracted" if success else "extraction-failed"
        lines = _attachment_metadata_lines(
            **base_kwargs,
            status=status,
            extractor="task-model-vision",
        )
        lines.extend(["", "#### Image OCR / Description", ""])
    else:
        success, extracted = _convert_attachment_to_markdown(
            part_bytes=part_bytes,
            filename=filename,
            request=request,
            attachment_converter=attachment_converter,
        )
        status = "extracted" if success else "extraction-failed"
        lines = _attachment_metadata_lines(
            **base_kwargs,
            status=status,
            extractor="skip-rag-convert",
        )
        lines.extend(["", "#### Extracted Content", ""])

    content, chars_used = _truncate_extracted_content(
        extracted,
        per_attachment_limit=limits["max_attachment_chars"],
        total_remaining=limits["max_total_attachment_chars"] - total_chars_used,
    )
    lines.append(content or "[No attachment content extracted]")
    return lines, chars_used


def _extract_attachments(
    msg: Message,
    warnings: list[str],
    *,
    request,
    attachment_converter,
) -> list[list[str]]:
    """Return Markdown lines for attachment entries with safe extraction."""
    parts = [part for part in (msg.walk() if msg.is_multipart() else [msg]) if _is_attachment_part(part)]
    attachment_lines: list[list[str]] = []
    limits = _attachment_limits()
    total_bytes = 0
    total_chars = 0

    for raw_index, part in enumerate(parts, start=1):
        if len(attachment_lines) >= limits["max_attachments"]:
            remaining = len(parts) - len(attachment_lines)
            warnings.append(f"Skipped {remaining} remaining attachments: exceeded limit.")
            break

        if total_chars >= limits["max_total_attachment_chars"]:
            remaining = len(parts) - len(attachment_lines)
            warnings.append(f"Skipped {remaining} remaining attachments: exceeded limit.")
            break

        index = len(attachment_lines) + 1
        filename = _safe_attachment_filename(_part_filename(part), index)
        try:
            part_bytes = _decoded_attachment_bytes(part)
        except Exception as exc:  # noqa: BLE001 - keep malformed attachments fail-closed
            reason = _sanitize_display(exc, fallback=exc.__class__.__name__)
            warnings.append(f"Failed to decode attachment payload for {filename}: {reason}")
            attachment_lines.append(
                _attachment_metadata_lines(
                    index=index,
                    filename=filename,
                    content_type=_sanitize_display(
                        part.get_content_type(),
                        fallback="application/octet-stream",
                    ),
                    size=0,
                    disposition=_sanitize_display(
                        part.get_content_disposition(),
                        fallback="unspecified",
                    ),
                    status="extraction-failed",
                    warning=f"failed to decode attachment payload: {reason}",
                )
            )
            continue

        size = len(part_bytes)
        if total_bytes + size > limits["max_total_attachment_bytes"]:
            remaining = len(parts) - raw_index + 1
            warnings.append(f"Skipped {remaining} remaining attachments: exceeded limit.")
            break

        total_bytes += size
        lines, chars_used = _attachment_part_lines(
            index=index,
            part=part,
            part_bytes=part_bytes,
            request=request,
            attachment_converter=attachment_converter,
            total_chars_used=total_chars,
            limits=limits,
        )
        total_chars += chars_used
        attachment_lines.append(lines)

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

    `attachment_converter` is expected to be skip-rag's convert_to_markdown
    entry point and is used for supported document/text attachments.
    """
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

        lines.extend(
            [
                "",
                "## Body",
                "",
                body,
            ]
        )

        attachments = _extract_attachments(
            msg,
            warnings,
            request=request,
            attachment_converter=attachment_converter,
        )
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
