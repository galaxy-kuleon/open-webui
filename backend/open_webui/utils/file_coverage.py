"""#17 slice-3 (backend half): Path-B handoff-DELIVERY coverage + a privacy-safe
"partial-materials" warning builder for a finalized file-heavy turn.

A file-heavy turn whose uploaded files did NOT all reach the Hermes Path-B handoff is a silent
"materials gap": the answer looks complete but some uploads may never have been delivered to the
agent. This module provides a pure classifier, a privacy-safe warning builder, a read-only
handoff scan, and a small dependency-injected persistence/event mechanism so ``middleware``
can ANNOTATE such a turn (a non-blocking ``message.warning``) instead of implying every file
was considered. The answer is still shown — this only annotates it.

MVP scope (Planner-approved, #17 slice-3):
  * Measures ONLY Path-B handoff DELIVERY — which of the turn's user-message uploads reached
    ``/handoff/user/<uid>/chat/<cid>/message/<user_msg_id>/`` (written SYNCHRONOUSLY before the
    stream by ``hermes_handoff.run_hermes_handoff`` — middleware:2944 — so it is fully settled at
    finalization; NOT a race). It deliberately does NOT depend on native extraction/OCR
    (``file.data.content``), which can lag at finalize — that is a later load-time endpoint.
  * This handoff scanner does not infer Path-A coverage. Path A supplies definitive typed
    conversion counts in request metadata and reuses the same privacy-safe builder/surface.
  * Warns iff: a handoff dir exists for the turn AND files>0 AND >=1 file is definitively NOT
    delivered (``unused>0``). Empty-turn (#16) takes priority over this warning.

Privacy (M3/M4): the ``/handoff`` path is already per-user-scoped. The warning payload carries
ONLY counts + a canonical ``kind`` + an opaque trace id (``t-<cid8>-<mid8>``, recomputable) —
NEVER raw file names / content / paths / error text. Raw values stay in the container (signals
are computed here; only counts leave). A handoff-scan failure → fixed bucket + the caller
SUPPRESSES the warning (never a raw error, never a false warning).

Single-sourcing (the container can't import the parent-repo ops script, same as
failure_surface↔chat_triage): ``classify_file`` / ``summarize`` and the status vocabulary MIRROR
``scripts/ops/openwebui_8083_file_coverage.py``; ``test_file_coverage`` cross-checks them
(drift-guard). This module deliberately has NO ``open_webui`` imports so it is unit-testable by
file path with no live stack.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from open_webui.utils.handoff_filename import (
    corrected_attachment_candidate,
    legacy_attachment_candidate,
)

# ── per-file status vocabulary (MIRROR scripts/ops/openwebui_8083_file_coverage.py) ──────────
FILE_STATUSES = ["handed_off", "extracted_or_ocr", "uploaded", "skipped", "failed", "unknown"]
SKIP_REASONS = frozenset({"unsupported_type", "too_large", "skipped_skiprag"})
FAIL_REASONS = frozenset({"upload_error", "extraction_failed", "ocr_failed", "handoff_error"})
ALL_REASONS = SKIP_REASONS | FAIL_REASONS | {"not_in_handoff", None}


def classify_file(sig: dict) -> dict:
    """Map one normalized per-file ``sig`` to a coverage status (VERBATIM mirror of the ops
    classifier — drift-guarded by the test). MVP callers pass ``extracted=False`` (delivery-only).
    ``sig`` keys: upload_status, error_bucket, extracted, handed_off, handoff_dir_present."""
    err = sig.get("error_bucket")
    status_raw = sig.get("upload_status")
    extracted = bool(sig.get("extracted"))
    handed_off = sig.get("handed_off")
    handoff_present = bool(sig.get("handoff_dir_present"))

    if err in SKIP_REASONS:
        return _fverdict("skipped", err, "high")
    if err in FAIL_REASONS:
        return _fverdict("failed", err, "high")
    if extracted:
        return _fverdict("extracted_or_ocr", None, "high")
    if handed_off is True:
        return _fverdict("handed_off", None, "high")
    if handoff_present and handed_off is False:
        return _fverdict("uploaded", "not_in_handoff", "medium")
    if status_raw == "uploaded" or status_raw is None:
        return _fverdict("uploaded", None, "low")
    return _fverdict("unknown", None, "low")


def _fverdict(status: str, reason, confidence: str) -> dict:
    assert status in FILE_STATUSES, status
    assert reason in ALL_REASONS, reason
    return {"status": status, "reason": reason, "confidence": confidence}


def summarize(rows: list, ctx: dict) -> dict:
    """Aggregate classified file rows into accepted/skipped/failed counts + coverage%
    (VERBATIM mirror of the ops summary — drift-guarded by the test)."""
    counts = {s: 0 for s in FILE_STATUSES}
    for r in rows:
        counts[r["verdict"]["status"]] += 1
    total = len(rows)
    failed = counts["failed"]
    skipped = counts["skipped"]
    used = counts["handed_off"] + counts["extracted_or_ocr"]
    accepted = counts["handed_off"] + counts["extracted_or_ocr"] + counts["uploaded"]
    coverage_pct = round(100.0 * used / accepted, 1) if accepted else None

    if total == 0:
        cov_conf = "n/a"
    elif ctx.get("handoff_dir_present"):
        cov_conf = "high"
    elif ctx.get("is_path_b"):
        cov_conf = "low"
    else:
        cov_conf = "low"

    return {
        "total_files": total,
        "accepted": accepted,
        "skipped": skipped,
        "failed": failed,
        "unknown": counts["unknown"],
        "used": used,
        "uploaded_only": counts["uploaded"],
        "by_status": counts,
        "coverage_pct": coverage_pct,
        "coverage_confidence": cov_conf,
    }


# ── privacy-safe partial-materials WARNING payload (MIRROR failure_surface.py discipline) ─────
WARNING_KIND_PARTIAL = "partial_materials"

# The CANONICAL set of warning ``kind`` labels this slice may emit. Enforced FAIL-LOUD in
# ``build_warning_payload`` / ``assert_warning_privacy_safe`` so an arbitrary/raw string can
# NEVER reach ``warning.kind`` or the banner (M4).
ALLOWED_WARNING_KINDS = frozenset({WARNING_KIND_PARTIAL})

# The ONLY keys permitted in an assistant-message ``warning`` payload that crosses the privacy
# boundary (M4). Counts + canonical kind + opaque trace only — anything else is a fail-loud bug.
WARNING_PAYLOAD_ALLOWED_KEYS = frozenset(
    {"content", "kind", "used", "total", "unused", "skipped", "failed", "trace_id"}
)
_COUNT_KEYS = ("used", "total", "unused", "skipped", "failed")


def make_trace_id(chat_id: str, message_id: str) -> str:
    """Opaque, server-generated trace id ``t-<chatid8>-<msgid8>`` — only ids, never content.
    Identical format to ``failure_surface.make_trace_id`` (redefined here to keep this module
    import-free + unit-testable by file path)."""
    return "t-{}-{}".format((chat_id or "")[:8], (message_id or "")[:8])


def build_warning_banner(used: int, total: int, unused: int) -> str:
    """The single source of the visible warning banner — composed ONLY from counts. Honest
    wording: 'may not have been used' (we measure DELIVERY to the agent, not its consumption)."""
    return (
        "Materials notice: {} of {} uploaded file(s) may not have been used in this answer "
        "— review before relying on it."
    ).format(unused, total)


def build_warning_payload(
    chat_id: str,
    message_id: str,
    used: int,
    total: int,
    unused: int,
    skipped: int = 0,
    failed: int = 0,
    kind: str = WARNING_KIND_PARTIAL,
) -> dict:
    """Build the privacy-safe assistant-message ``warning`` payload (counts + canonical kind +
    opaque trace only). Takes NO name/content/path argument by design; ``kind`` is FAIL-LOUD
    validated against ``ALLOWED_WARNING_KINDS``; counts must be non-negative ints;
    ``assert_warning_privacy_safe`` re-validates the result."""
    if kind not in ALLOWED_WARNING_KINDS:
        raise ValueError(
            "non-canonical warning kind {!r}; allowed: {}".format(kind, sorted(ALLOWED_WARNING_KINDS))
        )
    counts = {"used": used, "total": total, "unused": unused, "skipped": skipped, "failed": failed}
    for key, val in counts.items():
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise ValueError("warning count '{}' must be a non-negative int, got {!r}".format(key, val))
    trace_id = make_trace_id(chat_id, message_id)
    payload = {"content": build_warning_banner(used, total, unused), "kind": kind,
               "trace_id": trace_id, **counts}
    assert_warning_privacy_safe(payload)
    return payload


def assert_warning_privacy_safe(payload: dict) -> dict:
    """Fail-loud (M4): the warning payload may contain ONLY the allow-listed keys; ``kind`` must
    be canonical; the count fields must be non-negative ints; and ``content`` must be exactly the
    fixed counts-banner (a guard against a future edit interpolating a raw name/path)."""
    extra = set(payload) - WARNING_PAYLOAD_ALLOWED_KEYS
    if extra:
        raise ValueError("warning payload has non-allow-listed keys: {}".format(sorted(extra)))
    kind = payload.get("kind", "")
    if kind not in ALLOWED_WARNING_KINDS:
        raise ValueError(
            "warning payload 'kind' {!r} is not canonical; allowed: {}".format(kind, sorted(ALLOWED_WARNING_KINDS))
        )
    for key in _COUNT_KEYS:
        val = payload.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise ValueError("warning payload '{}' must be a non-negative int, got {!r}".format(key, val))
    if not isinstance(payload.get("trace_id"), str):
        raise ValueError("warning payload 'trace_id' must be str")
    rebuilt = build_warning_banner(payload["used"], payload["total"], payload["unused"])
    if payload.get("content") != rebuilt:
        raise ValueError("warning payload 'content' is not the fixed counts banner (possible leak)")
    return payload


async def persist_and_emit_warning(
    persist_message_update,
    event_emitter,
    chat_id: str,
    message_id: str,
    warning: dict,
) -> None:
    """Persist and emit one already-built privacy-safe warning through OWUI's
    existing message-warning contract. Dependencies are passed explicitly so
    the mechanism remains unit-testable without importing live app state."""
    assert_warning_privacy_safe(warning)
    await persist_message_update(chat_id, message_id, {"warning": warning})
    await event_emitter(
        {"type": "chat:message:warning", "data": {"warning": warning}}
    )


# ── runtime Path-B handoff-DELIVERY scan (impure: filesystem LIST/STAT only, never reads bytes) ─
_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
# Handoff filename scheme: {index:03d}-{uuid4hex8}-{attachment_candidate}.
_HANDOFF_PREFIX_RE = re.compile(r"^(\d+)-[0-9a-fA-F]{8}-(.*)$")


def safe_segment(raw, default: str = "file") -> str:
    """Filesystem-safe path segment — MIRRORS ``hermes_handoff._safe_segment`` (drift-guarded by
    the test) so a DB file name maps to the same sanitised name the handoff writer used."""
    cleaned = _SAFE_SEGMENT_RE.sub("_", (raw or "").strip())[:96].strip("._-")
    return cleaned if (cleaned and ".." not in cleaned) else default


def _name_key(name: str) -> str:
    """Hash one already-normalized physical or DB candidate name."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def bucket_error(raw_status, raw_error):
    """Map a raw file-ref status/error to a privacy-safe bucket (raw text stays in-container)."""
    blob = ((raw_status or "") + " " + (raw_error or "")).lower()
    if not (raw_error or "").strip() and raw_status in ("uploaded", "", None):
        return None
    if "unsupported" in blob or "not supported" in blob or "unrecognized" in blob:
        return "unsupported_type"
    if "too large" in blob or "exceeds" in blob or "size limit" in blob or "max size" in blob:
        return "too_large"
    if "skip" in blob:
        return "skipped_skiprag"
    if "ocr" in blob:
        return "ocr_failed"
    if "extract" in blob or "parse" in blob or "decode" in blob:
        return "extraction_failed"
    if "handoff" in blob:
        return "handoff_error"
    if (raw_error or "").strip() or (raw_status not in ("uploaded", "", None)):
        return "upload_error"
    return None


