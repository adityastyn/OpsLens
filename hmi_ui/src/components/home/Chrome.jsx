import { layoutViewportWidth, unzoom } from '../../lib/uiScale.js';
import React, { useEffect, useState } from 'react';
import wordmark from '../../assets/opslens-wordmark.png';
import { AlertTriangle, BookOpen, ChevronLeft, ChevronRight, ClipboardCheck, History, Home, ListChecks, MessageSquareText, ShieldCheck, UserRound } from 'lucide-react';
import { hhmm as fmtHhmm, hhmmss, ddmmyyyy } from '../../lib/format';
import { FitLine } from './Fit.jsx';

/** How the SOP was found, in two or three words: the search that found it, or
 *  "tag link" when search found nothing or ranked another SOP first. */
export function foundByText(found) {
  if (!found) return '';
  if (found.agrees && found.method === 'qdrant_vector_search') return ' · Qdrant';
  if (found.agrees && found.method === 'bm25') return ' · BM25';
  return ' · tag link';
}

export const TABS = ['Alarms', 'Incidents', 'Root Cause & Analysis', 'Time-Series & Prediction', 'Shifts History'];

const LEVEL_CLS = { High: 'hi', Medium: 'mid', Low: 'lo' };

/** One confidence for the leading hypothesis: High, Medium or Low. Tap for the reasons. */
// The backend's evidence score, read as given: High >= 75, Medium >= 45 (see incident_insight.confidence).
const STRENGTH = { High: 'Strong evidence', Medium: 'Moderate evidence', Low: 'Limited evidence' };

export function Confidence({ confidence, open, setOpen }) {
  if (!confidence) return null;
  const score = Number.isFinite(confidence.score) ? confidence.score : null;
  const strength = STRENGTH[confidence.level] || 'Limited evidence';
  return (
    <button type="button" className={`hm-conf hm-support ${LEVEL_CLS[confidence.level] || 'lo'}`} aria-expanded={open}
      aria-label={`Evidence support ${score ?? ''}%, ${strength}. Why this score?`} onClick={(e) => { e.stopPropagation(); setOpen(!open); }}>
      <span className="hm-support-top"><small>EVIDENCE SUPPORT</small><b className="hm-conf-pct">{score != null ? `${score}%` : '—'}</b></span>
      <span className="hm-support-bar" aria-hidden="true"><i style={{ width: `${score ?? 0}%` }} /></span>
      <span className="hm-support-word">{strength}</span>
    </button>
  );
}

