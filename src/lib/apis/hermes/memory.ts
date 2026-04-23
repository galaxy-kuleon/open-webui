import { WEBUI_API_BASE_URL } from '$lib/constants';

/**
 * Typed frontend client for /api/v1/hermes/memory/* (W3 backend).
 *
 * All endpoints are current_user-scoped on the backend — the router
 * ignores any identity fields in the request body and resolves from
 * the authenticated session. These helpers accept the user's JWT
 * token and pass it verbatim; no identity is sent client-side.
 */

interface HermesToolResponse<T = unknown> {
	result: T;
}

interface FactListResponse {
	facts?: Array<{
		id: number;
		content: string;
		category?: string;
		tags?: string;
		trust_score?: number;
	}>;
	count?: number;
}

interface FactAddResponse {
	fact_id: number;
	status: string;
}

interface FactRemoveResponse {
	removed: boolean;
}

interface FactSearchResponse {
	results?: Array<{
		id: number;
		content: string;
		trust_score?: number;
	}>;
	count?: number;
}

async function _requestJSON<T>(
	method: 'GET' | 'POST' | 'DELETE',
	path: string,
	token: string,
	body?: object
): Promise<HermesToolResponse<T>> {
	const url = `${WEBUI_API_BASE_URL}/hermes/memory${path}`;
	const headers: Record<string, string> = {
		Accept: 'application/json',
		authorization: `Bearer ${token}`
	};
	if (body !== undefined) {
		headers['Content-Type'] = 'application/json';
	}
	const res = await fetch(url, {
		method,
		headers,
		body: body === undefined ? undefined : JSON.stringify(body)
	});
	if (!res.ok) {
		let detail: string;
		try {
			const data = await res.json();
			detail = data?.detail ?? JSON.stringify(data);
		} catch {
			detail = `HTTP ${res.status}`;
		}
		throw new Error(detail);
	}
	return res.json();
}

export async function getHermesProfile(
	token: string
): Promise<HermesToolResponse<FactListResponse>> {
	return _requestJSON<FactListResponse>('GET', '/profile', token);
}

export async function addHermesFact(
	token: string,
	content: string,
	opts?: { category?: string; tags?: string }
): Promise<HermesToolResponse<FactAddResponse>> {
	return _requestJSON<FactAddResponse>('POST', '/profile', token, {
		content,
		category: opts?.category ?? 'user_pref',
		tags: opts?.tags
	});
}

export async function removeHermesFact(
	token: string,
	fact_id: number
): Promise<HermesToolResponse<FactRemoveResponse>> {
	return _requestJSON<FactRemoveResponse>('DELETE', `/profile/${fact_id}`, token);
}

export async function searchHermesMemory(
	token: string,
	query: string,
	limit: number = 10
): Promise<HermesToolResponse<FactSearchResponse>> {
	const q = encodeURIComponent(query);
	return _requestJSON<FactSearchResponse>('GET', `/search?q=${q}&limit=${limit}`, token);
}
