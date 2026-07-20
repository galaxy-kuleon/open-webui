"""Pure Path-B attachment-basename policy.

Identity path segments remain owned by ``hermes_handoff._safe_segment``. This
module owns physical attachment basenames plus the direct corrected and legacy
name policies. Coverage derives corrected names per parsed physical slot index.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_ATTACHMENT_BASENAME_LENGTH = 96
HANDOFF_NONCE_HEX_LENGTH = 8

_UNSAFE_STEM_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_VALID_SUFFIX_RE = re.compile(r"\.[A-Za-z0-9]{1,16}\Z")
_VALID_NONCE_RE = re.compile(
    rf"[0-9a-fA-F]{{{HANDOFF_NONCE_HEX_LENGTH}}}\Z"
)


def _validate_index(index: int) -> None:
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("attachment index must be a non-negative integer")


def _prefix(index: int, nonce: str) -> str:
    _validate_index(index)
    if not isinstance(nonce, str) or not _VALID_NONCE_RE.fullmatch(nonce):
        raise ValueError("attachment nonce must be exactly 8 hexadecimal characters")
    return f"{index:03d}-{nonce}-"


def _portable_basename(raw_name: str | None) -> str:
    normalized = str(raw_name or "").replace("\\", "/")
    return normalized.rsplit("/", 1)[-1].strip()


def _stem_and_valid_suffix(raw_name: str | None) -> tuple[str, str]:
    basename = _portable_basename(raw_name)
    stem, separator, suffix_body = basename.rpartition(".")
    if not separator:
        return basename, ""

    suffix = f".{suffix_body}"
    if _VALID_SUFFIX_RE.fullmatch(suffix):
        return stem, suffix
    return stem, ""


def _safe_stem(raw_stem: str) -> str:
    cleaned = _UNSAFE_STEM_RE.sub("_", (raw_stem or "").strip()).strip("._-")
    return cleaned or "file"


def _corrected_candidate_for_prefix(
    raw_name: str | None,
    prefix: str,
) -> str:
    raw_stem, suffix = _stem_and_valid_suffix(raw_name)
    stem_budget = MAX_ATTACHMENT_BASENAME_LENGTH - len(prefix) - len(suffix)
    if stem_budget < 1:
        raise ValueError("attachment prefix and suffix leave no stem budget")
    stem = _safe_stem(raw_stem)[:stem_budget]
    return f"{stem}{suffix}"


def corrected_attachment_candidate(
    raw_name: str | None,
    index: int,
) -> str:
    """Return the corrected tail for an actual physical handoff index."""
    prefix = _prefix(index, "0" * HANDOFF_NONCE_HEX_LENGTH)
    return _corrected_candidate_for_prefix(raw_name, prefix)


def legacy_attachment_candidate(raw_name: str | None) -> str:
    """Reproduce the old whole-basename sanitizer exactly."""
    basename = Path(str(raw_name or "")).name or "file"
    cleaned = _UNSAFE_STEM_RE.sub("_", basename.strip())[
        :MAX_ATTACHMENT_BASENAME_LENGTH
    ].strip("._-")
    return cleaned if (cleaned and ".." not in cleaned) else "file"


def build_attachment_basename(
    raw_name: str | None,
    index: int,
    nonce: str,
) -> str:
    """Return the corrected physical ``index-nonce-safeStem.validSuffix``."""
    prefix = _prefix(index, nonce)
    candidate = _corrected_candidate_for_prefix(raw_name, prefix)
    basename = f"{prefix}{candidate}"
    if len(basename) > MAX_ATTACHMENT_BASENAME_LENGTH:
        raise RuntimeError("attachment basename exceeds its length contract")
    return basename
