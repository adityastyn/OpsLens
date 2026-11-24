/**
 * How far a reading is from where it should be, in plain words.
 *
 * Order of importance: past an alarm limit, then outside the normal range,
 * then inside the normal range (with the margin left to the nearest alarm
 * limit). Percentages are relative to the threshold that was crossed.
 */
const fmt = (n) => {
  const a = Math.abs(n);
  const s = a >= 100 ? a.toFixed(0) : a >= 10 ? a.toFixed(1) : a.toFixed(2);
  return s.replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1');
};
// Catalog limit labels are SOP wording; use them only when short.
const short = (label) => (label && label.length <= 22 ? label : 'alarm limit');
const pct = (delta, base) => (base ? Math.round((Math.abs(delta) / Math.abs(base)) * 100) : null);

export function deviation({ now, normalLow, normalHigh, limits = [], unit = '' }) {
  const v = Number(now);
  if (!Number.isFinite(v)) return null;
  const u = unit ? ` ${unit}` : '';
  const lo = normalLow == null ? null : Number(normalLow);
  const hi = normalHigh == null ? null : Number(normalHigh);

  // Past an alarm limit: the worst one crossed.
  const crossed = (limits || [])
    .map((l) => ({ ...l, value: Number(l.value), dir: String(l.direction || '').toLowerCase() }))
    .filter((l) => Number.isFinite(l.value) && ((/high|above|max/.test(l.dir) && v > l.value) || (/low|below|min/.test(l.dir) && v < l.value)))
    // The most severe limit crossed is the one nearest the value (for example high-high before high).
    .sort((a, b) => Math.abs(v - a.value) - Math.abs(v - b.value));
  if (crossed.length) {
    const l = crossed[0];
    const d = v - l.value;
    return {
      kind: 'limit', delta: d, percent: pct(d, l.value),
      short: `${d > 0 ? '+' : '−'}${fmt(d)} past limit`, signed: `${d > 0 ? '+' : '−'}${fmt(d)}`,
      text: `${fmt(d)}${u} ${d > 0 ? 'above' : 'below'} the ${short(l.label)} (${fmt(l.value)}${u})${pct(d, l.value) != null ? `, ${pct(d, l.value)}% past it` : ''}`,
    };
  }
  // Zero on a reading that is never zero in service (motor current, pump flow):
  // the equipment is stopped. Not a deviation to rank; the trip is its own alarm.
  if (v === 0 && lo != null && Number.isFinite(lo) && lo > 0) {
    return { kind: 'stopped', delta: 0, percent: 0, short: 'stopped', text: 'Reads 0: the equipment is not running' };
  }
  // Outside the normal range.
  if (hi != null && Number.isFinite(hi) && v > hi) {
    const d = v - hi;
    return { kind: 'high', delta: d, percent: pct(d, hi), short: `+${fmt(d)} over normal`, signed: `+${fmt(d)}`,
      text: `${fmt(d)}${u} above normal (max ${fmt(hi)}${u}), ${pct(d, hi)}% over` };
  }
  if (lo != null && Number.isFinite(lo) && v < lo) {
    const d = v - lo;
    return { kind: 'low', delta: d, percent: pct(d, lo), short: `−${fmt(d)} under normal`, signed: `−${fmt(d)}`,
      text: `${fmt(d)}${u} below normal (min ${fmt(lo)}${u}), ${pct(d, lo)}% under` };
  }
  // Inside the normal range: margin to the nearest alarm limit.
  const margins = (limits || []).map((l) => ({ ...l, value: Number(l.value) })).filter((l) => Number.isFinite(l.value))
    .map((l) => ({ ...l, gap: Math.abs(l.value - v) })).sort((a, b) => a.gap - b.gap);
  if (lo == null && hi == null && !margins.length) return null;
  const m = margins[0];
  return { kind: 'normal', delta: 0, percent: 0, short: 'in range',
    text: `Within normal${lo != null && hi != null ? ` (${fmt(lo)}–${fmt(hi)}${u})` : ''}${m ? `; ${fmt(m.gap)}${u} from the ${short(m.label)}` : ''}` };
}
