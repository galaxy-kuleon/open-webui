import { describe, expect, test } from 'vitest';
import { compile } from 'svelte/compiler';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/**
 * The pill guard, asserted on the COMPILER'S classification instead of on markup.
 *
 * The Python guard in `tests/ops/test_daily_report_cause.py` reads this component as
 * TEXT and applies the rule "`{cause}` may follow an `=`, never anything else". That is a
 * real property, and it was mutation-verified — but it reads markup the way a person reads
 * it. Svelte decides what is an attribute and what is a text node, and this reads that
 * decision out of the generated code:
 *
 *   $.set_attribute(span, 'data-cause', cause());   <- the routing key, for ops
 *   $.set_attribute(span, 'title', cause());
 *   $.set_text(text_1, $0);                          <- $0 = causeLabel(cause())
 *
 * Written after adversarial review restored the defect twice past a guard that pinned one
 * historical spelling: once as `>{cause} — {causeLabel(cause)}<`, once as a separate span a
 * line-filter never saw. Both become a `set_text` carrying a bare `cause()`, which is
 * exactly what this refuses — whatever the markup around them looks like.
 */
const SOURCE = fileURLToPath(new URL('./EmptyTurnNotice.svelte', import.meta.url));

const compiled = () =>
	compile(readFileSync(SOURCE, 'utf8'), { filename: SOURCE }).js.code;

/** Every argument list passed to `$.set_text(...)`, balanced-paren aware. */
const setTextArgs = (js: string): string[] => {
	const out: string[] = [];
	const needle = 'set_text(';
	let i = js.indexOf(needle);
	while (i !== -1) {
		let depth = 0;
		let j = i + needle.length - 1;
		for (; j < js.length; j++) {
			if (js[j] === '(') depth++;
			else if (js[j] === ')') {
				depth--;
				if (depth === 0) break;
			}
		}
		out.push(js.slice(i + needle.length, j));
		i = js.indexOf(needle, j);
	}
	return out;
};

describe('EmptyTurnNotice: the cause code is never a text node', () => {
	test('the component compiles and the guard has something to look at', () => {
		const js = compiled();
		// Positive control. Without it, every assertion below is vacuously true on an
		// empty string — the shape of half the false zeros in this repo's register.
		expect(js).toContain('empty-turn-cause');
		expect(setTextArgs(js).length).toBeGreaterThan(0);
	});

	test('the raw cause reaches ONLY attributes', () => {
		const js = compiled();
		// Strip the calls that are allowed to carry it, then nothing may be left.
		// `deep_read_state` is Svelte's reactivity bookkeeping — it READS the value to
		// register a dependency and renders nothing — so it belongs here rather than in
		// the failure set. Found by the guard flagging it on a clean tree, which is the
		// baseline every negative control needs before its verdict means anything.
		const withoutAttrs = js
			.replace(/set_attribute\([^;]*?\);/g, '')
			.replace(/deep_read_state\(cause\(\)\)/g, '');
		const leftovers = withoutAttrs
			.split('\n')
			.filter((l) => /\bcause\(\)/.test(l) && !/causeLabel\(cause\(\)\)/.test(l));
		expect(leftovers).toEqual([]);
	});

	test('no set_text call receives a bare cause()', () => {
		for (const arg of setTextArgs(compiled())) {
			const bare = arg.replace(/causeLabel\(cause\(\)\)/g, '');
			expect(bare).not.toMatch(/\bcause\(\)/);
		}
	});

	test('ops keeps the routing key on the pill', () => {
		const js = compiled();
		expect(js).toMatch(/set_attribute\([^,]+,\s*'data-cause',\s*cause\(\)\)/);
	});

	test('the words a person reads come from causeLabel', () => {
		expect(compiled()).toContain('causeLabel(cause())');
	});
});
