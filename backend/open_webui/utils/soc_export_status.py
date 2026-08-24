"""Terminal-state resolution for SOC / SOCv2 same-origin export directories.

The MCP reconciler materializes DOCX under ``/handoff/exports/<prefix>/<nonce>/``
and settles non-success jobs with a co-located ``.failed`` marker (timeout /
needs_attention / failed / canceled). The OpenWebUI export route uses this
helper so users clicking chat links always see an honest terminal state.

States
------
* ``done`` — ``.done`` present (DOCX should be served by the route when the
  requested path is an existing file)
* ``failed`` — ``.failed`` present; ``detail`` is the marker text (timeout
  reason, etc.), never a filename
* ``pending`` — directory exists, no terminal marker yet
* ``absent`` — no artifact directory (unknown / wrong link)

Mutation note: removing marker writes in the MCP reconciler must make the
failed/timeout unit tests red — those tests create fixtures with only
``.failed`` and assert this helper returns ``failed``.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

DONE_MARKER = '.done'
FAILED_MARKER = '.failed'
MAX_FAILED_DETAIL_CHARS = 400

# Named terminal reasons we surface (refutable).
STATE_DONE = 'done'
STATE_FAILED = 'failed'
STATE_PENDING = 'pending'
STATE_ABSENT = 'absent'

PHASE_LABELS = {
    'queued': '排隊中 / queued',
    'preparing': '準備中 / preparing',
    'extracting': '解析文件 / extracting',
    'ocr': '掃描辨識 / OCR',
    'ir': '版面分析 / layout analysis',
    'translating': '翻譯中 / translating',
    'rendering': '產生 DOCX / rendering',
}


def _read_stage(art_dir: Path) -> dict:
    try:
        marker = art_dir / '.stage'
        if not marker.is_file():
            return {}
        value = json.loads(marker.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def read_failed_detail(art_dir: Path) -> str:
    """Read ``.failed`` marker text; empty string if missing/unreadable."""
    marker = art_dir / FAILED_MARKER
    try:
        if not marker.is_file():
            return ''
        text = marker.read_text(encoding='utf-8', errors='replace').strip()
        return text[:MAX_FAILED_DETAIL_CHARS]
    except OSError:
        return ''


def resolve_artifact_state(art_dir: Path) -> tuple[str, str]:
    """Return ``(state, detail)`` for a SOC export nonce directory.

    Priority: failed > done > pending > absent.
    A directory with both markers is treated as failed (honest fail-closed).
    """
    try:
        if not art_dir.is_dir():
            return STATE_ABSENT, ''
    except OSError:
        return STATE_ABSENT, ''

    detail = read_failed_detail(art_dir)
    if detail or (art_dir / FAILED_MARKER).is_file():
        # Marker exists even if empty → still failed (settled terminal).
        if not detail:
            detail = 'The conversion did not succeed. You can try again.'
        return STATE_FAILED, detail

    try:
        if (art_dir / DONE_MARKER).is_file():
            return STATE_DONE, ''
    except OSError:
        return STATE_ABSENT, ''

    return STATE_PENDING, ''


def failed_user_message(detail: str) -> str:
    """Human-facing body for a failed export (bilingual, actionable)."""
    reason = (detail or '').strip() or 'The conversion did not succeed.'
    return (
        f'{reason}\n\n'
        '請回到聊天視窗，直接請 AI「重新轉檔」即可 — 不需要重新上傳檔案。'
        ' Go back to the chat and ask the AI to retry the conversion '
        '(no re-upload needed).'
    )


def _minutes_since(raw: str, now: dt.datetime) -> int | None:
    try:
        value = dt.datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.UTC)
        return max(0, int((now - value).total_seconds() // 60))
    except (TypeError, ValueError):
        return None


def _progress_details(progress: object, phase: str = '') -> list[str]:
    if not isinstance(progress, dict):
        return []
    try:
        completed = int(progress.get('completed'))
        total = int(progress.get('total'))
        failed = int(progress.get('failed') or 0)
    except (TypeError, ValueError):
        return []
    if not (1 <= completed <= total and 0 <= failed <= completed):
        return []

    details = [f'進度 {completed}/{total}（{completed * 100 // total}%）']
    if failed:
        if phase == 'ocr':
            details.append(f'{failed} 頁辨識失敗但工作仍繼續')
        elif phase == 'translating':
            details.append(f'{failed} 個翻譯區塊需要重試或保留原文')
        else:
            details.append(f'{failed} 個處理單位需要重試或降級處理')
    return details


def pending_user_message(
    art_dir: Path, now: dt.datetime | None = None
) -> str:
    """Build an honest bilingual pending message from the atomic stage view."""
    now = now or dt.datetime.now(dt.UTC)
    stage = _read_stage(art_dir)

    phase = str(stage.get('phase') or '')
    label = PHASE_LABELS.get(phase, phase)
    progress = stage.get('progress')
    details = [f'目前階段：{label}'] if label else []
    details.extend(_progress_details(progress, phase))

    elapsed = _minutes_since(str(stage.get('created_at') or ''), now)
    if elapsed is not None:
        details.append(f'已進行約 {elapsed} 分鐘')
    updated_raw = ''
    if isinstance(progress, dict):
        updated_raw = str(progress.get('updated_at') or '')
    updated_raw = updated_raw or str(stage.get('updated_at') or '')
    updated = _minutes_since(updated_raw, now)
    if updated is not None:
        details.append(f'最近進度約 {updated} 分鐘前')

    prefix = '，'.join(details)
    if prefix:
        prefix += '。 '
    return (
        f'{prefix}文件還在轉換中；可以安全離開，稍後回到同一連結。此頁面每 15 秒會自動重新整理，'
        '完成後會直接開始下載。 The document is still being converted. You can leave '
        'and return to this link later. This page refreshes every 15 seconds and starts '
        'the download only after the server confirms completion.'
    )
