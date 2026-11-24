import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { getIncidentEvidence, getIncidentTimeline } from '../../api/client';
import { fmtValue, hhmmss, severityOf, sourceName } from '../../lib/format';
import { ALARM_LABELS } from '../../lib/plantTags.generated.js';
import OperatorBrief from '../OperatorBrief';

export function NoIncident() {
  return <p className="ov-empty">Select an alarm on the left to open its incident.</p>;
}

function useIncidentData(loader, eventId, refreshKey) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!eventId) { setData(null); return undefined; }
    let live = true;
    setError('');
    loader(eventId).then((d) => live && setData(d)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [eventId, refreshKey, loader]);
  return { data, error };
}

const KEY_EVENT = (row) => row.kind === 'observation' || row.kind === 'operator';

export function EventStorySection({ eventId, refreshKey }) {
  const { data, error } = useIncidentData(getIncidentTimeline, eventId, refreshKey);
  const [all, setAll] = useState(false);
  if (!eventId) return <NoIncident />;
  if (error) return <p role="alert" className="ov-error">The event story could not be loaded: {error}</p>;
  if (!data) return <p className="ov-empty"><Loader2 size={13} className="spin" /> Loading…</p>;
  const rows = all ? data.events : data.events.filter(KEY_EVENT);
  return (
    <section className="con-section" aria-label="Event Story">
      <header className="con-head"><div><h2>Event Story</h2><p>{data.note}</p></div>
        <label className="ts-check"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Complete event history ({data.events.length})</label></header>
      <div className="con-table"><table className="ov-table con-story">
        <thead><tr><th>Time</th><th>Event or alarm</th><th>Equipment</th><th>Sensor</th><th>Severity</th><th>Relationship to this incident</th></tr></thead>
        <tbody>{rows.map((row, i) => (
          <tr key={`${row.timestamp}-${row.tag}-${i}`} className={/Alarm|Limit/.test(row.severity) ? 'hit' : ''}>
            <td className="mono">{hhmmss(row.timestamp)}</td>
            <td><b>{row.event}</b>{row.change && <div className="mono muted">{row.change}</div>}{row.notes && <div className="muted">{row.notes}</div>}{row.operator && <div className="muted">By {row.operator}</div>}</td>
            <td>{row.equipment || '—'}</td><td className="mono">{row.tag || '—'}</td><td>{row.severity}</td>
            <td>{row.relationship}<div className="muted">{row.basis}</div></td>
          </tr>
        ))}</tbody>
      </table></div>
    </section>
  );
}

export function EvidenceRootCauseSection({ eventId, refreshKey }) {
  const { data, error } = useIncidentData(getIncidentEvidence, eventId, refreshKey);
  if (!eventId) return <NoIncident />;
  if (error) return <p role="alert" className="ov-error">The evidence could not be loaded: {error}</p>;
  if (!data) return <p className="ov-empty"><Loader2 size={13} className="spin" /> Loading…</p>;
  return (
    <section className="con-section" aria-label="Evidence and root cause">
      <header className="con-head"><div><h2>Evidence and root cause</h2><p>{data.score_note}</p></div></header>
      <h3>Possible causes (hypotheses, not confirmed)</h3>
      {data.candidate_note && <p className="op-muted">{data.candidate_note}</p>}
      {data.comparison && <p className="con-compare" data-testid="cause-comparison">
        <b>Why {data.comparison.leading} leads{data.comparison.runner_up ? ` over ${data.comparison.runner_up}` : ''}:</b> {data.comparison.why}</p>}
      {!data.hypotheses.length && <p className="op-muted">The record does not point to a likely cause.</p>}
      {data.hypotheses.map((h) => (
        <details key={`${h.rank}-${h.tag}`} className="con-hypothesis" open={h.rank === 1}>
          <summary><span className={`ov-chip sm ${h.role === 'leading' ? 'amber' : ''} con-role ${h.role || ''}`}>{(h.role_label || 'HYPOTHESIS').toUpperCase()}</span> {h.statement}
            {h.evidence_points != null && <small className="con-points">{h.evidence_points} evidence points</small>}
            {h.ranking_score != null && <small>Ranking score {h.ranking_score}</small>}</summary>
          {h.ranking_note && <p className="con-why"><b>Why it ranks here:</b> {h.ranking_note}</p>}
          {h.role_note && <p className="op-muted">{h.role_note}</p>}
          <p>{h.why_ranked}</p>
          {h.factors && <table className="ov-table con-factors" aria-label="Evidence factors">
            <thead><tr><th>Evidence factor</th><th className="r">Earned</th><th className="r">Of</th></tr></thead>
            <tbody>{h.factors.map((f) => <tr key={f.key} className={f.earned ? '' : 'muted'}><td>{f.factor}</td><td className="r mono">{f.earned > 0 ? `+${f.earned}` : f.earned}</td><td className="r mono">{f.points ?? '—'}</td></tr>)}
              <tr><td><b>Total</b></td><td className="r mono"><b>{h.evidence_points}</b></td><td className="r mono">100</td></tr></tbody>
          </table>}
          <div className="con-two"><div><h4>Supports</h4><ul>{h.supporting.map((x) => <li key={x}>{x}</li>)}{!h.supporting.length && <li>Nothing recorded.</li>}</ul></div>
            <div><h4>Contradicts</h4><ul>{h.contradicting.map((x) => <li key={x}>{x}</li>)}{!h.contradicting.length && <li>Nothing recorded.</li>}</ul></div></div>
        </details>
      ))}
      <h3>Evidence</h3>
      <div className="con-table"><table className="ov-table">
        <thead><tr><th>Rank</th><th>Evidence item</th><th>Source</th><th className="r">Observed</th><th>Expected</th><th>Time</th><th>Relevance</th><th className="r">Ranking score</th></tr></thead>
        <tbody>{data.items.map((row) => <tr key={row.tag} title={row.explanation}><td>{row.rank}</td><td><b>{row.item}</b><div className="mono muted">{row.tag}</div></td><td>{row.source}</td><td className="r mono">{fmtValue(row.observed)} {row.unit !== 'status' ? row.unit : ''}</td><td>{row.expected || '—'}</td><td className="mono">{hhmmss(row.timestamp)}</td><td>{row.relevance}</td><td className="r mono">{row.ranking_score}</td></tr>)}</tbody>
      </table></div>
    </section>
  );
}

