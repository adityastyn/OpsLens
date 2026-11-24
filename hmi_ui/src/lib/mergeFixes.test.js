import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeFixes, fixWhen } from './mergeFixes.js';

const NOTE = 'No SOP for this alarm; fixed with the maintenance engineer and verified in the field.';

test('the same fix on two dates is one entry', () => {
  const fixes = mergeFixes([
    { resolution_notes: NOTE, when_text: '2026-11-07 14:25 UTC' },
    { resolution_notes: ` ${NOTE.replace('; ', ';  ').toUpperCase()} `, when_text: '2026-11-04 17:17 UTC' },
  ]);
  assert.equal(fixes.length, 1);
  assert.equal(fixWhen(fixes[0]), 'fixed this way 2 times, last 2026-11-07 14:25 UTC');
});

test('dates written into the note do not make it a different fix', () => {
  const fixes = mergeFixes([
    { resolution_notes: 'Replaced ballast on 07/11 at 14:20.', when_text: 'a' },
    { resolution_notes: 'Replaced ballast on 04/11 at 17:10.', when_text: 'b' },
  ]);
  assert.equal(fixes.length, 1);
});

test('different fixes stay separate; empty notes are skipped', () => {
  const fixes = mergeFixes([
    { resolution_notes: 'Replaced the lamp bank 2 ballast.', when_text: 'a' },
    { resolution_notes: 'Cleaned the quartz sleeves.', when_text: 'b' },
    { resolution_notes: '', when_text: 'c' },
  ]);
  assert.equal(fixes.length, 2);
});
