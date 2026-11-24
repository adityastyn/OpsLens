import React, { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Loader2, Search } from 'lucide-react';
import InvestigationPath, { stepFor } from './InvestigationPath';
import { getHandoverHistory, getHandoverRecord, getRecentEvents, getSop, listSopLibrary } from '../../api/client';
import { ALARM_LABELS } from '../../lib/plantTags.generated.js';
import { isAlarmAcknowledged, isAlarmActive } from '../../lib/alarmState';
import { hhmmss, dateTime } from '../../lib/format';
import KnowledgeGraphPage from '../hmi/KnowledgeGraphPage';
import ShiftHandoverPage from '../ShiftHandoverPage';
import DiagnosisCard from '../console/DiagnosisCard';
import { EventStorySection, EvidenceRootCauseSection, IncidentAnalysisSection } from '../console/IncidentSections';

const when = (ts) => dateTime(ts);
export const priorityOf = (incident) => {
  const s = String(incident?.scenario_severity || '').toLowerCase();
  return s === 'critical' || s === 'high' ? 'HIGH' : s === 'medium' ? 'MED' : 'LOW';
};

function PageHead({ title, children }) {
  return <header className="hm-page-head"><h1>{title}</h1>{children}</header>;
}

/** Active alarms first, then the standard alarm summary of every stored alarm. */
export function AlarmsPage({ incidents, alarmHistory, loading, onOpenIncident, onAcknowledge, busy }) {
  const [q, setQ] = useState('');
  const active = incidents.filter((i) => i.is_open !== false && i.status !== 'RESOLVED')
    .flatMap((i) => (i.individual_alarms || []).filter(isAlarmActive).map((a) => ({ ...a, incident: i })));
  const rows = useMemo(() => alarmHistory.filter((a) => !q || JSON.stringify([a.alarm_id, a.alarm_description, a.equipment, a.status]).toLowerCase().includes(q.toLowerCase())), [alarmHistory, q]);
  return (
    <div className="hm-page">
      <PageHead title="Alarms"><span className="hm-count">{active.length} active</span></PageHead>
      <section className="hm-card">
        <h2>Active alarms</h2>
        <div className="hm-table-wrap"><table className="hm-table">
          <thead><tr><th>Priority</th><th>Alarm</th><th>Description</th><th>Equipment</th><th>State</th><th>Since</th><th /></tr></thead>
          <tbody>{active.map((a) => {
            const ack = isAlarmAcknowledged(a);
            return <tr key={`${a.incident.event_id}-${a.tag}`} className={ack ? '' : 'unack'}>
              <td><span className={`hm-pill ${priorityOf(a.incident).toLowerCase()}`}>{priorityOf(a.incident)}</span></td>
              <td className="mono">{a.tag}</td><td>{ALARM_LABELS[a.tag] || a.description || a.tag}</td><td>{a.equipment_id || a.incident.display_equipment}</td>
              <td>{ack ? 'Acknowledged' : <b>Not acknowledged</b>}</td><td className="mono">{hhmmss(a.first_seen_timestamp || a.incident.trigger_timestamp)}</td>
              <td className="hm-row-actions">{!ack && <button type="button" className="hm-btn small ack" disabled={busy} onClick={() => onAcknowledge(a.incident.event_id)}
                title="Records the acknowledgement in OpsLens only; acknowledge the plant alarm on SCADA/HMI as usual.">Acknowledge</button>}
                <button type="button" className="hm-btn small" onClick={() => onOpenIncident(a.incident.event_id)}>Open</button></td>
            </tr>;
          })}{!active.length && <tr><td colSpan={7} className="hm-muted">No active alarms.</td></tr>}</tbody>
        </table></div>
      </section>
      <section className="hm-card">
        <div className="hm-card-head"><div><h2>Alarm summary (all stored alarms)</h2>
          <p className="hm-muted hm-retention">Every alarm is kept until the database is reset; OpsLens never deletes records on its own. Rows marked <em className="hm-hist-tag">simulated history</em> are the seeded past month, not live plant data.</p></div>
          <label className="hm-search"><Search size={16} /><input aria-label="Search alarms" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search alarm, equipment or state" /></label></div>
        <div className="hm-table-wrap"><table className="hm-table">
          <thead><tr><th>Last occurred</th><th>Alarm</th><th>Description</th><th>Equipment</th><th>Priority</th><th>State</th><th>Count</th><th /></tr></thead>
          <tbody>{rows.map((a) => <tr key={a.alarm_record_id || `${a.incident_id}-${a.alarm_id}`}>
            <td className="mono">{when(a.latest_triggered_at)}</td><td className="mono">{a.alarm_id}</td><td>{ALARM_LABELS[a.alarm_id] || (String(a.alarm_description || '').startsWith('Recorded') ? '—' : a.alarm_description) || '—'}</td><td>{a.equipment || '—'}</td>
            <td>{a.priority && !/not (specified|recorded)/i.test(a.priority) ? a.priority : '—'}</td><td>{String(a.status || '').replaceAll('_', ' ').toLowerCase()}{/simulated history/i.test(a.data_source || '') && <em className="hm-hist-tag">simulated history</em>}</td><td>×{a.repeat_count || 1}</td>
            <td>{a.incident_id && <button type="button" className="hm-btn small" onClick={() => onOpenIncident(a.incident_id)}>Open</button>}</td>
          </tr>)}{!rows.length && <tr><td colSpan={8} className="hm-muted">{loading ? 'Loading…' : 'No alarm records.'}</td></tr>}</tbody>
        </table></div>
      </section>
    </div>
  );
}

