// Clock times are built by hand: some browsers' Intl output flips 12 to 00
// between calls with a 24-hour option, which put "00:27" between two "12:27" ticks.
const two = (n) => String(n).padStart(2, '0');
export function hhmm(value) {
  const t = Date.parse(value || '');
  if (!Number.isFinite(t)) return '—';
  const d = new Date(t);
  return `${two(d.getHours())}:${two(d.getMinutes())}`;
}
export function hhmmss(value) {
  const t = Date.parse(value || '');
  if (!Number.isFinite(t)) return '—';
  return `${hhmm(value)}:${two(new Date(t).getSeconds())}`;
}

export function fmtValue(value) {
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value !== 'number' || !Number.isFinite(value)) return value == null ? '—' : String(value);
  if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString();
  return String(Math.round(value * 100) / 100);
}

/** Severity from the incident's catalog severity, the SOP's P1 rule or the urgency band. */
export function severityOf(incident, brief) {
  if (brief?.escalation?.p1_reached) return { cls: 'p1', label: 'P1' };
  const s = String(brief?.scenario?.severity || incident?.scenario_severity || '').toLowerCase();
  if (s === 'critical') return { cls: 'p1', label: 'CRITICAL' };
  if (s === 'high') return { cls: 'p2', label: 'HIGH' };
  if (s === 'medium') return { cls: 'p3', label: 'MEDIUM' };
  const band = incident?.urgency?.urgency_band;
  if (band === 'URGENT') return { cls: 'p1', label: 'URGENT' };
  if (band === 'ELEVATED') return { cls: 'p2', label: 'ELEVATED' };
  if (band === 'ROUTINE') return { cls: 'p4', label: 'ROUTINE' };
  return { cls: 'p4', label: '—' };
}

export const cleanLabel = (text) => String(text || '').replace(/^(CONFIRMED|HYPOTHESIS|UNKNOWN|GENERAL ADVICE)\s*[:\-]?\s*/i, '');

export function sourceName(value) {
  const source = String(value || '').toLowerCase();
  if (/simulat|synthetic/.test(source)) return 'SIMULATED';
  if (/live|plc|opc/.test(source)) return 'LIVE PLC';
  return value ? 'HISTORICAL' : 'UNKNOWN';
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** 10/11/2026 (day/month/year), as the HMI clock shows it. */
export function ddmmyyyy(value) {
  const t = value instanceof Date ? value.getTime() : Date.parse(value || '');
  if (!Number.isFinite(t)) return '—';
  const d = new Date(t);
  return `${two(d.getDate())}/${two(d.getMonth() + 1)}/${d.getFullYear()}`;
}
/** 10/11/2026, 12:25:26 */
export function dateTime(value) {
  const t = Date.parse(value || '');
  return Number.isFinite(t) ? `${ddmmyyyy(value)}, ${hhmmss(value)}` : '—';
}
/** Nov 08, 08:44 */
export function shortDateTime(value) {
  const t = Date.parse(value || '');
  if (!Number.isFinite(t)) return '—';
  const d = new Date(t);
  return `${MONTHS[d.getMonth()]} ${two(d.getDate())}, ${hhmm(value)}`;
}