function ConfidencePopover({ confidence, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const score = Number.isFinite(confidence.score) ? confidence.score : null;
  return (
    <div className="hm-conf-pop" role="dialog" aria-label="Evidence support">
      <header><b>Evidence support{score != null ? ` · ${score}%` : ''} · {STRENGTH[confidence.level] || 'Limited evidence'}</b><button type="button" className="hm-icon-btn" aria-label="Close" onClick={onClose}>×</button></header>
      {score != null && <>
        <div className="hm-conf-meter" aria-hidden="true"><span style={{ width: `${score}%` }} /><i style={{ left: '45%' }} /><i style={{ left: '75%' }} /></div>
        <p className="hm-conf-scale"><span>Limited</span><span>Moderate 45</span><span>Strong 75</span></p>
      </>}
      <p className="hm-support-def">Evidence Support is the weighted evidence available for the leading hypothesis. It is not a statistical probability that the root cause is correct.</p>
      {(confidence.flags || []).length > 0 && <ul className="flags">{confidence.flags.map((f) => <li key={f.flag}>{f.text}</li>)}</ul>}
      {(confidence.checks || []).length > 0 ? (
        <details className="hm-support-why">
          <summary>Why this score?</summary>
          <table className="hm-conf-checks"><tbody>{confidence.checks.map((c) => (
            <tr key={c.check} className={c.earned === c.points ? 'ok' : c.earned ? 'part' : 'no'}>
              <td>{c.earned === c.points ? '✓' : c.earned ? '◐' : '✗'}</td><td>{c.check}</td><td>{c.earned}/{c.points}</td></tr>))}
            <tr className="total"><td /><td>Evidence support</td><td>{score}/100</td></tr></tbody></table>
          <small>Points for each check the record meets. Separate from the cause ranking (evidence points per cause, on the Root Cause page) and from the urgency score.</small>
        </details>
      ) : <ul>{(confidence.reasons || []).map((r) => <li key={r}>{r.charAt(0).toUpperCase() + r.slice(1)}.</li>)}</ul>}
    </div>
  );
}

const hhmm = (iso) => (iso ? fmtHhmm(iso) : '—');
function timeLeft(endIso, now) {
  const ms = Date.parse(endIso) - now.getTime();
  if (!Number.isFinite(ms)) return '';
  if (ms <= 0) return 'ended';
  const h = Math.floor(ms / 3600000); const m = Math.round((ms % 3600000) / 60000);
  return h ? `${h} h ${m} min left` : `${m} min left`;
}
const countOf = (x) => (Array.isArray(x) ? x.length : Array.isArray(x?.items) ? x.items.length : 0);

/** The person icon opens the current shift: which shift, who is on duty, what
 *  is open, the last handover, and where the data and AI answers come from. */
function ShiftPanel({ operator, setOperator, open, setOpen, defaultName, handover, sourceTag, ai, clock, onOpenShifts }) {
  const initials = operator.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  const shift = handover?.current_shift;
  const last = handover?.latest_handover;
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, setOpen]);
  return (
    <div className="hm-operator">
      <button type="button" className="hm-icon-btn hm-person" aria-expanded={open} aria-label="Current shift and operator"
        title={`${shift?.name && shift.in_progress !== false ? `${shift.name} shift · ` : ''}Operator ${operator}`} onClick={() => setOpen(!open)}><UserRound size={18} /> {initials}</button>
      {open && <div className="hm-operator-pop hm-shift-pop" role="dialog" aria-label="Current shift">
        <header><b>{!shift?.configured ? 'Shift not configured' : shift.in_progress === false ? 'No shift in progress' : `${shift.name} shift`}</b>
          <button type="button" className="hm-icon-btn" aria-label="Close" onClick={() => setOpen(false)}>×</button></header>
        {!shift?.configured
          ? <p className="hm-muted">Set OPSLENS_SHIFTS (or OPSLENS_SHIFT_NAME, _START and _END) in edge_intelligence\.env.</p>
          : shift.in_progress === false
            ? <p className="hm-muted">{shift.name} shift ran {hhmm(shift.start)} – {hhmm(shift.end)} and next starts at {hhmm(shift.next_start)}.
                To cover every hour, set a rotation in OPSLENS_SHIFTS.</p>
            : <p className="hm-shift-time">{hhmm(shift.start)} – {hhmm(shift.end)} <span>· {timeLeft(shift.end, clock)}</span></p>}
        <label htmlFor="hm-operator-name">On duty</label>
        <input id="hm-operator-name" value={operator} maxLength={60} onChange={(e) => setOperator(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') setOpen(false); }}
          onBlur={() => { if (!operator.trim()) setOperator(defaultName); }} />
        <small>Acknowledgements, notes and resolutions are recorded with this name.</small>
        <dl className="hm-shift-counts">
          <div><dt>Open alarms</dt><dd>{countOf(handover?.open_alarms)}</dd></div>
          <div><dt>Not acknowledged</dt><dd>{countOf(handover?.unacknowledged_alarms)}</dd></div>
          <div><dt>Open incidents</dt><dd>{countOf(handover?.unresolved_incidents)}</dd></div>
        </dl>
        <p className="hm-shift-line"><span>Last handover</span>{last ? `${last.outgoing_operator || '—'} → ${last.incoming_operator || 'not yet received'} · ${hhmm(last.submitted_at)}` : 'None recorded for this shift'}</p>
        <button type="button" className="hm-btn" onClick={() => { setOpen(false); onOpenShifts(); }}>Shifts History ›</button>
      </div>}
    </div>
  );
}

/** Home: full bar with priorities, active alarms and the clock. Other pages: logo, tabs and a home icon. */
const ORDINAL = (n) => `${n}${n % 10 === 1 && n % 100 !== 11 ? 'st' : n % 10 === 2 && n % 100 !== 12 ? 'nd' : n % 10 === 3 && n % 100 !== 13 ? 'rd' : 'th'}`;

/** Why this incident is shown in this position: the rule that decided it against its neighbour. */
const dur = (secs) => (secs == null ? null : secs < 90 ? `${Math.round(secs)} s` : `${Math.round(secs / 60)} min`);
const hms = (ts) => { const d = new Date(ts); return Number.isNaN(d.getTime()) ? null : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }); };

