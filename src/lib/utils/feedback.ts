export const PRODUCT_FEEDBACK_CATEGORIES = [
	{
		key: 'speed_too_slow',
		label: 'Too slow'
	},
	{
		key: 'stuck_or_incomplete',
		label: 'Got stuck or incomplete'
	},
	{
		key: 'download_or_export_failed',
		label: 'Download or export failed'
	},
	{
		key: 'formatting_or_layout_problem',
		label: 'Formatting or layout problem'
	},
	{
		key: 'ui_friction',
		label: 'Hard to use'
	},
	{
		key: 'other_product_issue',
		label: 'Other product issue'
	}
] as const;

export const LEGAL_QUALITY_FEEDBACK_CATEGORIES = [
	{
		key: 'missing_important_facts',
		label: 'Missing important facts'
	},
	{
		key: 'wrong_or_weak_jurisdiction_terminology',
		label: 'Wrong or weak jurisdiction terminology'
	},
	{
		key: 'risk_framing_insufficient',
		label: 'Risk framing insufficient'
	},
	{
		key: 'not_client_ready',
		label: 'Not client-ready'
	},
	{
		key: 'legal_advice_boundary_problem',
		label: 'Legal advice boundary problem'
	},
	{
		key: 'other_quality_issue',
		label: 'Other quality issue'
	}
] as const;

type FeedbackCategory =
	| (typeof PRODUCT_FEEDBACK_CATEGORIES)[number]
	| (typeof LEGAL_QUALITY_FEEDBACK_CATEGORIES)[number];

export const FEEDBACK_CATEGORIES: FeedbackCategory[] = [
	...PRODUCT_FEEDBACK_CATEGORIES,
	...LEGAL_QUALITY_FEEDBACK_CATEGORIES
];

export type FeedbackArtifact = {
	source: 'message_file' | 'code_execution_file' | 'status_url' | 'markdown_link';
	url?: string;
	id?: string;
	file_id?: string;
	name?: string;
	label?: string;
	type?: string;
};

const ARTIFACT_URL_PATTERNS = [
	'/download',
	'/export',
	'/files/',
	'/api/v1/files',
	'/v1/artifacts',
	'.docx',
	'.pdf',
	'.xlsx',
	'.pptx',
	'.csv',
	'.zip'
];

const isArtifactUrl = (url: unknown) => {
	if (typeof url !== 'string' || url.trim() === '') {
		return false;
	}

	const normalized = url.toLowerCase();
	return ARTIFACT_URL_PATTERNS.some((pattern) => normalized.includes(pattern));
};

const pushArtifact = (
	artifacts: FeedbackArtifact[],
	artifact: FeedbackArtifact,
	seen: Set<string>
) => {
	const key = `${artifact.source}:${artifact.url ?? ''}:${artifact.id ?? ''}:${artifact.file_id ?? ''}`;
	if (seen.has(key)) {
		return;
	}

	seen.add(key);
	artifacts.push(artifact);
};

export const extractFeedbackArtifacts = (message: Record<string, any> | null | undefined) => {
	const artifacts: FeedbackArtifact[] = [];
	const seen = new Set<string>();

	for (const file of message?.files ?? []) {
		if (file?.url || file?.id || file?.file_id) {
			pushArtifact(
				artifacts,
				{
					source: 'message_file',
					url: file.url,
					id: file.id,
					file_id: file.file_id,
					name: file.name,
					type: file.type
				},
				seen
			);
		}
	}

	for (const execution of message?.code_executions ?? []) {
		for (const file of execution?.result?.files ?? []) {
			if (file?.url || file?.id || file?.file_id) {
				pushArtifact(
					artifacts,
					{
						source: 'code_execution_file',
						url: file.url,
						id: file.id,
						file_id: file.file_id,
						name: file.name,
						type: file.type
					},
					seen
				);
			}
		}
	}

	for (const status of [message?.status, ...(message?.statusHistory ?? [])]) {
		for (const url of status?.urls ?? []) {
			if (isArtifactUrl(url)) {
				pushArtifact(
					artifacts,
					{
						source: 'status_url',
						url
					},
					seen
				);
			}
		}
	}

	const content = typeof message?.content === 'string' ? message.content : '';
	const markdownLinkPattern = /\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
	for (const match of content.matchAll(markdownLinkPattern)) {
		const label = match[1]?.trim();
		const url = match[2]?.trim();
		if (isArtifactUrl(url)) {
			pushArtifact(
				artifacts,
				{
					source: 'markdown_link',
					label,
					url
				},
				seen
			);
		}
	}

	return artifacts;
};

export const getFeedbackRequestErrorMessage = (error: unknown, fallback: string) => {
	const message =
		error instanceof Error
			? error.message
			: typeof error === 'string'
				? error
				: error === null || error === undefined
					? ''
					: String(error);

	return message.trim() || fallback;
};

export type FeedbackDialogState = 'idle' | 'submitting' | 'success' | 'error';

export const getFeedbackDialogState = ({
	submitted,
	saving,
	error,
	currentState
}: {
	submitted: boolean;
	saving: boolean;
	error: string;
	currentState: FeedbackDialogState;
}): FeedbackDialogState => {
	if (!submitted) {
		return currentState;
	}

	if (saving) {
		return 'submitting';
	}

	if (error.trim()) {
		return 'error';
	}

	if (currentState === 'submitting') {
		return 'success';
	}

	return currentState;
};
