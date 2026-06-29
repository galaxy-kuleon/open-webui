/**
 * #16 runtime slice (frontend): detect + describe a silent empty / interrupted assistant
 * turn so the UI can replace a blank "completed" bubble with a visible, recoverable state.
 *
 * Mirrors the backend `backend/open_webui/utils/failure_surface.py`. Two cases:
 *  1. BACKEND-FLAGGED — the finalizer set `message.error = {content, cause, trace_id}` on a
 *     done=true+empty turn. We render an in-place notice for it.
 *  2. LOAD-TIME GUARD — the done=false majority the finalizer can't catch (interrupted +
 *     persisted done=false, no error). We derive the same notice IFF the turn rendered to
 *     NOTHING and there is no active task for the chat (so a turn streaming live in another
 *     tab / a navigated-away turn is NOT flagged).
 *
 * Privacy (M3/M4): nothing here surfaces serialize_output / raw content / title / names —
 * ONLY a bucketed (canonical) cause label + an opaque trace id. `safeCause` is a render-side
 * defense-in-depth guard so a non-canonical/raw cause can never reach the UI even if a future
 * backend bug emitted one.
 */

// Canonical bucketed cause label. Mirrors failure_surface.CAUSE_EMPTY_FINALIZED and
// scripts/ops/openwebui_8083_chat_triage.py CAUSES['db_stream_flush'] (task ended but the
// final assistant message is empty/done=false). Keep in sync by hand (separate runtimes).
export const EMPTY_TURN_CAUSE = 'db_stream_flush';

// The ONLY cause labels the UI is permitted to render. Anything else → 'unknown' fallback.
export const ALLOWED_CAUSES: ReadonlySet<string> = new Set([EMPTY_TURN_CAUSE]);

export interface TurnLike {
	role?: string;
	content?: unknown;
	done?: boolean;
	error?: unknown;
	id?: string;
}

/** Rendered-empty == the same "no visible content" notion the backend keys on. */
export const isRenderedEmpty = (content: unknown): boolean =>
	typeof content !== 'string' || content.trim() === '';

/** The backend already surfaced this turn (case 1): error is the structured #16 payload. */
export const hasStructuredEmptyError = (message: TurnLike | null | undefined): boolean => {
	const err = message?.error as { cause?: unknown } | undefined;
	return !!err && typeof err === 'object' && typeof err.cause === 'string';
};

/**
 * Case 2 detector (load-time guard). True iff a persisted assistant turn rendered to NOTHING,
 * has no error already, the chat has NO active task, and the turn is `done === true`.
 *
 * Why `done === true` (not `false`): v0.9.6's `Chat.svelte:loadChat` already probes active
 * tasks (`getTaskIdsByChatId`) and, finding NONE, reconciles an interrupted leaf
 * `done=false → done=true` — so at RENDER time an interrupted/crashed turn is `done=true`,
 * while a turn still streaming (this tab or another) is `done=false`. Keying on `done===true`
 * therefore (a) catches the interrupted turn after OWUI's own task-probe reconciliation and
 * (b) can NEVER flag a live stream. `chatHasActiveTask` is an explicit secondary suppressor
 * (from checkActiveChats); default false so flagging rides on the reliable `done===true`
 * signal rather than coupling to a probe that may not have resolved.
 */
export const shouldFlagInterrupted = (
	message: TurnLike | null | undefined,
	chatHasActiveTask = false
): boolean => {
	if (!message || message.role !== 'assistant') return false;
	if (message.error) return false; // backend already surfaced it (case 1)
	if (!isRenderedEmpty(message.content)) return false; // tool / skip-rag / partial → not empty
	if (message.done !== true) return false; // streaming (done=false) is never a failure here
	if (chatHasActiveTask) return false; // explicit gate: live task → not a failure
	return true;
};

/** Opaque, deterministic trace id `t-<chatid8>-<msgid8>` — only ids, never content. */
export const makeTraceId = (chatId?: string, messageId?: string): string =>
	`t-${(chatId ?? '').slice(0, 8)}-${(messageId ?? '').slice(0, 8)}`;

/** Render-side M4 guard: only a canonical cause may show; otherwise a generic fallback. */
export const safeCause = (cause: unknown): string =>
	typeof cause === 'string' && ALLOWED_CAUSES.has(cause) ? cause : 'unknown';

/** Single source of the visible banner text — composed ONLY from cause + trace id. */
export const buildBanner = (cause: string, traceId: string): string =>
	`This response finished without any content (cause: ${cause}). ` +
	`Nothing was delivered — retry, or share trace ${traceId} with ops.`;

export interface EmptyTurnView {
	cause: string;
	traceId: string;
	banner: string;
}

/**
 * View-model for the in-place notice, for EITHER case. The banner is ALWAYS rebuilt from the
 * (safe) cause + trace — the backend's `error.content` is deliberately NOT trusted/rendered,
 * so a raw string can never reach the UI. The trace prefers the backend-provided opaque id
 * (validated shape) and otherwise synthesizes it deterministically.
 */
export const emptyTurnView = (message: TurnLike | null | undefined, chatId?: string): EmptyTurnView => {
	const err = message?.error as { cause?: unknown; trace_id?: unknown } | undefined;
	const cause = safeCause(err && typeof err === 'object' ? err.cause : EMPTY_TURN_CAUSE);
	// The trace is ALWAYS the value RECOMPUTED from the frontend-held ids — it is never a
	// regex-trusted backend string. The backend `trace_id` is accepted ONLY when it EXACTLY
	// equals the recomputed canonical id (then it is, by definition, that same id); any other
	// value — including a trace-SHAPED raw label like "t-mrchuang-caseabc" — is discarded and
	// the canonical id is synthesized instead. So a raw value can never reach traceId/banner.
	const canonical = makeTraceId(chatId, message?.id);
	const backendTrace = err && typeof err === 'object' ? err.trace_id : undefined;
	const traceId = backendTrace === canonical ? backendTrace : canonical;
	return { cause, traceId, banner: buildBanner(cause, traceId) };
};
