import { describe, expect, test } from 'vitest';

import {
	ALLOWED_CAUSES,
	EMPTY_TURN_CAUSE,
	buildBanner,
	emptyTurnView,
	hasStructuredEmptyError,
	isRenderedEmpty,
	makeTraceId,
	safeCause,
	shouldFlagInterrupted
} from './empty_turn';

const RAW = 'RAW_CLIENT_MATTER_123 — privileged & confidential';

describe('isRenderedEmpty', () => {
	test('empty / whitespace / non-string is empty', () => {
		expect(isRenderedEmpty('')).toBe(true);
		expect(isRenderedEmpty('   \n\t ')).toBe(true);
		expect(isRenderedEmpty(undefined)).toBe(true);
		expect(isRenderedEmpty(null)).toBe(true);
	});
	test('a tool/skip-rag render or any text is NOT empty', () => {
		expect(isRenderedEmpty('<details>tool</details>')).toBe(false);
		expect(isRenderedEmpty('Here is your answer.')).toBe(false);
	});
});

describe('shouldFlagInterrupted (case 2 load-time guard)', () => {
	// At render time v0.9.6's loadChat has reconciled an interrupted leaf done=false→done=true
	// (gated on its own active-task probe); a still-streaming turn stays done=false.
	const reconciledEmpty = { role: 'assistant', content: '', done: true };

	test('rendered-empty + done=true (reconciled) + no active task → flag', () => {
		expect(shouldFlagInterrupted(reconciledEmpty, false)).toBe(true);
		expect(shouldFlagInterrupted(reconciledEmpty)).toBe(true); // default no-active-task
	});
	test('explicit active task → NOT flagged (suppressor)', () => {
		expect(shouldFlagInterrupted(reconciledEmpty, true)).toBe(false);
	});
	test('still streaming (done=false) → NOT flagged (never flag a live stream)', () => {
		expect(shouldFlagInterrupted({ role: 'assistant', content: '', done: false }, false)).toBe(false);
	});
	test('non-empty tool/skip-rag/partial turn → NOT flagged', () => {
		expect(shouldFlagInterrupted({ role: 'assistant', content: 'partial…', done: true }, false)).toBe(false);
		expect(shouldFlagInterrupted({ role: 'assistant', content: '<tool/>', done: true }, false)).toBe(false);
	});
	test('already has an error → leave to case 1', () => {
		expect(
			shouldFlagInterrupted({ role: 'assistant', content: '', done: true, error: { cause: EMPTY_TURN_CAUSE } }, false)
		).toBe(false);
	});
	test('non-assistant roles never flag', () => {
		expect(shouldFlagInterrupted({ role: 'user', content: '', done: true }, false)).toBe(false);
	});
});

describe('hasStructuredEmptyError (case 1)', () => {
	test('true only for the structured {cause,...} payload', () => {
		expect(hasStructuredEmptyError({ error: { content: 'x', cause: EMPTY_TURN_CAUSE, trace_id: 't-a-b' } })).toBe(true);
		expect(hasStructuredEmptyError({ error: true })).toBe(false);
		expect(hasStructuredEmptyError({ error: { content: 'legacy only' } })).toBe(false);
		expect(hasStructuredEmptyError({})).toBe(false);
	});
});

describe('trace id', () => {
	test('format + 8-char truncation', () => {
		expect(makeTraceId('deadbeefcafef00d', '0123456789abcdef')).toBe('t-deadbeef-01234567');
	});
	test('tolerates short / missing', () => {
		expect(makeTraceId('abc', '')).toBe('t-abc-');
		expect(makeTraceId(undefined, undefined)).toBe('t--');
	});
});

describe('safeCause (M4 render guard)', () => {
	test('passes a canonical cause', () => {
		expect(safeCause(EMPTY_TURN_CAUSE)).toBe('db_stream_flush');
		expect(ALLOWED_CAUSES.has(EMPTY_TURN_CAUSE)).toBe(true);
	});
	test('a non-canonical / raw cause collapses to "unknown" (never rendered raw)', () => {
		expect(safeCause(RAW)).toBe('unknown');
		expect(safeCause('provider outage')).toBe('unknown');
		expect(safeCause(42)).toBe('unknown');
		expect(safeCause(undefined)).toBe('unknown');
	});
});

