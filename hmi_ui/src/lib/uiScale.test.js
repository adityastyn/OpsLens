import test from 'node:test';
import assert from 'node:assert/strict';
import { scaleFor } from './uiScale.js';

test('1280 × 800 and smaller panels are not scaled', () => {
  assert.equal(scaleFor(1280, 800), 1);
  assert.equal(scaleFor(1024, 768), 1);
  assert.equal(scaleFor(1300, 820), 1);
});

test('a 1920 × 1200 panel is scaled 1.5', () => {
  assert.equal(scaleFor(1920, 1200), 1.5);
});

test('the smaller side decides, in 0.05 steps, at most 2', () => {
  assert.equal(scaleFor(1920, 1080), 1.35);
  assert.equal(scaleFor(3840, 2400), 2);
});

test('?scale overrides', () => {
  assert.equal(scaleFor(1920, 1200, '1'), 1);
  assert.equal(scaleFor(1920, 1200, '1.25'), 1.25);
  assert.equal(scaleFor(1920, 1200, 'x'), 1.5);
});
