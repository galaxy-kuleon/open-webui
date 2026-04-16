"""
Image Analysis Pipeline

Automatically analyzes uploaded images:
1. Classifies as OCR-text vs photo/diagram using a fast vision model
2. Routes to KG1 (GLM-OCR) for text extraction or vision model for description
3. If KG1 is not configured, falls back to vision model for OCR transcription
4. Stores result in file.data.content for RAG and non-vision model fallback
"""

import asyncio
import base64
import logging
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from PIL import Image

log = logging.getLogger(__name__)

CLASSIFY_PROMPT = (
    'Look at this image. Reply with exactly one word:\n'
    '"OCR" if it is primarily text content '
    '(documents, scanned pages, screenshots of text, receipts, code).\n'
    '"VISUAL" if it is a photo, diagram, chart, illustration, '
    'or other visual content.'
)

DESCRIBE_PROMPT = (
    "Describe this image in detail. Include: what it shows, key elements, "
    "layout, any visible text, spatial relationships, and context. "
    "Be thorough. Use plain text."
)

VISION_OCR_FALLBACK_PROMPT = (
    "Transcribe ALL text visible in this image. "
    "Preserve the original layout, formatting, and structure as closely "
    "as possible. Output as markdown."
)


def resize_image_for_analysis(file_path: str, max_width: int = 2000) -> str:
    """
    Resize image to max_width (keeping aspect ratio), return base64 data URI.

    If image is already within max_width, encodes as-is preserving original format.
    RGBA images are converted to RGB for JPEG compatibility.
    """
    img = Image.open(file_path)
    original_format = img.format or "JPEG"

    if img.width > max_width:
        ratio = max_width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.LANCZOS)

    # JPEG can't handle alpha channel
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
        output_format = "JPEG"
        mime = "image/jpeg"
    else:
        output_format = original_format if original_format in ("JPEG", "PNG", "WEBP") else "JPEG"
        mime = f"image/{output_format.lower()}"

    buf = BytesIO()
    img.save(buf, format=output_format, quality=85)
    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:{mime};base64,{encoded}"


def _build_vision_messages(
    prompt: str, image_b64_uri: str
) -> list[dict]:
    """Build OpenAI-format multimodal messages for a vision LLM call."""
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_b64_uri}},
            ],
        }
    ]


def call_vision_llm(
    app: Any,
    prompt: str,
    image_b64_uri: str,
    model_id: str,
    timeout: float = 120.0,
) -> str:
    """
    Synchronous bridge for multimodal LLM call.

    Schedules the async _async_llm_completion on the main event loop
    and blocks until result or timeout.
    """
    from open_webui.utils.knowledge_export import _async_llm_completion

    loop = getattr(getattr(app, "state", None), "main_loop", None)
    if loop is None or loop.is_closed():
        raise RuntimeError("Main event loop not available (app.state.main_loop)")

    messages = _build_vision_messages(prompt, image_b64_uri)

    future = asyncio.run_coroutine_threadsafe(
        _async_llm_completion(app, messages, model_id),
        loop,
    )

    try:
        return future.result(timeout=timeout)
    except TimeoutError:
        future.cancel()
        raise TimeoutError(
            f"Vision LLM timed out after {timeout}s (model={model_id})"
        )


def _run_kg1_ocr(app: Any, stored_file_path: str, file_id: str) -> str:
    """
    Run KG1Loader on the original image file, return extracted markdown text.

    Uses the original (non-resized) image for maximum OCR quality.
    """
    from open_webui.retrieval.loaders.kg1 import KG1Loader
    from open_webui.models.files import Files

    config = app.state.config

    kg1_timeout = config.KG1_TIMEOUT or "600"
    try:
        kg1_timeout = int(kg1_timeout)
    except (ValueError, TypeError):
        kg1_timeout = 600

    kg1_port = config.KG1_OLLAMA_PORT or "11434"
    try:
        kg1_port = int(kg1_port)
    except (ValueError, TypeError):
        kg1_port = 11434

    kg1_concurrency = config.KG1_GLM_OCR_CONCURRENCY or "1"
    try:
        kg1_concurrency = int(kg1_concurrency)
    except (ValueError, TypeError):
        kg1_concurrency = 1

    loader = KG1Loader(
        file_path=stored_file_path,
        glmocr_project_dir=config.KG1_GLMOCR_PROJECT_DIR,
        ollama_host=config.KG1_OLLAMA_HOST or "127.0.0.1",
        ollama_port=kg1_port,
        layout_device=config.KG1_LAYOUT_DEVICE or "mps",
        soffice_path=config.KG1_SOFFICE_PATH or "soffice",
        timeout=kg1_timeout,
        concurrency=kg1_concurrency,
        status_callback=lambda s: Files.update_file_data_by_id(
            file_id, {"status": s}
        ),
    )

    docs = loader.load()
    return "\n\n".join(doc.page_content for doc in docs if doc.page_content)