export function IncidentAnalysisSection({ fullEvent, brief, incident, lifecycle, refreshKey }) {
  if (!fullEvent) return <NoIncident />;
  const sev = severityOf(incident, brief);
  const problem = brief?.problem || {};
  const alarms = lifecycle?.alarm_inputs || [];
  const last = [...alarms.map((a) => a.first_seen), incident?.last_seen_timestamp, ...(fullEvent.story?.steps || []).map((s) => s.timestamp)].filter(Boolean).sort().at(-1);
  const facts = [
    ['Incident', problem.alarm_description], ['Incident ID', fullEvent.event_id],
    ['Process area', problem.process_area], ['Affected equipment', problem.equipment],
    ['Alarm ID', problem.alarm_tag], ['Severity', sev.label], ['First occurrence', hhmmss(problem.first_triggered_at)], ['Last occurrence', hhmmss(last)],
    ['Current status', lifecycle?.label], ['Acknowledgement', lifecycle ? (lifecycle.unacknowledged_count ? `${lifecycle.unacknowledged_count} active alarm(s) not acknowledged` : 'No active alarm waiting') : '—'],
    ['Data source', sourceName(alarms[0]?.data_source)], ['Resolution', fullEvent.status === 'RESOLVED' ? 'Resolved by an operator' : 'Not resolved'],
  ];
  return (
    <section className="con-section" aria-label="Incident analysis">
      <header className="con-head"><div><h2>Incident analysis</h2><p>{brief?.what_happened}</p></div></header>
      <dl className="con-facts">{facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v || '—'}</dd></div>)}</dl>
      <details open><summary>Related sensor readings ({brief?.incident_readings?.length || 0})</summary>
        <div className="con-table"><table className="ov-table"><thead><tr><th>Sensor</th><th className="r">Start</th><th className="r">Worst</th><th className="r">Now</th><th>Normal</th><th>State</th></tr></thead>
          <tbody>{(brief?.incident_readings || []).map((r) => <tr key={r.tag} className={r.state_now === 'LIMIT EXCEEDED' ? 'hit' : ''}><td>{r.label}<div className="mono muted">{r.tag}</div></td><td className="r mono">{fmtValue(r.first_value)}</td><td className="r mono">{fmtValue(r.worst_value)}</td><td className="r mono">{fmtValue(r.now_value)} {r.unit}</td><td>{r.normal_text || '—'}</td><td>{r.state_now}</td></tr>)}</tbody></table></div>
      </details>
      <details><summary>Related alarms ({alarms.length})</summary><ul className="con-list">{alarms.map((a) => <li key={a.alarm_id}><b>{ALARM_LABELS[a.tag] || a.tag}</b> <span className="mono">{a.tag}</span> · {a.active ? 'Active' : 'Normal'} · {a.acknowledged ? 'Acknowledged' : 'Not acknowledged'}</li>)}</ul></details>
      <details><summary>Corrective action history ({(fullEvent.actions || []).length})</summary>{(fullEvent.actions || []).length ? <ul className="con-list">{fullEvent.actions.map((a) => <li key={a.action_id}><span className="mono">{hhmmss(a.timestamp)}</span> {String(a.action_type).replaceAll('_', ' ')} by {a.operator}{a.notes ? `: ${a.notes}` : ''}</li>)}</ul> : <p className="op-muted">No operator action is recorded yet.</p>}</details>
      <details><summary>Technical brief</summary><OperatorBrief eventId={fullEvent.event_id} refreshKey={refreshKey} /></details>
    </section>
  );
}

