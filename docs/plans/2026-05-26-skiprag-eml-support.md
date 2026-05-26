# Skip-RAG `.eml` Email Upload Support Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add first-class `.eml` support to the existing OpenWebUI skip-RAG upload path, including safe parsing of message metadata/body and optional extraction of supported attachments.

**Architecture:** Treat `.eml` as a special converter inside `open_webui.skiprag.convert`, not as a Docling document. Add a small email parsing module that normalizes one RFC822 message into Markdown, then route `.eml` files through that parser before fallback/docling. Attachments should be represented inside the same Markdown source block, with text extraction delegated back to the existing `convert_to_markdown` pipeline only for a narrow, safe MVP allowlist. The parser must fail closed: it should return a minimal Markdown document plus parse warnings instead of raising or returning an empty string.

**Tech Stack:** Python stdlib `email` package, existing `open_webui.skiprag.convert`, BeautifulSoup for HTML fallback, pytest.

---

## Current Codebase Notes

Relevant inspected files:

- `backend/open_webui/skiprag/convert.py`
  - Public entrypoint: `convert_to_markdown(file_bytes_or_path, filename, request=None) -> str`
  - Current routing:
    - `.md`, `.txt` read directly
    - legacy Office via soffice then Docling
    - modern Office/PDF/images via Docling
    - unknown extension tries Docling, then OWUI loader fallback
- `backend/open_webui/skiprag/inject.py`
  - Calls `convert_to_markdown(...)` in `_load_and_convert(...)`
  - Wraps output as `<source name="filename" resource-type="file">...`
  - Removes handled files from `metadata.files` to avoid native RAG embedding
- There are no dedicated skip-RAG tests yet under `backend/open_webui/test/`; create a new test module.

Important design consequence: `.eml` support should be implemented at conversion level. `inject.py` should not need special cases.

---

## Codex Review Integration Notes

Codex reviewed this plan in read-only mode and returned **Pass with changes**. The plan has been updated to incorporate the blocking recommendations before implementation:

- Use the final `request=request` routing shape in `convert.py`; do not present an intermediate `request=None` `.eml` example.
- Require `eml_to_markdown()` to fail closed with non-empty Markdown and parse warnings.
- Narrow the default attachment extraction allowlist to low-risk document/text formats.
- Add total attachment byte/character limits, not just per-attachment limits.
- Treat image/OCR, legacy Office, nested `.eml`, archives, executables, scripts, and unknown binaries as metadata-only/skipped by default.
- Require header/filename sanitization and cache isolation in tests.

---

## MVP Scope

### Must support

1. Upload a single `.eml` file through skip-RAG.
2. Parse stable metadata:
   - Subject
   - From
   - To
   - Cc
   - Date
   - Message-ID
   - In-Reply-To
3. Extract body text:
   - Prefer `text/plain`
   - If only `text/html` exists, convert HTML to readable plain text/Markdown-ish text
   - Ignore inline tracking/image-only parts
4. Attachments:
   - Include attachment metadata in the Markdown output
   - For narrowly supported text/document attachments, optionally extract text using the existing skip-RAG converter
   - Never fail the whole email when one attachment fails
5. Safe limits:
   - Max attachments processed per email
   - Max attachments converted per email
   - Max attachment bytes per attachment
   - Max total attachment bytes per email
   - Max extracted chars per attachment
   - Max total extracted chars per email
   - Sanitized filenames
6. Failure behavior:
   - `eml_to_markdown()` must catch parser-level errors and still return non-empty Markdown with `## Parse Warnings`
   - The skip-RAG item should never silently disappear because a malformed email returned `''`

### Not in first `.eml` MVP

- `.mbox`, `.msg`, `.pst`, `.ost`
- Email thread reconstruction
- Persisting individual emails as multiple logical files
- Rendering raw HTML in UI
- Deep nested archive extraction
- Default OCR/image attachment extraction
- Default legacy Office attachment extraction (`.doc`, `.xls`, `.ppt`)

---

## Attachment Answer

Yes, attachments are feasible.

Recommended MVP behavior:

- Always list attachments with filename, MIME type, size, and disposition.
- Extract text only from safe/supported types by reusing existing `convert_to_markdown(...)`:
  - `.txt`, `.md`
  - `.pdf`
  - `.docx`, `.xlsx`, `.pptx`
