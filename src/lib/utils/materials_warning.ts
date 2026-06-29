/**
 * #17 slice-3 (frontend): detect + describe a "partial-materials" turn so the UI can ANNOTATE
 * the answer with a NON-BLOCKING warning when some uploaded files were not confirmed delivered to
 * the agent (backend `message.warning = {kind:'partial_materials', used, total, unused, ...}`,
 * set at finalization by `backend/open_webui/utils/file_coverage.py`).
 *
 * Mirrors the #16 `empty_turn.ts` pattern, but this is a WARNING (amber, additive) — it never
 * hides/replaces the answer; the answer renders normally and the notice sits alongside it.
 *
 * Privacy (M3/M4): nothing here surfaces raw names/content/paths — ONLY counts + a canonical
 * kind + an opaque trace id. The banner is ALWAYS rebuilt from the (sanitized) counts; the
 * backend `warning.content` is deliberately NOT trusted/rendered. The trace is recomputed
 * frontend-side from (chatId, message.id); the backend `trace_id` is accepted ONLY when it
 * EXACTLY equals the recomputed canonical id — so a raw / trace-shaped value can never reach the
 * UI (the #16 trace-injection lesson).
 */

// Canonical warning kind. Mirrors file_coverage.WARNING_KIND_PARTIAL.
export const PARTIAL_MATERIALS_KIND = 'partial_materials';

// The ONLY warning kinds the UI is permitted to act on. Anything else → suppressed/'unknown'.
export const ALLOWED_WARNING_KINDS: ReadonlySet<string> = new Set([PARTIAL_MATERIALS_KIND]);

export interface WarningLike {
	kind?: unknown;
	used?: unknown;
	total?: unknown;
	unused?: unknown;
	skipped?: unknown;
	failed?: unknown;
	trace_id?: unknown;
	content?: unknown;
}

export interface MessageLike {
	id?: string;
	role?: string;
	warning?: unknown;
}

/** Coerce to a non-negative integer (counts only); anything else (raw/float/neg/NaN) → 0. */
export const safeCount = (n: unknown): number =>
	typeof n === 'number' && Number.isInteger(n) && n >= 0 ? n : 0;

/** Render-side M4 guard: only a canonical kind may be acted on; otherwise a generic fallback. */
export const safeKind = (kind: unknown): string =>
	typeof kind === 'string' && ALLOWED_WARNING_KINDS.has(kind) ? kind : 'unknown';

/** The message carries a structured warning payload (object with a string `kind`). */
export const hasStructuredWarning = (message: MessageLike | null | undefined): boolean => {
	const w = message?.warning as { kind?: unknown } | undefined;
	return !!w && typeof w === 'object' && typeof w.kind === 'string';
};

/**
 * Show iff the backend marked this ASSISTANT turn as `partial_materials` AND ≥1 uploaded file is
 * unused. A non-canonical kind is never shown (`safeKind` collapses it); `unused<=0` (all used)
 * is never shown.
 */
export const shouldShowMaterialsWarning = (message: MessageLike | null | undefined): boolean => {
	if (!message || message.role !== 'assistant') return false;
	const w = message.warning as WarningLike | undefined;
	if (!w || typeof w !== 'object') return false;
	if (safeKind(w.kind) !== PARTIAL_MATERIALS_KIND) return false;
	return safeCount(w.unused) > 0;
};

/** Opaque, deterministic trace id `t-<chatid8>-<msgid8>` — only ids, never content. */
export const makeTraceId = (chatId?: string, messageId?: string): string =>
	`t-${(chatId ?? '').slice(0, 8)}-${(messageId ?? '').slice(0, 8)}`;

/** Single source of the visible banner text — composed ONLY from counts. Honest wording: we
 * measure DELIVERY to the agent, not whether it read each file ("may not have been used"). */
export const buildBanner = (unused: number, total: number): string =>
	`${unused} of ${total} uploaded file(s) may not have been used — review before relying on this answer.`;

export interface MaterialsWarningView {
	kind: string;
	used: number;
	total: number;
	unused: number;
	skipped: number;
	failed: number;
	traceId: string;
	banner: string;
}

/**
 * View-model for the in-place notice. The banner is ALWAYS rebuilt from the (safe) counts — the
 * backend's `warning.content` is deliberately NOT trusted/rendered, so a raw string can never
 * reach the UI. The trace prefers the backend-provided opaque id ONLY when it EXACTLY equals the
 * recomputed canonical; otherwise it is synthesized from the frontend-held ids.
 */
export const materialsWarningView = (
	message: MessageLike | null | undefined,
	chatId?: string
): MaterialsWarningView => {
	const w = (message?.warning as WarningLike | undefined) ?? {};
	const used = safeCount(w.used);
	const total = safeCount(w.total);
	const unused = safeCount(w.unused);
	const skipped = safeCount(w.skipped);
	const failed = safeCount(w.failed);
	const canonical = makeTraceId(chatId, message?.id);
	const backendTrace = typeof w.trace_id === 'string' ? w.trace_id : undefined;
	const traceId = backendTrace === canonical ? backendTrace : canonical;
	return { kind: safeKind(w.kind), used, total, unused, skipped, failed, traceId, banner: buildBanner(unused, total) };
};
