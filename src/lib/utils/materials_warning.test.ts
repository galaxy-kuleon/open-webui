import { describe, expect, test } from 'vitest';

import {
	ALLOWED_WARNING_KINDS,
	PARTIAL_MATERIALS_KIND,
	buildBanner,
	hasStructuredWarning,
	makeTraceId,
	materialsWarningView,
	safeCount,
	safeKind,
	shouldShowMaterialsWarning
} from './materials_warning';

const RAW = 'RAW_CLIENT_MATTER_123 — privileged & confidential';

const warn = (over: Record<string, unknown> = {}) => ({
	role: 'assistant',
	id: 'msg45678cafef00d',
	warning: { kind: PARTIAL_MATERIALS_KIND, used: 10, total: 12, unused: 2, skipped: 0, failed: 0, trace_id: 't-chat1234-msg45678', ...over }
});

describe('safeCount', () => {
	test('passes non-negative integers; everything else → 0', () => {
		expect(safeCount(0)).toBe(0);
		expect(safeCount(7)).toBe(7);
		expect(safeCount(-1)).toBe(0);
		expect(safeCount(2.5)).toBe(0);
		expect(safeCount('2')).toBe(0);
		expect(safeCount(NaN)).toBe(0);
		expect(safeCount(undefined)).toBe(0);
	});
});

describe('safeKind (M4 render guard)', () => {
	test('passes the canonical kind', () => {
		expect(safeKind(PARTIAL_MATERIALS_KIND)).toBe('partial_materials');
		expect(ALLOWED_WARNING_KINDS.has(PARTIAL_MATERIALS_KIND)).toBe(true);
	});
	test('a non-canonical / raw kind collapses to "unknown"', () => {
		expect(safeKind(RAW)).toBe('unknown');
		expect(safeKind('materials_problem')).toBe('unknown');
		expect(safeKind(42)).toBe('unknown');
		expect(safeKind(undefined)).toBe('unknown');
	});
});

describe('hasStructuredWarning', () => {
	test('true only for the structured {kind,...} payload', () => {
		expect(hasStructuredWarning(warn())).toBe(true);
		expect(hasStructuredWarning({ warning: true })).toBe(false);
		expect(hasStructuredWarning({ warning: { used: 1 } })).toBe(false);
		expect(hasStructuredWarning({})).toBe(false);
	});
});

describe('shouldShowMaterialsWarning', () => {
	test('show iff partial_materials kind AND unused>0 on an assistant turn', () => {
		expect(shouldShowMaterialsWarning(warn())).toBe(true);
		expect(shouldShowMaterialsWarning(warn({ unused: 0 }))).toBe(false); // all used
		expect(shouldShowMaterialsWarning(warn({ unused: -3 }))).toBe(false); // sanitized → 0
		expect(shouldShowMaterialsWarning(warn({ kind: 'unknown_kind' }))).toBe(false); // non-canonical
		expect(shouldShowMaterialsWarning(warn({ kind: RAW }))).toBe(false);
	});
	test('non-assistant role / no warning → never show', () => {
		expect(shouldShowMaterialsWarning({ ...warn(), role: 'user' })).toBe(false);
		expect(shouldShowMaterialsWarning({ role: 'assistant' })).toBe(false);
		expect(shouldShowMaterialsWarning(null)).toBe(false);
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

describe('buildBanner', () => {
	test('composed only from counts; honest wording', () => {
		const b = buildBanner(2, 12);
		expect(b).toBe('2 of 12 uploaded file(s) may not have been used — review before relying on this answer.');
		expect(b).toContain('may not have been used');
	});
});

describe('materialsWarningView (no raw ever reaches the UI)', () => {
	test('counts rendered; backend trace accepted ONLY when == recomputed canonical', () => {
		const v = materialsWarningView(warn({ content: RAW }), 'chat1234cafef00d');
		expect(v.kind).toBe('partial_materials');
		expect([v.used, v.total, v.unused]).toEqual([10, 12, 2]);
		expect(v.traceId).toBe('t-chat1234-msg45678'); // == canonical → accepted
		expect(v.banner).toBe(buildBanner(2, 12));
		// the backend warning.content (which carried RAW) is NOT rendered
		expect(v.banner).not.toContain('RAW_CLIENT_MATTER_123');
		expect(JSON.stringify(v)).not.toContain('RAW_CLIENT_MATTER_123');
	});

	test('REGRESSION: a trace-SHAPED raw label is NOT trusted → recomputed canonical', () => {
		for (const poison of ['t-secretclient-matter', 't-mrchuang-caseabc', 't-johndoe-v-acme']) {
			const v = materialsWarningView(warn({ trace_id: poison }), 'chat1234cafef00d');
			expect(v.traceId).toBe('t-chat1234-msg45678');
			expect(v.traceId).not.toBe(poison);
			expect(v.banner).not.toContain(poison);
			expect(JSON.stringify(v)).not.toContain('secretclient');
			expect(JSON.stringify(v)).not.toContain('mrchuang');
			expect(JSON.stringify(v)).not.toContain('acme');
		}
	});

	test('REGRESSION: well-formed backend trace for DIFFERENT ids is discarded → recomputed', () => {
		const v = materialsWarningView(
			{ role: 'assistant', id: 'aaaaaaaabbbb', warning: { kind: PARTIAL_MATERIALS_KIND, used: 1, total: 3, unused: 2, trace_id: 't-deadbeef-feedface' } },
			'ccccccccdddd'
		);
		expect(v.traceId).toBe('t-cccccccc-aaaaaaaa');
		expect(v.traceId).not.toBe('t-deadbeef-feedface');
	});

	test('non-canonical kind + raw counts/content → sanitized (no raw, counts coerced)', () => {
		const v = materialsWarningView(
			{ role: 'assistant', id: 'msg45678aaaa', warning: { kind: RAW, used: '10', total: RAW, unused: -2, content: RAW, trace_id: RAW } },
			'chat1234bbbb'
		);
		expect(v.kind).toBe('unknown');
		expect([v.used, v.total, v.unused]).toEqual([0, 0, 0]); // all raw/invalid → 0
		expect(v.traceId).toBe('t-chat1234-msg45678'); // raw trace ignored → recomputed
		expect(JSON.stringify(v)).not.toContain('RAW_CLIENT_MATTER_123');
	});

	test('skipped/failed counts surfaced as integers (sanitized)', () => {
		const v = materialsWarningView(warn({ skipped: 1, failed: 2 }), 'chat1234cafef00d');
		expect([v.skipped, v.failed]).toEqual([1, 2]);
	});
});