- Treat these as metadata-only by default in MVP unless explicitly enabled later:
  - legacy Office: `.doc`, `.xls`, `.ppt`
  - images / OCR: `.png`, `.jpg`, `.jpeg`, `.gif`, `.tiff`, `.webp`, `.bmp`
  - nested emails: `.eml`, `message/rfc822`
- Skip or metadata-only for unsupported/binary/risky types:
  - `.exe`, `.dll`, `.js`, `.sh`, `.zip`, `.rar`, unknown binary
- Add explicit parse warnings rather than raising.

For MVP, default should be: `include attachment metadata + extract safe attachment text within strict limits`, where "safe" means both extension and MIME/magic checks pass. Do not trust attachment filenames alone.

---

## Proposed Markdown Output Format

For an uploaded `sample.eml`, `convert_to_markdown(...)` should return:

```markdown
# Email: Re: Invoice question

## Metadata

- From: Alice <alice@example.com>
- To: Bob <bob@example.com>
- Cc: Carol <carol@example.com>
- Date: Tue, 26 May 2026 10:30:00 +0800
- Message-ID: <abc@example.com>
- In-Reply-To: <prev@example.com>

## Body

Hi Bob,

Please see the invoice attached.

## Attachments

### Attachment 1: invoice.pdf

- Content-Type: application/pdf
- Size: 123456 bytes
- Status: extracted

```text
[extracted attachment text here]
```

### Attachment 2: logo.png

- Content-Type: image/png
- Size: 4567 bytes
- Status: skipped
- Reason: inline image or unsupported attachment type

## Parse Warnings

- HTML body converted to text because text/plain was missing.
```

Notes:

- Use Markdown, because skip-RAG already injects Markdown into `<source>` blocks.
- Do not include raw HTML.
- Keep attachment sections clearly separated so the extractor can preserve structure.

---

## Task 1: Add Email Parser Module

**Objective:** Create an isolated parser that converts raw `.eml` bytes into Markdown without touching skip-RAG routing yet.

**Files:**

- Create: `backend/open_webui/skiprag/email.py`
- Test: `backend/open_webui/test/skiprag/test_email_parser.py`

**Step 1: Write failing test for basic plain-text email**

Create `backend/open_webui/test/skiprag/test_email_parser.py` with a test shaped like:

```python
from open_webui.skiprag.email import eml_to_markdown


def test_eml_to_markdown_extracts_metadata_and_plain_text_body():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Re: Invoice question\r\n"
        b"Date: Tue, 26 May 2026 10:30:00 +0800\r\n"
        b"Message-ID: <abc@example.com>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hi Bob,\r\nPlease see the invoice attached.\r\n"
    )

    md = eml_to_markdown(raw, filename="sample.eml")

    assert "# Email: Re: Invoice question" in md
    assert "- From: Alice <alice@example.com>" in md
    assert "- To: Bob <bob@example.com>" in md
    assert "Hi Bob," in md
    assert "Please see the invoice attached." in md
```

**Step 2: Run test to verify failure**

Run:

```bash
cd /Users/admin/Works/kg-openwebui-stack-v2/open-webui
PYTHONPATH=backend pytest backend/open_webui/test/skiprag/test_email_parser.py -q
```

Expected: FAIL because `open_webui.skiprag.email` does not exist.

**Step 3: Implement minimal parser**

In `backend/open_webui/skiprag/email.py`:

