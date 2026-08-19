// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../support/index.d.ts" />

// E2E verification of the OpenWebUI conversation feedback flow (issue #5),
// run against the LIVE 8083 stack via the REAL browser path.
//
// Auth: the live stack has signup DISABLED and an initialized DB, so the
// usual first-user-admin bootstrap does not apply. A clearly-labelled
// DISPOSABLE test user is created out-of-band (docker exec, see the run
// wrapper / report) and this spec authenticates programmatically (signin).
// It never touches real users or their chats/feedback. The chat is seeded
// via API with a completed assistant message, the feedback widget is driven
// in the browser, persistence is verified via the evaluations API, and all
// disposable records are cleaned up afterwards.

const BASE = Cypress.config('baseUrl');
const EMAIL = Cypress.env('E2E_FEEDBACK_EMAIL') || 'e2e-feedback-cypress-disposable@example.com';
const PASSWORD = Cypress.env('E2E_FEEDBACK_PASSWORD') || 'Cypr3ssE2E-feedback!';
const MODEL = Cypress.env('E2E_FEEDBACK_MODEL') || 'lmstudio.qwen3.5-4b-mtp';

// Two categories spanning BOTH taxonomy groups (src/lib/utils/feedback.ts).
const PRODUCT_CATEGORY = { key: 'formatting_or_layout_problem', label: 'Formatting or layout problem' };
const LEGAL_CATEGORY = { key: 'missing_important_facts', label: 'Missing important facts' };
const FREE_TEXT = 'E2E disposable feedback: layout was off and a key fact was missing.';

const uuid = () =>
	'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
		const r = (Math.random() * 16) | 0;
		const v = c === 'x' ? r : (r & 0x3) | 0x8;
		return v.toString(16);
	});

