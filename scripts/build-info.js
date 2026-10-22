import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export async function resolveBuildInfo({
	env = process.env,
	cwd = fileURLToPath(new URL('../', import.meta.url))
} = {}) {
	const git = async (...args) => {
		try {
			const { stdout } = await promisify(execFile)('git', args, {
				cwd,
				encoding: 'utf8',
				stdio: ['ignore', 'pipe', 'ignore']
			});
            return stdout.trim();
		} catch {
			return '';
		}
	};

	const suppliedHash = env.APP_BUILD_HASH ?? '';
	const hash = /^(?:[a-f\d]{7,40}|[a-f\d]{64})$/i.test(suppliedHash)
		? suppliedHash
		: await git('rev-parse', 'HEAD');
	let channel = env.APP_BUILD_CHANNEL;
	if (!['main', 'dev', 'unknown'].includes(channel)) {
		const branch = await git('branch', '--show-current');
		channel = branch === 'main' || branch === 'dev' ? branch : 'unknown';
		if (!branch && hash) {
			try {
				const { version } = JSON.parse(await readFile(join(cwd, 'package.json'), 'utf8'));
				if ((await git('tag', '--points-at', 'HEAD')).split('\n').includes(`v${version}`)) {
					channel = 'main';
				}
			} catch {
				// Source archives may have neither Git metadata nor a package version.
			}
		}
	}

	return { channel, hash };
}
