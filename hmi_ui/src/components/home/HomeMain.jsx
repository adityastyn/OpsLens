import React, { useEffect, useState } from 'react';
import { AlertTriangle, ArrowDown, ArrowUp, Check, Loader2, RefreshCw } from 'lucide-react';
import { ALARM_LABELS } from '../../lib/plantTags.generated.js';
import { cleanLabel, fmtValue, hhmmss } from '../../lib/format';
import { SOURCE_STYLE, STAGE_TEXT } from '../../lib/useDiagnosis';
import { getTelemetrySeries } from '../../api/client';
import { FitBody, MoreSheet, Paged } from './Fit';
import { storyLine, storySummary, withRecordedTrips } from '../../lib/eventStory.js';
import { deviation } from '../../lib/deviation.js';
import { mergeFixes, fixWhen } from '../../lib/mergeFixes.js';
import { stepSource } from '../../lib/planSource.js';

function Panel({ title, extra, children, className = '' }) {
  return (
    <section className={`hm-panel ${className}`} aria-label={title}>
      <header><h2>{title}</h2>{extra}</header>
      <div className="hm-panel-body">{children}</div>
    </section>
  );
}

const KEY_KINDS = new Set(['observation', 'alarm_state']);

// The headline is the cause itself: drop the AI's labels ("HYPOTHESIS:", "Confidence:",
// "Most likely cause:", "(leading hypothesis)") that come with the line.
const LABEL = /^(?:\s*(?:CONFIRMED|HYPOTHESIS|UNKNOWN|GENERAL ADVICE|Confidence(?: and limitations)?|Most likely(?: root)? cause|Likely cause|Leading hypothesis)\s*[:\u2013\u2014-]\s*)+/i;
const strip = (line) => {
  const t = cleanLabel(String(line || '').replace(LABEL, '').replace(/\s*\((?:the )?leading hypothesis\)\s*\.?$/i, '.'));
  return t.charAt(0).toUpperCase() + t.slice(1);
};

/** "Most likely" line from the AI's possible causes, or its first one. Used only
 *  for answers stored before the backend checked the leading hypothesis. */
function likelyCause(card) {
  const causes = card?.causes || [];
  const limits = card?.limits || [];
  const named = [...causes, ...limits].find((l) => /most likely|likeliest|leading hypothesis|probabl/i.test(l));
  return strip(named || causes[0] || '');
}

/** The leading hypothesis every screen shows: the AI's line when it names the
 *  record's leading cause, otherwise the record's own sentence. */
export function leadingHypothesis(result, insight) {
  const ai = result?.source_status === 'ai_generated';
  const lead = result?.leading;
  if (lead?.show) return { text: strip(lead.show), source: lead.source, note: lead.note };
  if (insight?.leading_hypothesis) return { text: insight.leading_hypothesis, source: 'record', note: null };
  if (ai) { const text = likelyCause(result?.operator_card); return text ? { text, source: 'ai', note: null } : null; }
  return null;
}

const ROLE_TEXT = { leading: 'LEADING', contributing: 'CONTRIBUTING', chain: 'RELATED CONDITION', effect: 'POSSIBLE CONSEQUENCE', insufficient: 'INSUFFICIENT EVIDENCE' };
export const causeName = (c) => c.short || c.statement.split(':')[0].split('. ')[0].split(', as a cause')[0];

/** The assessment: the leading hypothesis (the same one every screen shows),
 *  the AI's why and what is unknown, then what the record shows.
 *  No scrolling: what does not fit is counted on a "+N more" button and opens in full. */
