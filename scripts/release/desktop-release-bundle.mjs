import { readFile, writeFile, mkdir, cp, rm, lstat, readdir, readlink } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { join, basename, resolve } from 'node:path';
import { createHash, createPublicKey } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileManifest, validateReleaseManifest } from '../../apps/desktop/package-utils.mjs';
import { signMetadata, validateMetadata } from './desktop-update-manifest.mjs';
import { keyFingerprint } from './desktop-release-key.mjs';

const requireDesktop = createRequire(new URL('../../apps/desktop/package.json', import.meta.url));
const yauzl = requireDesktop('yauzl');
export async function verifyReleaseArchive(archive, app) {
  const expected = new Map();
  async function walk(path, relative) {
    const stat = await lstat(path);
    if (stat.isDirectory()) {
      expected.set(relative + '/', { kind: 'directory', mode: stat.mode & 0o777 });
      for (const name of await readdir(path)) await walk(join(path, name), relative + '/' + name);
    } else if (stat.isSymbolicLink()) expected.set(relative, { kind: 'link', mode: stat.mode & 0o777, bytes: Buffer.from(await readlink(path)) });
    else if (stat.isFile()) {
      const hash = createHash('sha256');
      for await (const chunk of createReadStream(path)) hash.update(chunk);
      expected.set(relative, { kind: 'file', mode: stat.mode & 0o777, size: stat.size, sha256: hash.digest('hex') });
    } else throw Error('Unexpected app special file.');
  }
  await walk(app, basename(app));
  const zip = await new Promise((yes, no) => yauzl.open(archive, { lazyEntries: true, decodeStrings: true, validateEntrySizes: true, strictFileNames: true }, (e, z) => e ? no(e) : yes(z)));
  await new Promise((yes, no) => {
    let count = 0, expanded = 0;
    const fail = e => { zip.close(); no(e); };
    zip.on('error', fail);
    zip.on('end', () => expected.size ? fail(Error('Archive is missing app files.')) : yes());
    zip.on('entry', entry => { (async () => {
      const item = expected.get(entry.fileName), mode = entry.externalFileAttributes >>> 16;
      if (++count > 100000 || !item || (mode & 0o777) !== item.mode || (mode & 0o7000)) throw Error('Archive path/mode does not match the sealed app.');
      expected.delete(entry.fileName);
      const kind = mode & 0o170000;
      if (item.kind === 'directory') {
        if (kind !== 0o040000 || entry.uncompressedSize !== 0) throw Error('Archive directory mismatch.');
      } else {
        if (kind !== (item.kind === 'link' ? 0o120000 : 0o100000) || entry.uncompressedSize !== (item.bytes?.length ?? item.size)) throw Error('Archive type/size mismatch.');
        const stream = await new Promise((ok, bad) => zip.openReadStream(entry, (e, s) => e ? bad(e) : ok(s)));
        const hash = createHash('sha256'); let bytes = 0;
        for await (const chunk of stream) {
          bytes += chunk.length; expanded += chunk.length;
          if (bytes > entry.uncompressedSize || expanded > 2 * 1024 ** 3) { stream.destroy(); throw Error('Archive expands beyond bounds.'); }
          hash.update(chunk);
        }
        if (bytes !== entry.uncompressedSize || hash.digest('hex') !== (item.sha256 ?? createHash('sha256').update(item.bytes).digest('hex'))) throw Error('Archive bytes differ from the sealed app.');
      }
      zip.readEntry();
    })().catch(fail); });
    zip.readEntry();
  });
}

