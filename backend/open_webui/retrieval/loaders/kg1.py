import os
import glob
import shutil
import subprocess
import tempfile
import threading
import logging
from pathlib import Path
from typing import List, Optional

from langchain_core.documents import Document
from fastapi import HTTPException, status

log = logging.getLogger(__name__)

# Module-level semaphore for concurrency control
_semaphore: Optional[threading.Semaphore] = None
_semaphore_value: int = 0
_semaphore_lock = threading.Lock()

# File extensions that glm-ocr can process directly (no LibreOffice conversion needed)
DIRECT_OCR_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "bmp", "gif", "webp"}

# Plain text extensions: read directly, skip soffice + glm-ocr entirely
PLAINTEXT_EXTENSIONS = {
    # Markup / prose
    "txt", "md", "rst", "org", "adoc",
    # Data / config
    "json", "yaml", "yml", "toml", "ini", "conf", "env", "csv", "tsv",
    # Programming languages
    "py", "js", "ts", "jsx", "tsx", "rs", "go", "java", "c", "cpp", "h", "hpp",
    "cs", "swift", "dart", "rb", "pl", "pm", "php", "lua", "r", "scala",
    "ex", "exs", "erl", "hs", "lhs", "sh", "bash", "bat", "ps1", "cmd",
    # Web / style
    "css", "html", "htm", "xml", "svg", "vue", "svelte",
    # Database / query
    "sql", "plsql", "db2",
    # Other
    "log", "dockerfile", "makefile", "cmake",
}

# Extensions whose content is already markdown or plain text — use as-is
RAW_MARKDOWN_EXTENSIONS = {"md", "txt", "rst", "org", "adoc"}

# Map file extension → markdown code fence language identifier
EXTENSION_LANG_MAP = {
    "py": "python", "js": "javascript", "ts": "typescript",
    "jsx": "jsx", "tsx": "tsx", "rs": "rust", "go": "go",
    "java": "java", "rb": "ruby", "sh": "bash", "bash": "bash",
    "ps1": "powershell", "bat": "batch", "cmd": "batch",
    "css": "css", "html": "html", "htm": "html", "xml": "xml",
    "svg": "xml", "sql": "sql", "json": "json", "yaml": "yaml",
    "yml": "yaml", "toml": "toml", "ini": "ini", "conf": "ini",
    "c": "c", "cpp": "cpp", "h": "c", "hpp": "cpp", "cs": "csharp",
    "swift": "swift", "dart": "dart", "lua": "lua", "r": "r",
    "scala": "scala", "ex": "elixir", "exs": "elixir", "erl": "erlang",
    "hs": "haskell", "lhs": "haskell", "pl": "perl", "pm": "perl",
    "php": "php", "vue": "vue", "svelte": "svelte",
    "dockerfile": "dockerfile", "makefile": "makefile", "cmake": "cmake",
}


def _get_semaphore(concurrency: int) -> threading.Semaphore:
    """Get or recreate the module-level semaphore if concurrency value changed."""
    global _semaphore, _semaphore_value
    with _semaphore_lock:
        if _semaphore is None or _semaphore_value != concurrency:
            _semaphore = threading.Semaphore(max(1, concurrency))
            _semaphore_value = concurrency
            log.info(f"KG1 task queue semaphore set to concurrency={concurrency}")
        return _semaphore


