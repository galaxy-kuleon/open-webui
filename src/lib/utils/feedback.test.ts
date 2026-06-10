import { describe, expect, test } from 'vitest';

import {
	FEEDBACK_CATEGORIES,
	extractFeedbackArtifacts,
	getFeedbackDialogState,
	getFeedbackRequestErrorMessage,
	PRODUCT_FEEDBACK_CATEGORIES,
	LEGAL_QUALITY_FEEDBACK_CATEGORIES
} from './feedback';

describe('feedback utilities', () => {
	test('exports both product and legal quality category groups', () => {
		expect(FEEDBACK_CATEGORIES.map((category) => category.key)).toEqual([
			...PRODUCT_FEEDBACK_CATEGORIES.map((category) => category.key),
			...LEGAL_QUALITY_FEEDBACK_CATEGORIES.map((category) => category.key)
		]);
	});

	test('extracts artifact metadata from files, execution output, status URLs, and markdown links', () => {
		const artifacts = extractFeedbackArtifacts({
			files: [{ id: 'file-1', name: 'source.pdf', type: 'file', url: '/api/v1/files/file-1' }],
			code_executions: [
				{
					result: {
						files: [{ name: 'result.docx', url: '/downloads/result.docx' }]
					}
				}
			],
			status: {
				urls: ['/v1/artifacts/a1/output.docx/download/1893456000/sig', 'https://example.com/page']
			},
			content:
				'Generated [DOCX](/v1/artifacts/a1/output.docx/download/1893456000/sig) and [site](https://example.com/page).'
		});

		expect(artifacts).toEqual([
			{
				source: 'message_file',
				url: '/api/v1/files/file-1',
				id: 'file-1',
				file_id: undefined,
				name: 'source.pdf',
				type: 'file'
			},
			{
				source: 'code_execution_file',
				url: '/downloads/result.docx',
				id: undefined,
				file_id: undefined,
				name: 'result.docx',
				type: undefined
			},
			{
				source: 'status_url',
				url: '/v1/artifacts/a1/output.docx/download/1893456000/sig'
			},
			{
				source: 'markdown_link',
				label: 'DOCX',
				url: '/v1/artifacts/a1/output.docx/download/1893456000/sig'
			}
		]);
	});

	test('deduplicates repeated artifact sources', () => {
		const artifacts = extractFeedbackArtifacts({
			files: [
				{ id: 'file-1', url: '/api/v1/files/file-1' },
				{ id: 'file-1', url: '/api/v1/files/file-1' }
			]
		});

		expect(artifacts).toHaveLength(1);
	});

	test('uses request-failed fallback for silent feedback submission failures', () => {
		expect(getFeedbackRequestErrorMessage(null, 'Feedback request failed')).toBe(
			'Feedback request failed'
		);
		expect(getFeedbackRequestErrorMessage('', 'Feedback request failed')).toBe(
			'Feedback request failed'
		);
		expect(getFeedbackRequestErrorMessage(new Error(''), 'Feedback request failed')).toBe(
			'Feedback request failed'
		);
		expect(getFeedbackRequestErrorMessage(new Error('server unavailable'), 'Feedback request failed')).toBe(
			'server unavailable'
		);
	});

	test('keeps silent failed feedback submissions in error state instead of false success', () => {
		const fallbackError = getFeedbackRequestErrorMessage(null, 'Request failed');

		expect(
			getFeedbackDialogState({
				submitted: true,
				saving: false,
				error: fallbackError,
				currentState: 'submitting'
			})
		).toBe('error');
	});
});
