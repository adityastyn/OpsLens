import { ALARM_LABELS } from '../../lib/plantTags.generated.js';
import React, { useEffect, useState } from 'react';
import { CheckCircle2, Loader2, Play, Square, Wrench, X, XCircle } from 'lucide-react';
import { applySimulatedCorrectiveAction, getScenarioStatus, getSeededHistory, listScenarios, runScenario, stopScenario } from '../../api/client';
import { shortDateTime } from '../../lib/format';

const RUNNING = ['running', 'recovering', 'stopping'];

function Sheet({ side, title, onClose, children, label }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="hm-scrim" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <aside className={`hm-sheet ${side}`} role="dialog" aria-modal="true" aria-label={label || title}>
        <header><h2>{title}</h2><button type="button" className="hm-icon-btn" aria-label="Close" onClick={onClose}><X size={22} /></button></header>
        <div className="hm-sheet-body">{children}</div>
      </aside>
    </div>
  );
}

/** Opened from the logo. Starts the simulated incidents; never touches a PLC. */
export function ScenarioSidebar({ onClose, eventId }) {
  const [items, setItems] = useState(null);
  const [status, setStatus] = useState(null);
  const [speed, setSpeed] = useState(10);
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState({ text: '', bad: false });
  const poll = () => getScenarioStatus().then(setStatus).catch(() => setStatus(null));
  useEffect(() => {
    listScenarios().then(setItems).catch((e) => { setItems([]); setMsg({ text: e.message, bad: true }); });
    poll();
    const t = window.setInterval(poll, 2000);
    return () => window.clearInterval(t);
  }, []);
  const running = RUNNING.includes(status?.status);
  const act = async (key, fn, ok) => {
    setBusy(key); setMsg({ text: '', bad: false });
    try { await fn(); setMsg({ text: ok, bad: false }); } catch (e) { setMsg({ text: e.message, bad: true }); } finally { setBusy(''); poll(); }
  };
  return (
    <Sheet side="left" title="Scenarios" onClose={onClose} label="Scenario simulator">
      <p className="hm-sim-note">SIMULATED data for demonstration and training. Nothing here reads from or writes to a PLC.</p>
      <div className="hm-sim-status">
        <span>Simulator: <b>{status?.status || 'unknown'}</b>{status?.scenario_id ? ` · ${status.scenario_id}` : ''}</span>
        <label>Speed <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{[1, 5, 10, 20].map((v) => <option key={v} value={v}>{v}×</option>)}</select></label>
      </div>
      <div className="hm-sim-actions">
        <button type="button" className="hm-btn" disabled={!running || !!busy} onClick={() => act('stop', stopScenario, 'Simulation stopped.')}><Square size={18} /> Stop</button>
        <button type="button" className="hm-btn" disabled={!!busy || (!status?.scenario_id && !eventId)}
          title="Makes the simulated plant behave as if the fix was done. The operator still verifies and confirms."
          onClick={() => act('fix', () => applySimulatedCorrectiveAction(eventId ? { event_id: eventId } : {}), 'Simulated fix applied. Use Verify Status to check the readings.')}>
          <Wrench size={18} /> Simulate the fix</button>
      </div>
      {msg.text && <p role="status" className={msg.bad ? 'hm-alert' : 'hm-note'}>{msg.text}</p>}
      {!items && <p className="hm-muted"><Loader2 size={16} className="spin" /> Loading scenarios…</p>}
      <ul className="hm-sim-list">{(items || []).map((s) => (
        <li key={s.scenario_id} className={status?.scenario_id === s.scenario_id && running ? 'on' : ''}>
          <div><b>{s.number ? `${s.number}. ` : ''}{s.name}</b><small>{s.equipment_id} · {String(s.severity || '').toUpperCase()}</small></div>
          <button type="button" className="hm-btn primary" disabled={running || !!busy} aria-label={`Start ${s.name}`}
            onClick={() => act(s.scenario_id, () => runScenario(s.scenario_id, speed), `${s.name} started.`)}>
            {busy === s.scenario_id ? <Loader2 size={18} className="spin" /> : <Play size={18} />} Start</button>
        </li>))}</ul>
    </Sheet>
  );
}