def _scan_turn_handoff(handoff_dir: str, user_id: str, chat_id: str, user_message_id):
    """LIST/STAT the per-turn handoff dir → (present, physical-slots, scan_error). Uses only
    is_dir / rglob / is_file / .name — NEVER reads a handoff file's bytes (M4). Per-user-scoped."""
    root = (Path(handoff_dir) / "user" / safe_segment(user_id, "nouser")
            / "chat" / safe_segment(chat_id, "nochat")
            / "message" / safe_segment(user_message_id, "nomsg"))
    present = False
    physical_slots: list[tuple[int | None, str]] = []
    scan_error = False
    try:
        present = root.is_dir()
        if present:
            for p in root.rglob("*"):
                if p.is_file():
                    mo = _HANDOFF_PREFIX_RE.fullmatch(p.name)
                    if mo:
                        physical_index = int(mo.group(1))
                        tail = mo.group(2)
                    else:
                        physical_index = None
                        tail = p.name
                    physical_slots.append((physical_index, _name_key(tail)))
    except OSError:
        scan_error = True
    return present, physical_slots, scan_error


def _matched_file_indexes(
    slot_edges_by_file: list[tuple[int, ...]],
    slot_count: int,
) -> set[int]:
    """Maximize one-to-one file/physical-slot matches."""
    owner_by_slot: dict[int, int] = {}
    slot_by_file: dict[int, int] = {}

    def assign(file_index: int, seen_slots: set[int]) -> bool:
        for slot_index in slot_edges_by_file[file_index]:
            if slot_index < 0 or slot_index >= slot_count:
                continue
            if slot_index in seen_slots:
                continue
            seen_slots.add(slot_index)
            previous_owner = owner_by_slot.get(slot_index)
            if previous_owner is None or assign(previous_owner, seen_slots):
                owner_by_slot[slot_index] = file_index
                slot_by_file[file_index] = slot_index
                return True
        return False

    for file_index in range(len(slot_edges_by_file)):
        assign(file_index, set())
    return set(slot_by_file)


