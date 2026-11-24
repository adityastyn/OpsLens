const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const script = path.join(__dirname, 'check_npm_deps.cjs');

function project({ npmExit = 0 } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'opslens-npm-'));
  const log = path.join(root, 'npm.log');
  const npm = path.join(root, 'fake-npm');
  fs.writeFileSync(npm, `#!/bin/sh\necho "$1" >> "${log}"\nmkdir -p node_modules\nexit ${npmExit}\n`, { mode: 0o755 });
  fs.writeFileSync(path.join(root, 'package-lock.json'), '{"v":1}\n');
  const run = () => spawnSync(process.execPath, [script], { encoding: 'utf8', env: { ...process.env, OPSLENS_HMI_ROOT: root, OPSLENS_NPM: npm } });
  const calls = () => (fs.existsSync(log) ? fs.readFileSync(log, 'utf8').trim().split('\n') : []);
  return { root, run, calls };
}

test('first launch runs npm ci, then a normal launch runs nothing', { skip: process.platform === 'win32' }, () => {
  const p = project();
  assert.equal(p.run().status, 0);
  assert.deepEqual(p.calls(), ['ci']);
  assert.equal(p.run().status, 0);
  assert.deepEqual(p.calls(), ['ci']);
});

test('a changed lockfile updates in place and never deletes node_modules', { skip: process.platform === 'win32' }, () => {
  const p = project();
  p.run();
  const marker = path.join(p.root, 'node_modules', 'keep.txt');
  fs.writeFileSync(marker, 'x');
  fs.writeFileSync(path.join(p.root, 'package-lock.json'), '{"v":2}\n');
  assert.equal(p.run().status, 0);
  assert.deepEqual(p.calls(), ['ci', 'install']);
  assert.ok(fs.existsSync(marker));
  p.run();
  assert.deepEqual(p.calls(), ['ci', 'install']);
});

test('source changes and line endings do not trigger npm', { skip: process.platform === 'win32' }, () => {
  const p = project();
  p.run();
  fs.writeFileSync(path.join(p.root, 'App.jsx'), 'changed');
  fs.writeFileSync(path.join(p.root, 'package-lock.json'), '{"v":1}\r\n');
  p.run();
  assert.deepEqual(p.calls(), ['ci']);
});

test('a failed install stops startup and keeps the old fingerprint', { skip: process.platform === 'win32' }, () => {
  const p = project();
  p.run();
  const stamp = path.join(p.root, 'node_modules', '.opslens-lock.sha256');
  const before = fs.readFileSync(stamp, 'utf8');
  fs.writeFileSync(path.join(p.root, 'package-lock.json'), '{"v":3}\n');
  const log = path.join(p.root, 'npm.log');
  fs.writeFileSync(path.join(p.root, 'fake-npm'), `#!/bin/sh\necho "$1" >> "${log}"\nexit 7\n`, { mode: 0o755 });
  const result = p.run();
  assert.equal(result.status, 7);
  assert.match(result.stderr, /Startup stopped/);
  assert.equal(fs.readFileSync(stamp, 'utf8'), before);
});
