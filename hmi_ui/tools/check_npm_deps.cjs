// Install frontend packages only when package-lock.json has changed.
//
// Same fingerprint: npm does not run. node_modules missing: first install (npm ci).
// Lockfile changed: `npm install` updates in place. `npm ci` is not used for that,
// because it deletes node_modules first. Source files are not part of the fingerprint.
//
//   node tools/check_npm_deps.cjs
//
// Exit code 0 when ready, otherwise npm's exit code.
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = process.env.OPSLENS_HMI_ROOT || path.resolve(__dirname, '..');
const npm = process.env.OPSLENS_NPM || 'npm';
const lockFile = path.join(root, 'package-lock.json');
const modules = path.join(root, 'node_modules');
const stampFile = path.join(modules, '.opslens-lock.sha256');

function fingerprint() {
  // CRLF and LF checkouts of the same file must match.
  return crypto.createHash('sha256').update(fs.readFileSync(lockFile, 'utf8').replace(/\r\n/g, '\n')).digest('hex');
}

function run(args) {
  const result = spawnSync(npm, args, { cwd: root, stdio: 'inherit', shell: process.platform === 'win32' });
  return result.status ?? 1;
}

if (!fs.existsSync(lockFile)) {
  console.error('[ERROR] package-lock.json is missing. It is needed to detect frontend dependency changes.');
  process.exit(1);
}

const firstInstall = !fs.existsSync(modules);
const saved = !firstInstall && fs.existsSync(stampFile) ? fs.readFileSync(stampFile, 'utf8').trim() : null;
if (saved && saved === fingerprint()) {
  console.log('[OK] Frontend dependencies unchanged (package-lock.json). Skipping npm.');
  process.exit(0);
}

console.log(firstInstall ? 'Installing frontend packages for the first time...' : 'package-lock.json changed or is not recorded yet. Updating node_modules...');
const code = run(firstInstall ? ['ci', '--no-audit', '--no-fund'] : ['install', '--no-audit', '--no-fund']);
if (code !== 0) {
  console.error(`\n[ERROR] npm failed with exit code ${code}. The output above shows why.`);
  console.error('        Startup stopped. The fingerprint was not updated, so the next start tries again.');
  process.exit(code);
}
// Read after installing: npm may rewrite the lockfile, and the saved value must match what is on disk.
fs.writeFileSync(stampFile, `${fingerprint()}\n`);
console.log('[OK] Frontend dependencies updated.');
