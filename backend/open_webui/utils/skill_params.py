"""
Skill Parameter Extraction

Before dispatching a chat to an agent skill (e.g. "anything-to-docx"),
this module uses an internal LLM call to analyze the full chat context
and extract structured parameters that enrich the skill prompt.

Returns a plain dict — never raises on failure (returns {} instead).
"""

import json
import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

# The expected keys in the extraction result.
# Each is an optional string — None if the LLM could not detect it.
PARAM_KEYS = frozenset({"lang", "style", "terms", "context_summary", "task_intent"})

EXTRACTION_SYSTEM_PROMPT = """\
You are a parameter extraction assistant. You will receive a chat conversation \
and a skill name. Analyze the FULL conversation — not just the last message — \
and extract structured parameters as a single JSON object.

Fields to extract:

- "lang": target output language as ISO 639-1 code or BCP-47 tag. \
Infer from explicit requests ("翻成日文" → "ja", "translate to Traditional Chinese" → "zh-TW"). \
If the user only writes in one language without requesting translation, set to that language. \
null if truly ambiguous.

- "style": style or tone preferences. Examples: "formal", "casual", "academic", \
"technical", "bullet points", "concise". Infer from explicit requests or from the \
register the user is writing in. null if no preference detected.

- "terms": domain-specific terms, glossary entries, or term mappings the user mentioned \
that should be preserved as-is or translated consistently. Format as a string: \
"RAG=檢索增強生成, embedding=嵌入向量" for mappings, or "Kubernetes, pod, ReplicaSet" \
for terms to preserve. null if none detected.

- "context_summary": 1-3 sentence summary of the FULL conversation context. \
What is the user working on? What documents or topics have been discussed? \
This helps the skill understand the broader situation.

- "task_intent": synthesize what the user wants the skill to do. Combine information \
from multiple messages if the intent was built up over the conversation. Be specific. \
E.g. "Convert the uploaded PDF about neural networks to DOCX and translate to Japanese \
with formal academic style."

Output ONLY a valid JSON object. No markdown fences, no commentary, no extra text.

--- FEW-SHOT EXAMPLES ---

Chat: [USER: 我有一份關於 RAG pipeline 的技術報告，請幫我翻成日文，用學術風格。]
Skill: anything-to-docx
Output: {"lang": "ja", "style": "academic", "terms": "RAG=検索拡張生成, pipeline=パイプライン", "context_summary": "User has a technical report about RAG pipelines and wants it translated.", "task_intent": "Convert the technical report to DOCX and translate to Japanese with formal academic style."}

Chat: [USER: Here's my meeting notes PDF. Can you convert it to Word?]
Skill: anything-to-docx
Output: {"lang": "en", "style": null, "terms": null, "context_summary": "User uploaded meeting notes in PDF format.", "task_intent": "Convert the uploaded meeting notes PDF to DOCX format."}

Chat: [USER: 我們在做一個 Kubernetes 的部署文件。] [ASSISTANT: 好的，我可以幫你處理。需要什麼格式？] [USER: 轉成 Word，保持技術術語不要翻譯，但其他部分翻成繁體中文，正式一點。]
Skill: anything-to-docx
Output: {"lang": "zh-TW", "style": "formal", "terms": "Kubernetes, pod, ReplicaSet, deployment", "context_summary": "User is working on Kubernetes deployment documentation across multiple messages. They want technical terms preserved untranslated.", "task_intent": "Convert the Kubernetes deployment document to DOCX, translate non-technical content to Traditional Chinese in formal style, preserving technical terms untranslated."}
"""


def _format_chat_for_extraction(messages: list[dict], skill_name: str) -> str:
    """Format chat messages into a readable string for the extraction LLM.

    Pure function: messages in, string out. No mutation.
    """
    parts = [f"Skill to be invoked: {skill_name}", "", "Chat history:"]
    for msg in messages:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        # Content can be a string or a list of content parts
        if isinstance(content, list):
            text_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    text_parts.append(part)
            content = "\n".join(text_parts)
        parts.append(f"{role}: {content}")
    return "\n".join(parts)


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", re.DOTALL)


