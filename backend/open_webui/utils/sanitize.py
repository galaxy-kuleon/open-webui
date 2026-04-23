import os
import re
import unicodedata

from anyascii import anyascii


# ── Filename sanitization ────────────────────────────────────────────
# Used everywhere a user-supplied filename touches the filesystem or
# gets embedded into a prompt (upload handler, skill intercept,
# skip_rag header).  Pure function — no I/O, no side effects.

# Control characters: C0 (0x00-0x1F), DEL (0x7F), C1 (0x80-0x9F)
_CTRL_CHAR_RE = re.compile(r'[\x00-\x1f\x7f-\x9f]+')

# Whitespace variants: ASCII whitespace (\s), NBSP, general punctuation
# spaces (U+2000–U+200F), line/paragraph separators (U+2028–U+202F),
# medium math space (U+205F), ideographic space (U+3000), BOM (U+FEFF)
_WHITESPACE_RE = re.compile(r'[\s\u00a0\u2000-\u200f\u2028-\u202f\u205f\u3000\ufeff]+')

# Characters hostile to shells, paths, or prompt structure
_HOSTILE_CHAR_RE = re.compile(r"[<>:\"/\\|?*(){}[\]!@#$%^&+=~`',;]+")

_MULTI_HYPHEN_RE = re.compile(r'-{2,}')

# Windows reserved device names — opening these as files on Windows silently
# fails or redirects I/O to the device.  Prefix with underscore to defuse.
_WINDOWS_RESERVED = frozenset(
    ['CON', 'PRN', 'AUX', 'NUL'] + [f'COM{i}' for i in range(1, 10)] + [f'LPT{i}' for i in range(1, 10)]
)

# Extract a sane file extension at the end of the name.
# Dot + 1-10 ASCII alphanumeric chars, anchored to end-of-string.
# This rejects injection payloads that merge with the extension after
# control-char stripping (e.g. "file.pdf\n## inject" → no match because
# the tail is far longer than 10 chars).
_VALID_EXT_RE = re.compile(r'\.([a-zA-Z0-9]{1,10})$')


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
        return 'unnamed'

    # Truncate at the first null byte — prevents attackers from smuggling a
    # second extension past the visible filename (e.g. "file.pdf\x00.exe").
    if '\x00' in raw:
        raw = raw[: raw.index('\x00')]

    # Normalize Windows backslash separators so basename works on all OSes
    name = os.path.basename(raw.replace('\\', '/'))

    # Strip control characters early so they can't hide in the extension
    name = _CTRL_CHAR_RE.sub('', name)
    name = unicodedata.normalize('NFKC', name)

    # Transliterate non-ASCII → ASCII (CJK → romanization, accented → base)
    name = anyascii(name)

    # Extract extension via regex — only matches a sane, short extension
    # anchored at the very end.  If injection content got concatenated
    # after the real extension (e.g. ".pdf## Override..."), the tail is
    # too long / contains non-alphanumeric → no match → extension dropped.
    m = _VALID_EXT_RE.search(name)
    if m:
        ext = m.group(0)  # e.g. ".pdf"
        stem = name[: m.start()]
    else:
        ext = ''
        stem = name

    # ── Stem ──
    stem = _WHITESPACE_RE.sub('-', stem)
    stem = _HOSTILE_CHAR_RE.sub('-', stem)
    stem = _MULTI_HYPHEN_RE.sub('-', stem)
    stem = stem.strip('-.')
    if not stem:
        stem = 'unnamed'

    # Defuse Windows reserved device names (CON, NUL, PRN, …)
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f'_{stem}'

    # Filesystem limit: most FSes cap at 255 bytes per name component.
    # Truncate the stem, leaving room for the extension.
    max_stem = 200
    if len(stem) > max_stem:
        stem = stem[:max_stem].rstrip('-')

    return f'{stem}{ext}'


# ── ANSI escape code stripping ───────────────────────────────────────

