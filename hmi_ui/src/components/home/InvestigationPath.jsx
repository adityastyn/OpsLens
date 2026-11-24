import React from 'react';

/**
 * One investigation, in order: where this page sits and what comes next.
 * Shown on the investigation pages when an incident is open, so the tabs read
 * as steps of the same investigation rather than separate features.
 */
const RCA = 'Root Cause & Analysis';
export const INVESTIGATION_STEPS = [
  { key: 'alarms', label: 'Alarm priority', go: { page: 'Alarms' } },
  { key: 'story', label: 'Event story', go: { page: 'Incidents', detail: true } },
  { key: 'evidence', label: 'Root-cause evidence', go: { page: RCA, view: 'Evidence and root cause' } },
  { key: 'graph', label: 'Knowledge graph', go: { page: RCA, view: 'Knowledge graph' } },
  { key: 'analysis', label: 'Incident analysis & AI', go: { page: RCA, view: 'Incident analysis' } },
  { key: 'sop', label: 'SOP', go: { page: 'SOPs' } },
  { key: 'trends', label: 'Trends', go: { page: 'Time-Series & Prediction' } },
  { key: 'verify', label: 'Verify recovery', go: { verify: true } },
];

export function stepFor(page, view, detail) {
  if (page === RCA) return { 'Knowledge graph': 'graph', 'Incident analysis': 'analysis' }[view] || 'evidence';
  return { Alarms: 'alarms', Incidents: detail ? 'story' : null, SOPs: 'sop', 'Time-Series & Prediction': 'trends' }[page] || null;
}

export default function InvestigationPath({ current, onGo }) {
  const at = INVESTIGATION_STEPS.findIndex((s) => s.key === current);
  if (at < 0) return null;
  const next = INVESTIGATION_STEPS[at + 1];
  return (
    <nav className="hm-path" aria-label="Investigation steps">
      <ol>
        {INVESTIGATION_STEPS.map((s, i) => (
          <li key={s.key} className={i === at ? 'now' : i < at ? 'done' : ''}>
            <button type="button" aria-current={i === at ? 'step' : undefined} onClick={() => onGo(s.go)}><span>{i + 1}</span>{s.label}</button>
          </li>
        ))}
      </ol>
      {next && <button type="button" className="hm-btn small hm-path-next" onClick={() => onGo(next.go)}>Next: {next.label} ›</button>}
    </nav>
  );
}
