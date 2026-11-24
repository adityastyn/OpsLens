import test from 'node:test';
import assert from 'node:assert/strict';
import { stepSource } from './planSource.js';

const approved = { sop_id: 'SOP-X-001', sop_status: 'APPROVED', sop_approved: true };
const draft = { sop_id: 'SOP-LFT-001', sop_status: 'DRAFT / NOT APPROVED', sop_approved: false };
const none = { sop_id: null, sop_status: null, sop_approved: false };

test('an evidence check with recorded tags is telemetry evidence and names them', () => {
  const s = stepSource({ source: 'evidence', kind: 'CHECK', evidence_tags: ['P-101-TRIP', 'TT-101'], alarm_ids: [] }, none);
  assert.equal(s.label, 'TELEMETRY EVIDENCE');
  assert.equal(s.ref, 'P-101-TRIP · TT-101');
});

test('an evidence check without identifiable tags gets no label', () => {
  assert.equal(stepSource({ source: 'evidence', kind: 'CHECK', evidence_tags: [] }, none), null);
  assert.equal(stepSource({ source: 'evidence', kind: 'CHECK' }, none), null);  // older stored plans
});

test('the final recovery check is operator verification', () => {
  const s = stepSource({ source: 'evidence', kind: 'VERIFY', final: true, evidence_tags: ['LT-101', 'FIT-102'] }, draft);
  assert.equal(s.label, 'OPERATOR VERIFICATION');
  assert.match(s.note, /normal range/);
});

test('an approved SOP step is SOP-verified with its reference', () => {
  const s = stepSource({ source: 'sop', sop_step: 4, page: 12 }, approved);
  assert.equal(s.label, 'SOP-VERIFIED');
  assert.equal(s.ref, 'SOP-X-001 step 4 · p.12');
});

test('a draft SOP step is never shown as verified', () => {
  const s = stepSource({ source: 'sop', sop_step: 4, page: 12 }, draft);
  assert.equal(s.label, 'SOP DRAFT · NOT APPROVED');
  // the status text wins over a stray approved flag
  assert.equal(stepSource({ source: 'sop', sop_step: 2 }, { ...draft, sop_approved: true }).label, 'SOP DRAFT · NOT APPROVED');
});

test('no SOP, or no step reference: no SOP label at all', () => {
  assert.equal(stepSource({ source: 'sop', sop_step: 4 }, none), null);
  assert.equal(stepSource({ source: 'sop', sop_step: null }, approved), null);
});

test('AI steps say they are not SOP-verified', () => {
  assert.equal(stepSource({ source: 'ai' }, approved).label, 'AI-GENERATED · NOT SOP-VERIFIED');
});

test('a historical resolution names the earlier incident and says it is not guaranteed', () => {
  const s = stepSource({ source: 'history', history_events: ['5f0e1a2b3c4d'], history_when: '07 Nov 14:25', history_simulated: true }, none);
  assert.equal(s.label, 'HISTORICAL RESOLUTION');
  assert.equal(s.ref, 'Incident 5f0e1a2b');
  assert.match(s.note, /not a guaranteed solution and not an approved SOP instruction/i);
  assert.match(s.note, /simulated history/);
  assert.equal(stepSource({ source: 'history' }, none), null);
});