def _run_vision_ocr_fallback(
    app: Any, image_b64_uri: str, model_id: str
) -> str:
    """
    Fallback OCR: use vision model to transcribe text when KG1 is not configured.
    """
    return call_vision_llm(
        app, VISION_OCR_FALLBACK_PROMPT, image_b64_uri, model_id, timeout=180.0
    )


def _is_kg1_configured(config) -> bool:
    """Check if KG1 (GLM-OCR) is properly configured."""
    return bool(getattr(config, "KG1_GLMOCR_PROJECT_DIR", None))


def analyze_image(
    app: Any,
    file_id: str,
    file_path: str,
    content_type: str,
) -> None:
    """
    Main entry point for image analysis. Called from process_uploaded_file
    as a background task.

    1. Resize image for classifier (width <= max_classify_width)
    2. Classify: OCR or VISUAL
    3. OCR → KG1 pipeline (or vision fallback if KG1 not configured)
    4. VISUAL → vision model description
    5. Store result in file.data.content
    """
    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    config = app.state.config

    if not getattr(config, "IMAGE_ANALYSIS_ENABLED", False):
        log.debug(f"Image analysis disabled, skipping {file_id}")
        return

    model_id = getattr(config, "IMAGE_ANALYSIS_CLASSIFIER_MODEL", "")
    if not model_id:
        log.warning(f"IMAGE_ANALYSIS_CLASSIFIER_MODEL not set, skipping {file_id}")
        return

    max_width = getattr(config, "IMAGE_ANALYSIS_MAX_CLASSIFY_WIDTH", 2000)

    try:
        # Get actual file from storage
        stored_path = Storage.get_file(file_path)
        stored_path = str(stored_path) if stored_path else file_path

        # Step 1: Resize for classifier
        Files.update_file_data_by_id(file_id, {"status": "processing:classifying"})
        log.info(f"Image analysis: classifying {file_id} (max_width={max_width})")

        image_b64 = resize_image_for_analysis(stored_path, max_width)

        # Step 2: Classify
        classification_response = call_vision_llm(
            app, CLASSIFY_PROMPT, image_b64, model_id, timeout=60.0
        )
        is_ocr = "ocr" in classification_response.strip().lower()
        log.info(
            f"Image analysis: {file_id} classified as "
            f"{'OCR' if is_ocr else 'VISUAL'} "
            f"(raw: {classification_response.strip()[:50]})"
        )

        # Step 3: Process based on classification
        if is_ocr:
            if _is_kg1_configured(config):
                Files.update_file_data_by_id(
                    file_id, {"status": "processing:extracting (OCR)"}
                )
                log.info(f"Image analysis: running KG1 OCR on {file_id}")
                content = _run_kg1_ocr(app, stored_path, file_id)
            else:
                Files.update_file_data_by_id(
                    file_id, {"status": "processing:extracting (vision fallback)"}
                )
                log.info(f"Image analysis: KG1 not configured, using vision OCR fallback for {file_id}")
                content = _run_vision_ocr_fallback(app, image_b64, model_id)
        else:
            Files.update_file_data_by_id(
                file_id, {"status": "processing:describing"}
            )
            log.info(f"Image analysis: describing {file_id}")
            content = call_vision_llm(
                app, DESCRIBE_PROMPT, image_b64, model_id, timeout=120.0
            )

        if not content or not content.strip():
            raise RuntimeError("Image analysis produced empty content")

        # Step 4: Store result
        analysis_type = "ocr" if is_ocr else "description"
        Files.update_file_data_by_id(
            file_id,
            {
                "content": content.strip(),
                "image_analysis_type": analysis_type,
                "status": "completed",
            },
        )
        log.info(
            f"Image analysis: {file_id} completed ({analysis_type}, "
            f"{len(content)} chars)"
        )

    except Exception as e:
        log.error(f"Image analysis failed for {file_id}: {e}")
        Files.update_file_data_by_id(
            file_id,
            {
                "status": "failed",
                "error": str(e),
            },
        )