/** Active incidents on top, every logged incident below; a row opens its details. */
export function IncidentsPage({ incidents, detail, setDetail, onOpenIncident, current, refreshKey }) {
  const [events, setEvents] = useState(null);
  useEffect(() => { getRecentEvents(50).then(setEvents).catch(() => setEvents([])); }, [refreshKey]);
  const open = incidents.filter((i) => i.is_open !== false && i.status !== 'RESOLVED');
  if (detail && current.fullEvent) {
    const { fullEvent, brief, incident, lifecycle } = current;
    return (
      <div className="hm-page">
        <PageHead title={brief?.problem?.alarm_description || fullEvent.primary_tag}>
          <button type="button" className="hm-btn" onClick={() => setDetail(false)}><ArrowLeft size={18} /> All incidents</button>
        </PageHead>
        <dl className="hm-facts hm-ids" aria-label="Incident identifiers">
          <div><dt>Equipment</dt><dd>{brief?.problem?.equipment || fullEvent.display_equipment || '—'}</dd></div>
          <div><dt>Alarm tag</dt><dd><code>{brief?.problem?.alarm_tag || fullEvent.primary_tag}</code></dd></div>
          <div><dt>Incident ID</dt><dd><code>{fullEvent.event_id}</code></dd></div>
          <div><dt>First alarm</dt><dd>{dateTime(fullEvent.trigger_timestamp)}</dd></div>
          <div><dt>Status</dt><dd>{lifecycle?.label || fullEvent.status || '—'}</dd></div>
        </dl>
        <section className="hm-card"><h2>Explanation</h2><DiagnosisCard eventId={fullEvent.event_id} refreshKey={refreshKey} /></section>
        <EventStorySection eventId={fullEvent.event_id} refreshKey={refreshKey} />
        <IncidentAnalysisSection fullEvent={fullEvent} brief={brief} incident={incident} lifecycle={lifecycle} refreshKey={refreshKey} />
      </div>
    );
  }
  return (
    <div className="hm-page">
      <PageHead title="Incidents"><span className="hm-count">{open.length} open</span></PageHead>
      <section className="hm-card">
        <h2>Active incidents</h2>
        <div className="hm-inc-cards">{open.map((i) => (
          <button key={i.event_id} type="button" className={`hm-inc ${i.alarm_inputs_active === false ? 'ok' : priorityOf(i).toLowerCase()}`} onClick={() => onOpenIncident(i.event_id, true)}>
            {i.alarm_inputs_active === false
              ? <span className="hm-pill ok">INCIDENT RESOLVED</span>
              : <span className={`hm-pill ${priorityOf(i).toLowerCase()}`}>{priorityOf(i)}</span>}
            <b>{ALARM_LABELS[i.primary_tag] || i.primary_tag}</b><small>{i.display_equipment} · {(i.individual_alarms || []).filter(isAlarmActive).length} active alarm(s) · since {hhmmss(i.trigger_timestamp)}</small>
          </button>))}
          {!open.length && <p className="hm-muted">No open incidents.</p>}</div>
      </section>
      <section className="hm-card">
        <h2>All logged incidents and events</h2>
        <div className="hm-table-wrap"><table className="hm-table clickable">
          <thead><tr><th>Time</th><th>Alarm</th><th>Description</th><th>Equipment</th><th>Status</th><th>Repeats</th></tr></thead>
          <tbody>{(events || []).map((e) => <tr key={e.event_id} data-event-id={e.event_id} tabIndex={0} onClick={() => onOpenIncident(e.event_id, true)} onKeyDown={(k) => k.key === 'Enter' && onOpenIncident(e.event_id, true)}>
            <td className="mono">{when(e.timestamp)}</td><td className="mono">{e.alarm_id}</td><td>{ALARM_LABELS[e.alarm_id] || e.description}</td><td>{e.equipment || '—'}</td>
            <td>{e.status === 'RESOLVED' ? 'Resolved' : e.is_open ? 'Open' : String(e.status || '').toLowerCase()}</td><td>×{e.repeat_count || 1}</td></tr>)}
            {events && !events.length && <tr><td colSpan={6} className="hm-muted">No incidents logged.</td></tr>}
            {!events && <tr><td colSpan={6} className="hm-muted"><Loader2 size={14} className="spin" /> Loading…</td></tr>}</tbody>
        </table></div>
      </section>
    </div>
  );
}

