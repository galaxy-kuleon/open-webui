import { WEBUI_API_BASE_URL } from '$lib/constants';

/**
 * Typed frontend client for GET /api/v1/hermes/continuation/probe.
 *
 * The endpoint is always current_user-scoped on the backend — no identity
 * fields are accepted from the client. Pass the user's JWT token only.
 */

export interface ContinuationProbeResult {
	suggested: boolean;
	task_summary: string | null;
	confidence: 'low' | 'medium' | 'high';
	last_session_age_hours: number | null;
}

const _FALLBACK: ContinuationProbeResult = {
	suggested: false,
	task_summary: null,
	confidence: 'low',
	last_session_age_hours: null
};

/**
 * Probe hermes for a continuation suggestion from the previous session.
 *
 * Always resolves (never rejects) — network errors and non-2xx responses
 * are returned as ``{suggested: false}``.
 */
export async function probeContinuation(token: string): Promise<ContinuationProbeResult> {
	try {
		const res = await fetch(`${WEBUI_API_BASE_URL}/hermes/continuation/probe`, {
			method: 'GET',
			headers: {
				Accept: 'application/json',
				authorization: `Bearer ${token}`
			}
		});
		if (!res.ok) {
			return { ..._FALLBACK };
		}
		const data = await res.json();
		return {
			suggested: Boolean(data.suggested),
			task_summary: data.task_summary ?? null,
			confidence: data.confidence ?? 'low',
			last_session_age_hours: data.last_session_age_hours ?? null
		};
	} catch {
		return { ..._FALLBACK };
	}
}
