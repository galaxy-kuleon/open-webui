/**
 * Reconciling a reloaded chat against its running tasks — with UNKNOWN as a
 * first-class answer.
 *
 * This exists because two independently reasonable `.catch()` handlers combined
 * into silence. On reload the browser makes two probes: one asks whether the
 * chat has an active task, the other asks for its pending task ids. The first
 * swallowed its failure and left the active-state map ABSENT, which the
 * renderer reads as "still running" and therefore suppresses the empty-turn
 * notice. The second swallowed its failure as `[]` — "no tasks" — and the
 * reconciler then marked an unfinished assistant message `done`.
 *
 * Together: `done` is true so there is no spinner, the notice is suppressed
 * because the state is unknown, and the content is empty. The user gets a blank
 * space that claims to be a finished answer. Neither catch is wrong on its own,
 * which is exactly why this needed to be one decision instead of two.
 *
 * The rule: only a SUCCESSFUL probe reporting no tasks may finish a turn.
 * "I could not ask" must never become "there is nothing running".
 */

/** What the task probe actually told us. `null` means the probe failed. */
export type PendingTaskIds = string[] | null;

export interface ReconcileInput {
	pendingTaskIds: PendingTaskIds;
	/** The reloaded assistant message already carries done=true. */
	responseComplete: boolean;
	/** There is an assistant leaf that has not finished. */
	assistantIncomplete: boolean;
}

export interface ReconcileDecision {
	/** Task ids to track, or null when there is nothing to track. */
	taskIds: string[] | null;
	/** Mark the unfinished assistant message as done. */
	markDone: boolean;
	/** Why — for tests and for anyone reading a bug report later. */
	reason: 'probe_failed_state_unknown' | 'tasks_running' | 'no_tasks_interrupted' | 'already_complete';
}

export const reconcileTasks = (input: ReconcileInput): ReconcileDecision => {
	if (input.pendingTaskIds === null) {
		// UNKNOWN. Change nothing: an unfinished message keeps its spinner, which
		// is honest. Marking it done shows a blank bubble asserting the turn ended.
		return { taskIds: null, markDone: false, reason: 'probe_failed_state_unknown' };
	}
	if (input.pendingTaskIds.length > 0 && !input.responseComplete) {
		return { taskIds: input.pendingTaskIds, markDone: false, reason: 'tasks_running' };
	}
	if (input.assistantIncomplete) {
		// Definitely no active tasks and the turn never finished — interrupted.
		return { taskIds: null, markDone: true, reason: 'no_tasks_interrupted' };
	}
	return { taskIds: null, markDone: false, reason: 'already_complete' };
};
