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


DRAFT_DATE_DEFERRAL_CONTRACT = """
Date-deferral contract for the requested draft:
- The user's latest instruction says calendar dates are not settled yet. Apply that instruction to every calendar date in the draft, not only the main event date.
- Replace every absolute or anticipated calendar date retained from source material with a specific placeholder such as [publication date] or [expected result date], or omit it if the sentence does not need it. This includes dates embedded in forecasts, deadlines, payment requests, signatures, and footers.
- A relative duration such as "15 to 24 months from publication" may remain when it is part of the requested source content; do not convert it into a calendar date.
- Do not silently reuse a source year, update it to the current year, or calculate a new date.
- Before answering, scan the complete draft and its subject, notes, tables, and closing. No concrete year, month/day date, or stale calendar-date claim may remain unless the user explicitly asked to preserve that exact date.
""".strip()


def requests_draft_date_deferral(prompt: str | None) -> bool:
    """Return true only when the latest turn both drafts text and defers dates."""
    if not prompt:
        return False
    return any(pattern.search(prompt) for pattern in _DRAFTING_INTENT_PATTERNS) and any(
        pattern.search(prompt) for pattern in _DATE_DEFERRAL_PATTERNS
    )
