// Explicit offline publisher tool. Never generates keys, uploads or overwrites files.
import { readFile, writeFile, lstat } from 'node:fs/promises';
import { createPrivateKey, createPublicKey, sign, verify, createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

export function validateMetadata(m) {
  const keys = ['schemaVersion', 'tag', 'version', 'target', 'bundleId', 'asset', 'size', 'sha256'];
  if (!m || Object.keys(m).length !== keys.length || keys.some(k => !(k in m)) || m.schemaVersion !== 1 ||
      !/^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/.test(m.version) ||
      m.tag !== `v${m.version}` || !['darwin-arm64', 'darwin-x64'].includes(m.target) ||
      !['io.crucible.desktop', 'io.crucible.desktop.preview'].includes(m.bundleId) ||
      m.asset !== `crucible-${m.version}-${m.target}${m.bundleId.endsWith('.preview') ? '-preview' : ''}.zip` ||
      !Number.isSafeInteger(m.size) || m.size < 1 || m.size > 1024 ** 3 || !/^[a-f0-9]{64}$/.test(m.sha256)) {
    throw Error('Invalid exact desktop update metadata.');
  }
  return m;
}

export async function signMetadata({ keyPath, manifestPath, archivePath, publicKeyPath, out }) {
  const bytes = await readFile(manifestPath);
  if (bytes.length > 32768) throw Error('Manifest is too large.');
  const metadata = validateMetadata(JSON.parse(bytes.toString('utf8')));
  const stat = await lstat(archivePath);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size !== metadata.size) throw Error('Archive size/type mismatch.');
  const archive = await readFile(archivePath);
  if (createHash('sha256').update(archive).digest('hex') !== metadata.sha256) throw Error('Archive checksum mismatch.');
  const privateStat = await lstat(keyPath);
  if (!privateStat.isFile() || privateStat.isSymbolicLink() || (process.platform !== 'win32' && (privateStat.mode & 0o077))) throw Error('Private key must be a regular owner-only file.');
  const key = createPrivateKey(await readFile(keyPath));
  const publicKey = createPublicKey(await readFile(publicKeyPath));
  if (key.asymmetricKeyType !== 'ed25519' || publicKey.asymmetricKeyType !== 'ed25519' ||
      !createPublicKey(key).export({ type: 'spki', format: 'der' }).equals(publicKey.export({ type: 'spki', format: 'der' }))) throw Error('Release private key does not match the bundled public identity.');
  const signature = sign(null, bytes, key);
  if (!verify(null, bytes, publicKey, signature)) throw Error('Release self-verification failed.');
  await writeFile(out, JSON.stringify({ payload: bytes.toString('base64'), signature: signature.toString('base64') }, null, 2) + '\n', { flag: 'wx', mode: 0o644 });
  return metadata;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const o = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const arg = process.argv[i];
    if (!['--key', '--public-key', '--manifest', '--archive', '--out'].includes(arg) || !process.argv[i + 1] || o[arg]) throw Error('Use --key private.pem --public-key public.pem --manifest metadata.json --archive final.zip --out signed.json.');
    o[arg] = process.argv[i + 1];
  }
  if (Object.keys(o).length !== 5) throw Error('All five release signing arguments are required.');
  await signMetadata({ keyPath: o['--key'], publicKeyPath: o['--public-key'], manifestPath: o['--manifest'], archivePath: o['--archive'], out: o['--out'] });
}