def _slot_edges_for_name(
    name: str,
    physical_slots: list[tuple[int | None, str]],
) -> tuple[int, ...]:
    """Return corrected-first then legacy edges to opaque physical slot ids."""
    legacy_key = _name_key(legacy_attachment_candidate(name))
    corrected_keys: dict[int, str | None] = {}
    corrected_edges = []
    legacy_edges = []

    for slot_index, (physical_index, tail_key) in enumerate(physical_slots):
        corrected_key = None
        if physical_index is not None:
            if physical_index not in corrected_keys:
                try:
                    corrected_keys[physical_index] = _name_key(
                        corrected_attachment_candidate(name, physical_index)
                    )
                except ValueError:
                    corrected_keys[physical_index] = None
            corrected_key = corrected_keys[physical_index]

        if corrected_key is not None and tail_key == corrected_key:
            corrected_edges.append(slot_index)
        elif tail_key == legacy_key:
            legacy_edges.append(slot_index)

    return tuple(corrected_edges + legacy_edges)


def _file_signals(
    file_items: list,
    present: bool,
    physical_slots: list[tuple[int | None, str]],
) -> list:
    """Per current-turn file → minimal classify signal (delivery-only: ``extracted=False``).
    Matches corrected/legacy candidates against opaque physical slot identities.
    A file and physical slot are each consumed at most once."""
    records = []
    for f in file_items:
        if not isinstance(f, dict) or f.get("type", "file") != "file":
            continue
        nested = f.get("file") if isinstance(f.get("file"), dict) else {}
        name = f.get("name") or nested.get("filename") or (nested.get("meta") or {}).get("name") or ""
        records.append((f, _slot_edges_for_name(name, physical_slots)))

    matched = (
        _matched_file_indexes(
            [slot_edges for _, slot_edges in records],
            len(physical_slots),
        )
        if present
        else set()
    )
    out = []
    for record_index, (f, _) in enumerate(records):
        out.append({
            "upload_status": f.get("status"),
            "error_bucket": bucket_error(f.get("status"), f.get("error")),
            "extracted": False,          # MVP: delivery-only; native extraction not consulted
            "handed_off": record_index in matched if present else None,
            "handoff_dir_present": present,
        })
    return out


