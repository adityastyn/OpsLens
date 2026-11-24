import test from 'node:test';
import assert from 'node:assert/strict';
import { PRIMARY_SCENARIO_CATALOG, resolvePrimaryScenarioCatalog } from './scenarioCatalog.js';

test('eight incidents: seven with their SOP, one with none', () => {
  const { scenarios } = resolvePrimaryScenarioCatalog(PRIMARY_SCENARIO_CATALOG);
  assert.deepEqual(scenarios.map((s) => s.name), [
    'Rags jam the bar screen', 'The lift station pumps clog', 'Nocardia foam on the aeration tank',
    'RAS pump fails, sludge blanket rises', 'CHP engine trips, gas holder fills', 'Centrate poisons the plant inlet',
    'Low dissolved oxygen due to aeration blower failure', 'UV disinfection intensity low (no SOP)',
  ]);
  assert.deepEqual(scenarios.map((s) => s.sop_id), [
    'SOP-INL-001', 'SOP-LFT-001', 'SOP-BIO-001', 'SOP-BIO-002', 'SOP-GAS-001', 'SOP-SLD-001', 'SOP-AER-001', '',
  ]);
  const withSop = scenarios.filter((s) => s.sop_id);
  // Without the backend, no approval status is claimed either way.
  assert.ok(withSop.every((s) => s.sop_approval_status === null));
  assert.equal(scenarios.find((s) => s.scenario_id === 'uv_disinfection_low').sop_coverage, 'none');
  assert.equal(scenarios.find((s) => s.scenario_id === 'centrifuge_capture').sop_coverage, 'partial');
});

test('legacy or removed scenarios from an old API cannot reappear', () => {
  const old = [
    { scenario_id: 'valve_restriction', name: 'Pump-02 — Valve Restriction' },
    { scenario_id: 'filters_uv_disinfection_failure', name: 'Filters / UV' },
    { scenario_id: 'monitoring_due', name: 'Sampling due' },
    { scenario_id: 'wet_well_high_level', name: 'Old name', sop_id: 'SOP-XYZ', approval_status: 'Approved' },
  ];
  const { scenarios } = resolvePrimaryScenarioCatalog(old);
  assert.equal(scenarios.length, 8);
  assert.ok(!scenarios.some((s) => ['valve_restriction', 'filters_uv_disinfection_failure', 'monitoring_due'].includes(s.scenario_id)));
  const lift = scenarios.find((s) => s.scenario_id === 'wet_well_high_level');
  assert.equal(lift.name, 'The lift station pumps clog');
  assert.equal(lift.sop_id, 'SOP-LFT-001');
});

test('the SOP approval status comes from the backend', () => {
  const { scenarios } = resolvePrimaryScenarioCatalog([{ scenario_id: 'wet_well_high_level', sop_approval_status: 'Approved for use' }]);
  assert.equal(scenarios.find((s) => s.scenario_id === 'wet_well_high_level').sop_approval_status, 'Approved for use');
  assert.equal(scenarios.find((s) => s.scenario_id === 'uv_disinfection_low').sop_approval_status, 'No SOP in the manual');
});