- Use `email.parser.BytesParser(policy=email.policy.default)`.
- Add `eml_to_markdown(raw_bytes: bytes, filename: str = "email.eml", request=None, attachment_converter=None) -> str`.
- Wrap the top-level parser body in defensive error handling. On malformed input, return a non-empty Markdown document with `## Parse Warnings`; do not raise and do not return `''`.
- Add helpers for safe display strings:
  - strip CR/LF/control characters from headers and filenames
  - cap display length for subject/header/filename values
  - basename attachment filenames across both `/` and `\`
  - use a safe fallback such as `attachment-1.bin`
  - avoid Markdown fence/source confusion in displayed metadata
- For body extraction:
  - If non-multipart and `text/plain`, decode payload.
  - If multipart, walk parts; first collect non-attachment `text/plain` parts.
  - Ignore parts with `Content-Disposition: attachment` for body.
- Format metadata and body into Markdown.

**Step 4: Run test to verify pass**

Same pytest command.

---

## Task 2: Add HTML Body Fallback

**Objective:** Support `.eml` messages that contain only HTML bodies.

**Files:**

- Modify: `backend/open_webui/skiprag/email.py`
- Modify: `backend/open_webui/test/skiprag/test_email_parser.py`

**Step 1: Write failing test**

```python
def test_eml_to_markdown_converts_html_body_when_plain_text_missing():
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: HTML only\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<html><body><h1>Hello</h1><p>HTML body</p><script>bad()</script></body></html>"
    )

    md = eml_to_markdown(raw, filename="html.eml")

    assert "Hello" in md
    assert "HTML body" in md
    assert "bad()" not in md
    assert "<script>" not in md
```

**Step 2: Implement HTML-to-text helper**

- Use `bs4.BeautifulSoup` because `beautifulsoup4` is already in `pyproject.toml`.
- Remove `script`, `style`, `noscript` tags.
- Use `soup.get_text("\n")`, collapse excessive blank lines.
- Add a parse warning if HTML fallback was used.

---

## Task 3: Add Attachment Metadata

**Objective:** List attachments safely without extracting content yet.

**Files:**

- Modify: `backend/open_webui/skiprag/email.py`
- Modify: `backend/open_webui/test/skiprag/test_email_parser.py`

**Step 1: Write failing test**

Create a multipart email with a text body and a base64 PDF-like attachment. Assert Markdown contains:

- `## Attachments`
- attachment filename
- content type
- byte size
- `Status: skipped` or `Status: metadata-only`

**Step 2: Implement attachment discovery**

- Walk MIME parts.
- Treat a part as attachment if:
  - `part.get_content_disposition() == "attachment"`, or
  - it has a filename and is not selected as body.
- Sanitize filename:
  - strip path separators
  - fallback to `attachment-N.bin`
  - limit display length
- Decode payload with `part.get_payload(decode=True) or b""`.
- Do not raise on malformed payload; add parse warning.

---

## Task 4: Add Safe Attachment Text Extraction

**Objective:** Reuse existing skip-RAG conversion pipeline for safe/supported attachments.

**Files:**

- Modify: `backend/open_webui/skiprag/email.py`
- Modify: `backend/open_webui/test/skiprag/test_email_parser.py`

**Recommended constants in `email.py`:**

```python
EMAIL_MAX_ATTACHMENTS = int(os.environ.get("SKIP_RAG_EMAIL_MAX_ATTACHMENTS", "10"))
EMAIL_MAX_CONVERTED_ATTACHMENTS = int(os.environ.get("SKIP_RAG_EMAIL_MAX_CONVERTED_ATTACHMENTS", "5"))
EMAIL_MAX_ATTACHMENT_BYTES = int(os.environ.get("SKIP_RAG_EMAIL_MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024)))
EMAIL_MAX_TOTAL_ATTACHMENT_BYTES = int(os.environ.get("SKIP_RAG_EMAIL_MAX_TOTAL_ATTACHMENT_BYTES", str(25 * 1024 * 1024)))
EMAIL_MAX_ATTACHMENT_CHARS = int(os.environ.get("SKIP_RAG_EMAIL_MAX_ATTACHMENT_CHARS", "50000"))
EMAIL_MAX_TOTAL_ATTACHMENT_CHARS = int(os.environ.get("SKIP_RAG_EMAIL_MAX_TOTAL_ATTACHMENT_CHARS", "150000"))
EMAIL_ATTACHMENT_EXT_ALLOWLIST = frozenset({
    "txt", "md", "pdf", "docx", "xlsx", "pptx",
})
```

MVP default exclusions:

- Do not convert images/OCR attachments by default; list metadata only.
- Do not convert legacy Office attachments (`.doc`, `.xls`, `.ppt`) by default; list metadata only.
- Do not recursively parse nested `.eml` / `message/rfc822`; list metadata only.
- Do not convert archives, executables, scripts, or unknown binary attachments.