export async function prepareRelease({ packageDir, publicKeyPath, keyPath, out }) {
  const evidence = JSON.parse(await readFile(join(packageDir, 'package-evidence.json'), 'utf8'));
  if (evidence.preview !== false || evidence.identity !== 'io.crucible.desktop' || evidence.platform !== 'darwin-arm64' ||
      evidence.signed !== false || evidence.codeSeal?.kind !== 'ad-hoc' || evidence.codeSeal?.verified !== true || evidence.codeSeal?.notarized !== false) throw Error('Use the final freely sealed Apple Silicon candidate, not a preview fixture.');
  const app = join(packageDir, 'Crucible.app');
  if (process.platform !== 'darwin') throw Error('Native Mac verification is required before preparing a Mac release.');
  await promisify(execFile)('/usr/bin/codesign', ['--verify', '--deep', '--strict', app]);
  const payload = join(app, 'Contents/Resources/app/dist');
  const application = JSON.parse(await readFile(join(app, 'Contents/Resources/app/package.json'), 'utf8'));
  if (application.version !== evidence.version || application.name !== 'crucible-desktop') throw Error('Packaged app identity differs from evidence.');
  const files = await fileManifest(payload);
  if (JSON.stringify(files) !== JSON.stringify(evidence.files)) throw Error('Candidate payload changed after packaging.');
  const bundledPublic = createPublicKey(await readFile(join(payload, 'resources/desktop-update-key.pem')));
  const publicKey = createPublicKey(await readFile(publicKeyPath));
  if (publicKey.asymmetricKeyType !== 'ed25519' || keyFingerprint(publicKey) !== keyFingerprint(bundledPublic)) throw Error('Candidate update identity differs from the release public key.');
  const manifest = validateReleaseManifest(JSON.parse(await readFile(join(payload, 'resources/runtime-manifest.json'), 'utf8')), evidence.platform);
  if (manifest.releaseTag !== `v${evidence.version}` || JSON.stringify(manifest).includes('local-test')) throw Error('Candidate runtime must use the actual versioned release, without local test URLs.');
  const asset = `crucible-${evidence.version}-${evidence.platform}.zip`;
  const archivePath = join(packageDir, asset), stat = await lstat(archivePath);
  if (!stat.isFile() || stat.isSymbolicLink()) throw Error('Final archive must be a regular file.');
  const sha256 = createHash('sha256').update(await readFile(archivePath)).digest('hex');
  const metadata = validateMetadata({ schemaVersion: 1, tag: `v${evidence.version}`, version: evidence.version, target: evidence.platform, bundleId: evidence.identity, asset, size: stat.size, sha256 });
  const embedded = JSON.parse(await readFile(join(payload, 'resources/runtime-assets.json'), 'utf8'));
  await mkdir(out, { recursive: false });
  try {
    await cp(archivePath, join(out, asset));
    await verifyReleaseArchive(join(out, asset), app);
    await writeFile(join(out, asset + '.sha256'), `${sha256}  ${asset}\n`, { flag: 'wx' });
    await writeFile(join(out, 'desktop-update-metadata.json'), JSON.stringify(metadata, null, 2) + '\n', { flag: 'wx' });
    await writeFile(join(out, 'desktop-update-public.pem'), publicKey.export({ type: 'spki', format: 'pem' }), { flag: 'wx' });
    await writeFile(join(out, 'desktop-runtime-manifest.json'), JSON.stringify(manifest, null, 2) + '\n', { flag: 'wx' });
    for (const [url, entry] of Object.entries(embedded)) {
      if (!/^[a-zA-Z0-9_.+-]+$/.test(entry.file)) throw Error('Unsafe runtime filename.');
      const source = join(payload, 'resources/runtime-assets', entry.file);
      if (createHash('sha256').update(await readFile(source)).digest('hex') !== entry.sha256) throw Error('Embedded runtime changed.');
      if (url.startsWith(`https://github.com/pradeepA2125/crucible/releases/download/${metadata.tag}/`)) await cp(source, join(out, entry.file), { errorOnExist: true, force: false });
    }
    const signed = `crucible-desktop-update-${evidence.platform}.json`;
    if (keyPath) await signMetadata({ keyPath, publicKeyPath, manifestPath: join(out, 'desktop-update-metadata.json'), archivePath: join(out, asset), out: join(out, signed) });
    await writeFile(join(out, 'desktop-release-evidence.json'), JSON.stringify({ ...evidence, archive: asset, archiveSha256: sha256, publicKeySha256: keyFingerprint(publicKey), metadataSigned: !!keyPath, releaseEligible: false }, null, 2) + '\n', { flag: 'wx' });
    await writeFile(join(out, 'INSTALL.txt'), `Crucible ${evidence.version} — Apple Silicon test build\n\nDownload ${asset} and extract it. Move Crucible.app to a user-writable Applications folder before opening; do not run from the ZIP or Downloads extraction.\nThis community build has no Apple Developer ID/notarization. Only if you trust this project's download, use macOS Privacy & Security → Open Anyway for Crucible. Keep Gatekeeper enabled.\n\nThe bundled runtime contains the current backend/indexer and pinned vendor tools. Setup may download Python/dependencies from their normal registries. Node is optional for language-server features.\n\nDraft storage and notifications are off by default. Never use sensitive projects for the first test. Settings → Updates checks published stable releases; GitHub prereleases are deliberately ignored.\n\nKeep the prior app for manual recovery. App data lives outside the app bundle; replacing the app does not require removing its profile.\nPublic update key SHA256: ${keyFingerprint(publicKey)}\n\nThis is a second-laptop test candidate, not a claim that all release QA has passed.\n`, { flag: 'wx' });
    return { out: resolve(out), archive: asset, sha256, metadataSigned: !!keyPath, publicKeySha256: keyFingerprint(publicKey), releaseEligible: false };
  } catch (e) { await rm(out, { recursive: true, force: true }); throw e; }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const o = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const a = process.argv[i];
    if (!['--package', '--public-key', '--key', '--out'].includes(a) || !process.argv[i + 1] || o[a]) throw Error('Use --package package-dir --public-key public.pem [--key private.pem] --out new-dir.');
    o[a] = process.argv[i + 1];
  }
  if (!o['--package'] || !o['--public-key'] || !o['--out']) throw Error('Package, public key and new output directory are required.');
  console.log(JSON.stringify(await prepareRelease({ packageDir: o['--package'], publicKeyPath: o['--public-key'], keyPath: o['--key'], out: o['--out'] })));
}
