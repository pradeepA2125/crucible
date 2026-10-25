import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, rm, stat, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash, createPublicKey, verify } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { createReleaseKey, keyFingerprint } from './desktop-release-key.mjs';
import { signMetadata, validateMetadata } from './desktop-update-manifest.mjs';
import { verifyReleaseArchive } from './desktop-release-bundle.mjs';

async function fixture(fn) { const dir = await mkdtemp(join(tmpdir(), 'crucible-publisher-')); try { await fn(dir); } finally { await rm(dir, { recursive: true, force: true }); } }
async function signingFixture(dir) {
  const identity = await createReleaseKey(join(dir, 'identity'));
  const archivePath = join(dir, 'crucible-0.9.0-darwin-arm64.zip'); await writeFile(archivePath, 'original archive bytes');
  const metadata = { schemaVersion: 1, tag: 'v0.9.0', version: '0.9.0', target: 'darwin-arm64', bundleId: 'io.crucible.desktop', asset: 'crucible-0.9.0-darwin-arm64.zip', size: 22, sha256: createHash('sha256').update(await readFile(archivePath)).digest('hex') };
  const manifestPath = join(dir, 'metadata.json'); await writeFile(manifestPath, JSON.stringify(metadata));
  return { identity, metadata, archivePath, manifestPath, keyPath: identity.privateKey, publicKeyPath: identity.publicKey, out: join(dir, 'signed.json') };
}

test('release key is owner-only, separate from source and never overwritten', async () => fixture(async d => {
  const i = await createReleaseKey(join(d, 'key'));
  assert.equal((await stat(i.directory)).mode & 0o777, 0o700);
  assert.equal((await stat(i.privateKey)).mode & 0o777, 0o600);
  assert.equal(keyFingerprint(createPublicKey(await readFile(i.publicKey))), i.publicKeySha256);
  const original = await readFile(i.privateKey);
  await assert.rejects(createReleaseKey(i.directory), /EEXIST/);
  assert.deepEqual(await readFile(i.privateKey), original);
  await mkdir(join(d, 'repo')); await writeFile(join(d, 'repo/.git'), 'gitdir: elsewhere');
  await assert.rejects(createReleaseKey(join(d, 'repo/key')), /outside source/);
}));

test('signed exact bytes verify against the matching public key and cannot overwrite output', async () => fixture(async d => {
  const f = await signingFixture(d); await signMetadata(f);
  const wire = JSON.parse(await readFile(f.out));
  assert.equal(verify(null, Buffer.from(wire.payload, 'base64'), createPublicKey(await readFile(f.publicKeyPath)), Buffer.from(wire.signature, 'base64')), true);
  assert.deepEqual(Buffer.from(wire.payload, 'base64'), await readFile(f.manifestPath));
  await assert.rejects(signMetadata(f), /EEXIST/);
}));

test('different release identity, changed same-size archive and broad private-key permissions refuse signing', async () => fixture(async d => {
  const f = await signingFixture(d), other = await createReleaseKey(join(d, 'other'));
  await assert.rejects(signMetadata({ ...f, publicKeyPath: other.publicKey }), /does not match/);
  await writeFile(f.archivePath, 'modified archive bytes');
  await assert.rejects(signMetadata(f), /checksum/);
  await writeFile(f.archivePath, 'original archive bytes'); await chmod(f.keyPath, 0o644);
  if (process.platform !== 'win32') await assert.rejects(signMetadata(f), /owner-only/);
}));

test('metadata rejects inconsistent version, platform, filename, fields and unsafe size', async () => fixture(async d => {
  const { metadata } = await signingFixture(d);
  for (const delta of [{ tag: 'v0.9.1' }, { target: 'linux-x64' }, { version: '01.9.0' }, { asset: '../escape.zip' }, { size: 2 ** 40 }, { unknown: true }]) assert.throws(() => validateMetadata({ ...metadata, ...delta }));
}));

test('archive must contain exactly the sealed app tree, modes and bytes', async () => fixture(async d => {
  const app = join(d, 'Crucible.app'); await mkdir(app); await writeFile(join(app, 'payload'), 'trusted');
  const zip = join(d, 'app.zip');
  execFileSync('python3', ['-c', `import zipfile,sys,pathlib\np=pathlib.Path(sys.argv[1])\nwith zipfile.ZipFile(sys.argv[2],'w') as z:\n z.write(p,p.name+'/')\n for f in p.rglob('*'): z.write(f,p.name+'/'+str(f.relative_to(p)))`, app, zip]);
  await verifyReleaseArchive(zip, app);
  await writeFile(join(app, 'payload'), 'changed');
  await assert.rejects(verifyReleaseArchive(zip, app), /bytes differ/);
  await writeFile(join(app, 'payload'), 'trusted');
  execFileSync('python3', ['-c', `import zipfile,sys\nwith zipfile.ZipFile(sys.argv[1],'a') as z:z.writestr('Crucible.app/unreviewed','extra')`, zip]);
  await assert.rejects(verifyReleaseArchive(zip, app), /path\/mode/);
}));
