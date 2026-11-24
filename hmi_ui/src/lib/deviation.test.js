import test from 'node:test';
import assert from 'node:assert/strict';
import { deviation } from './deviation.js';

test('below normal: how far under the minimum, and by how much', () => {
  const d = deviation({ now: 3.5, normalLow: 7, normalHigh: 12, unit: 'mW/cm²' });
  assert.equal(d.kind, 'low');
  assert.equal(d.short, '−3.5 under normal');
  assert.equal(d.text, '3.5 mW/cm² below normal (min 7 mW/cm²), 50% under');
});

test('past an alarm limit beats outside normal', () => {
  const d = deviation({ now: 3.85, normalLow: 1, normalHigh: 2.4, unit: 'm',
    limits: [{ value: 3.0, direction: 'high', label: 'high alarm' }, { value: 3.6, direction: 'high', label: 'high-high alarm' }] });
  assert.equal(d.kind, 'limit');
  assert.equal(d.short, '+0.25 past limit');
  assert.match(d.text, /^0\.25 m above the high-high alarm \(3\.6 m\), 7% past it$/);
});

test('inside the normal range shows the margin to the nearest limit', () => {
  const d = deviation({ now: 1.6, normalLow: 1, normalHigh: 2.4, unit: 'm', limits: [{ value: 3.0, direction: 'high', label: 'high alarm' }] });
  assert.equal(d.kind, 'normal');
  assert.equal(d.short, 'in range');
  assert.equal(d.text, 'Within normal (1–2.4 m); 1.4 m from the high alarm');
});

test('above normal without a limit', () => {
  const d = deviation({ now: 52, normalLow: 10, normalHigh: 30, unit: 'cm' });
  assert.equal(d.short, '+22 over normal');
  assert.match(d.text, /73% over$/);
});

test('nothing to compare with gives nothing', () => {
  assert.equal(deviation({ now: 5 }), null);
  assert.equal(deviation({ now: 'n/a', normalLow: 1, normalHigh: 2 }), null);
});

test('a stopped motor reads as stopped, not as a 100 % deviation', () => {
  const d = deviation({ now: 0, normalLow: 28, normalHigh: 34, unit: 'A', limits: [{ value: 42, direction: 'high', label: 'overload' }] });
  assert.equal(d.kind, 'stopped');
  assert.equal(d.short, 'stopped');
  // A crossed low limit still wins: zero flow past a low-flow alarm is an alarm.
  assert.equal(deviation({ now: 0, normalLow: 380, normalHigh: 450, limits: [{ value: 300, direction: 'low', label: 'low alarm' }] }).kind, 'limit');
});
