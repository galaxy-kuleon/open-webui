// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../support/index.d.ts" />

// LIVE 8083 human-PoV verification of the #17 slice-3 partial-materials warning.
// A REAL backend-produced partial-materials turn (a hermes-agent Path-B turn whose assistant
// message carries the privacy-safe {kind:'partial_materials', used,total,unused,...} warning the
// finalize hook persists + the live `chat:message:warning` socket event) is created out-of-band
// by the socket harness; this spec loads it in a REAL browser and asserts the amber
// MaterialsWarningNotice (counts + copyable opaque trace, NO Retry) renders ALONGSIDE — never
// replacing — the visible answer, that NO raw name/content leaks, and that an all-delivered turn
// shows NO notice. Uses a self-provisioned DISPOSABLE user; real users/chats are never touched.

const BASE = Cypress.config('baseUrl');
const EMAIL = Cypress.env('PM_EMAIL');
const PASSWORD = Cypress.env('PM_PASSWORD');
const PM_CHAT_ID = Cypress.env('PM_CHAT_ID'); // backend-produced partial-materials chat (used=1/total=2)
const PM_TRACE = Cypress.env('PM_TRACE'); // expected opaque trace id
const PM_NEG_CHAT_ID = Cypress.env('PM_NEG_CHAT_ID'); // all-delivered Path-B chat → NO notice

describe('OpenWebUI partial-materials warning surface (live 8083 E2E, issue #17 slice-3)', () => {
	let token: string;

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

	it('renders the amber MaterialsWarningNotice (counts + copyable trace, NO Retry) ALONGSIDE the visible answer', () => {
		expect(PM_CHAT_ID, 'partial-materials chat id provided').to.be.a('string').and.not.be.empty;
		openChat(PM_CHAT_ID);

		// The notice annotates the answer (additive), not a silent all-success.
		cy.get('[data-testid="materials-warning-notice"]', { timeout: 30000 }).should('be.visible');
		// Counts: 1/2 used · 1 not delivered (counts only — never file names).
		cy.get('[data-testid="materials-warning-counts"]')
			.should('contain', '1/2 used')
			.and('contain', '1 not delivered');
		// Opaque, copyable trace id — matches the backend-emitted trace.
		cy.get('[data-testid="materials-warning-trace"]').should('contain', PM_TRACE);
		// Banner is the fixed counts-only text.
		cy.get('[data-testid="materials-warning-banner"]').should('contain', 'may not have been used');
		// It is a WARNING, not an error: NO Retry affordance (the answer is valid).
		cy.get('[data-testid="materials-warning-notice"]').contains('button', 'Retry').should('not.exist');

		// The answer is STILL shown — the notice sits alongside it, never replacing it. The response
		// content container holds the rendered answer AND the notice; proving the container text is
		// strictly longer than the notice text confirms real answer content coexists (not blocked).
		cy.get('#response-content-container').then(($c) => {
			const containerText = $c.text().trim();
			const noticeText = $c.find('[data-testid="materials-warning-notice"]').text().trim();
			expect(noticeText.length, 'notice rendered').to.be.greaterThan(0);
			expect(
				containerText.length,
				'answer content renders alongside the notice (not blocked)'
			).to.be.greaterThan(noticeText.length);
		});

		// M4: nothing beyond counts + banner + trace appears in the notice — no raw file names.
		cy.get('[data-testid="materials-warning-notice"]')
			.invoke('text')
			.then((t) => {
				expect(t).to.contain(PM_TRACE as string);
				expect(t).to.not.contain('materials_alpha');
				expect(t).to.not.contain('materials_beta');
				expect(t).to.not.contain('.txt');
			});

		// Durable visual evidence (screenshotsFolder set via CYPRESS_screenshotsFolder).
		cy.screenshot('materials-warning-notice-live8083', { capture: 'viewport', overwrite: true });
	});

	it('does NOT surface the notice on an all-files-delivered Path-B turn', () => {
		expect(PM_NEG_CHAT_ID, 'all-delivered chat id provided').to.be.a('string').and.not.be.empty;
		openChat(PM_NEG_CHAT_ID);
		// The real answer renders...
		cy.get('#response-content-container', { timeout: 20000 }).should('exist');
		cy.get('#response-content-container').invoke('text').should('have.length.greaterThan', 0);
		// ...and the partial-materials notice does NOT.
		cy.get('[data-testid="materials-warning-notice"]').should('not.exist');
	});

	// NOTE: cleanup (disposable user + all throwaway chats) is performed centrally by the
	// verification orchestration AFTER durable evidence is written to reports/ — so this spec
	// intentionally does not delete, to keep the loaded chats available for evidence capture.
});
