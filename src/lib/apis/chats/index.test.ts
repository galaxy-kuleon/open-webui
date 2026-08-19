import { afterEach, describe, expect, test, vi } from 'vitest';

import { reconcileServerTurnState, updateChatById } from './index';

const response = (status: number, body: object) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'Content-Type': 'application/json' }
	});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('updateChatById stale terminal-state recovery', () => {
	test('refetches server-owned state and retries once without losing client metadata', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(
				response(409, {
					detail: { reason: 'stale_turn_state', message_ids: ['assistant-1'] }
				})
			)
			.mockResolvedValueOnce(
				response(200, {
					chat: {
						history: {
							messages: {
								'assistant-1': {
									done: true,
									error: { content: 'server terminal banner' }
								}
							}
						}
					}
				})
			)
			.mockResolvedValueOnce(response(200, { id: 'chat-1', saved: true }));
		vi.stubGlobal('fetch', fetchMock);

		const client = {
			title: 'new title',
			history: {
				messages: {
					'assistant-1': { role: 'assistant', done: false, content: 'answer' }
				}
			}
		};
		const result = await updateChatById('token', 'chat-1', client);

		expect(result).toEqual({ id: 'chat-1', saved: true });
		expect(fetchMock).toHaveBeenCalledTimes(3);
		expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'GET' });
		const retried = JSON.parse(fetchMock.mock.calls[2][1].body as string).chat;
		expect(retried.title).toBe('new title');
		expect(retried.history.messages['assistant-1']).toMatchObject({
			done: true,
			error: { content: 'server terminal banner' }
		});
		expect(client.history.messages['assistant-1']).toEqual({
			role: 'assistant',
			done: false,
			content: 'answer'
		});
	});

	test('does not retry unrelated errors', async () => {
		const body = { detail: 'forbidden' };
		const fetchMock = vi.fn().mockResolvedValue(response(403, body));
		vi.stubGlobal('fetch', fetchMock);
		vi.spyOn(console, 'error').mockImplementation(() => undefined);

		await expect(updateChatById('token', 'chat-1', {})).rejects.toEqual(body);
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	test('stops after one retry when the server changes again', async () => {
		const conflict = {
			detail: { reason: 'stale_turn_state', message_ids: ['assistant-1'] }
		};
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(response(409, conflict))
			.mockResolvedValueOnce(
				response(200, {
					chat: { history: { messages: { 'assistant-1': { done: true } } } }
				})
			)
			.mockResolvedValueOnce(response(409, conflict));
		vi.stubGlobal('fetch', fetchMock);
		vi.spyOn(console, 'error').mockImplementation(() => undefined);

		await expect(
			updateChatById('token', 'chat-1', {
				history: { messages: { 'assistant-1': { role: 'assistant', done: false } } }
			})
		).rejects.toEqual(conflict);
		expect(fetchMock).toHaveBeenCalledTimes(3);
	});
});

describe('reconcileServerTurnState', () => {
	test('fails closed when the conflicting server message cannot be fetched', () => {
		expect(
			reconcileServerTurnState(
				{ history: { messages: { 'assistant-1': { done: false } } } },
				{ history: { messages: {} } },
				['assistant-1']
			)
		).toBeNull();
	});
});
