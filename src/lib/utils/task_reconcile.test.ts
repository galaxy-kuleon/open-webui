import { describe, expect, test } from 'vitest';
import { reconcileTasks } from './task_reconcile';

describe('reconcileTasks — a failed probe is not an answer', () => {
	test('THE DEFECT: a failed probe must not finish an unfinished turn', () => {
		// Round 55's disproof. `.catch(() => [])` made "I could not ask" mean
		// "there are no tasks", and the reconciler marked the message done. With
		// the sibling probe also failed, the notice is suppressed as unknown and
		// the spinner is gone because done=true: blank space, no explanation.
		const d = reconcileTasks({
			pendingTaskIds: null,
			responseComplete: false,
			assistantIncomplete: true
		});
		expect(d.markDone).toBe(false);
		expect(d.reason).toBe('probe_failed_state_unknown');
	});

	test('a SUCCESSFUL empty probe may finish an interrupted turn', () => {
		// The distinction that matters: [] is a fact, null is the absence of one.
		const d = reconcileTasks({
			pendingTaskIds: [],
			responseComplete: false,
			assistantIncomplete: true
		});
		expect(d.markDone).toBe(true);
		expect(d.reason).toBe('no_tasks_interrupted');
	});

	test('null and [] must not produce the same decision', () => {
		const unknown = reconcileTasks({
			pendingTaskIds: null,
			responseComplete: false,
			assistantIncomplete: true
		});
		const empty = reconcileTasks({
			pendingTaskIds: [],
			responseComplete: false,
			assistantIncomplete: true
		});
		expect(unknown.markDone).not.toBe(empty.markDone);
	});

	test('running tasks are tracked and nothing is finished', () => {
		const d = reconcileTasks({
			pendingTaskIds: ['t-1', 't-2'],
			responseComplete: false,
			assistantIncomplete: true
		});
		expect(d.taskIds).toEqual(['t-1', 't-2']);
		expect(d.markDone).toBe(false);
		expect(d.reason).toBe('tasks_running');
	});

	test('background work after a complete response does not block the input', () => {
		// Follow-ups and title generation keep running after the answer lands.
		const d = reconcileTasks({
			pendingTaskIds: ['title-gen'],
			responseComplete: true,
			assistantIncomplete: false
		});
		expect(d.taskIds).toBeNull();
		expect(d.reason).toBe('already_complete');
	});

	test('a complete turn is never re-marked', () => {
		const d = reconcileTasks({
			pendingTaskIds: [],
			responseComplete: true,
			assistantIncomplete: false
		});
		expect(d.markDone).toBe(false);
	});

	test('every reason is reachable, so none is decorative', () => {
		const seen = new Set(
			[
				{ pendingTaskIds: null, responseComplete: false, assistantIncomplete: true },
				{ pendingTaskIds: ['a'], responseComplete: false, assistantIncomplete: true },
				{ pendingTaskIds: [], responseComplete: false, assistantIncomplete: true },
				{ pendingTaskIds: [], responseComplete: true, assistantIncomplete: false }
			].map((i) => reconcileTasks(i).reason)
		);
		expect(seen).toEqual(
			new Set([
				'probe_failed_state_unknown',
				'tasks_running',
				'no_tasks_interrupted',
				'already_complete'
			])
		);
	});
});