describe('OpenWebUI conversation feedback flow (live 8083 E2E, issue #5)', () => {
	let token: string;
	const seededChatIds: string[] = [];

	const apiReq = (method: string, url: string, body?: unknown) =>
		cy.request({
			method,
			url: `${BASE}${url}`,
			headers: { Authorization: `Bearer ${token}` },
			body,
			failOnStatusCode: false
		});

	// Self-provision the disposable user (signup is disabled on the live stack),
	// then programmatic login. The provision task is idempotent, so repeated
	// `cypress run` is reproducibly green. Real users are never touched.
	before(() => {
		cy.task('provisionFeedbackUser').then((out) => {
			cy.log(`provisionFeedbackUser: ${out}`);
			expect(String(out), 'disposable user provisioned').to.contain('USER_ID=');
		});
		cy.request({
			method: 'POST',
			url: `${BASE}/api/v1/auths/signin`,
			body: { email: EMAIL, password: PASSWORD },
			failOnStatusCode: false
		}).then((res) => {
			expect(res.status, 'disposable test user signin').to.eq(200);
			token = res.body.token;
			expect(token, 'auth token').to.be.a('string').and.not.to.be.empty;
		});
	});

	// Seed a disposable chat with a COMPLETED assistant message (no LLM call,
	// fully deterministic) so the feedback widget renders on a real message.
	const seedChat = () => {
		const userId = uuid();
		const assistantId = uuid();
		const now = Math.floor(Date.now() / 1000);
		const userMsg = {
			id: userId,
			parentId: null,
			childrenIds: [assistantId],
			role: 'user',
			content: 'E2E feedback disposable prompt',
			timestamp: now,
			models: [MODEL]
		};
		const assistantMsg = {
			id: assistantId,
			parentId: userId,
			childrenIds: [],
			role: 'assistant',
			content: 'Disposable E2E assistant response used to exercise the feedback widget.',
			model: MODEL,
			modelName: 'Origin Flash',
			done: true,
			timestamp: now,
			// Pre-set tags ([] is truthy) so the thumbs path skips the LLM
			// tag-generation call — keeps the flow deterministic + offline.
			annotation: { tags: [] }
		};
		const chat = {
			id: '',
			title: 'E2E Feedback DISPOSABLE',
			models: [MODEL],
			params: {},
			files: [],
			tags: [],
			timestamp: now * 1000,
			history: { currentId: assistantId, messages: { [userId]: userMsg, [assistantId]: assistantMsg } },
			messages: [userMsg, assistantMsg]
		};
		return apiReq('POST', '/api/v1/chats/new', { chat }).then((res) => {
			expect(res.status, 'seed chat').to.eq(200);
			const chatId = res.body.id;
			seededChatIds.push(chatId);
			return cy.wrap({ chatId, assistantId }, { log: false });
		});
	};

	// Authenticate the browser session (token in localStorage) and open the chat.
	const openChat = (chatId: string) => {
		cy.visit(`/c/${chatId}`, {
			onBeforeLoad(win) {
				win.localStorage.setItem('token', token);
				win.localStorage.setItem('locale', 'en-US');
				win.localStorage.setItem('version', '0.0.0'); // non-null => skip changelog dialog
			}
		});
		// Dismiss onboarding dialog if it still appears.
		cy.get('body').then(($b) => {
			if ($b.find('button:contains("Okay, Let\'s Go!")').length) {
				cy.contains('button', "Okay, Let's Go!").click();
			}
		});
	};

	it('submits feedback (thumbs + rating + multiple categories across both groups + free text) and persists the binding', () => {
		seedChat().then(({ chatId, assistantId }) => {
			openChat(chatId);

			// The seeded assistant message renders with its action toolbar.
			cy.get('button[aria-label="Good Response"]', { timeout: 20000 })
				.should('exist')
				.first()
				.click({ force: true });

			// The inline RateComment panel for this message appears.
			const panel = `#message-feedback-${assistantId}`;
			cy.get(panel, { timeout: 15000 }).should('be.visible');

			cy.get(panel).within(() => {
				// Detailed rating (thumbs-up enables 6-10).
				cy.get('button[aria-label="Rate 8 out of 10"]').click({ force: true });
				// Multiple categories across BOTH groups.
				cy.contains('button', PRODUCT_CATEGORY.label).click({ force: true });
				cy.contains('button', LEGAL_CATEGORY.label).click({ force: true });
				cy.contains('button', PRODUCT_CATEGORY.label).should('have.attr', 'aria-pressed', 'true');
				cy.contains('button', LEGAL_CATEGORY.label).should('have.attr', 'aria-pressed', 'true');
				// Free text.
				cy.get('textarea[aria-label="Additional feedback comments"]').type(FREE_TEXT);
				// Submit.
				cy.contains('button', 'Save').click({ force: true });
			});

			// UI success state.
			cy.contains('Thanks for your feedback!', { timeout: 15000 }).should('exist');

			// Persistence: verify via the evaluations feedback API.
			const findFeedback = (attempt = 0) =>
				apiReq('GET', '/api/v1/evaluations/feedbacks/user').then((res) => {
					expect(res.status, 'list user feedbacks').to.eq(200);
					const fb = (res.body || []).find(
						(f: any) => f?.meta?.message_id === assistantId
					);
					if (!fb && attempt < 5) {
						cy.wait(700);
						return findFeedback(attempt + 1);
					}
					expect(fb, 'persisted feedback for the rated message').to.exist;

					// Binding present: user_id / chat_id / message_id / model.
					expect(fb.user_id, 'bound user_id').to.be.a('string').and.not.be.empty;
					expect(fb.meta.chat_id, 'bound chat_id').to.eq(chatId);
					expect(fb.meta.message_id, 'bound message_id').to.eq(assistantId);
					expect(fb.data.model_id, 'bound model').to.eq(MODEL);
					expect(fb.type).to.eq('rating');

					// Multiple categories across both groups.
					expect(fb.data.categories, 'categories[]').to.include(PRODUCT_CATEGORY.key);
					expect(fb.data.categories, 'categories[]').to.include(LEGAL_CATEGORY.key);

					// Free text + positive signal.
					const freeText = fb.data.free_text || fb.data.comment;
					expect(freeText, 'free text').to.contain('layout was off');
					expect(fb.data.rating_signal, 'thumbs-up signal').to.eq('positive');

					// Graceful no-artifact behavior: no artifacts on a plain text message.
					expect(fb.meta.artifact_count, 'artifact_count').to.eq(0);
					expect(fb.data.artifacts ?? [], 'no artifacts').to.have.length(0);
				});
			findFeedback();
		});
	});

	// Cleanup: remove this disposable user's feedbacks + seeded chats (API),
	// THEN delete the disposable user row itself (idempotent task) so a re-run
	// re-provisions cleanly. Real users/feedback are never touched.
	after(() => {
		if (token) {
			apiReq('DELETE', '/api/v1/evaluations/feedbacks'); // disposable user's feedbacks only
			seededChatIds.forEach((id) => apiReq('DELETE', `/api/v1/chats/${id}`));
		}
		cy.task('deleteFeedbackUser').then((out) => cy.log(`deleteFeedbackUser: ${out}`));
	});
});