**Step 1: Write failing test with fake converter**

```python
def test_eml_to_markdown_extracts_supported_attachment_with_converter():
    calls = []

    def fake_converter(data, filename, request=None):
        calls.append((data, filename))
        return "extracted attachment text"

    raw = build_email_with_attachment(
        filename="notes.txt",
        content_type="text/plain",
        data=b"hello attachment",
    )

    md = eml_to_markdown(
        raw,
        filename="with-attachment.eml",
        attachment_converter=fake_converter,
    )

    assert calls
    assert "notes.txt" in md
    assert "Status: extracted" in md
    assert "extracted attachment text" in md
```

**Step 2: Implement converter hook**

- `eml_to_markdown(..., attachment_converter=None)` accepts a callable.
- If attachment extension is allowed, MIME/magic checks are compatible, bytes are below per-file and total limits, and converted attachment count remains below limit:
  - call `attachment_converter(attachment_bytes, safe_filename, request=request)`
  - truncate returned text to `EMAIL_MAX_ATTACHMENT_CHARS`
  - enforce `EMAIL_MAX_TOTAL_ATTACHMENT_CHARS` across all attachments
  - include in fenced code block
- If converter raises:
  - mark `Status: failed`
  - include sanitized error summary
- If unsupported or too large:
  - mark skipped with reason
- Do not trust filename extension alone. At minimum, cross-check extension with `Content-Type`; add a lightweight magic-byte sniff for common formats where cheap:
  - PDF starts with `%PDF-`
  - OOXML files are zip containers but must be one of `docx/xlsx/pptx`; do not treat arbitrary zip as supported
  - plain text/Markdown should decode as text within a bounded sample

**Step 3: Avoid recursion traps**

If an attached file is itself `.eml` or has MIME type `message/rfc822`, skip conversion in MVP and list metadata only. Do not recursively parse unlimited nested messages.

---

## Task 5: Route `.eml` in `convert.py`

**Objective:** Make skip-RAG upload automatically use the email parser.

**Files:**

- Modify: `backend/open_webui/skiprag/convert.py`
- Test: `backend/open_webui/test/skiprag/test_convert_eml.py`

**Step 1: Write failing integration test**

```python
from open_webui.skiprag.convert import convert_to_markdown


def test_convert_to_markdown_routes_eml_without_docling(monkeypatch):
    def fail_docling(*args, **kwargs):
        raise AssertionError(".eml should not go to docling")

    monkeypatch.setattr("open_webui.skiprag.convert._docling_convert", fail_docling)

    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: Bob <bob@example.com>\r\n"
        b"Subject: Direct EML\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hello from eml.\r\n"
    )

    md = convert_to_markdown(raw, "direct.eml")

    assert "# Email: Direct EML" in md
    assert "Hello from eml." in md
```

**Step 2: Modify routing**

In `convert.py`:

- Add `EMAIL_EXTS = frozenset({"eml"})` near existing extension constants.
- Refactor `_convert_uncached(...)` to accept `request` and route `.eml` before legacy/docling routing.

Final intended shape:

```python
def convert_to_markdown(file_bytes_or_path, filename, request=None) -> str:
    ...
    try:
        md = _convert_uncached(raw_bytes, filename, ext, request=request)
    except Exception as exc:
        ...


def _convert_uncached(raw_bytes: bytes, filename: str, ext: str, request=None) -> str:
    if ext in EMAIL_EXTS:
        from open_webui.skiprag.email import eml_to_markdown

        return eml_to_markdown(
            raw_bytes,
            filename=filename,
            request=request,
            attachment_converter=convert_to_markdown,
        )

    if ext in LEGACY_EXT_TO_TARGET:
        ...
```

Do not show or implement an intermediate `request=None` `.eml` example; pass the real `request` from the start so attachment conversion can use the same fallback config.

**Step 3: Run tests**

```bash
cd /Users/admin/Works/kg-openwebui-stack-v2/open-webui
PYTHONPATH=backend pytest \
  backend/open_webui/test/skiprag/test_email_parser.py \
  backend/open_webui/test/skiprag/test_convert_eml.py \
  -q
```

---

## Task 6: Add Safety Tests

