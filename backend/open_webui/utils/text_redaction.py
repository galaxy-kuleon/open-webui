"""Minimal output contract for user-requested text anonymisation.

Detection is policy only.  The model still performs the requested rewrite; the
transport adds one narrow instruction that prevents an anonymised document from
shipping a reversible key beside the result.
"""

from __future__ import annotations

import re

TEXT_REDACTION_CONTRACT = """
Text anonymisation contract for the current turn:
- Perform a substitution-only transformation: replace the requested identifiers with consistent neutral tokens or
  reference names, while preserving every non-target word, character, quotation, citation, paragraph, and its order.
- Return the transformed text only. Do not paraphrase, summarise, omit, reorder, explain, or add a legend, key, token
  list, preface, note, commentary, or example unless the user explicitly requests that separate material.
- Never reproduce, quote, explain, or map a replaced original identifier anywhere in the response, including a
  legend, key, table, preface, note, example, or visible reasoning section.
- Provide a reversible token-to-original key only if the user explicitly asks for that separate key.
""".strip()

_UNAMBIGUOUS_REDACTION_RE = re.compile(
    r'\b(?:anonymi[sz](?:e|ed|ing|ation)|de[ -]?identif(?:y|ied|ication)|'
    r'pseudonymi[sz](?:e|ed|ing|ation)|redact(?:ed|ing|ion)?)\b|'
    r'(?:匿名化|去識別|去识别|隱去|隐去|刪去識別|删去识别)',
    re.I,
)
_MASK_RE = re.compile(r'\bmask(?:ed|ing)?\b|(?:遮罩|遮蔽|遮蓋|遮盖)', re.I)
_TEXTUAL_TARGET_RE = re.compile(
    r'\b(?:token|reference|name|identifier|party|parties|decision|document|'
    r'text|content|case|judgment|judgement)\b|'
    r'(?:代稱|代称|名稱|名称|姓名|識別|识别|當事人|当事人|判決|判决|文件|文本|內容|内容)',
    re.I,
)


def requests_text_redaction(user_message: object) -> bool:
    """Return whether the current turn asks for textual identity replacement.

    ``mask`` alone is ambiguous (for example an image mask), so it requires an
    accompanying textual target.  The other verbs are already specific to
    anonymisation/redaction.
    """
    text = str(user_message or '')
    return bool(_UNAMBIGUOUS_REDACTION_RE.search(text) or (_MASK_RE.search(text) and _TEXTUAL_TARGET_RE.search(text)))
