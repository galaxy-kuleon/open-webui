// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../support/index.d.ts" />

// LIVE 8083 human-PoV verification of the #16 runtime slice (empty/stuck assistant turn).
// A REAL backend-produced empty turn (its assistant message carries the privacy-safe
// {content, cause: db_stream_flush, trace_id} error the finalizer/cancel branch writes) is
// created out-of-band by the socket-stop harness; this spec loads it in a REAL browser and
// asserts the in-place EmptyTurnNotice (cause chip + copyable opaque trace + Retry) renders
// instead of a silent blank, that NO raw content leaks, and that a normal non-empty turn is
// unaffected. Uses a self-provisioned DISPOSABLE user; real users/chats are never touched.

const BASE = Cypress.config('baseUrl');
const EMAIL = Cypress.env('ET_EMAIL');
const PASSWORD = Cypress.env('ET_PASSWORD');
const ET_CHAT_ID = Cypress.env('ET_CHAT_ID'); // backend-produced empty-turn chat (Case 1)
const ET_TRACE = Cypress.env('ET_TRACE'); // expected opaque trace id
const MODEL = 'lmstudio.qwen3.5-4b';

const uuid = () =>
	'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
		const r = (Math.random() * 16) | 0;
		const v = c === 'x' ? r : (r & 0x3) | 0x8;
		return v.toString(16);
	});

describe('OpenWebUI empty/stuck turn surface (live 8083 E2E, issue #16)', () => {
	let token: string;
	const seededChatIds: string[] = [];

	const apiReq = (method: string, url: string, body?: unknown) =>
		cy.request({ method, url: `${BASE}${url}`, headers: { Authorization: `Bearer ${token}` }, body, failOnStatusCode: false });

	before(() => {
		cy.request({
			method: 'POST',
			url: `${BASE}/api/v1/auths/signin`,
			body: { email: EMAIL, password: PASSWORD },
			failOnStatusCode: false
		}).then((res) => {
			expect(res.status, 'disposable user signin').to.eq(200);
			token = res.body.token;
			expect(token, 'auth token').to.be.a('string').and.not.to.be.empty;
		});
	});

	const openChat = (chatId: string) => {
		cy.visit(`/c/${chatId}`, {
			onBeforeLoad(win) {
				win.localStorage.setItem('token', token);
				win.localStorage.setItem('locale', 'en-US');
				win.localStorage.setItem('version', '0.0.0');
			}
		});
		cy.get('body').then(($b) => {
			if ($b.find('button:contains("Okay, Let\'s Go!")').length) {
				cy.contains('button', "Okay, Let's Go!").click();
			}
		});
	};

	it('renders the in-place EmptyTurnNotice (cause + copyable trace + Retry) for a real backend-produced empty turn', () => {
		expect(ET_CHAT_ID, 'empty-turn chat id provided').to.be.a('string').and.not.be.empty;
		openChat(ET_CHAT_ID);

		// The notice replaces a silent blank, in-place on the assistant message.
		cy.get('[data-testid="empty-turn-notice"]', { timeout: 30000 }).should('be.visible');
		// Human words on screen; the CODE is an attribute for ops, never a text node.
		cy.get('[data-testid="empty-turn-cause"]').should('contain', 'cause unknown');
		cy.get('[data-testid="empty-turn-cause"]').should('have.attr', 'data-cause', 'db_stream_flush');
		// Opaque, copyable trace id — matches the backend-emitted trace.
		cy.get('[data-testid="empty-turn-trace"]').should('contain', ET_TRACE);
		// Explicit Retry affordance.
		cy.get('[data-testid="empty-turn-notice"]').contains('button', 'Retry').should('exist');
		// Banner is the fixed cause+trace text (no raw content).
		cy.get('[data-testid="empty-turn-banner"]').should('contain', 'without a final answer');

		// M4: nothing beyond the fixed banner + cause + trace appears in the notice.
		cy.get('[data-testid="empty-turn-notice"]')
			.invoke('text')
			.then((t) => {
				// The routing key must NOT be readable text anywhere in the notice.
				expect(t).to.contain('cause unknown');
				expect(t).to.not.contain('db_stream_flush');
				expect(t).to.contain(ET_TRACE as string);
				// the disposable prompt filler must NOT be echoed into the failure UI
				expect(t).to.not.contain('The sea is vast and deep');
			});

		// Durable visual evidence (screenshotsFolder set via CYPRESS_screenshotsFolder).
		cy.screenshot('empty-turn-notice-live8083', { capture: 'viewport', overwrite: true });
	});

	it('does NOT surface the notice on a normal non-empty assistant turn', () => {
		const userId = uuid();
		const assistantId = uuid();
		const now = Math.floor(Date.now() / 1000);
		const NORMAL = 'This is a perfectly normal completed answer with visible content.';
		const userMsg = { id: userId, parentId: null, childrenIds: [assistantId], role: 'user', content: 'normal prompt', timestamp: now, models: [MODEL] };
		const assistantMsg = { id: assistantId, parentId: userId, childrenIds: [], role: 'assistant', content: NORMAL, model: MODEL, modelName: 'Origin Flash', done: true, timestamp: now };
		const chat = {
			id: '', title: 'ET NORMAL DISPOSABLE', models: [MODEL], params: {}, files: [], tags: [], timestamp: now * 1000,
			history: { currentId: assistantId, messages: { [userId]: userMsg, [assistantId]: assistantMsg } },
			messages: [userMsg, assistantMsg]
		};
		apiReq('POST', '/api/v1/chats/new', { chat }).then((res) => {
			expect(res.status, 'seed normal chat').to.eq(200);
			const chatId = res.body.id;
			seededChatIds.push(chatId);
			openChat(chatId);
			// The real answer renders...
			cy.contains(NORMAL, { timeout: 20000 }).should('exist');
			// ...and the empty-turn notice does NOT.
			cy.get('[data-testid="empty-turn-notice"]').should('not.exist');
		});
	});

	// NOTE: cleanup (disposable user + all throwaway chats) is performed centrally by the
	// verification orchestration AFTER durable evidence is written to reports/ — so this spec
	// intentionally does not delete, to keep the loaded chat available for evidence capture.
});