def _strip_markdown_fences(text: str) -> str:
    """Strip markdown code fences (```json ... ```) if present.

    Pure function. Returns the inner content or the original text unchanged.
    """
    match = _FENCE_RE.match(text.strip())
    return match.group(1).strip() if match else text.strip()


def _parse_extraction_response(raw: Any) -> dict:
    """Parse the LLM's JSON response into a validated params dict.

    Pure function. Returns a dict with PARAM_KEYS, each value is Optional[str].
    Returns {} on any parse failure — including non-string input.
    Handles markdown fence wrapping (```json ... ```).
    """
    if not isinstance(raw, str):
        log.warning("Skill params: expected str, got %s", type(raw).__name__)
        return {}

    cleaned = _strip_markdown_fences(raw)

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        log.warning("Skill params: failed to parse LLM response as JSON: %s", raw[:200])
        return {}

    if not isinstance(data, dict):
        log.warning("Skill params: LLM response is not a dict: %s", type(data))
        return {}

    # Extract only the expected keys, normalize values to Optional[str].
    # Empty/whitespace strings are normalized to None — they carry no semantic
    # value and would produce degraded prompt output (blank labels).
    result = {}
    for key in PARAM_KEYS:
        val = data.get(key)
        val_str = str(val).strip() if val is not None else None
        result[key] = val_str if val_str else None
    return result


async def _call_llm(app: Any, messages: list[dict], model_id: str) -> str:
    """Internal LLM completion following the _async_llm_completion pattern
    from knowledge_export.py.

    Returns the raw content string from the LLM response.
    Raises RuntimeError on failure (caller catches).
    """
    from starlette.datastructures import Headers
    from starlette.requests import Request

    from open_webui.models.users import Users
    from open_webui.utils.chat import generate_chat_completion

    admin_user = Users.get_super_admin_user()
    if admin_user is None:
        admin_user = Users.get_first_user()
    if admin_user is None:
        raise RuntimeError("No admin user available for skill param extraction")

    request = Request(
        {
            "type": "http",
            "asgi.version": "3.0",
            "asgi.spec_version": "2.0",
            "method": "POST",
            "path": "/internal/skill-param-extraction",
            "query_string": b"",
            "headers": Headers({}).raw,
            "client": ("127.0.0.1", 0),
            "server": ("127.0.0.1", 80),
            "scheme": "http",
            "app": app,
        }
    )

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": False,
        "metadata": {"task": "skill_param_extraction"},
    }

    response = await generate_chat_completion(
        request, form_data=payload, user=admin_user, bypass_filter=True
    )

    # Handle the 3 response types (dict, StreamingResponse, JSONResponse)
    # following the exact pattern from knowledge_export.py
    if isinstance(response, dict) and "choices" in response:
        content = (
            response["choices"][0].get("message", {}).get("content", "")
            if response["choices"]
            else ""
        )
    elif hasattr(response, "body_iterator"):
        # StreamingResponse — drain and extract content
        content = None
        async for chunk in response.body_iterator:
            data = json.loads(chunk.decode("utf-8", "replace"))
            if "choices" in data and data["choices"]:
                content = data["choices"][0].get("message", {}).get("content")
        if hasattr(response, "background") and response.background is not None:
            await response.background()
        content = content or ""
    elif hasattr(response, "body"):
        # JSONResponse — parse the body
        data = json.loads(response.body.decode("utf-8", "replace"))
        content = (
            data["choices"][0].get("message", {}).get("content", "")
            if data.get("choices")
            else ""
        )
    else:
        raise RuntimeError(f"Unexpected response type from LLM: {type(response)}")

    if not content:
        raise RuntimeError("LLM returned empty content for skill param extraction")

    return content