/** One line of facts the ranking rules use, from the backend's values for this incident. */
function factsLine(f) {
  if (!f) return null;
  const parts = [];
  parts.push(f.live ? (f.active_alarms != null ? `${f.active_alarms} alarm${f.active_alarms === 1 ? '' : 's'} live` : 'alarms live') : 'all alarms back to normal');
  if (f.safety_rules?.length) parts.push(`safety rule: ${f.safety_rules.join('; ')}`);
  if (f.time_to_limit_seconds != null) parts.push(`limit ${f.target_limit != null ? `${f.target_limit} ` : ''}reached in ~${dur(f.time_to_limit_seconds)}`);
  parts.push(`${String(f.band || 'UNKNOWN').toLowerCase()}${f.score != null ? ` ${Math.round(f.score)}/100` : ''}`);
  if (hms(f.started)) parts.push(`opened ${hms(f.started)}`);
  return parts.join(' · ');
}

function RankWhy({ position, count, reason, order, currentId, onPick }) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(null);  // fixed position under the button: the alarm bar clips anything inside it
  const toggle = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    // Rects are in screen pixels; on a scaled-up panel convert back to layout pixels.
    setAt({ top: unzoom(r.bottom) + 8, left: Math.max(8, Math.min(layoutViewportWidth() - 468, unzoom(r.left + r.width / 2) - 230)) });
    setOpen((o) => !o);
  };
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [open]);
  if (!reason && !order?.length) return <span>{position}/{count}</span>;
  return (
    <div className="hm-rank-why">
      <b className="hm-rank-pos">{position}/{count}</b>
      <button type="button" className="hm-rank-btn" aria-expanded={open} aria-label={`Why is this incident ${ORDINAL(position)} of ${count}?`}
        title={`Why ${ORDINAL(position)}?`} onClick={toggle}>why?</button>
      {open && <div className="hm-rank-pop" role="dialog" aria-label="Why this order" style={at || undefined}>
        <h3>Why the incidents are in this order</h3>
        {order?.length ? <ol className="hm-rank-list">
          {order.map((o) => <li key={o.id} className={o.id === currentId ? 'now' : ''}>
            <button type="button" className="hm-rank-name" onClick={() => { onPick?.(o.id); setOpen(false); }}>
              <span className={`prio ${o.prio}`}>{o.prio}</span>{o.title}<small>{o.equipment}</small>{o.id === currentId && <em>viewing</em>}
            </button>
            <p className="facts">{factsLine(o.facts)}</p>
            {o.vsNext && <p className="vs">{o.vsNext}</p>}
          </li>)}
        </ol> : <p>{reason}</p>}
        <small>Rules, in order: live alarms first · safety rules · sooner to reach a limit · urgency band · urgency score · opened first. The first rule on which two incidents differ decides. Advisory only.</small>
        <button type="button" className="hm-btn" onClick={() => setOpen(false)}>Close</button>
      </div>}
    </div>
  );
}

