import { defineConfig } from 'cypress';
import { execFileSync } from 'child_process';

// Disposable user used by cypress/e2e/feedback.cy.ts (issue #5). Signup is
// disabled on the live 8083 stack, so the spec self-provisions this user via
// the backend's own user-creation path inside the owui container. Idempotent
// create + delete keep repeated `cypress run` reproducibly green. Real users
// are never touched.
const FEEDBACK_E2E_EMAIL = 'e2e-feedback-cypress-disposable@example.com';
const FEEDBACK_E2E_PASSWORD = 'Cypr3ssE2E-feedback!';
const FEEDBACK_E2E_NAME = 'E2E Feedback Cypress DISPOSABLE';
const FEEDBACK_E2E_CONTAINER = process.env.E2E_FEEDBACK_CONTAINER || 'owui';

// Run a python snippet inside the owui container via stdin (no shell quoting).
const runInOwui = (py: string): string =>
	execFileSync('docker', ['exec', '-i', FEEDBACK_E2E_CONTAINER, 'python3', '-'], {
		input: py,
		encoding: 'utf8'
	});

export default defineConfig({
	e2e: {
		baseUrl: 'http://localhost:8080',
		setupNodeEvents(on) {
			on('task', {
				// Idempotent: create the disposable user if missing; return its id.
				provisionFeedbackUser() {
					const py = [
						'import asyncio',
						'from open_webui.models.auths import Auths',
						'from open_webui.utils.auth import get_password_hash',
						'from open_webui.internal.db import get_db',
						'from open_webui.models.users import User',
						`E = ${JSON.stringify(FEEDBACK_E2E_EMAIL)}`,
						'with get_db() as db:',
						'    ex = db.query(User).filter(User.email == E).first()',
						'    uid = ex.id if ex else None',
						'if uid is None:',
						`    u = asyncio.run(Auths.insert_new_auth(E, get_password_hash(${JSON.stringify(
							FEEDBACK_E2E_PASSWORD
						)}), ${JSON.stringify(FEEDBACK_E2E_NAME)}, role='user'))`,
						'    uid = u.id if u else None',
						'print("USER_ID=" + str(uid))'
					].join('\n');
					return runInOwui(py).trim();
				},
				// Idempotent: delete the disposable user row if present.
				deleteFeedbackUser() {
					const py = [
						'import asyncio',
						'from open_webui.models.users import Users, User',
						'from open_webui.internal.db import get_db',
						`E = ${JSON.stringify(FEEDBACK_E2E_EMAIL)}`,
						'with get_db() as db:',
						'    ex = db.query(User).filter(User.email == E).first()',
						'    uid = ex.id if ex else None',
						'res = asyncio.run(Users.delete_user_by_id(uid)) if uid else None',
						'print("DELETED=" + str(res))'
					].join('\n');
					return runInOwui(py).trim();
				}
			});
		}
	},
	video: true
});
