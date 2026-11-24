import test from 'node:test';
import assert from 'node:assert/strict';

test('clock times use 00-23 hours and never print noon as 00', async () => {
  const { hhmm, hhmmss, dateTime, shortDateTime } = await import('./format.js');
  const noon = new Date(2026, 10, 10, 12, 27, 15).toISOString();
  const midnight = new Date(2026, 10, 10, 0, 5, 9).toISOString();
  assert.equal(hhmmss(noon), '12:27:15');
  assert.equal(hhmm(midnight), '00:05');
  assert.equal(hhmmss(new Date(2026, 10, 10, 13, 0, 0).toISOString()), '13:00:00');
  assert.equal(dateTime(noon), '10/11/2026, 12:27:15');
  assert.equal(shortDateTime(noon), 'Nov 10, 12:27');
  assert.equal(hhmmss('not a time'), '—');
});