export function TopBar({ home, page, setPage, onLogo, counts, activeCount, clock, sourceTag, ai, operatorProps, handover, line }) {
  const tabs = TABS.map((t) => (
    <button key={t} type="button" className="hm-tab" aria-current={page === t ? 'page' : undefined} onClick={() => setPage(t)}>{t}</button>
  ));
  const status = (
    <div className="hm-status">
      <ShiftPanel {...operatorProps} handover={handover} sourceTag={sourceTag} ai={ai} clock={clock} onOpenShifts={() => setPage('Shifts History')} />
    </div>
  );
  // The line under the top bar follows the incident being viewed: its priority
  // colour while open, green once resolved, dark blue if logged as unresolved.
  const state = `st-${line || 'ok'}`;
  if (!home) {
    return (
      <header className={`hm-top compact ${state}`} aria-label="Navigation">
        <button type="button" className="hm-logo small" onClick={onLogo} title="Open scenarios"><img src={wordmark} alt="OpsLens" /></button>
        <nav className="hm-tabs" aria-label="Sections">
          <button type="button" className="hm-tab home" aria-label="Home" title="Home" onClick={() => setPage('Home')}><Home size={22} /></button>
          {tabs}
        </nav>
        {status}
      </header>
    );
  }
  return (
    <header className={`hm-top ${state}`} aria-label="Plant status and navigation">
      <div className="hm-brand">
        <button type="button" className="hm-logo" onClick={onLogo} title="Open scenarios"><img src={wordmark} alt="OpsLens" /></button>
        <div className="hm-prio" aria-label="Active alarms by priority" title="Active alarms across all open incidents, by each alarm's configured priority: red = high/critical, orange = medium, yellow = low.">
          <span className="high">{counts.high} HIGH</span><span className="med">{counts.med} MED</span><span className="low">{counts.low} LOW</span>
        </div>
      </div>
      <div className="hm-active">
        <button type="button" className={`hm-active-btn ${activeCount ? 'on' : ''}`} onClick={() => setPage('Alarms')} title="Open the Alarms tab">
          <span>Active Alarms</span><b>{activeCount}</b>
        </button>
        <time className="hm-clock">{ddmmyyyy(clock)} {hhmmss(clock.toISOString())}</time>
      </div>
      <nav className="hm-tabs" aria-label="Sections">{tabs}</nav>
      {status}
    </header>
  );
}

