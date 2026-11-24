import test from 'node:test';
import assert from 'node:assert/strict';
import { storyLine, storySummary, withRecordedTrips } from './eventStory.js';

const obs = (t, event, change) => ({ kind: 'observation', timestamp: `2026-11-09T05:${t}Z`, event, change });

// The lift-station timeline as the backend records it: an alarm arrives as an observation with no change.
const LIFT = [
  obs('23:34', 'Inflow to wet well', '446.12 → 460.962 m³/h'),
  obs('23:42', 'P-102 discharge pressure', '2.906 → 2.781 bar'),
  obs('23:42', 'P-102 flow', '389.093 → 368.467 m³/h'),
  obs('23:48', 'P-102 motor current', '28.268 → 26.99 A'),
  obs('23:48', 'P-102 flow low', null),
  obs('24:06', 'Wet well level', '2.378 → 2.42 m'),
];

test('the story says what moved first, what followed and which alarm came on', () => {
  const text = storySummary(LIFT);
  assert.match(text, /^Inflow to wet well rose first \(\d{2}:\d{2}:\d{2}\)\./);
  assert.match(text, /Within 14 s, P-102 discharge pressure, P-102 flow and P-102 motor current fell\./);
  assert.match(text, /Then the P-102 flow low alarm came on\.$/);
});

test('an alarm recorded as an observation is marked as an alarm', () => {
  assert.equal(storyLine(LIFT[4]).alarm, true);
  assert.equal(storyLine(LIFT[0]).verb, 'rose');
});

test('mixed directions name each change', () => {
  const text = storySummary([obs('00:00', 'WAS (wasting) rate', '180 → 165 m³/d'), obs('00:10', 'Sludge age (SRT)', '9.7 → 10.4 days')]);
  assert.match(text, /WAS \(wasting\) rate fell first/);
  assert.match(text, /Within 10 s, Sludge age \(SRT\) rose\./);
});

test('a fault that comes on first starts the story', () => {
  const text = storySummary([
    obs('43:57', 'Lamp bank 2 ballast fault', null),
    obs('44:09', 'UV intensity', '8.206 → 7.659 mW/cm²'),
    obs('44:23', 'UV dose', '35.346 → 33.649 mJ/cm²'),
    obs('44:33', 'UV dose low', null),
  ]);
  assert.match(text, /^Lamp bank 2 ballast fault came on first \(\d{2}:\d{2}:\d{2}\)\./);
  assert.match(text, /Within 26 s, UV intensity and UV dose fell\./);
  assert.match(text, /Then the UV dose low alarm came on\.$/);
});

test('one recorded event still gives a sentence; nothing gives none', () => {
  assert.match(storySummary([obs('00:00', 'P-102 flow low', null)]), /^P-102 flow low came on first/);
  assert.equal(storySummary([]), '');
  assert.equal(storySummary([{ kind: 'alarm_state', timestamp: '2026-11-09T05:00:00Z', event: 'P-102 flow low: returned to normal' }]), '');
});

test('a recorded trip missing from the timeline starts the story', () => {
  const events = [obs('51:41', 'CHP gas consumption', '160.84 → 80.42 m³/h'), obs('51:41', 'CHP gas consumption low', null), obs('52:05', 'Holder fill level', '69.5 → 71.2 %')];
  const merged = withRecordedTrips(events, [{ kind: 'trip', short: 'CHP engine trip', first_seen_at: '2026-11-09T05:51:39+00:00' },
    { kind: 'early', short: 'Holder fill level high', first_seen_at: '2026-11-09T05:52:05+00:00' }]);
  assert.equal(merged.length, 4);
  const text = storySummary(merged);
  assert.match(text, /^CHP engine trip came on first/);
  assert.match(text, /Within 2 s, CHP gas consumption fell\. Then the CHP gas consumption low alarm came on\.$/);
  assert.equal(withRecordedTrips(events, []), events);
});

test('a LOW advisory alarm is not named as the alarm that came on', () => {
  const t = (s) => `2026-11-11T10:00:${String(s).padStart(2, '0')}Z`;
  const text = storySummary([
    { kind: 'observation', event: 'Inflow to wet well', change: '425 → 610 m³/h', timestamp: t(1) },
    { kind: 'alarm_state', event: 'Inflow to wet well above normal: active', timestamp: t(3) },
    { kind: 'observation', event: 'P-102 flow', change: '421 → 90 m³/h', timestamp: t(9) },
    { kind: 'alarm_state', event: 'P-102 flow low: active', timestamp: t(15) },
  ]);
  assert.match(text, /Then the P-102 flow low alarm came on\.$/);
  assert.match(text, /Within 8 s, P-102 flow fell\./);
});
