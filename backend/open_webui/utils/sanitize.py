import os
import re
import unicodedata

from anyascii import anyascii


# ── Filename sanitization ────────────────────────────────────────────
# Used everywhere a user-supplied filename touches the filesystem or
# gets embedded into a prompt (upload handler, skill intercept,
# skip_rag header).  Pure function — no I/O, no side effects.

# Control characters: C0 (0x00-0x1F), DEL (0x7F), C1 (0x80-0x9F)
_CTRL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")

# Whitespace variants: ASCII whitespace (\s), NBSP, general punctuation
# spaces (U+2000–U+200F), line/paragraph separators (U+2028–U+202F),
# medium math space (U+205F), ideographic space (U+3000), BOM (U+FEFF)
_WHITESPACE_RE = re.compile(
    r"[\s\u00a0\u2000-\u200f\u2028-\u202f\u205f\u3000\ufeff]+"
)

# Characters hostile to shells, paths, or prompt structure
_HOSTILE_CHAR_RE = re.compile(r"[<>:\"/\\|?*(){}[\]!@#$%^&+=~`',;]+")

_MULTI_HYPHEN_RE = re.compile(r"-{2,}")

# Windows reserved device names — opening these as files on Windows silently
# fails or redirects I/O to the device.  Prefix with underscore to defuse.
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

# Extract a sane file extension at the end of the name.
# Dot + 1-10 ASCII alphanumeric chars, anchored to end-of-string.
# This rejects injection payloads that merge with the extension after
# control-char stripping (e.g. "file.pdf\n## inject" → no match because
# the tail is far longer than 10 chars).
_VALID_EXT_RE = re.compile(r"\.([a-zA-Z0-9]{1,10})$")


def sanitize_filename(raw: str | None) -> str:
    """Sanitize a user-supplied filename for safe filesystem and prompt use.

    Guarantees:
    - No directory traversal (basename extraction, handles both / and \\)
    - No control characters (newlines, null bytes, etc.)
    - No shell/path-hostile characters
    - No exotic whitespace — collapsed to hyphens
    - Unicode NFKC normalized (fullwidth → ASCII, compatibility forms)
    - CJK / non-ASCII → ASCII romanization via anyascii
    - Extension is a short alphanumeric token (rejects merged payloads)
    - Non-empty stem (falls back to "unnamed")

    Output is pure ASCII: alphanumeric, dots, underscores, and hyphens.
    """
    if not raw:
        return "unnamed"

    # Truncate at the first null byte — prevents attackers from smuggling a
    # second extension past the visible filename (e.g. "file.pdf\x00.exe").
    if "\x00" in raw:
        raw = raw[: raw.index("\x00")]

    # Normalize Windows backslash separators so basename works on all OSes
    name = os.path.basename(raw.replace("\\", "/"))

    # Strip control characters early so they can't hide in the extension
    name = _CTRL_CHAR_RE.sub("", name)
    name = unicodedata.normalize("NFKC", name)

    # Transliterate non-ASCII → ASCII (CJK → romanization, accented → base)
    name = anyascii(name)

    # Extract extension via regex — only matches a sane, short extension
    # anchored at the very end.  If injection content got concatenated
    # after the real extension (e.g. ".pdf## Override..."), the tail is
    # too long / contains non-alphanumeric → no match → extension dropped.
    m = _VALID_EXT_RE.search(name)
    if m:
        ext = m.group(0)        # e.g. ".pdf"
        stem = name[: m.start()]
    else:
        ext = ""
        stem = name

    # ── Stem ──
    stem = _WHITESPACE_RE.sub("-", stem)
    stem = _HOSTILE_CHAR_RE.sub("-", stem)
    stem = _MULTI_HYPHEN_RE.sub("-", stem)
    stem = stem.strip("-.")
    if not stem:
        stem = "unnamed"

    # Defuse Windows reserved device names (CON, NUL, PRN, …)
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"

    # Filesystem limit: most FSes cap at 255 bytes per name component.
    # Truncate the stem, leaving room for the extension.
    max_stem = 200
    if len(stem) > max_stem:
        stem = stem[:max_stem].rstrip("-")

    return f"{stem}{ext}"


# ── ANSI escape code stripping ───────────────────────────────────────

# ANSI escape code pattern - matches all common ANSI sequences
# This includes color codes, cursor movement, and other terminal control sequences
ANSI_ESCAPE_PATTERN = re.compile(
    r"\x1b\[[0-9;]*[A-Za-z]|\x1b\([AB]|\x1b[PX^_].*?\x1b\\|\x1b\].*?(?:\x07|\x1b\\)"
)


def strip_ansi_codes(text: str) -> str:
    """
    Strip ANSI escape codes from text.

    ANSI escape codes can be introduced by LLMs that include terminal
    color codes in their output. These codes cause syntax errors when
    the code is sent to Jupyter for execution.

    Common ANSI codes include:
    - Color codes: \x1b[31m (red), \x1b[32m (green), etc.
    - Reset codes: \x1b[0m, \x1b[39m
    - Cursor movement: \x1b[1A, \x1b[2J, etc.
    """
    return ANSI_ESCAPE_PATTERN.sub("", text)


def strip_markdown_code_fences(code: str) -> str:
    """
    Strip markdown code fences if present.

    This is a defensive, non-breaking change — if the code doesn't
    contain fences, it passes through unchanged.

    Handles patterns like:
    - ```python
    - ```py
    - ```
    """
    code = code.strip()
    # Remove opening fence (```python, ```py, ``` etc.)
    code = re.sub(r"^```\w*\n?", "", code)
    # Remove closing fence
    code = re.sub(r"\n?```\s*$", "", code)
    return code.strip()


def sanitize_code(code: str) -> str:
    """
    Sanitize code for execution by applying all necessary cleanup steps.

    This is the recommended function to use before sending code to
    interpreters like Jupyter or Pyodide.

    Steps applied:
    1. Strip ANSI escape codes (from LLM output)
    2. Strip markdown code fences (if model included them)
    """
    code = strip_ansi_codes(code)
    code = strip_markdown_code_fences(code)
    return code