def compute_turn_delivery_coverage(handoff_dir: str, user_id: str, chat_id: str,
                                   user_message_id, file_items: list) -> dict:
    """Path-B handoff-delivery coverage for ONE finalized turn. Returns counts only (no raw).
    On a handoff-scan OSError returns ``{'scan_error': True, ...}`` so the caller SUPPRESSES the
    warning (never a raw error, never a false warning)."""
    present, physical_slots, scan_error = _scan_turn_handoff(
        handoff_dir,
        user_id,
        chat_id,
        user_message_id,
    )
    if scan_error:
        return {"scan_error": True, "present": False,
                "total": 0, "used": 0, "unused": 0, "skipped": 0, "failed": 0}
    items = [f for f in (file_items or []) if isinstance(f, dict)]
    sigs = _file_signals(items, present, physical_slots)
    summ = summarize([{"verdict": classify_file(s)} for s in sigs],
                     {"handoff_dir_present": present, "is_path_b": True})
    return {
        "scan_error": False,
        "present": present,
        "total": summ["total_files"],
        "used": summ["used"],
        "unused": summ["uploaded_only"],   # accepted-but-not-delivered = the delivery gap
        "skipped": summ["skipped"],
        "failed": summ["failed"],
    }


def should_warn(cov: dict) -> bool:
    """Warn iff: scan ok, a handoff dir exists for the turn, files>0, and >=1 uploaded file is
    definitively NOT delivered (``unused>0``). Path A (no handoff dir) ⇒ False (unmeasurable)."""
    return bool(
        cov and not cov.get("scan_error")
        and cov.get("present") and cov.get("total", 0) > 0 and cov.get("unused", 0) > 0
    )
