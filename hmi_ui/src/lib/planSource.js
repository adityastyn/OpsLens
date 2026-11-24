/**
 * Where a Next Best Action step comes from, as one label the operator can trust.
 * Built only from the step's own provenance fields; it never changes the step,
 * its order or its wording, and never adds an SOP reference that is not there.
 *
 *   TELEMETRY EVIDENCE            a check resting on recorded tags of this incident
 *   HISTORICAL RESOLUTION         how an earlier incident was resolved (not guaranteed, not an SOP instruction)
 *   SOP-VERIFIED                  an SOP step with a reference, from an APPROVED SOP
 *   SOP DRAFT · NOT APPROVED      the same, from a draft SOP (never shown as verified)
 *   AI-GENERATED · NOT SOP-VERIFIED   the AI's own step, not supported by a retrieved SOP
 *   OPERATOR VERIFICATION         the check that readings are back within their normal range
 *
 * Returns null when the source cannot be identified (e.g. an evidence check whose
 * tags are not in the record): no label is better than a misleading one.
 */
export const SOURCE = {
  telemetry: { label: 'TELEMETRY EVIDENCE', cls: 'ev' },
  history: { label: 'HISTORICAL RESOLUTION', cls: 'hist' },
  sop: { label: 'SOP-VERIFIED', cls: 'sop' },
  sopDraft: { label: 'SOP DRAFT · NOT APPROVED', cls: 'draft' },
  ai: { label: 'AI-GENERATED · NOT SOP-VERIFIED', cls: 'ai' },
  verify: { label: 'OPERATOR VERIFICATION', cls: 'verify' },
};

const list = (items, n = 3) => {
  const xs = (items || []).filter(Boolean);
  return xs.length > n ? `${xs.slice(0, n).join(' · ')} +${xs.length - n}` : xs.join(' · ');
};

/** @param plan {sop_id, sop_status, sop_approved}: the plan the step belongs to. */
export function stepSource(step, plan = {}) {
  if (!step) return null;
  const tags = step.evidence_tags || [];
  if (step.source === 'evidence') {
    if (!tags.length) return null;
    const alarms = (step.alarm_ids || []).filter((a) => tags.includes(a));
    if (step.final) {
      return { ...SOURCE.verify, ref: list(tags, 4), note: 'Confirms the readings are back within their configured normal range.' };
    }
    return { ...SOURCE.telemetry, ref: list(tags), note: `Recorded ${alarms.length ? 'alarm and readings' : 'readings and status'} of this incident: ${tags.join(', ')}` };
  }
  if (step.source === 'history') {
    const ids = (step.history_events || []).map((id) => String(id).slice(0, 8));
    if (!ids.length && !step.history_when) return null;
    return {
      ...SOURCE.history,
      // The step text already ends with when it was fixed; the reference names the incident.
      ref: ids.length ? `Incident ${list(ids, 2)}` : step.history_when,
      note: `How an earlier incident was resolved${step.history_simulated ? ' (simulated history)' : ''}. Not a guaranteed solution and not an approved SOP instruction.`,
    };
  }
  if (step.source === 'sop') {
    // A reference needs the SOP and the step it comes from; without both, say nothing about the SOP.
    if (!plan.sop_id || !step.sop_step) return null;
    const ref = `${plan.sop_id} step ${step.sop_step}${step.page ? ` · p.${step.page}` : ''}`;
    const approved = plan.sop_approved === true && !/DRAFT|NOT APPROVED/i.test(plan.sop_status || '');
    return approved
      ? { ...SOURCE.sop, ref, note: `Step ${step.sop_step} of the approved ${plan.sop_id}.` }
      : { ...SOURCE.sopDraft, ref, note: `${plan.sop_id} is a draft and has not been approved. Follow site practice before acting on it.` };
  }
  if (step.source === 'ai') return { ...SOURCE.ai, ref: '', note: 'Written by the AI from the incident facts. No retrieved SOP supports this step.' };
  return null;
}