# ANSI escape code pattern - matches all common ANSI sequences
# This includes color codes, cursor movement, and other terminal control sequences
ANSI_ESCAPE_PATTERN = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\([AB]|\x1b[PX^_].*?\x1b\\|\x1b\].*?(?:\x07|\x1b\\)')


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
    return ANSI_ESCAPE_PATTERN.sub('', text)


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
    code = re.sub(r'^```\w*\n?', '', code)
    # Remove closing fence
    code = re.sub(r'\n?```\s*$', '', code)
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


# ── LLM-injected markdown sanitizer (skip_rag prompt-injection hardening) ───
#
# Strip set (ONE compiled character class — single pass, no composition):
#   Unicode control/direction/bidi code points:
#     U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR
#     U+200B–U+200D (zero-width space, non-joiner, joiner)
#     U+202A–U+202E (bidi embedding / override controls)
#     U+2066–U+2069 (bidi isolate controls)
#   ASCII C0 EXCEPT \t (0x09), \n (0x0A), \r (0x0D):
#     [0x00–0x08] [0x0B] [0x0C] [0x0E–0x1F]
#
# file_id whitelist: [a-zA-Z0-9_-] only — prevents forging END>> delimiter.

_LLM_INJECT_STRIP_RE = re.compile(
    r'[\u2028\u2029\u200B-\u200D\u202A-\u202E\u2066-\u2069'
    r'\x00-\x08\x0B\x0C\x0E-\x1F]'
)

_FILE_ID_VALID_RE = re.compile(r'^[a-zA-Z0-9_\-]+$')

# Byte cap for skip_rag direct injection: 256 KiB measured in UTF-8 bytes.
_SKIP_RAG_MAX_BYTES = 256 * 1024  # 256 KiB — measure via len(s.encode('utf-8'))


def sanitize_llm_injected_markdown(text: str, file_id: str) -> str:
    """Sanitize user-supplied file content before injecting into an LLM prompt.

    Pure function — no I/O, no logging, no globals mutated. Deterministic.

    Idempotence guarantee (choice a):
        ``sanitize_llm_injected_markdown(sanitize_llm_injected_markdown(t, fid), fid)``
        returns the same string as a single call.  This holds because:
        (1) the strip regex is idempotent on its own output (stripped chars are gone),
        (2) the delimiter wrapping is NOT double-applied: the function wraps the
            *raw text* argument, not a pre-wrapped result.  Callers must invoke
            exactly once per file — see contract below.

        Caller invariant: invoke exactly once per (file_id, content) pair.
        The returned string already contains the ``<<FILE ...>>`` delimiters;
        do NOT pass the return value back into this function.

    Strip set (single compiled regex, one pass):
        Unicode: U+2028, U+2029, U+200B–U+200D, U+202A–U+202E, U+2066–U+2069.
        ASCII C0 EXCEPT \\t (0x09), \\n (0x0A), \\r (0x0D):
          [0x00–0x08], 0x0B, 0x0C, [0x0E–0x1F].

    Args:
        text:    Raw content string to sanitize and wrap.
        file_id: Identifier substituted into delimiter markers.
                 Must match ``[a-zA-Z0-9_-]+``.

    Returns:
        Sanitized content wrapped in ``<<FILE file-{file_id} BEGIN>>`` /
        ``<<FILE file-{file_id} END>>`` delimiter lines.

    Raises:
        ValueError: If ``file_id`` contains characters outside ``[a-zA-Z0-9_-]``.
                    This prevents a crafted file_id from forging an END>> marker
                    inside the delimiter (e.g. ``x END>> injected_system``).
    """
    if not _FILE_ID_VALID_RE.match(file_id):
        raise ValueError(
            f'sanitize_llm_injected_markdown: file_id {file_id!r} contains '
            'characters outside the whitelist [a-zA-Z0-9_-]. '
            'Rejected to prevent delimiter-forgery via file_id.'
        )

    sanitized = _LLM_INJECT_STRIP_RE.sub('', text)

    return f'<<FILE file-{file_id} BEGIN>>\n{sanitized}\n<<FILE file-{file_id} END>>'