function Assessment({ brief, insight, timeline, diagnosis, onFullStory }) {
  const { result, draft, stage, error, loading, elapsed, retry } = diagnosis;
  const style = SOURCE_STYLE[result?.source_status];
  const ai = result?.source_status === 'ai_generated';
  const card = result?.operator_card || {};
  const lead = leadingHypothesis(result, insight);
  const why = ai ? (card.why || []).map(strip).filter(Boolean).slice(0, 3) : [];
  const unknown = ai ? (card.missing || []).map(strip).filter(Boolean).slice(0, 2) : [];
  const removed = (result?.validation?.removed_lines || []).length;
  const problem = brief?.problem || {};
  const chips = (brief?.incident_readings || []).filter((r) => r.state_at_worst === 'LIMIT EXCEEDED' || r.state_now === 'LIMIT EXCEEDED').slice(0, 2);
  const story = (timeline?.events || []).filter((e) => KEY_KINDS.has(e.kind)).slice(0, 5);
  const summary = storySummary(withRecordedTrips((timeline?.events || []).filter((e) => KEY_KINDS.has(e.kind)).slice(0, 8), insight?.cause_candidates));
  const blocks = [
    // The alarm and equipment are already in the notification bar above.
    !result && !error && <p key="wait" className="hm-muted"><Loader2 size={14} className="spin" /> {STAGE_TEXT[stage] || 'Preparing the AI assessment'} · {elapsed.toFixed(0)} s{draft ? ' · writing' : ''}</p>,
    error && <p key="err" className="hm-alert clamp">{error}</p>,
    result && !ai && <p key="not" className="hm-note clamp" style={{ '--lines': 3 }}>Not AI-generated. {result.fallback_reason}</p>,
    lead && <div key="cause"><p className="dx-k">LEADING HYPOTHESIS <small>· {lead.source === 'ai' ? 'AI, agrees with the record' : 'from the record'}</small></p>
      <p className="dx-cause clamp" style={{ '--lines': 3 }}>{lead.text}</p>
      {lead.note && <p className="dx-lead-note clamp" style={{ '--lines': 2 }}>{lead.note}</p>}</div>,
    summary && <div key="sum"><p className="dx-k record">WHAT HAPPENED</p><p className="dx-story clamp" style={{ '--lines': 3 }}>{summary}</p></div>,
    ...why.map((l, i) => <div key={`why${i}`} className="dx-li">{i === 0 && <p className="dx-k">WHY</p>}<p className="clamp">• {l}</p></div>),
    ...unknown.map((l, i) => <div key={`unk${i}`} className="dx-li unknown">{i === 0 && <p className="dx-k">STILL UNKNOWN</p>}<p className="clamp">• {l}</p></div>),
    removed > 0 && <p key="removed" className="hm-muted clamp">{removed} AI statement{removed === 1 ? '' : 's'} removed: they could not be checked against the record.</p>,
    chips.length > 0 && <div key="chips"><p className="dx-k record">FROM THE RECORD</p><div className="dx-chips">{chips.map((r) => (
      <div key={r.tag} className="dx-chip"><b>{r.label} {fmtValue(r.worst_value)} {r.unit}</b>
        <small>normal {r.normal_low != null && r.normal_high != null ? `${fmtValue(r.normal_low)}–${fmtValue(r.normal_high)} ${r.unit}` : (r.normal_text || '—')}</small></div>))}</div></div>,
    ...story.map((e, i) => {
      const line = storyLine(e);
      return <div key={`st${i}`} className={`dx-tl-row ${line.alarm ? 'alarm' : ''} ${i === 0 ? 'first' : ''}`}>
        <time>{hhmmss(e.timestamp)}</time><span className="clamp" style={{ '--lines': 1 }}>{line.alarm && <span className="tag">ALARM</span>}{line.text}{i === 0 && <em className="start">← started here</em>}</span></div>;
    }),
  ];
  return (
    <Panel title="Assessment" className="diag"
      extra={<>{style && <span className={`hm-src-label ${style.cls}`}>{style.text}</span>}
        <button type="button" className="hm-mini" disabled={loading} onClick={retry} title="Ask the AI again" aria-label="Retry the AI assessment"><RefreshCw size={14} /></button>
        <button type="button" className="hm-mini" onClick={onFullStory}>Full assessment ›</button></>}>
      <FitBody title="Assessment" moreText="more" signature={`${result?.facts_hash || ''}${lead?.text}${why.length}${story.length}${error}${stage}`}>{blocks}</FitBody>
    </Panel>
  );
}

