"""Explicit user-intent policy for irreversible-looking document jobs.

The tool selector is a small model.  It may interpret ordinary prose such as
"turn this note into a detailed note" as file-format conversion merely because
an attachment and SOCv2 are available.  Conversion creates a durable
background job and user-visible artifact, so model plausibility is not enough:
the current user turn must contain an explicit conversion, translation, or
export intent.

This module is pure policy.  Tool execution remains in middleware.
"""

from __future__ import annotations

import re


SOCV2_SUBMIT_TOOL_SUFFIX = "submit_conversion"

_EXPLICIT_TOOL_RE = re.compile(r"(?:mcp[_-]?soc[_-]?v?2|submit_conversion)", re.I)
_STRONG_CONVERSION_RE = re.compile(
    r"\b(?:convert|translate|export)\b|"
    r"(?:轉成|转成|轉換|转换|轉檔|转档|翻譯|翻译|匯出|汇出|輸出檔案|输出文件)",
    re.I,
)
_GENERIC_TRANSFORM_RE = re.compile(r"\bturn\b.{0,40}\binto\b", re.I | re.S)
_ARTIFACT_RE = re.compile(
    r"\b(?:docx|pdf|word|document|file|download|artifact)\b|"
    r"(?:可下載|可下载|下載連結|下载链接|文件|檔案|文檔|文档)",
    re.I,
)


def is_socv2_submit_tool(tool_name: object) -> bool:
    return str(tool_name or "").lower().endswith(SOCV2_SUBMIT_TOOL_SUFFIX)


def has_explicit_conversion_intent(user_message: object) -> bool:
    """Return whether the current turn clearly asks for a file conversion job.

    ``translate`` is intentionally strong enough on its own because this
    stack's published conversion workflow accepts "translate the attachment"
    as an explicit request for translated DOCX.  Generic ``turn ... into`` is
    weaker and needs an output-artifact noun; it must not capture requests to
    turn notes into a summary, guide, table, explanation, or detailed note.
    """
    text = str(user_message or "")
    if _EXPLICIT_TOOL_RE.search(text) or _STRONG_CONVERSION_RE.search(text):
        return True
    return bool(_GENERIC_TRANSFORM_RE.search(text) and _ARTIFACT_RE.search(text))


def allow_selected_tool(tool_name: object, user_message: object) -> bool:
    if not is_socv2_submit_tool(tool_name):
        return True
    return has_explicit_conversion_intent(user_message)
