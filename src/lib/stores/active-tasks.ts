import { writable } from 'svelte/store';

/**
 * #16 runtime slice: per-chat active-task state for the interrupted-turn load-time guard.
 *
 * Map key present  → the chat was probed (via checkActiveChats → POST /tasks/active/chats).
 * Map value (bool) → the chat currently HAS an active task.
 * Key ABSENT       → unprobed; callers MUST treat this as "active/unknown" and NOT flag, so a
 *                    turn streaming live in another tab / a navigated-away turn is never
 *                    misflagged as interrupted.
 */
export const chatActiveTasks = writable<Map<string, boolean>>(new Map());

/** Record a probe result without losing other chats' state (new Map → reactive update). */
export const setChatActiveTask = (chatId: string, active: boolean) => {
	chatActiveTasks.update((m) => {
		const next = new Map(m);
		next.set(chatId, active);
		return next;
	});
};