const RC_VIEWS = ['Evidence and root cause', 'Knowledge graph', 'Incident analysis'];

export function RootCausePage({ current, refreshKey, open, onGo }) {
  const [view, setView] = useState(open?.view || RC_VIEWS[0]);
  // "More ›" on Home's Root-Cause Evidence panel opens the knowledge graph directly.
  useEffect(() => { if (open?.view) setView(open.view); }, [open?.n]); // eslint-disable-line react-hooks/exhaustive-deps
  const id = current.fullEvent?.event_id;
  return (
    <div className="hm-page">
      {onGo && <InvestigationPath current={stepFor('Root Cause & Analysis', view)} onGo={(to) => (to.view && to.page === 'Root Cause & Analysis' ? setView(to.view) : onGo(to))} />}
      <PageHead title="Root Cause & Analysis">
        <div className="hm-seg" role="tablist">{RC_VIEWS.map((v) => <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => setView(v)}>{v}</button>)}</div>
      </PageHead>
      {view === RC_VIEWS[0] && <EvidenceRootCauseSection eventId={id} refreshKey={refreshKey} />}
      {view === RC_VIEWS[1] && <KnowledgeGraphPage eventId={id} />}
      {view === RC_VIEWS[2] && <IncidentAnalysisSection fullEvent={current.fullEvent} brief={current.brief} incident={current.incident} lifecycle={current.lifecycle} refreshKey={refreshKey} />}
    </div>
  );
}

const NOTE_LABELS = {
  outgoing_confirmation: 'Outgoing operator confirmed', shift_notes: 'Shift notes', equipment_observations: 'Equipment observations',
  maintenance_updates: 'Maintenance updates', important_context: 'Important context', unresolved_issues: 'Unresolved issues',
  highlighted_alarms: 'Highlighted alarms', pending_work: 'Pending work', equipment_out_of_service: 'Equipment out of service',
  active_permits_loto: 'Active permits and LOTO', process_changes: 'Process changes', abnormal_readings: 'Abnormal readings',
  chemical_polymer_stock: 'Chemical and polymer stock', pending_laboratory_results: 'Pending laboratory results',
};
const EMPTY_NOTE = /^(none|n\/a|na|-|—|not recorded|nil)$/i;
const itemName = (x) => x.alarm_description || ALARM_LABELS[x.alarm_id] || x.alarm_id || x.alarm
  || (x.incident_id ? `Incident ${String(x.incident_id).slice(0, 8)}` : '') || x.action_type || x.tag || 'Item';

/** The outgoing operator's typed notes as label: value rows. Alarms and
 *  incidents saved with the handover are listed above with their names. */
function HandoverNotes({ entries }) {
  const notes = (entries || []).filter((e) => e.value != null && !e.item);
  if (!notes.length) return null;
  const filled = notes.filter((e) => !EMPTY_NOTE.test(String(e.value).trim()));
  const empty = notes.filter((e) => EMPTY_NOTE.test(String(e.value).trim()));
  return (
    <section className="hm-handover-notes">
      <h3>Notes from the outgoing operator</h3>
      <dl>{filled.map((e) => <div key={e.item_id || e.category}><dt>{NOTE_LABELS[e.category] || String(e.category).replaceAll('_', ' ')}</dt><dd>{e.value}</dd></div>)}</dl>
      {empty.length > 0 && <p className="hm-muted">Nothing to report: {empty.map((e) => (NOTE_LABELS[e.category] || e.category).toLowerCase()).join(', ')}.</p>}
    </section>
  );
}

