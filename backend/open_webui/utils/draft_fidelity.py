"""Narrow fidelity contracts for user-requested document drafting."""

from __future__ import annotations

import re


_DRAFTING_INTENT_PATTERNS = (
    re.compile(r"(?:寫|改寫|重寫|草擬|起草|擬定|潤飾|修改).{0,24}(?:email|e-mail|電郵|郵件|郵件|信|函|通知|草稿|版本)", re.IGNORECASE),
    re.compile(r"(?:email|e-mail|電郵|郵件|信|函|通知).{0,24}(?:寫|改|草擬|起草|潤飾|修改)", re.IGNORECASE),
    re.compile(r"\b(?:draft|rewrite|revise|write|prepare|edit)\b.{0,40}\b(?:email|e-mail|letter|notice|message|template|draft)\b", re.IGNORECASE),
)

_DATE_DEFERRAL_PATTERNS = (
    re.compile(r"(?:日期|日子|年月日|時間).{0,8}(?:先不管|先不要|不用管|暫不|待定|留白|空著|之後再填|稍後再填|先忽略)", re.IGNORECASE),
    re.compile(r"(?:先不管|先不要|不用管|暫不|待定|留白|空著|之後再填|稍後再填|先忽略).{0,8}(?:日期|日子|年月日|時間)", re.IGNORECASE),
    re.compile(r"\b(?:ignore|omit|defer|leave|skip)\b.{0,24}\bdates?\b", re.IGNORECASE),
    re.compile(r"\bdates?\b.{0,24}\b(?:later|tbd|blank|placeholder)\b", re.IGNORECASE),
)

_SOURCE_BOUND_TEXT_INTENT_PATTERNS = (
    re.compile(r"(?:比較|對照|差異|不同|哪裡不一樣|翻譯|改寫|改成|重寫|潤飾|修改|按.{0,12}內容|依照.{0,12}內容)", re.IGNORECASE),
    re.compile(r"\b(?:compare|contrast|difference|translate|rewrite|revise|adapt|based on)\b", re.IGNORECASE),
)

_SOURCE_TEXT_REFERENCE_PATTERNS = (
    re.compile(r"(?:中文|英文|中英|原文|原稿|這封|這段|上述|上面|以下|英文函|中文函|內容)", re.IGNORECASE),
    re.compile(r"\b(?:source|text|passage|original|above|below|letter|email|version)\b", re.IGNORECASE),
)


DRAFT_DATE_DEFERRAL_CONTRACT = """
Date-deferral contract for the requested draft:
- The user's latest instruction says calendar dates are not settled yet. Apply that instruction to every calendar date in the draft, not only the main event date.
- Replace every absolute or anticipated calendar date retained from source material with a specific placeholder such as [publication date] or [expected result date], or omit it if the sentence does not need it. This includes dates embedded in forecasts, deadlines, payment requests, signatures, and footers.
- A relative duration such as "15 to 24 months from publication" may remain when it is part of the requested source content; do not convert it into a calendar date.
- Do not silently reuse a source year, update it to the current year, or calculate a new date.
- Before answering, scan the complete draft and its subject, notes, tables, and closing. No concrete year, month/day date, or stale calendar-date claim may remain unless the user explicitly asked to preserve that exact date.
""".strip()


USER_TEXT_SOURCE_FIDELITY_CONTRACT = """
User-text source-fidelity contract:
- The user asked to compare, translate, rewrite, or adapt text they supplied. Treat that supplied text as the evidence boundary unless the user explicitly requests external research.
- Preserve what each supplied version actually says and clearly mark what it leaves unspecified. Do not turn language, writing style, a generic procedure, or a reference to a "local agent" into a country, jurisdiction, legal system, filing route, or other fact.
- In particular, Chinese-language text does not establish China as the jurisdiction. Name a jurisdiction only when the supplied text names it.
- Keep source statements separate from suggestions and bounded inferences. If a requested draft needs an unstated fact, use a clear placeholder or ask for it; do not complete it from plausibility.
- Before answering, verify every concrete entity, jurisdiction, number, date, procedural stage, enclosure, and payment statement against the supplied text and the user's latest instructions.
""".strip()


def requests_draft_date_deferral(prompt: str | None) -> bool:
    """Return true only when the latest turn both drafts text and defers dates."""
    if not prompt:
        return False
    return any(pattern.search(prompt) for pattern in _DRAFTING_INTENT_PATTERNS) and any(
        pattern.search(prompt) for pattern in _DATE_DEFERRAL_PATTERNS
    )


def requests_user_text_source_fidelity(prompt: str | None) -> bool:
    """Detect comparison or transformation of user-supplied text."""
    if not prompt:
        return False
    has_intent = any(pattern.search(prompt) for pattern in _SOURCE_BOUND_TEXT_INTENT_PATTERNS)
    has_source_reference = len(prompt) >= 300 or any(
        pattern.search(prompt) for pattern in _SOURCE_TEXT_REFERENCE_PATTERNS
    )
    return has_intent and has_source_reference