class KG1Loader:
    """
    KG1 document loader using glm-ocr for OCR-based content extraction.

    Supports all file formats via two-stage processing:
    1. Non-PDF/image files are converted to PDF using LibreOffice (headless soffice)
    2. PDF/image files are processed by glm-ocr (via local Ollama) to extract markdown

    Concurrency is controlled by a module-level threading.Semaphore to prevent
    GPU/CPU overload from multiple simultaneous glm-ocr processes.
    """

    def __init__(
        self,
        file_path: str,
        glmocr_project_dir: str,
        ollama_host: str = "127.0.0.1",
        ollama_port: int = 11434,
        layout_device: str = "mps",
        soffice_path: str = "soffice",
        timeout: int = 600,
        concurrency: int = 1,
        status_callback: Optional[callable] = None,
    ):
        self.file_path = file_path
        self.glmocr_project_dir = glmocr_project_dir
        self.ollama_host = ollama_host
        self.ollama_port = ollama_port
        self.layout_device = layout_device
        self.soffice_path = soffice_path
        self.timeout = timeout
        self.concurrency = concurrency
        self.status_callback = status_callback

    def load(self) -> List[Document]:
        """
        Load and process the document through glm-ocr pipeline.
        Acquires semaphore to control concurrency before processing.
        """
        semaphore = _get_semaphore(self.concurrency)
        log.info(f"KG1: Waiting for task queue slot (concurrency={self.concurrency})")

        semaphore.acquire()
        try:
            return self._process()
        finally:
            semaphore.release()

    def _read_as_text(self, file_path: str, file_ext: str) -> Optional[str]:
        """
        Try reading a file as UTF-8 text. Returns formatted content on success,
        or None if the file is not valid UTF-8 (falls through to OCR).
        """
        filename = os.path.basename(file_path)
        try:
            with open(file_path, "r", encoding="utf-8-sig", errors="strict") as f:
                content = f.read()
        except (UnicodeDecodeError, UnicodeError):
            log.warning(
                f"KG1: '{filename}' has .{file_ext} extension but is not valid UTF-8, "
                f"falling back to OCR"
            )
            return None
        except OSError as e:
            log.error(f"KG1: Failed to read '{filename}': {e}")
            return None

        if not content or not content.strip():
            return None

        # .md, .txt, .rst etc: use content as-is (already prose/markdown)
        if file_ext in RAW_MARKDOWN_EXTENSIONS:
            return content

        # Code/data files: wrap in fenced code block with language annotation
        lang = EXTENSION_LANG_MAP.get(file_ext, file_ext)
        return f"```{lang}\n{content}\n```"

    @staticmethod
    def _get_pdf_page_count(pdf_path: str) -> Optional[int]:
        """Get page count from a PDF file. Returns None if not a PDF or unreadable."""
        try:
            with open(pdf_path, "rb") as f:
                # Quick scan for /Count in PDF trailer (avoid heavy deps)
                # Fallback: count /Type /Page occurrences
                data = f.read()
            import re
            # Try /Count N pattern (most reliable)
            counts = re.findall(rb"/Count\s+(\d+)", data)
            if counts:
                return max(int(c) for c in counts)
            # Fallback: count page objects
            pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
            return pages if pages > 0 else None
        except Exception:
            return None

    def _calculate_timeout(self, ocr_input_path: str) -> int:
        """Calculate dynamic timeout based on PDF page count. ~60s per page, min 300s."""
        page_count = self._get_pdf_page_count(ocr_input_path)
        if page_count is None:
            return self.timeout

        dynamic = max(300, page_count * 60)
        log.info(f"KG1: {page_count} pages detected, timeout set to {dynamic}s")
        return dynamic

    def _process(self) -> List[Document]:
        """Core processing logic: read text directly, convert if needed, then OCR."""
        filename = os.path.basename(self.file_path)
        file_ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        log.info(f"KG1: Processing file '{filename}' (ext={file_ext})")

        # Fast path: plain text files — read directly, skip soffice + glm-ocr
        if file_ext in PLAINTEXT_EXTENSIONS:
            content = self._read_as_text(self.file_path, file_ext)
            if content is not None:
                log.info(
                    f"KG1: Direct text read for '{filename}': "
                    f"{len(content)} chars (skipped OCR)"
                )
                metadata = {
                    "source": filename,
                    "processing_engine": "kg1",
                    "processing_mode": "direct_text",
                }
                return [Document(page_content=content, metadata=metadata)]
            log.info(f"KG1: '{filename}' failed UTF-8 validation, proceeding with OCR")

        with tempfile.TemporaryDirectory(prefix="kg1_") as work_dir:
            # Step 1: Determine the file to OCR
            if file_ext in DIRECT_OCR_EXTENSIONS:
                ocr_input_path = self.file_path
            else:
                ocr_input_path = self._convert_to_pdf(self.file_path, work_dir)

            # Step 2: Calculate dynamic timeout based on page count, then run glm-ocr
            ocr_timeout = self._calculate_timeout(ocr_input_path)
            output_dir = os.path.join(work_dir, "output")
            os.makedirs(output_dir, exist_ok=True)
            self._run_glmocr(ocr_input_path, output_dir, timeout=ocr_timeout)

            # Step 3: Read markdown result
            markdown_content = self._read_markdown_output(output_dir)

            # Step 4: Collect image info
            image_count = self._count_images(output_dir)

        if not markdown_content or not markdown_content.strip():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"KG1: glm-ocr returned empty content for '{filename}'",
            )

        log.info(
            f"KG1: Successfully extracted {len(markdown_content)} chars, "
            f"{image_count} images from '{filename}'"
        )

        metadata = {
            "source": filename,
            "processing_engine": "kg1",
            "layout_device": self.layout_device,
            "image_count": image_count,
        }

        return [Document(page_content=markdown_content, metadata=metadata)]

    def _convert_to_pdf(self, file_path: str, work_dir: str) -> str:
        """Convert a non-PDF file to PDF using LibreOffice headless mode."""
        filename = os.path.basename(file_path)
        log.info(f"KG1: Converting '{filename}' to PDF via LibreOffice")

        convert_dir = os.path.join(work_dir, "converted")
        os.makedirs(convert_dir, exist_ok=True)

        cmd = [
            self.soffice_path,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            convert_dir,
            file_path,
        ]

        soffice_stderr = []

        def _drain_soffice(stream, label, collect=None):
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    log.info(f"[soffice:{label}] {line[:5000]}")
                if collect is not None:
                    collect.append(line)
            stream.close()

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "LibreOffice (soffice) not found. "
                    "Install LibreOffice for non-PDF file conversion, "
                    f"or check the configured path: '{self.soffice_path}'"
                ),
            )

        t_out = threading.Thread(target=_drain_soffice, args=(proc.stdout, "stdout"), daemon=True)
        t_err = threading.Thread(target=_drain_soffice, args=(proc.stderr, "stderr", soffice_stderr), daemon=True)
        t_out.start()
        t_err.start()

        try:
            proc.wait(timeout=min(self.timeout, 120))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"KG1: LibreOffice conversion timed out for '{filename}'",
            )

        t_out.join(timeout=10)
        t_err.join(timeout=10)

        if proc.returncode != 0:
            stderr = "\n".join(soffice_stderr) or "No error output"
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"KG1: LibreOffice conversion failed for '{filename}': "
                    f"{stderr[:500]}"
                ),
            )

        # Find the converted PDF
        pdf_files = glob.glob(os.path.join(convert_dir, "*.pdf"))
        if not pdf_files:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"KG1: LibreOffice produced no PDF output for '{filename}'",
            )

        pdf_path = pdf_files[0]
        log.info(f"KG1: Converted to PDF: {os.path.basename(pdf_path)}")
        return pdf_path

    def _run_glmocr(self, input_path: str, output_dir: str, timeout: Optional[int] = None) -> None:
        """Run glm-ocr via subprocess using uv run."""
        filename = os.path.basename(input_path)
        log.info(f"KG1: Running glm-ocr on '{filename}'")

        env = os.environ.copy()
        # Remove venv/conda env vars that would cause uv run to resolve
        # the wrong Python interpreter or site-packages in the glm-ocr project
        for key in [
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
            "CONDA_DEFAULT_ENV",
            "PYTHONHOME",
            "PYTHONPATH",
            "UV_PROJECT_ENVIRONMENT",
        ]:
            env.pop(key, None)
        env["GLMOCR_OCR_API_HOST"] = self.ollama_host
        env["GLMOCR_OCR_API_PORT"] = str(self.ollama_port)
        env["GLMOCR_MODE"] = "selfhosted"
        env["GLMOCR_ENABLE_LAYOUT"] = "true"
        env["GLMOCR_LAYOUT_DEVICE"] = self.layout_device

        cmd = [
            "uv",
            "run",
            "glmocr",
            "parse",
            input_path,
            "--output",
            output_dir,
            "--mode",
            "selfhosted",
            "--no-layout-vis",
            "--log-level",
            "WARNING",
        ]

        effective_timeout = timeout or self.timeout
        stderr_lines = []
        page_count = self._get_pdf_page_count(input_path)

        if self.status_callback and page_count:
            self.status_callback(f"processing:extracting (OCR {page_count} pages)")

        def _drain(stream, label, collect=None):
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    log.info(f"[glm-ocr:{label}] {line[:5000]}")
                    # Update progress from glm-ocr stderr hints
                    if self.status_callback and label == "stderr":
                        low = line.lower()
                        if "pipeline started" in low:
                            self.status_callback(f"processing:extracting (OCR running, {page_count or '?'} pages)")
                        elif "loading weights" in low and "100%" in line:
                            self.status_callback("processing:extracting (model loaded, starting OCR)")
                if collect is not None:
                    collect.append(line)
            stream.close()

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.glmocr_project_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "uv command not found. Install uv (https://docs.astral.sh/uv/) "
                    "to use the KG1 engine."
                ),
            )

        t_out = threading.Thread(target=_drain, args=(proc.stdout, "stdout"), daemon=True)
        t_err = threading.Thread(target=_drain, args=(proc.stderr, "stderr", stderr_lines), daemon=True)
        t_out.start()
        t_err.start()

        try:
            proc.wait(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"KG1: glm-ocr processing timed out after {effective_timeout}s for '{filename}'",
            )

        t_out.join(timeout=10)
        t_err.join(timeout=10)

        if proc.returncode != 0:
            stderr = "\n".join(stderr_lines) or "No error output"
            log.error(f"KG1: glm-ocr failed: {stderr}")
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=f"KG1: glm-ocr processing failed for '{filename}': {stderr[:500]}",
            )

        log.info(f"KG1: glm-ocr completed successfully for '{filename}'")

    def _read_markdown_output(self, output_dir: str) -> str:
        """Find and read the markdown output file from glm-ocr results."""
        # glm-ocr output structure: {output_dir}/{stem}/{stem}.md
        md_files = []
        for root, dirs, files in os.walk(output_dir):
            for f in files:
                if f.endswith(".md"):
                    md_files.append(os.path.join(root, f))

        if not md_files:
            # List what's actually in the output dir for debugging
            all_files = []
            for root, dirs, files in os.walk(output_dir):
                for f in files:
                    all_files.append(os.path.join(root, f))
            log.error(f"KG1: No .md files found. Available files: {all_files}")
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=f"KG1: glm-ocr produced no markdown output. Files found: {all_files}",
            )

        # Use the first .md file found
        md_path = md_files[0]
        log.info(f"KG1: Reading markdown from {md_path}")

        with open(md_path, "r", encoding="utf-8") as f:
            content = f.read()

        return content

    def _count_images(self, output_dir: str) -> int:
        """Count extracted images in the output directory."""
        image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
        count = 0
        for root, dirs, files in os.walk(output_dir):
            for f in files:
                if Path(f).suffix.lower() in image_extensions:
                    count += 1
        return count
