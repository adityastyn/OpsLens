import React from 'react';
import { ChevronRight, Loader2, RefreshCw, ShieldAlert } from 'lucide-react';
import { cleanLabel } from '../../lib/format';
import { SOURCE_STYLE, STAGE_TEXT, useDiagnosis } from '../../lib/useDiagnosis';

function Block({ title, children, open = true }) {
  return <details className="op-block" open={open}><summary>{title}</summary>{children}</details>;
}
const List = ({ rows }) => (rows?.length ? <ul>{rows.map((line) => <li key={line}>{cleanLabel(line)}</li>)}</ul> : <p className="op-muted">Nothing recorded.</p>);

function SopChecks({ result }) {
  const checks = result.sop_checks || [];
  const context = result.sop_context || {};
  if (!context.sop_id) return <p className="op-notice">{context.status_text}</p>;
  return (
    <>
      <p className="op-muted">Copied from {context.sop_id} ({context.approval_label}). {context.status_text}</p>
      <ol className="op-checks">{checks.map((c) => <li key={c.step} value={c.step}>{c.text} <small>p.{c.page}</small></li>)}</ol>
    </>
  );
}

/** The diagnosis as a readable vertical list. Every answer shows its true source. */
export default function DiagnosisCard({ eventId, refreshKey, compact = false, onOpenFull, scroll = false }) {
  const { draft, result, stage, error, loading, elapsed, retry } = useDiagnosis(eventId, refreshKey);
  const card = result?.operator_card;
  const style = SOURCE_STYLE[result?.source_status];
  const removed = result?.validation?.removed_lines || [];
  return (
    <section className="op-diagnosis" aria-label="Assessment" aria-busy={loading}>
      <header>
        <h3>Assessment</h3>
        {style && <span className={`op-source ${style.cls}`}>{style.text}</span>}
        <button type="button" className="op-retry" disabled={loading} onClick={retry}><RefreshCw size={13} /> Retry</button>
      </header>
      {!result && !error && (
        <div className="op-progress">
          <p><Loader2 size={15} className="spin" /> {STAGE_TEXT[stage] || 'Preparing the incident facts'} · {elapsed.toFixed(0)} s</p>
          {draft && <p className="op-draft">{draft.slice(-260)}</p>}
          <p className="op-muted">The alarm details remain available while this runs.</p>
        </div>
      )}
      {error && <p role="alert" className="op-notice bad">{error}</p>}
      {result && result.source_status !== 'ai_generated' && (
        <p className="op-notice"><ShieldAlert size={14} /> <span><b>Not AI-generated.</b> {result.fallback_reason} {result.fix_hint}</span></p>
      )}
      {card && <div className={scroll ? 'op-diag-scroll' : undefined} tabIndex={scroll ? 0 : undefined} aria-label={scroll ? 'Diagnosis text, scrollable' : undefined}>
        <Block title="What is happening?"><List rows={card.what} /></Block>
        <Block title="Why do we think this?"><List rows={card.why} /></Block>
        <Block title="What are the possible causes?"><List rows={card.causes} /><p className="op-muted">Possible causes are hypotheses until checked in the field.</p></Block>
        <Block title="What should the operator check?"><SopChecks result={result} />{card.advisory?.length > 0 && <><h4>Additional advisory checks</h4><List rows={card.advisory} /></>}</Block>
        <Block title="How is it fixed, and how do we know it is fixed?"><List rows={card.fix} /></Block>
        {compact
          ? <button type="button" className="op-text-link" onClick={onOpenFull}>Relevant SOP, missing evidence and uncertainty <ChevronRight size={13} /></button>
          : <>
            <Block title="Which SOP is relevant?"><p>{result.sop_context?.sop_id ? `${result.sop_context.sop_id} ${result.sop_context.title}. ${result.sop_context.approval_label}. Pages ${result.sop_context.pages.join(', ')}.` : result.sop_context?.status_text}</p>{result.sop_context?.covered?.length > 0 && <p>Covers: {result.sop_context.covered.join(', ')}.</p>}{[...(result.sop_context?.not_covered || []), ...(result.sop_context?.notes || [])].map((n) => <p key={n} className="op-muted">Not addressed: {n}</p>)}</Block>
            <Block title="What evidence is missing?"><List rows={card.missing} /></Block>
            <Block title="What remains uncertain?"><List rows={card.limits} /></Block>
            {(result.validation?.added_causes?.length > 0 || result.validation?.repaired_lines > 0) && <p className="op-muted">{result.validation.warnings.filter((w) => /corrected|added because/.test(w)).join(' ')}</p>}
            {removed.length > 0 && <Block title={`Lines removed by the record check (${removed.length})`} open={false}><ul>{removed.map((r) => <li key={r.line}>{r.line} <small>({r.problems.join('; ')})</small></li>)}</ul></Block>}
          </>}
      </div>}
      <p className="op-advisory">Advisory checks only. OpsLens does not control equipment.</p>
    </section>
  );
}