def _build_extraction_messages(messages: list[dict], skill_name: str) -> list[dict]:
    """Construct the messages array for the extraction LLM call.

    Pure function: chat messages + skill name → LLM-ready messages list.
    Composes _format_chat_for_extraction with EXTRACTION_SYSTEM_PROMPT.
    """
    user_content = _format_chat_for_extraction(messages, skill_name)
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


# Fixed rendering order for deterministic output (frozenset iteration is arbitrary).
_PARAM_RENDER_ORDER = ("lang", "style", "terms")
_PARAM_LABELS = {
    "lang": "Target language",
    "style": "Style",
    "terms": "Terms to preserve",
}


def build_enriched_skill_prompt(
    params: dict,
    file_paths: list[str],
    user_message: str,
    skill_name: str,
) -> str:
    """Build an enriched prompt for OpenCode from extracted params + context.

    Pure function — no side effects, no async, no imports.

    When params is {} (fallback / extraction failed), gracefully degrades to
    task = user_message + file paths only. When a param value is None, that
    line is omitted entirely — the string "None" never appears in output.

    Args:
        params: Output from extract_skill_params. Either {} or a dict with
                keys from PARAM_KEYS, each Optional[str].
        file_paths: Paths to uploaded files in work_dir/input/.
        user_message: The last user message.
        skill_name: Name of the skill being invoked.

    Returns:
        Structured prompt string ready for OpenCode.
    """
    sections: list[str] = []

    # --- Task ---
    # Prefer task_intent from extraction; fall back to user_message.
    task = params.get("task_intent") or user_message
    sections.append(f"## Task\n{task}")

    # --- Input Files (omit if empty) ---
    if file_paths:
        file_lines = "\n".join(f"- {fp}" for fp in file_paths)
        sections.append(f"## Input Files\n{file_lines}")

    # --- Parameters (omit entire section if params is {} or all render keys are empty) ---
    # Defense-in-depth: use truthiness checks so even if parser normalization
    # is bypassed, empty/whitespace strings won't produce blank labels.
    if params:
        param_lines: list[str] = []
        for key in _PARAM_RENDER_ORDER:
            val = params.get(key)
            if val:
                param_lines.append(f"- {_PARAM_LABELS[key]}: {val}")
        if param_lines:
            sections.append("## Parameters\n" + "\n".join(param_lines))

    # --- Context (omit if params is {} or context_summary is empty) ---
    context_summary = params.get("context_summary")
    if context_summary:
        sections.append(f"## Context\n{context_summary}")

    # --- Original User Message ---
    sections.append(f"## Original User Message\n{user_message}")

    return "\n\n".join(sections)


async def extract_skill_params(
    app: Any,
    messages: list[dict],
    skill_name: str,
    model_id: str,
) -> dict:
    """Extract structured parameters from chat context for a skill invocation.

    Analyzes the full chat history using an LLM to produce enriched parameters
    that improve the skill's output quality.

    Args:
        app: Starlette application instance (needed for generate_chat_completion)
        messages: Full chat history as list of message dicts
        skill_name: Name of the skill being invoked (e.g. "anything-to-docx")
        model_id: Model ID to use for the extraction LLM call

    Returns:
        Dict with keys: lang, style, terms, context_summary, task_intent
        (each Optional[str], None if not detected).
        Returns {} on any failure — caller should fallback to current behavior.
    """
    if not messages:
        log.debug("Skill params: no messages provided, returning empty dict")
        return {}

    try:
        llm_messages = _build_extraction_messages(messages, skill_name)

        raw_response = await _call_llm(app, llm_messages, model_id)
        log.debug("Skill params: raw LLM response: %s", raw_response[:500])

        params = _parse_extraction_response(raw_response)
        if not params:
            log.warning("Skill params: extraction returned empty params")
            return {}

        log.info(
            "Skill params: extracted for skill=%s: %s",
            skill_name,
            {k: (v[:50] + "..." if v and len(v) > 50 else v) for k, v in params.items()},
        )
        return params

    except Exception:
        log.exception("Skill params: failed to extract params for skill=%s", skill_name)
        return {}
