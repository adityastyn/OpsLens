/** The record's event story in plain words, from the timeline only (no AI). */
import { ALARM_LABELS, ALARM_PRIORITY } from './plantTags.generated.js';
import { fmtValue, hhmmss } from './format.js';

const ALARM_NAMES = new Set(Object.values(ALARM_LABELS));
// LOW advisory alarms (a reading left its normal range) are not the alarm the story is about.
const ADVISORY_NAMES = new Set(Object.entries(ALARM_LABELS).filter(([tag]) => ALARM_PRIORITY?.[tag] === 'LOW').map(([, label]) => label));

/** One story line in words: "WAS rate fell to 70 m³/d", "ALARM Sludge age high". */
export function storyLine(e) {
  if (e.kind === 'alarm_state') {
    const [name, state] = String(e.event).split(/:\s*(?=[^:]*$)/);
    return { alarm: /^active/i.test(state || ''), cleared: /normal|clear|recover/i.test(state || ''), text: /^active/i.test(state || '') ? name : `${name}: ${state}` };
  }
  const m = String(e.change || '').match(/^(-?[\d.,]+)\s*→\s*(-?[\d.,]+)\s*(.*)$/);
  // An alarm recorded as an observation with no value change.
  if (!m && !e.change && ALARM_NAMES.has(String(e.event))) return { alarm: true, text: e.event };
  if (!m) return { text: e.event };
  const [before, after] = [Number(m[1].replace(/,/g, '')), Number(m[2].replace(/,/g, ''))];
  const verb = after > before ? 'rose to' : after < before ? 'fell to' : 'was';
  return { text: `${e.event} ${verb} ${fmtValue(after)} ${m[3]}`.trim(), name: e.event, verb: verb.split(' ')[0] };
}

const lower = (t) => (/^[A-Z][a-z]/.test(t) ? t.charAt(0).toLowerCase() + t.slice(1) : t);
const joinWords = (items) => (items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`);

/** The record's event story in one or two plain sentences, built only from the
 *  recorded changes (no AI): what happened first (a reading moving, or a fault or
 *  alarm coming on), what followed, and which alarm came on next. */
export function storySummary(events) {
  const rows = events.map((e) => ({ e, l: storyLine(e) }));
  const moving = (r) => r.l.verb && r.l.verb !== 'was';
  const cameOn = (r) => r.e.kind === 'observation' && !r.e.change && !r.l.cleared;
  const first = rows.find((r) => moving(r) || cameOn(r));
  if (!first) return '';
  const start = Date.parse(first.e.timestamp);
  const after = rows.slice(rows.indexOf(first) + 1);
  const alarmAt = after.findIndex((r) => r.l.alarm && !ADVISORY_NAMES.has(r.l.text));
  const moved = (alarmAt < 0 ? after : after.slice(0, alarmAt)).filter(moving);
  const parts = [moving(first)
    ? `${first.l.name} ${first.l.verb} first (${hhmmss(first.e.timestamp)}).`
    : `${first.l.text} came on first (${hhmmss(first.e.timestamp)}).`];
  if (moved.length) {
    const secs = Math.max(1, Math.round((Date.parse(moved[moved.length - 1].e.timestamp) - start) / 1000));
    const verbs = [...new Set(moved.map((r) => r.l.verb))];
    const list = verbs.length === 1
      ? `${joinWords(moved.map((r) => r.l.name))} ${verbs[0]}`
      : joinWords(moved.map((r) => `${r.l.name} ${r.l.verb}`));
    parts.push(`Within ${secs} s, ${list}.`);
  }
  if (alarmAt >= 0) parts.push(`Then the ${lower(after[alarmAt].l.text)} alarm came on.`);
  return parts.join(' ');
}

/** Trips and faults found in the stored status readings but not kept in the
 *  timeline (from the cause list), added in time order so the story can start
 *  with them. Only recorded status changes are added, never a hypothesis. */
export function withRecordedTrips(events, candidates) {
  const names = new Set(events.map((e) => String(e.event)));
  const trips = (candidates || [])
    .filter((c) => c.kind === 'trip' && c.first_seen_at && c.short && !names.has(c.short))
    .map((c) => ({ kind: 'observation', event: c.short, change: null, timestamp: c.first_seen_at }));
  if (!trips.length) return events;
  return [...events, ...trips].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
}