/** Note, then resolved, unresolved or note only. Resolved waits for the recovery checks. */
export function ConfirmResolutionDialog({ eventId, title, operator, lifecycle, busy, error, onSubmit, onClose }) {
  const [note, setNote] = useState('');
  const [choice, setChoice] = useState(lifecycle?.can_resolve ? 'RESOLVE' : 'MARK_UNRESOLVED');
  const canResolve = Boolean(lifecycle?.can_resolve);
  useEffect(() => { if (!canResolve && choice === 'RESOLVE') setChoice('MARK_UNRESOLVED'); }, [canResolve, choice]);
  const save = { RESOLVE: 'Log as resolved', MARK_UNRESOLVED: 'Log as unresolved', LOG_EVENT: 'Log note only' }[choice];
  return (
    <div className="hm-scrim center" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <section className="hm-dialog" role="dialog" aria-modal="true" aria-labelledby="hm-resolve-title">
        <header><h2 id="hm-resolve-title">Confirm resolution</h2><button type="button" className="hm-icon-btn" aria-label="Close" disabled={busy} onClick={onClose}><X size={22} /></button></header>
        <p className="hm-muted">{title} · {eventId} · Operator <b>{operator}</b></p>
        <div className="hm-criteria-box">
          <h3>Recovery checks</h3>
          <ul className="hm-criteria">{(lifecycle?.criteria || []).map((c) => <li key={c.id} className={c.passed ? 'ok' : 'bad'}>{c.passed ? <CheckCircle2 size={18} /> : <XCircle size={18} />}<span>{c.label}{c.detail ? <small> — {c.detail}</small> : null}</span></li>)}</ul>
        </div>
        <fieldset className="hm-choice">
          <legend>Outcome</legend>
          <label className={!canResolve ? 'off' : ''}><input type="radio" name="outcome" value="RESOLVE" disabled={!canResolve} checked={choice === 'RESOLVE'} onChange={() => setChoice('RESOLVE')} /> Resolved
            {!canResolve && <small>Available once every recovery check passes</small>}</label>
          <label><input type="radio" name="outcome" value="MARK_UNRESOLVED" checked={choice === 'MARK_UNRESOLVED'} onChange={() => setChoice('MARK_UNRESOLVED')} /> Not resolved<small>Keeps the incident open</small></label>
          <label><input type="radio" name="outcome" value="LOG_EVENT" checked={choice === 'LOG_EVENT'} onChange={() => setChoice('LOG_EVENT')} /> Note only<small>Adds to the record, no change</small></label>
        </fieldset>
        <label htmlFor="hm-resolve-note" className="hm-field-label">Note (required)</label>
        <textarea id="hm-resolve-note" rows={3} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What was found and what was done?" />
        <small className="hm-muted">Records your decision only. OpsLens does not write to the PLC or control equipment.</small>
        {error && <p role="alert" className="hm-alert">{error}</p>}
        <footer><button type="button" className="hm-btn" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="button" className="hm-btn primary" disabled={busy || !note.trim()} onClick={() => onSubmit(choice, note)}>{busy ? 'Saving…' : save}</button></footer>
      </section>
    </div>
  );
}

/** Earlier incidents of the same alarm or equipment, from this database. */
/** Why there is no history yet: the simulated past month is still being added,
 *  it failed, or it was turned off. Rechecks while it runs. */
function HistoryHint() {
  const [state, setState] = useState(null);
  useEffect(() => {
    let live = true; let timer;
    const load = () => getSeededHistory().then((s) => {
      if (!live) return;
      setState(s);
      if (s?.run?.status === 'running') timer = window.setTimeout(load, 4000);
    }).catch(() => {});
    load();
    return () => { live = false; window.clearTimeout(timer); };
  }, []);
  if (!state) return null;
  const status = state.run?.status;
  let text;
  if (status === 'running') text = 'One month of simulated past incidents is being added now (about a minute). Close and reopen this panel when it is done.';
  else if (status === 'failed') text = `Adding the simulated past month failed: ${state.run.detail || 'see the backend window'}. Try Engineering Console (port 5174 on the OpsLens PC) > Scenario Lab > Past month of incidents > Add one month.`;
  else if (state.incidents > 0) text = `${state.incidents} simulated past incidents are stored, none with this alarm or equipment.`;
  else if (state.enabled_on_start === false) text = 'The simulated past month is turned off (OPSLENS_SEED_HISTORY=false). Add it in the Engineering Console (port 5174 on the OpsLens PC) > Scenario Lab > Past month of incidents.';
  else text = 'No simulated past month yet. Add it in the Engineering Console (port 5174 on the OpsLens PC) > Scenario Lab > Past month of incidents, or restart the backend.';
  return <small className="hm-history-hint">{text}</small>;
}

export function RelatedIncidents({ history, onOpen, onClose }) {
  const rows = history?.earlier || [];
  const date = (iso) => (iso ? shortDateTime(iso) : '—');
  return (
    <Sheet side="right" title="Related incidents" onClose={onClose}>
      {!history ? <p className="hm-muted">Loading…</p> : <>
        <dl className="hm-related-stats">
          <div><dt>Same alarm, last 30 days</dt><dd>{history.same_alarm_last_30d ?? history.same_alarm_total}</dd></div>
          <div><dt>Last 24 h</dt><dd>{history.same_alarm_last_24h}</dd></div>
          <div><dt>Average time to fix</dt><dd>{history.same_alarm_avg_minutes_to_resolve != null ? `${history.same_alarm_avg_minutes_to_resolve} min` : '—'}</dd></div>
        </dl>
        <p className="hm-muted">{history.same_equipment_total} earlier on the same equipment. {history.note || ''}</p>
      </>}
      <ul className="hm-related">{rows.map((r) => (
        <li key={r.event_id}><button type="button" onClick={() => onOpen(r.event_id)}>
          <b>{ALARM_LABELS[r.alarm] || r.alarm} · {r.equipment}</b>
          <span>{date(r.when)} · {r.same_alarm ? 'same alarm' : 'same equipment'} · {r.resolved ? `resolved${r.minutes_to_resolve != null ? ` in ${Math.round(r.minutes_to_resolve)} min` : ''}` : 'not resolved'}
            {r.seeded_history && <em className="hm-hist-tag" title="Seeded past-month history: simulated, not live plant data">simulated history</em>}</span>
          {(r.inspection_notes || []).slice(-1).map((n) => <small key={n}>Found: {n.replace(/^Field check:\s*/, '')}</small>)}
          {r.resolution_notes && <small>Fix: {r.resolution_notes}</small>}
        </button></li>))}
        {history && !rows.length && <li className="hm-muted">No earlier incident on record.<HistoryHint /></li>}</ul>
    </Sheet>
  );
}