describe('emptyTurnView (no raw content ever reaches the UI)', () => {
	test('case 1: backend trace accepted ONLY when it equals the recomputed canonical id', () => {
		// id 'msg45678…' → 'msg45678', chatId 'chat1234…' → 'chat1234' ⇒ canonical t-chat1234-msg45678,
		// which the backend trace_id here matches → accepted (== recomputed).
		const v = emptyTurnView(
			{ id: 'msg45678cafef00d', error: { content: RAW, cause: EMPTY_TURN_CAUSE, trace_id: 't-chat1234-msg45678' } },
			'chat1234cafef00d'
		);
		expect(v.cause).toBe('db_stream_flush');
		expect(v.traceId).toBe('t-chat1234-msg45678');
		expect(v.banner).toBe(buildBanner('db_stream_flush', 't-chat1234-msg45678'));
		// the backend error.content (which carried RAW here) is NOT rendered
		expect(v.banner).not.toContain('RAW_CLIENT_MATTER_123');
		expect(JSON.stringify(v)).not.toContain('RAW_CLIENT_MATTER_123');
	});

	test('case 1 with a NON-canonical cause + non-shaped raw trace → sanitized, synthesized', () => {
		const v = emptyTurnView(
			{ id: 'msg45678aaaa', error: { content: RAW, cause: RAW, trace_id: RAW } },
			'chat1234bbbb'
		);
		expect(v.cause).toBe('unknown'); // raw cause never shown
		expect(v.traceId).toBe('t-chat1234-msg45678'); // bad trace ignored → recomputed canonical
		expect(JSON.stringify(v)).not.toContain('RAW_CLIENT_MATTER_123');
	});

	test('REGRESSION: a trace-SHAPED raw label is NOT trusted → recomputed canonical', () => {
		// the exact Evaluator probe: a client name / case label shaped like a trace id.
		for (const poison of ['t-mrchuang-caseabc', 't-secretclient-matter99', 't-johndoe-v-acme']) {
			const v = emptyTurnView(
				{ id: 'msg45678cafef00d', error: { content: RAW, cause: EMPTY_TURN_CAUSE, trace_id: poison } },
				'chat1234cafef00d'
			);
			// rendered trace is the deterministic canonical id from frontend ids, never the poison
			expect(v.traceId).toBe('t-chat1234-msg45678');
			expect(v.traceId).not.toBe(poison);
			expect(v.banner).not.toContain(poison);
			expect(v.banner).not.toContain('mrchuang');
			expect(v.banner).not.toContain('secretclient');
			expect(v.banner).not.toContain('acme');
			expect(JSON.stringify(v)).not.toContain(poison);
		}
	});

	test('REGRESSION: trace is recomputed from ids even if it differs from a (wrong) backend trace', () => {
		// backend trace_id is well-formed but for DIFFERENT ids than the frontend holds → discard
		const v = emptyTurnView(
			{ id: 'aaaaaaaabbbb', error: { cause: EMPTY_TURN_CAUSE, trace_id: 't-deadbeef-feedface' } },
			'ccccccccdddd'
		);
		expect(v.traceId).toBe('t-cccccccc-aaaaaaaa'); // makeTraceId(chatId, id)
		expect(v.traceId).not.toBe('t-deadbeef-feedface');
	});

	test('case 2 (derived): default canonical cause + synthesized trace', () => {
		const v = emptyTurnView({ id: 'msg45678aaaa', role: 'assistant', content: '', done: false }, 'chat1234bbbb');
		expect(v.cause).toBe('db_stream_flush');
		expect(v.traceId).toBe('t-chat1234-msg45678');
		expect(v.banner).toContain('db_stream_flush');
		expect(v.banner).toContain('t-chat1234-msg45678');
	});
});
