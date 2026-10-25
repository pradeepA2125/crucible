import { generateKeyPairSync, createHash } from 'node:crypto';
import { mkdir, writeFile, realpath, lstat, rm } from 'node:fs/promises';
import { resolve, dirname, basename, join, isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';

export function keyFingerprint(key) {
  return createHash('sha256').update(key.export({ type: 'spki', format: 'der' })).digest('hex');
}

// An explicit one-time operation. Builds and CI never create or rotate release keys.
export async function createReleaseKey(out) {
  if (!isAbsolute(out)) throw Error('Choose an absolute new directory outside every checkout.');
  const parent = await realpath(dirname(resolve(out)));
  for (let p = parent; ; p = dirname(p)) {
    try { await lstat(join(p, '.git')); throw Error('Release private keys must stay outside source checkouts.'); }
    catch (e) { if (e.code !== 'ENOENT') throw e; }
    if (dirname(p) === p) break;
  }
  const directory = join(parent, basename(resolve(out)));
  await mkdir(directory, { mode: 0o700 }); // EEXIST must never rotate an existing identity.
  try {
    const { privateKey, publicKey } = generateKeyPairSync('ed25519');
    await writeFile(join(directory, 'private.pem'), privateKey.export({ type: 'pkcs8', format: 'pem' }), { flag: 'wx', mode: 0o600 });
    await writeFile(join(directory, 'public.pem'), publicKey.export({ type: 'spki', format: 'pem' }), { flag: 'wx', mode: 0o644 });
    const fingerprint = keyFingerprint(publicKey);
    await writeFile(join(directory, 'identity.json'), JSON.stringify({ algorithm: 'Ed25519', publicKeySha256: fingerprint }, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    return { directory, publicKey: join(directory, 'public.pem'), privateKey: join(directory, 'private.pem'), publicKeySha256: fingerprint };
  } catch (e) { await rm(directory, { recursive: true, force: true }); throw e; }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length !== 4 || process.argv[2] !== '--out') throw Error('Use --out /absolute/new/private-directory.');
  console.log(JSON.stringify(await createReleaseKey(process.argv[3])));
}