/** Evidence → possible cause → SOP step, for the top cause. */
function MiniGraph({ candidate, fixStep, sopId }) {
  if (!candidate) return null;
  const evidence = (candidate.supporting || []).slice(0, 2).map((s) => s.replace(/ reached .*| turned on .*| stayed .*/, '').replace(/^[A-Z]+-[A-Z0-9-]+ /, '')).filter((e, i, all) => all.indexOf(e) === i);
  const W = 420; const rowH = 28; const H = Math.max(evidence.length, 1) * rowH + 6;
  const midY = H / 2;
  const box = (x, y, w, text, cls) => (
    <g className={`hm-g ${cls}`} key={`${cls}-${y}-${text}`}>
      <rect x={x} y={y - 12} width={w} height={24} rx="4" />
      <text x={x + 6} y={y + 5}><title>{text}</title>{text.length > 17 ? `${text.slice(0, 16)}…` : text}</text>
    </g>
  );
  const cause = causeName(candidate);
  return (
    <svg className="hm-graph" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Evidence ${evidence.join(', ')} supports ${cause}${fixStep ? `, addressed by SOP step ${fixStep.step}` : ''}`}>
      {evidence.map((_, i) => <path key={`l${i}`} className="hm-g-line hyp" d={`M134 ${i * rowH + 17} C 146 ${i * rowH + 17}, 142 ${midY}, 154 ${midY}`} />)}
      {fixStep && <path className="hm-g-line sop" d={`M290 ${midY} L302 ${midY}`} />}
      {evidence.map((e, i) => box(2, i * rowH + 17, 132, e, 'ev'))}
      {box(154, midY, 136, cause, 'cause')}
      {fixStep && box(302, midY, 116, `SOP step ${fixStep.step}`, 'fix')}
    </svg>
  );
}

function RootCause({ insight, onMore }) {
  const candidates = insight?.cause_candidates || [];
  const top = candidates[0];
  const fix = insight?.sop?.sop_id ? (insight?.bluf?.top_steps?.find((s) => s.step !== 1) || insight?.bluf?.top_steps?.[0]) : null;
  const blocks = !insight ? [<p key="l" className="hm-muted">Loading…</p>]
    : !top ? [<p key="n" className="hm-muted">The record does not point to a likely cause.</p>]
      // The evidence → cause → SOP step graph comes first: placed after the causes it was always the block pushed out of the panel.
      : [<MiniGraph key="g" candidate={top} fixStep={fix} sopId={insight.sop?.sop_id} />,
        ...candidates.slice(0, 4).map((c, i) => <div key={c.rank} className={`rc-item ${c.role || (i ? 'other' : '')}`}>
          <p className="clamp rc-cause" style={{ '--lines': 1 }} title={c.role_note || c.basis}><span className={`rc-role ${c.role || ''}`}>{ROLE_TEXT[c.role] || (i ? 'ALTERNATIVE' : 'LEADING')}</span>{causeName(c)}</p>
          <p className="rc-basis">{c.role_note || c.basis}</p></div>),
        <p key="h" className="hm-muted">Hypotheses until checked in the field.</p>];
  return (
    <Panel title="Root-Cause Evidence" className="rc" extra={<button type="button" className="hm-mini" onClick={onMore}>More ›</button>}>
      <FitBody title="Root-Cause Evidence" moreText="more" signature={`${insight?.event_id}${candidates.length}`}>{blocks}</FitBody>
    </Panel>
  );
}

const ALARM_STATE = (a) => (a.active ? (a.acknowledged ? ['ack', 'Active, acknowledged'] : ['unack', 'Active, not acknowledged']) : (a.acknowledged ? ['ok', 'Normal'] : ['unack-ok', 'Normal, not acknowledged']));

function RelatedAlarms({ lifecycle, onViewAll }) {
  const alarms = lifecycle?.alarm_inputs || [];
  return (
    <Panel title="Related Alarms" className="ra" extra={<button type="button" className="hm-mini" onClick={onViewAll}>All alarms ›</button>}>
      <FitBody title="Related alarms" moreText="alarms" signature={alarms.map((a) => `${a.tag}${a.active}${a.acknowledged}`).join()}>
        {alarms.map((a) => { const [cls, text] = ALARM_STATE(a); return (
          <div key={a.alarm_id || a.tag} className={`hm-alarm ${cls}`} title={`${a.tag}: ${text}`}>
            <div><b className="clamp" style={{ '--lines': 1 }}>{ALARM_LABELS[a.tag] || a.tag}</b><small>{a.tag}</small></div><span>{text}</span>
          </div>); })}
        {!alarms.length && <p className="hm-muted">No alarms recorded for this incident.</p>}
      </FitBody>
    </Panel>
  );
}

const draftTag = (status) => (/DRAFT/.test(status || '') ? ' (DRAFT)' : '');
const KIND_CLS = { CHECK: 'check', ADJUST: 'adjust', CALL: 'call', VERIFY: 'verify' };
const NUM_RE = /(\d[\d,]*(?:\.\d+)?\s*(?:%|mg\/L|m³\/h|m³\/d|m³|mbar|bar|kPa|cm|mm|m|A|rpm|days?|h|min|°C|g\/L|NTU|mW\/cm²|mJ\/cm²)(?![A-Za-z])|\d[\d,]*(?:\.\d+)?)/g;

/** Numbers and limits in bold; the words themselves are unchanged. */
function Emph({ text }) {
  const parts = String(text || '').split(NUM_RE);
  return <>{parts.map((p, i) => (i % 2 ? <b key={i}>{p}</b> : p))}</>;
}

/** First sentence bold, the rest smaller. Split at sentence ends only, never reworded. */
function Sentences({ text }) {
  const [first, ...rest] = String(text || '').replace(/\.\s+(?=[A-Z])/g, '.\n\u0000').split('\n\u0000'); // no look-behind: older panel browsers reject it
  return <><p className="main"><Emph text={first} /></p>{rest.length > 0 && <p className="more"><Emph text={rest.join(' ')} /></p>}</>;
}

/**
 * One plan step. In the panel (`expanded` false) the text is clamped to fit and the exact
 * SOP text opens in a pop-up. In the expanded view nothing is clamped: the full text, every
 * recorded tag and the exact SOP text are shown in place (no pop-up on top of a pop-up).
 */
function PlanStep({ step, plan, done, onToggle, expanded = false }) {
  const [open, setOpen] = useState(false);
  const src = stepSource(step, plan);
  const sopLabel = step.sop_step ? `SOP step ${step.sop_step}${step.page ? ` · p.${step.page}` : ''}` : '';
  return (
    <li className={done ? 'done' : ''}>
      <button type="button" className="n" aria-pressed={done} aria-label={done ? `Step ${step.order} done. Tap to undo` : `Mark step ${step.order} done`} onClick={onToggle}>
        {done ? <Check size={18} /> : step.order}</button>
      <div>
        <div className="top">
          <span className={`kind ${KIND_CLS[step.kind] || 'check'}`}>{step.kind}</span>
          {src && <span className={`kind src src-${src.cls}`} data-source={src.label} title={src.note}>{src.label}</span>}
          {src?.ref && step.source !== 'sop' && <span className="src-ref" title={src.note}>{src.ref}</span>}
          {step.sop_step && src && !expanded && <button type="button" className="ref" onClick={() => setOpen(true)} title="Show the exact SOP text">
            {sopLabel} ›</button>}
          {step.sop_step && src && expanded && <span className="ref-inline">{sopLabel}</span>}
        </div>
        <Sentences text={step.text.charAt(0).toUpperCase() + step.text.slice(1)} />
        {expanded && src?.note && step.source === 'evidence' && <p className="src-note ev">{src.note}</p>}
        {step.source === 'history' && src && <p className="src-note">{src.note}</p>}
        {(step.warnings || []).map((w) => <div key={w} className="warn"><AlertTriangle size={15} /> <Emph text={w} /></div>)}
        {expanded && step.sop_text && <>
          <blockquote className="sop-text"><small>Exact text from the SOP, word for word</small>{step.sop_text}</blockquote>
          {step.source === 'sop' && !step.verbatim && <p className="hm-muted">In the plan, the AI wrote this step as: “{step.text}”</p>}
        </>}
        {open && !expanded && <MoreSheet title={`SOP step ${step.sop_step}${step.page ? `, page ${step.page}` : ''}`} onClose={() => setOpen(false)}>
          <p className="hm-muted">Exact text from the SOP, word for word.</p><blockquote className="sop-text">{step.sop_text}</blockquote>
          {step.source === 'sop' && !step.verbatim && <p className="hm-muted">In the plan, the AI wrote this step as: “{step.text}”</p>}</MoreSheet>}
      </div>
    </li>
  );
}

/** An ordered plan. With an SOP: the AI's plan, each step linked to its SOP step.
 *  Without one: earlier fixes, then the AI's own suggestions, clearly labelled. */
function NextBestAction({ eventId, insight, diagnosis }) {
  const { result } = diagnosis;
  const [done, setDone] = useState({});
  const [expanded, setExpanded] = useState(false);
  useEffect(() => { setDone({}); setExpanded(false); }, [eventId]);
  const plan = result?.plan;
  const earlier = (insight?.history?.earlier || []).filter((e) => e.resolution_notes);
  let steps = plan?.steps || [];
  // What the step labels are checked against: the plan's SOP, or the insight's while the AI plan is being written.
  const sopOf = plan || { sop_id: insight?.sop?.sop_id, sop_status: insight?.sop?.status, sop_approved: insight?.sop?.approved === true };
  let line;
  if (plan?.basis === 'ai_plan') line = <>AI plan based on <b>{plan.sop_id}</b>{draftTag(plan.sop_status)}</>;
  else if (plan?.basis === 'sop_steps') line = <>Steps from <b>{plan.sop_id}</b>{draftTag(plan.sop_status)}, word for word{result?.source_status === 'ai_generated' ? '' : ' (AI not available)'}</>;
  else if (plan?.basis === 'no_sop' && earlier.length) {
    // One step per distinct fix: the same note on several past incidents is one fix done several times.
    const fixes = mergeFixes(earlier);
    const shown = fixes.slice(0, 3);
    // This incident's own evidence first, then how earlier incidents were fixed, then the rest.
    const own = steps.filter((s) => s.source === 'evidence' && s.kind !== 'VERIFY');
    const rest = steps.filter((s) => !own.includes(s));
    steps = [...own, ...shown.map((f) => ({ kind: 'CHECK', source: 'history', text: `${f.text} (${fixWhen(f)})`, history_events: f.events, history_when: f.when[0], history_simulated: f.simulated })), ...rest]
      .map((s, i) => ({ ...s, order: i + 1 }));
    line = <>No SOP found · from how earlier incidents were fixed{steps.some((s) => s.source === 'ai') ? ', then AI suggestions' : ''}</>;
  } else if (plan?.basis === 'no_sop') line = <>No SOP found · AI suggestions from the evidence</>;
  else if (!result) {
    steps = (insight?.bluf?.top_steps || []).map((s, i) => ({ order: i + 1, kind: 'CHECK', source: 'sop', sop_step: s.step, page: s.page, text: s.text }));
    line = <>From {insight?.sop?.sop_id || 'the SOP'}{draftTag(insight?.sop?.status)} · the AI plan is still being written</>;
  }
  const toggle = (order) => setDone((d) => ({ ...d, [order]: !d[order] }));
  const doneCount = steps.filter((s) => done[s.order]).length;
  return (
    <Panel title="Next Best Action" className="nba"
      extra={steps.length > 0 && <button type="button" className="hm-mini nba-expand" onClick={() => setExpanded(true)}
        aria-haspopup="dialog" title="Show every step in full, nothing cut off">Expand ›</button>}>
      {line && <p className="hm-source">{line}</p>}
      {plan?.basis === 'no_sop' && <p className="esc">Escalate to the shift supervisor: no SOP covers this alarm.</p>}
      {steps.length > 0 ? <Paged items={steps} signature={`${eventId}${steps.length}${plan?.basis}`} label="Steps"
        renderItem={(s) => <PlanStep key={`${s.source}-${s.sop_step ?? s.text}`} step={s} plan={sopOf}
          done={Boolean(done[s.order])} onToggle={() => toggle(s.order)} />} />
        : <p className="hm-muted">{result ? 'No step could be checked against the record. Escalate to an authorized engineer.' : 'Waiting for the AI diagnosis…'}</p>}
      <p className="hm-advisory">Tap a number when done (not sent anywhere). Then press <b>Verify Status</b>. Advisory only.</p>
      {expanded && steps.length > 0 && <MoreSheet title={`Next Best Action · all ${steps.length} steps`} onClose={() => setExpanded(false)}>
        {line && <p className="hm-source">{line}</p>}
        {plan?.basis === 'no_sop' && <p className="esc">Escalate to the shift supervisor: no SOP covers this alarm.</p>}
        <ol className="nba-full">
          {steps.map((s) => <PlanStep key={`${s.source}-${s.sop_step ?? s.text}`} step={s} plan={sopOf} expanded
            done={Boolean(done[s.order])} onToggle={() => toggle(s.order)} />)}
        </ol>
        <p className="hm-advisory">{doneCount} of {steps.length} steps marked done (not sent anywhere). Then press <b>Verify Status</b>. Advisory only.</p>
      </MoreSheet>}
    </Panel>
  );
}

/** One reading as a bar: dark grey band = normal range, ▼ = value now (green inside normal, amber outside, red past a limit). */
function Indicator({ r, series }) {
  const now = Number(r.now_value);
  // The bar shows only the normal range (dark grey) and the value now (▼), so it is scaled to those.
  const values = [r.normal_low, r.normal_high, now].map(Number).filter(Number.isFinite);
  const lo = Math.min(...values); const hi = Math.max(...values); const pad = (hi - lo || Math.abs(hi) || 1) * 0.12;
  const min = lo - pad; const max = hi + pad;
  const pos = (v) => `${Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100))}%`;
  // How far from the threshold: past an alarm limit, outside normal, or the margin left.
  const dev = deviation({ now, normalLow: r.normal_low, normalHigh: r.normal_high, limits: series?.limits || [], unit: r.unit });
  const stopped = dev?.kind === 'stopped';  // 0 A / 0 flow: not running, shown in grey
  const alarm = r.state_now === 'LIMIT EXCEEDED' && !stopped;
  const off = !alarm && !stopped && ((r.normal_high != null && now > r.normal_high) || (r.normal_low != null && now < r.normal_low));
  return (
    <div className={`ind ${alarm ? 'alm' : off ? 'off' : stopped ? 'stopped' : 'ok'}`} title={`${r.tag}. Normal ${r.normal_text || '—'}. Worst ${fmtValue(r.worst_value)} ${r.unit}.${dev ? ` ${dev.text}.` : ''}`}>
      <div className="ind-name clamp" style={{ '--lines': 2 }}>{r.label}</div>
      <div className="ind-gauge">
        <div className="ind-bar" aria-hidden="true">
          {r.normal_low != null && r.normal_high != null && <span className="nb" style={{ left: pos(Number(r.normal_low)), width: `calc(${pos(Number(r.normal_high))} - ${pos(Number(r.normal_low))})` }} />}
          {Number.isFinite(now) && <span className="pt" style={{ left: pos(now) }} />}
        </div>
      </div>
      <div className="ind-right">
        <div className="ind-val">{alarm || off ? (now > Number(r.normal_high) ? <ArrowUp size={16} /> : <ArrowDown size={16} />) : null}{fmtValue(r.now_value)}<small>{r.unit}</small></div>
        {dev && <div className={`ind-dev ${dev.kind}`}>{dev.signed || dev.short}</div>}
      </div>
    </div>
  );
}

function Readings({ rows, onTrends }) {
  const [series, setSeries] = useState({});
  const tags = (rows || []).slice(0, 6).map((r) => r.tag);
  const key = tags.join();
  useEffect(() => {
    let live = true;
    tags.forEach((tag) => getTelemetrySeries(tag, { minutes: 30 }).then((d) => live && setSeries((s) => ({ ...s, [tag]: d }))).catch(() => {}));
    return () => { live = false; };
  }, [key, rows?.[0]?.now_value]);
  return (
    <Panel title="Important Readings" className="rd" extra={<><span className="ind-key" title="Dark grey band = normal range · ▼ = value now (green inside the normal range, amber outside it, red past an alarm limit) · under the value: how far past the limit (or outside normal)">▼ now · ▬ normal</span><button type="button" className="hm-mini" onClick={onTrends}>Trends ›</button></>}>
      <FitBody title="Important readings" moreText="readings" signature={`${key}${Object.keys(series).length}`}>
        {(rows || []).slice(0, 6).map((r) => <Indicator key={r.tag} r={r} series={series[r.tag]} />)}
        {!rows?.length && <p className="hm-muted">No readings recorded for this incident.</p>}
      </FitBody>
    </Panel>
  );
}

export default function HomeMain({ brief, insight, lifecycle, timeline, diagnosis, onFullStory, onViewAlarms, onRootCause, onTrends, banner }) {
  return (
    <main className="hm-main" id="home">
      {banner}
      <div className="hm-cols">
        <div className="hm-col c1">
          <Assessment brief={brief} insight={insight} timeline={timeline} diagnosis={diagnosis} onFullStory={onFullStory} />
          <RootCause insight={insight} onMore={onRootCause} />
        </div>
        <div className="hm-col c2">
          <NextBestAction eventId={brief?.event_id || insight?.event_id} insight={insight} diagnosis={diagnosis} />
        </div>
        <div className="hm-col c3">
          <RelatedAlarms lifecycle={lifecycle} onViewAll={onViewAlarms} />
          <Readings rows={brief?.incident_readings} onTrends={onTrends} />
        </div>
      </div>
    </main>
  );
}
