// The eight Scenario Lab incidents. Six come from the plant incident map; the
// seventh uses demonstration values from draft SOP-AER-001; the eighth (UV
// disinfection) has no SOP in the manual, to show the no-SOP path.
// The UV entry is new; the old 'filters_uv_disinfection_failure' stays removed. The backend
// (/api/scenarios) is authoritative for telemetry; this allowlist keeps an
// older API from bringing back removed scenarios (Pump-02, filters, UV ...).
export const PRIMARY_SCENARIO_CATALOG = [
  { scenario_id: 'screen_differential_pressure', number: 1, name: 'Rags jam the bar screen', equipment_id: 'InletWorks-01', primary_alarm: 'ALM-SCR-110-DL', sop_id: 'SOP-INL-001', sop_title: 'Bar Screen Blockage Response', sop_coverage: 'full' },
  { scenario_id: 'wet_well_high_level', number: 2, name: 'The lift station pumps clog', equipment_id: 'LiftStation-01', primary_alarm: 'ALM-WW-101-HH', sop_id: 'SOP-LFT-001', sop_title: 'Lift Station High-High Level and Pump Clog Response', sop_coverage: 'full' },
  { scenario_id: 'biological_foam', number: 3, name: 'Nocardia foam on the aeration tank', equipment_id: 'AerationBasin-01', primary_alarm: 'ALM-AER-201-FOAM', sop_id: 'SOP-BIO-001', sop_title: 'Activated Sludge Foam Control', sop_coverage: 'full' },
  { scenario_id: 'ras_pump_failure', number: 4, name: 'RAS pump fails, sludge blanket rises', equipment_id: 'RASSystem-01', primary_alarm: 'ALM-SLB-201-HIGH', sop_id: 'SOP-BIO-002', sop_title: 'RAS Pump Failure and Sludge Blanket Control', sop_coverage: 'full' },
  { scenario_id: 'biogas_holder_pressure', number: 5, name: 'CHP engine trips, gas holder fills', equipment_id: 'BiogasSystem-01', primary_alarm: 'ALM-GAS-601-HIGH', sop_id: 'SOP-GAS-001', sop_title: 'Biogas Holder High Pressure, CHP Trip and Flare', sop_coverage: 'full' },
  { scenario_id: 'centrifuge_capture', number: 6, name: 'Centrate poisons the plant inlet', equipment_id: 'DewateringCentrifuge-01', primary_alarm: 'ALM-SLD-701-CAPTURE', sop_id: 'SOP-SLD-001', sop_title: 'Dewatering Centrifuge and Centrate Recycle', sop_coverage: 'partial' },
  { scenario_id: 'aeration_blower_failure', number: 7, name: 'Low dissolved oxygen due to aeration blower failure', equipment_id: 'AerationBlower-01', primary_alarm: 'ALM-AER-201-DO', sop_id: 'SOP-AER-001', sop_title: 'Aeration Blower Failure Response', sop_coverage: 'full' },
  { scenario_id: 'uv_disinfection_low', number: 8, name: 'UV disinfection intensity low (no SOP)', equipment_id: 'UVChannel-01', primary_alarm: 'ALM-UV-901-INT-LO', sop_id: '', sop_title: '', sop_coverage: 'none', sop_approval_status: 'No SOP in the manual' },
].map((entry) => ({ source_status: 'SIMULATED', ...entry }));

export function resolvePrimaryScenarioCatalog(apiScenarios = []) {
  const byId = new Map((Array.isArray(apiScenarios) ? apiScenarios : [])
    .filter((scenario) => scenario?.scenario_id)
    .map((scenario) => [scenario.scenario_id, scenario]));
  // Catalog identity (name, SOP) always wins over the API copy. The SOP's
  // approval status is the backend's (site setting); without it, none is shown.
  const scenarios = PRIMARY_SCENARIO_CATALOG.map((entry) => {
    const api = byId.get(entry.scenario_id) || {};
    return { ...api, ...entry, sop_approval_status: entry.sop_approval_status || api.sop_approval_status || null };
  });
  return { scenarios };
}