function ShiftDetail({ record }) {
  const s = record.summary || {};
  const list = (name, items) => <div><h3>{name} ({(items || []).length})</h3>{(items || []).length ? <><ul className="hm-list">{items.slice(0, 8).map((x, i) => <li key={i}><b>{itemName(x)}</b>{x.equipment ? ` · ${x.equipment}` : ''}{x.status ? ` · ${String(x.status).toLowerCase().replaceAll('_', ' ')}` : ''}{x.notes ? `: ${x.notes}` : ''}</li>)}</ul>{items.length > 8 && <p className="hm-muted">and {items.length - 8} more</p>}</> : <p className="hm-muted">None.</p>}</div>;
  return (
    <div className="hm-shift-detail">
      <dl className="hm-facts">
        <div><dt>Shift</dt><dd>{record.shift_name || '—'} · {record.shift_date || ''}</dd></div>
        <div><dt>Window</dt><dd>{when(record.shift_start)} – {when(record.shift_end)}</dd></div>
        <div><dt>Outgoing</dt><dd>{record.outgoing_operator || '—'}</dd></div>
        <div><dt>Incoming</dt><dd>{record.incoming_operator || record.received_by || 'Not yet received'}</dd></div>
        <div><dt>Status</dt><dd>{record.status}</dd></div>
        <div><dt>Submitted</dt><dd>{when(record.submitted_at)}</dd></div>
      </dl>
      <div className="hm-shift-grid">
        {list('Open alarms', s.open_alarms)}{list('Unresolved incidents', s.unresolved_incidents)}{list('Operator actions', s.actions_in_shift)}
      </div>
      <HandoverNotes entries={record.entries} />
    </div>
  );
}

/** The last shift on top (with an in-depth view), every earlier shift below. */
export function ShiftsPage({ handover, onSelectIncident, onOpenAlarms, onRefresh }) {
  const [records, setRecords] = useState(null);
  const [chosen, setChosen] = useState(null);
  const [mode, setMode] = useState('list');
  const load = () => getHandoverHistory({}).then((d) => setRecords(d.handovers || d || [])).catch(() => setRecords([]));
  useEffect(() => { load(); }, []);
  const open = async (id) => { try { setChosen(await getHandoverRecord(id)); setMode('record'); } catch (_) { /* stays on the list */ } };
  if (mode === 'prepare') {
    return <div className="hm-page"><PageHead title="Prepare shift handover"><button type="button" className="hm-btn" onClick={() => { setMode('list'); load(); }}><ArrowLeft size={18} /> Shifts History</button></PageHead>
      <ShiftHandoverPage summary={handover} onSelectIncident={onSelectIncident} onOpenAlarmHistory={onOpenAlarms} onRefresh={onRefresh} /></div>;
  }
  if (mode === 'record' && chosen) {
    return <div className="hm-page"><PageHead title={`Shift ${chosen.shift_name || ''} · ${chosen.shift_date || ''}`}><button type="button" className="hm-btn" onClick={() => setMode('list')}><ArrowLeft size={18} /> Shifts History</button></PageHead>
      <section className="hm-card"><ShiftDetail record={chosen} /></section></div>;
  }
  const last = records?.[0];
  return (
    <div className="hm-page">
      <PageHead title="Shifts History"><button type="button" className="hm-btn primary" onClick={() => setMode('prepare')}>Prepare this shift's handover</button></PageHead>
      <section className="hm-card">
        <div className="hm-card-head"><h2>Last shift</h2>{last && <button type="button" className="hm-btn" onClick={() => open(last.handover_id)}>In-depth view</button>}</div>
        {!records && <p className="hm-muted"><Loader2 size={14} className="spin" /> Loading…</p>}
        {records && last && <ShiftDetail record={last} />}
        {records && !last && <>
          <p className="hm-muted">No shift handover has been submitted yet. This is the current shift so far:</p>
          <dl className="hm-facts">
            <div><dt>Open alarms</dt><dd>{(handover?.open_alarms || []).length}</dd></div>
            <div><dt>Not acknowledged</dt><dd>{(handover?.unacknowledged_alarms || []).length}</dd></div>
            <div><dt>Unresolved incidents</dt><dd>{(handover?.unresolved_incidents || []).length}</dd></div>
            <div><dt>Operator actions</dt><dd>{(handover?.actions_in_shift || []).length}</dd></div>
          </dl>
        </>}
      </section>
      <section className="hm-card">
        <h2>Past shifts</h2>
        <div className="hm-table-wrap"><table className="hm-table clickable">
          <thead><tr><th>Date</th><th>Shift</th><th>Outgoing</th><th>Incoming</th><th>Status</th><th>Submitted</th></tr></thead>
          <tbody>{(records || []).map((r) => <tr key={r.handover_id} tabIndex={0} onClick={() => open(r.handover_id)} onKeyDown={(k) => k.key === 'Enter' && open(r.handover_id)}>
            <td>{r.shift_date}</td><td>{r.shift_name}</td><td>{r.outgoing_operator || '—'}</td><td>{r.incoming_operator || r.received_by || '—'}</td><td>{r.status}</td><td className="mono">{when(r.submitted_at)}</td></tr>)}
            {records && !records.length && <tr><td colSpan={6} className="hm-muted">No past shifts recorded.</td></tr>}</tbody>
        </table></div>
      </section>
    </div>
  );
}

