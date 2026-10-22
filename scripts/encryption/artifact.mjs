import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { join, relative } from 'node:path';
const root = new URL('../../', import.meta.url).pathname;
const source = 'crypto/encryption';
const artifact = 'src/lib/encryption/vendor';
const hash = (data) => createHash('sha256').update(data).digest('hex');
async function files(dir) {
	const entries = await readdir(join(root, dir), { withFileTypes: true });
	const paths = await Promise.all(entries.map(async (entry) => {
		if (entry.name === 'target' || entry.name.startsWith('.')) return [];
		const path = join(dir, entry.name);
		return entry.isDirectory() ? files(path) : [path];
	}));
	return paths.flat().sort();
}
const inputs = [
	...await files(source),
	'scripts/encryption/build.py',
	'scripts/encryption/artifact.mjs'
].sort();
const fingerprints = await Promise.all(inputs.map(async (path) =>
	`${path}\0${hash(await readFile(join(root, path)))}`));
const sourceFingerprint = hash(fingerprints.join('\n'));
const manifestPath = join(root, artifact, 'manifest.json');
if (process.argv.includes('--write')) {
	const metadata = JSON.parse(
		await readFile(process.argv[process.argv.indexOf('--metadata') + 1], 'utf8')
	);
	const paths = (await files(artifact)).filter((p) => !p.endsWith('/manifest.json'));
	const checksums = Object.fromEntries(await Promise.all(paths.map(async (p) =>
		[relative(artifact, p), hash(await readFile(join(root, p)))])));
	await writeFile(
		manifestPath,
		JSON.stringify(
			{
				format: 1,
				sourceFingerprint,
				sourceRevision: process.env.CRYPTO_SOURCE_REVISION,
				toolchain: '1.95.0',
				wasmBindgen: '0.2.126',
				dependencies: metadata.packages.map(({ name, version, license }) => ({
					name,
					version,
					license
				})),
				checksums
			},
			null,
			2
		) + '\n'
	);
} else {
	const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
	if (manifest.sourceFingerprint !== sourceFingerprint)
		throw Error('Encryption source changed: rebuild and review the artifact.');
	const actual = (await files(artifact))
		.filter((p) => !p.endsWith('/manifest.json'))
		.map((p) => relative(artifact, p))
		.sort();
	if (JSON.stringify(actual) !== JSON.stringify(Object.keys(manifest.checksums).sort()))
		throw Error('Unexpected crypto artifact files');
	for (const [p, digest] of Object.entries(manifest.checksums)) {
		if (hash(await readFile(join(root, artifact, p))) !== digest)
			throw Error(`Crypto artifact checksum mismatch: ${p}`);
	}
	console.log('Encryption artifact source and checksums verified');
}
