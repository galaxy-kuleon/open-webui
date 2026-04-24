"""
Docling CLI subprocess utility.

Converts PDF, DOCX, PPTX, XLSX files to Markdown via the docling CLI.
Pure async function — takes a file path, returns markdown content as a string.
"""

import asyncio
import logging
import os
import signal
import shutil
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Immutable set of supported extensions (lowercase, with dot)
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".pptx", ".xlsx"})

DOCLING_BINARY: str = str(Path.home() / ".local" / "bin" / "docling")

# Default timeout in seconds for the docling subprocess
DEFAULT_TIMEOUT_SECONDS: int = 300


class DoclingError(Exception):
    """Raised when docling conversion fails."""


class DoclingTimeoutError(DoclingError):
    """Raised when docling subprocess exceeds the timeout."""


class DoclingUnsupportedFileError(DoclingError):
    """Raised when the input file type is not supported."""


def _validate_source_file(source_path: Path) -> None:
    """Validate that the source file exists and has a supported extension.

    Raises DoclingUnsupportedFileError or DoclingError on validation failure.
    """
    if not source_path.exists():
        raise DoclingError(f"Source file does not exist: {source_path}")

    ext = source_path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise DoclingUnsupportedFileError(
            f"Unsupported file type '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )


def _find_output_markdown(output_dir: Path, source_stem: str) -> Path:
    """Locate the generated markdown file in the output directory.

    Docling names the output file based on the input filename stem.
    Returns the Path to the .md file or raises DoclingError.
    """
    expected = output_dir / f"{source_stem}.md"
    if expected.is_file():
        return expected

    # Fallback: look for any .md file in the output directory
    md_files = list(output_dir.glob("*.md"))
    if len(md_files) == 1:
        return md_files[0]
    if len(md_files) > 1:
        log.warning(
            "Multiple .md files found in docling output dir: %s. Using first.",
            [str(f) for f in md_files],
        )
        return md_files[0]

    raise DoclingError(
        f"No .md output file found in {output_dir}. "
        f"Expected: {expected}. Contents: {list(output_dir.iterdir())}"
    )


async def convert_to_markdown(
    source_path: str | Path,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ocr_languages: tuple[str, ...] = ("zh-Hant", "zh-Hans", "en-US"),
    docling_binary: Optional[str] = None,
) -> str:
    """Convert a document file to Markdown using the docling CLI.

    Args:
        source_path: Path to the input file (PDF, DOCX, PPTX, or XLSX).
        timeout_seconds: Maximum time to wait for the subprocess (default 300s).
        ocr_languages: OCR language codes passed to docling --ocr-lang.
        docling_binary: Override path to the docling binary (for testing).

    Returns:
        The converted markdown content as a string.

    Raises:
        DoclingUnsupportedFileError: If the file type is not supported.
        DoclingTimeoutError: If the subprocess exceeds the timeout.
        DoclingError: If the subprocess fails or no output is produced.
    """
    src = Path(source_path).resolve()
    _validate_source_file(src)

    # Use provided binary or module default
    binary = docling_binary or DOCLING_BINARY

    output_dir = Path(tempfile.mkdtemp(prefix="docling_"))
    try:
        cmd = [
            binary,
            "--to", "md",
            "--image-export-mode", "placeholder",
            "--ocr-engine", "ocrmac",
            "--ocr-lang", ",".join(ocr_languages),
            "--output", str(output_dir),
            str(src),
        ]

        log.info("Running docling: %s", " ".join(cmd))

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # Own process group so timeout can kill all children
            )
        except (FileNotFoundError, OSError) as e:
            raise DoclingError(
                f"Failed to start docling binary at '{binary}': {e}"
            ) from e

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            # Kill the entire process group (docling + any children it spawned).
            # process.kill() alone only kills the top-level process; children
            # survive and hold pipes open, causing process.wait() to hang.
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                # Process group already gone — race condition is benign
                try:
                    process.kill()
                except (ProcessLookupError, OSError):
                    pass  # Both process group and process gone — benign
            await process.wait()
            raise DoclingTimeoutError(
                f"Docling subprocess timed out after {timeout_seconds}s "
                f"for file: {src}"
            )

        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            raise DoclingError(
                f"Docling failed with return code {process.returncode} "
                f"for file: {src}. stderr: {stderr_text}"
            )

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        if stdout_text:
            log.debug("Docling stdout: %s", stdout_text[:500])

        md_path = _find_output_markdown(output_dir, src.stem)
        markdown_content = md_path.read_text(encoding="utf-8")

        log.info(
            "Docling conversion complete: %s -> %d chars of markdown",
            src.name,
            len(markdown_content),
        )

        return markdown_content

    finally:
        # Always clean up the temp directory
        shutil.rmtree(output_dir, ignore_errors=True)
