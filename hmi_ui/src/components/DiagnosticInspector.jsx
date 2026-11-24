/**
 * DiagnosticInspector — Engineering Console page (port 5174). Development and
 * demonstration view, never shown on the operator HMI.
 *
 * Shows what the analysis read and how it ranked the causes, for one incident:
 * provenance (predefined vs computed), the candidate ranking with every
 * evidence factor, alarms, limit breaches, the event timeline, readings and
 * per-reading ranking scores. The scenario's test reference is kept in a
 * separate, closed section: the analysis never reads it.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Microscope, RefreshCw } from 'lucide-react';
import { getIncidentInspection, getRecentEvents } from '../api/client';

// The inspection body reports UTC (as stored); the picker uses the same clock.
const utcTime = (t) => { const d = new Date(t); return Number.isNaN(d.getTime()) ? '—' : `${d.toISOString().slice(11, 19)} UTC`; };

const ROLE_STYLE = {
  leading: 'bg-[#FFF3CD] text-[#6B4E00] border-[#E0B84A]',
  contributing: 'bg-[#FFF8E6] text-[#6B4E00] border-[#F0D58A]',
  chain: 'bg-[#EEF2F6] text-[#344054] border-[#D0D5DD]',
  effect: 'bg-white text-[#64736A] border-dashed border-[#B8C2BB]',
  insufficient: 'bg-white text-[#8A6400] border-dotted border-[#E0B84A]',
};

function H({ children }) {
  return <h2 className="mt-8 mb-3 text-[11px] font-semibold uppercase tracking-[0.15em] text-[#64736A] border-b border-[#D8E2DA] pb-2">{children}</h2>;
}

function Table({ head, rows, empty = 'Nothing recorded.' }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[#D8E2DA] bg-white">
      <table className="w-full text-xs">
        <thead className="bg-[#F3F6F3] text-left text-[#64736A]"><tr>{head.map((h) => <th key={h} className="px-3 py-2 font-semibold">{h}</th>)}</tr></thead>
        <tbody>{rows.length ? rows.map((r, i) => <tr key={i} className="border-t border-[#EEF2EE] align-top">{r.map((c, j) => <td key={j} className="px-3 py-1.5">{c}</td>)}</tr>)
          : <tr><td colSpan={head.length} className="px-3 py-3 text-[#64736A]">{empty}</td></tr>}</tbody>
      </table>
    </div>
  );
}

const v = (x) => (x === true ? 'ON' : x === false ? 'off' : x == null ? '—' : typeof x === 'number' ? String(Math.round(x * 100) / 100) : String(x));

export default function DiagnosticInspector({ activeEventId = null }) {
  const [events, setEvents] = useState([]);
  const [eventId, setEventId] = useState(activeEventId);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getRecentEvents(20).then((list) => setEvents(list || [])).catch(() => setEvents([]));
  }, [activeEventId]);
  useEffect(() => { if (activeEventId && !eventId) setEventId(activeEventId); }, [activeEventId, eventId]);

  const load = useCallback(() => {
    if (!eventId) return;
    setLoading(true); setError(null);
    getIncidentInspection(eventId).then(setData).catch((e) => setError(e.message || String(e))).finally(() => setLoading(false));
  }, [eventId]);
  useEffect(() => { load(); }, [load]);

  const ref = data?.test_reference;
  return (
    <div className="mx-auto max-w-6xl px-6 pb-16 pt-6 font-sans text-[#26352C]" data-testid="diagnostic-inspector">
      <div className="flex flex-wrap items-center gap-3">
        <Microscope className="h-5 w-5 text-[#009530]" />
        <h1 className="text-lg font-black">Diagnostic inspection</h1>
        <span className="rounded-full border border-[#D8E2DA] bg-[#F3F6F3] px-2.5 py-0.5 text-xs font-semibold text-[#64736A]">Development view · not shown to operators</span>
        <div className="ml-auto flex items-center gap-2">
          <select aria-label="Incident to inspect" className="h-9 rounded-lg border border-[#D8E2DA] bg-white px-2 text-sm" value={eventId || ''} onChange={(e) => setEventId(e.target.value || null)}>
            <option value="">Choose an incident…</option>
            {activeEventId && !events.some((e) => e.event_id === activeEventId) && <option value={activeEventId}>Current incident · {activeEventId.slice(0, 8)}</option>}
            {events.map((e) => <option key={e.event_id} value={e.event_id}>{e.alarm_id || e.primary_tag || 'Incident'} · {e.equipment || ''} · {utcTime(e.timestamp || e.trigger_timestamp)} · {e.event_id.slice(0, 8)}</option>)}
          </select>
          <button type="button" onClick={load} className="flex h-9 items-center gap-1.5 rounded-lg border border-[#D8E2DA] bg-white px-3 text-xs font-semibold hover:bg-[#F3F6F3]"><RefreshCw className="h-3.5 w-3.5" /> Refresh</button>
        </div>
      </div>
      <p className="mt-2 text-sm text-[#64736A]">What the analysis read and how it ranked the possible causes. Everything below the test reference is computed from the stored telemetry and alarm records when you open this page.</p>

      {!eventId && <p className="mt-6 text-sm">Run a scenario in Scenario Lab, then choose its incident here.</p>}
      {error && <p role="alert" className="mt-6 text-sm text-[#B42318]">The inspection could not be loaded: {error}</p>}
      {loading && !data && <p className="mt-6 text-sm">Loading…</p>}

      {data && <>
        <H>Where each part comes from</H>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border border-[#D8E2DA] bg-white p-3"><b className="text-xs uppercase tracking-wider text-[#64736A]">Predefined (configuration and test setup)</b>
            <ul className="mt-2 list-disc pl-5 text-sm">{data.provenance.predefined.map((x) => <li key={x}>{x}</li>)}</ul></div>
          <div className="rounded-lg border border-[#009530]/30 bg-[#F4FBF6] p-3"><b className="text-xs uppercase tracking-wider text-[#006B35]">Computed by the backend from the telemetry</b>
            <ul className="mt-2 list-disc pl-5 text-sm">{data.provenance.computed.map((x) => <li key={x}>{x}</li>)}</ul></div>
        </div>

        <H>Candidate causes · final ordering</H>
        <p className="text-sm">Primary alarm <b>{data.primary_alarm?.tag}</b> {data.primary_alarm?.description}, first {data.primary_alarm?.first_at} · equipment {data.equipment} · {data.process_area}</p>
        {data.comparison && <p className="mt-2 rounded-md border-l-4 border-[#E0A100] bg-[#FFF8E1] px-3 py-2 text-sm" data-testid="inspector-comparison">
          <b>Why {data.comparison.leading} leads{data.comparison.runner_up ? ` over ${data.comparison.runner_up}` : ''}:</b> {data.comparison.why}</p>}
        <p className="mt-2 text-xs text-[#64736A]">{data.candidate_note}</p>
        <div className="mt-3">
          <Table head={['#', 'Role', 'Candidate', 'Trip', 'Before alarm', 'Deviation', 'Agree', 'SOP limit', 'Path', 'History', 'Normal nearby', 'Points']}
            rows={(data.candidates || []).map((c) => {
              const f = Object.fromEntries((c.factors || []).map((x) => [x.key, x.earned]));
              return [c.rank, <span className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase ${ROLE_STYLE[c.role] || ''}`}>{c.role_label}</span>,
                <div><b>{c.short || c.statement}</b><div className="text-[#64736A]">{c.ranking_note}</div></div>,
                f.direct, f.timing, f.magnitude, f.corroboration, f.sop, f.topology, f.history, f.contradiction, <b>{c.evidence_points}</b>];
            })} />
        </div>
        <details className="mt-3">
          <summary className="cursor-pointer text-sm font-semibold">Supporting and contradicting evidence per candidate</summary>
          {(data.candidates || []).map((c) => (
            <div key={c.rank} className="mt-2 rounded-lg border border-[#D8E2DA] bg-white p-3 text-sm">
              <b>{c.rank}. {c.statement}</b> <span className="text-[#64736A]">({c.role_label})</span>
              <div className="mt-1 grid gap-2 md:grid-cols-2">
                <div><span className="text-xs font-semibold text-[#006B35]">Supports</span><ul className="list-disc pl-5">{(c.supporting || []).map((x) => <li key={x}>{x}</li>)}{!c.supporting?.length && <li>Nothing recorded.</li>}</ul></div>
                <div><span className="text-xs font-semibold text-[#B42318]">Contradicts</span><ul className="list-disc pl-5">{(c.contradicting || []).map((x) => <li key={x}>{x}</li>)}{!c.contradicting?.length && <li>Nothing recorded.</li>}</ul></div>
              </div>
            </div>
          ))}
        </details>

        <H>Alarms raised · in order</H>
        <Table head={['First on', 'Alarm', 'Tag', 'Now']} rows={(data.alarms || []).map((a) => [a.first_on_hms || '—', a.label, <span className="font-mono">{a.tag}</span>, v(a.now)])} />

        <H>Limit breaches detected · first crossing</H>
        <Table head={['Time', 'Reading', 'Limit', 'Value', 'SOP action']} rows={(data.breaches || []).map((b) => [b.at_hms, <span>{b.label} <span className="font-mono text-[#64736A]">{b.tag}</span></span>, b.limit, `${v(b.value)} ${b.unit || ''}`, b.action || '—'])} />

        <H>Event timeline · as the Event Story records it</H>
        <Table head={['Time', 'Tag', 'Change']} rows={(data.timeline || []).map((s) => [s.time, <span className="font-mono">{s.tag}</span>, s.change])} />

        <H>Incoming readings · this incident's tags</H>
        <Table head={['Reading', 'Samples', 'First', 'Worst', 'Now', 'Normal', 'At worst', 'Now state']}
          rows={(data.readings || []).map((r) => [<span>{r.label} <span className="font-mono text-[#64736A]">{r.tag}</span></span>, r.samples,
            r.kind === 'measurement' ? `${v(r.first)} ${r.unit || ''}` : (r.first_on_hms ? `on ${r.first_on_hms}` : 'never on'),
            r.kind === 'measurement' ? `${v(r.worst)} ${r.unit || ''}` : '—', `${v(r.now)} ${r.kind === 'measurement' ? r.unit || '' : ''}`, r.normal || '—', r.state_at_worst || '—', r.state_now || '—'])} />

        <H>Reading ranking scores · per reading, not per cause</H>
        <p className="mb-2 text-xs text-[#64736A]">Change from the normal midpoint × tag weight × 1/(steps to the alarm + 1) × 1.2 if before the alarm (0.8 after). Orders the readings; not a probability.</p>
        <Table head={['#', 'Reading', 'Change %', 'Weight', 'Steps', 'Before alarm', 'Score']}
          rows={(data.evidence_items || []).map((i) => [i.rank, <span>{i.item} <span className="font-mono text-[#64736A]">{i.tag}</span></span>, v(i.deviation_pct), v(i.severity_weight), v(i.topology_distance), i.precedes_alarm ? 'yes' : 'no', <b className="font-mono">{v(i.ranking_score)}</b>])} />

        {ref && <>
          <H>Test reference · not read by the analysis</H>
          <details className="rounded-lg border border-dashed border-[#B8C2BB] bg-[#FAFAF7] p-3" data-testid="test-reference">
            <summary className="cursor-pointer text-sm font-semibold">Show the scenario's injected conditions and expected outcome</summary>
            <p className="mt-2 text-xs text-[#64736A]">{ref.note}</p>
            <p className="mt-2 text-sm">Scenario <b>{ref.scenario_name}</b> <span className="font-mono text-[#64736A]">{ref.scenario_id}</span></p>
            <p className="mt-2 text-xs font-semibold uppercase tracking-wider text-[#64736A]">Injected conditions</p>
            <ul className="list-disc pl-5 text-sm">{ref.injected_conditions.map((x) => <li key={x}>{x}</li>)}</ul>
            <p className="mt-2 text-sm"><b>Expected primary:</b> {ref.expected_primary || '—'}</p>
            <p className="text-sm"><b>Expected secondary:</b> {ref.expected_secondary.join('; ') || '—'}</p>
            <p className="mt-2 text-sm"><b>Computed leading hypothesis:</b> {data.computed_leading || 'none'} ·{' '}
              <span className={ref.computed_matches_expected ? 'font-bold text-[#006B35]' : 'font-bold text-[#B42318]'}>
                {ref.computed_matches_expected == null ? 'not comparable' : ref.computed_matches_expected ? 'matches the reference' : 'does not match the reference'}</span></p>
          </details>
        </>}
      </>}
    </div>
  );
}