/** One incident across the screen: what it is, what to check first, and the main actions. */
export function NotificationBar({ incident, confidence, component, sop, lifecycle, nav, busy, onAcknowledge, onDetails, onViewSop, onRelated, onResolve }) {
  const [why, setWhy] = useState(false);
  if (!incident) {
    return <section className="hm-notify empty" aria-label="Current incident"><ShieldCheck size={26} /><div><b>No active incident</b><span>New alarms appear here as soon as they are recorded. Open the scenarios from the logo to run a simulation.</span></div></section>;
  }
  // After the fix the incident is no longer an alert: a green tick, no priority or urgency.
  const settled = incident.resolved || incident.recovered;
  return (
    <section className={`hm-notify ${settled ? 'settled' : `${incident.sev.cls} prio-${incident.prio || 'high'}`}`} aria-label="Current incident">
      {settled ? <div className="hm-sev ok" title={incident.resolved ? 'The operator confirmed the resolution.' : 'Every alarm input is back to normal. Confirm the resolution after checking in the field.'}>
        <ShieldCheck size={26} /><span className="hm-sev-resolved">INCIDENT RESOLVED</span></div>
      : <div className="hm-sev"><AlertTriangle size={24} /><span>{incident.sev.label}</span>
        {incident.urgency && <b className="hm-urgency" title={`Urgency ${incident.urgency.score}/100 (${incident.urgency.band}). ${incident.urgency.reasons.join('. ')}. Rule-based score, not a probability.`}>
          <small>URGENCY</small>{incident.urgency.score}<i>/100</i></b>}</div>}
      <div className="hm-title">
        <FitLine as="h1" text={incident.title} min={14} />
        <p title={incident.alarmTag}>{incident.equipment}{incident.startedAt && <time className="hm-started" dateTime={incident.startedAt} title="When the first alarm of this group came on">· {hms(incident.startedAt)}</time>}</p>
        <p className="hm-sub"><b>{incident.alarmCount} alarm{incident.alarmCount === 1 ? '' : 's'}{settled ? ' cleared' : ''}</b> ×{incident.occurrences}
          {incident.resolved ? <span className="hm-state resolved">Resolved by the operator</span>
            : lifecycle && <span className={`hm-state ${incident.recovered ? 'recovered' : String(lifecycle.state).toLowerCase()}`} title={lifecycle.label}>{lifecycle.label}</span>}</p>
      </div>
      {nav.count > 1 && <div className="hm-nav" aria-label="Switch incident">
        <button type="button" className="hm-icon-btn" aria-label="Previous incident" onClick={nav.prev}><ChevronLeft size={22} /></button>
        <RankWhy position={nav.index + 1} count={nav.count} reason={nav.reason} order={nav.order} currentId={nav.currentId} onPick={nav.pick} />
        <button type="button" className="hm-icon-btn" aria-label="Next incident" onClick={nav.next}><ChevronRight size={22} /></button>
      </div>}
      {settled && !incident.resolved && incident.canResolve && <button type="button" className="hm-btn ok" disabled={busy} onClick={onResolve}
        title="All alarm inputs are back to normal and the recovery checks pass. Confirm the resolution after checking in the field.">Confirm Resolution</button>}
      {lifecycle?.can_acknowledge && <button type="button" className={`hm-btn ${settled ? '' : 'ack'}`} disabled={busy} onClick={onAcknowledge}
        title="Records the acknowledgement in OpsLens. Acknowledge the plant alarm on the SCADA/HMI alarm system as usual: OpsLens is not connected to it and never writes to the PLC.">Acknowledge</button>}
      <button type="button" className="hm-btn" onClick={onDetails}>View Details</button>
      <div className="hm-info check" title={component?.reason}>
        <span>CHECK FIRST</span>
        <b>{component?.name || '—'}</b>
        <Confidence confidence={confidence} open={why} setOpen={setWhy} />
      </div>
      <button type="button" className="hm-info as-btn" disabled={!sop?.sop_id} onClick={onViewSop} aria-label={sop?.sop_id ? `View Suggested SOP ${sop.sop_id}` : 'No suggested SOP'} title={sop?.title ? `${sop.title}. ${sop.match_label || ''}${sop.matched_on?.length ? `: matches ${sop.matched_on.join(', ').toLowerCase()}` : ''}.${sop.found_by?.note ? ` ${sop.found_by.note}` : ''} Tap to view the SOP.` : (sop?.found_by?.note || 'No SOP matched this incident')}>
        <span>SUGGESTED SOP</span>
        <b>{sop?.sop_id || 'None found'}</b>
        <small>{sop?.sop_id && /DRAFT/.test(sop.status || '') ? <em>DRAFT</em> : null}{sop?.sop_id ? `${sop.match_label || 'View SOP'}${foundByText(sop.found_by)} ›` : ''}</small>
      </button>
      <button type="button" className="hm-btn" onClick={onRelated}>Related Incidents</button>
      {why && confidence && <ConfidencePopover confidence={confidence} onClose={() => setWhy(false)} />}
    </section>
  );
}

/** On every screen. */
export function BottomBar({ page, onVerify, onSops, onChat, onResolve, onShift, busy, hasIncident, chatOpen }) {
  return (
    <footer className="hm-bottom" aria-label="Main actions">
      <button type="button" className="hm-bbtn" disabled={!hasIncident || busy} onClick={onVerify}><ListChecks size={22} /> Verify Status</button>
      <button type="button" className="hm-bbtn" aria-pressed={page === 'SOPs'} onClick={onSops}><BookOpen size={22} /> SOPs</button>
      <button type="button" className="hm-bbtn" aria-pressed={chatOpen} disabled={!hasIncident} onClick={onChat}><MessageSquareText size={22} /> Ask Chatbot</button>
      <button type="button" className="hm-bbtn" disabled={!hasIncident || busy} onClick={onResolve}><ClipboardCheck size={22} /> Confirm Resolution</button>
      <button type="button" className="hm-bbtn" aria-pressed={page === 'Shifts History'} onClick={onShift}><History size={22} /> Previous Shift Details</button>
    </footer>
  );
}