/** Every SOP, grouped by process area, with a preview of the selected one. */
export function SopsPage({ suggested }) {
  const [docs, setDocs] = useState(null);
  const [chosen, setChosen] = useState(suggested || null);
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => { listSopLibrary().then((d) => setDocs(d.documents || d || [])).catch((e) => { setDocs([]); setError(e.message); }); }, []);
  useEffect(() => {
    if (!chosen) return undefined;
    let live = true;
    setDoc(null);
    getSop(chosen).then((d) => live && setDoc(d?.data || d)).catch((e) => live && setDoc({ error: e.message }));
    return () => { live = false; };
  }, [chosen]);
  const groups = useMemo(() => {
    const out = {};
    (docs || []).forEach((d) => { (out[d.process_area || 'Other'] = out[d.process_area || 'Other'] || []).push(d); });
    return Object.entries(out).sort(([a], [b]) => a.localeCompare(b));
  }, [docs]);
  return (
    <div className="hm-page hm-sops">
      <aside className="hm-sop-list" aria-label="SOP list">
        <h1>SOPs</h1>
        {error && <p className="hm-alert">{error}</p>}
        {!docs && <p className="hm-muted"><Loader2 size={14} className="spin" /> Loading…</p>}
        {groups.map(([area, items]) => <div key={area}><h2>{area}</h2>
          {items.map((d) => <button key={d.sop_id} type="button" aria-current={chosen === d.sop_id ? 'true' : undefined} onClick={() => setChosen(d.sop_id)}>
            <b>{d.sop_id}</b><span>{d.title}</span>{d.sop_id === suggested && <em>Suggested</em>}<small className={d.approved ? 'ok' : 'draft'}>{d.approved ? 'APPROVED' : /draft/i.test(d.approval_status || '') ? 'DRAFT' : 'REFERENCE'}</small></button>)}
        </div>)}
      </aside>
      <section className="hm-sop-preview" aria-label="SOP preview">
        {!chosen && <p className="hm-muted">Choose an SOP to preview it.</p>}
        {chosen && !doc && <p className="hm-muted"><Loader2 size={14} className="spin" /> Loading {chosen}…</p>}
        {doc?.error && <p className="hm-alert">{doc.error}</p>}
        {doc && !doc.error && <>
          {/* Approval status is in the list on the left and the revision line below. */}
          <header><h2>{doc.sop_id} · {doc.title}</h2></header>
          <p className="hm-muted">{doc.revision_label ? `Revision ${doc.revision_label}. ` : ''}{doc.source_pdf ? `Source: ${doc.source_pdf}` : ''}{doc.source_pages ? `, pages ${[].concat(doc.source_pages).join(', ')}` : ''}</p>
          {(doc.content_pages || []).map((p) => <article key={p.page} className="hm-sop-page"><h3>Page {p.page}</h3><pre>{p.text}</pre></article>)}
          {!doc.content_pages && (doc.steps || []).length > 0 && <ol className="hm-list">{doc.steps.map((s, i) => <li key={i}>{s.text || s.instruction || String(s)}</li>)}</ol>}
        </>}
      </section>
    </div>
  );
}