**Objective:** Verify malformed emails and risky attachments degrade safely.

**Files:**

- Modify: `backend/open_webui/test/skiprag/test_email_parser.py`

Test cases:

1. Missing subject/from/to still returns Markdown.
2. Invalid charset does not crash.
3. Attachment filename `../../evil.pdf` is displayed as `evil.pdf` or safe fallback.
4. Oversized attachment is skipped.
5. Unsupported `.exe` attachment is skipped.
6. Converter exception marks one attachment failed but keeps email body.
7. Parser-level exception or malformed message returns non-empty Markdown with `## Parse Warnings`.
8. `multipart/alternative` prefers `text/plain` over `text/html`.
9. RFC2047 encoded subject/display names decode correctly.
10. RFC2231 encoded attachment filename (`filename*=`) decodes and sanitizes correctly.
11. Nested `message/rfc822` attachment is metadata-only and is not recursively parsed.
12. Attachment bomb cases enforce:
    - max attachment count
    - max converted attachment count
    - max total attachment bytes
    - max total extracted chars
13. Renamed malicious binary with allowed-looking extension is skipped by MIME/magic mismatch.
14. `convert_to_markdown(...)` tests monkeypatch `open_webui.skiprag.convert.CACHE_DIR` to a temp directory to avoid stale cache and test pollution.
15. PDF/page or converter timeout guard behavior is covered at least with a fake converter that raises/timeouts and gets marked failed.

Verification:

```bash
PYTHONPATH=backend pytest backend/open_webui/test/skiprag -q
```

---

## Task 7: Manual End-to-End Verification

**Objective:** Prove the real skip-RAG path injects `.eml` content and does not use native RAG.

**Files:**

- No code changes.

Manual checks:

1. Start stack normally.
2. Select a model with `skip_rag` capability.
3. Upload a small `.eml` containing:
   - plain body
   - subject/from/to/date
   - one `.txt` attachment
4. Ask: `請整理這封 email 的寄件者、主旨、正文和附件內容。`
5. Expected:
   - answer includes email metadata/body
   - answer includes safe attachment text
   - logs include `skip-rag: Path A done`
   - logs do not show vector DB save for this file

---

## Edge Cases / Pitfalls

- Multipart emails often contain both `text/plain` and `text/html`; prefer plain text to avoid noisy HTML.
- Inline images often have filenames but should usually be skipped unless treated as attachments explicitly.
- Outlook/Apple Mail may emit unusual charsets; use stdlib policy decoding and fallback gracefully.
- Do not render raw HTML in Markdown output.
- Do not let one corrupt attachment fail the email.
- Keep attachment extraction bounded. Email attachments can be huge and skip-RAG already has large-context pressure.
- Bound both per-attachment and total attachment cost; many small attachments can still DoS Docling/OCR.
- Do not trust attachment filenames alone; check extension, MIME type, and cheap magic bytes where possible.
- Sanitize headers and filenames before Markdown output to avoid control-character, fence, or `<source>` confusion.
- Isolate conversion cache in tests by monkeypatching `CACHE_DIR` to `tmp_path`.
- Do not add `.eml` to `PLAINTEXT_EXTS`; it needs structured parsing, not raw text passthrough.

---

## Recommended Acceptance Criteria

- `.eml` upload in skip-RAG produces a Markdown source with metadata and body.
- `.eml` does not call Docling for the email container itself.
- Narrowly supported attachments can be extracted via existing converter under per-file and total limits.
- Unsupported/large/malformed attachments are listed but skipped or marked failed.
- Malformed `.eml` input still returns non-empty Markdown with parse warnings.
- Attachment extraction cannot recursively parse nested emails or convert unbounded attachment sets.
- All parser tests pass locally.
- Existing skip-RAG behavior for PDF/DOCX/TXT remains unchanged.

---

## Suggested Implementation Order

1. Build and test `email.py` basic metadata/body parser.
2. Add HTML fallback.
3. Add attachment metadata.
4. Add attachment converter hook with limits.
5. Route `.eml` in `convert.py`.
6. Add safety tests.
7. Manual end-to-end upload test.

This keeps the first deliverable narrow: one `.eml` equals one skip-RAG source, optionally enriched with safe attachment text.
