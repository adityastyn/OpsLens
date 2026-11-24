import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { getForecast, getPredictiveWarnings, getTelemetryCompare, getTelemetrySeries, getTelemetryTags } from '../../api/client';
import { hhmmss, shortDateTime, sourceName } from '../../lib/format';

const RANGES = [['Last 5 min', 5], ['Last 15 min', 15], ['Last 60 min', 60], ['Last 24 h', 1440], ['Last 7 days', 10080], ['Last 30 days', 43200], ['All records', 100000]];
const WIDTH = 900;
const HEIGHT = 320;
const PAD = { top: 16, right: 150, bottom: 28, left: 58 };

function niceTicks(lo, hi, count = 5) {
  const span = hi - lo || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) ticks.push(Math.round(v / step) * step);
  return ticks;
}
const fmt = (v) => (typeof v !== 'number' ? '—' : Math.abs(v) >= 1000 ? Math.round(v).toLocaleString() : String(Math.round(v * 100) / 100));

export function Chart({ series, forecast, showAnomalies = true }) {
  const [hover, setHover] = useState(null);
  const svgRef = useRef(null);
  const model = useMemo(() => {
    const points = (series?.points || []).map((p) => ({ ...p, ms: Date.parse(p.t) }));
    if (!points.length) return null;
    const future = forecast?.available ? forecast.points.map((p) => ({ ...p, ms: Date.parse(p.t) })) : [];
    const values = [...points.map((p) => p.v), ...future.flatMap((p) => [p.lo, p.hi]), ...(series.normal || []).filter((v) => v != null)];
    const inView = (series.limits || []).filter((l) => l.value >= Math.min(...values) * 0.8 && l.value <= Math.max(...values) * 1.2);
    values.push(...inView.map((l) => l.value));
    let lo = Math.min(...values); let hi = Math.max(...values);
    const pad = (hi - lo || 1) * 0.08; lo -= pad; hi += pad;
    const t0 = points[0].ms; const t1 = (future.at(-1) || points.at(-1)).ms;
    const x = (ms) => PAD.left + ((ms - t0) / (t1 - t0 || 1)) * (WIDTH - PAD.left - PAD.right);
    const y = (v) => HEIGHT - PAD.bottom - ((v - lo) / (hi - lo)) * (HEIGHT - PAD.top - PAD.bottom);
    return { points, future, inView, lo, hi, x, y, t0, t1 };
  }, [series, forecast]);
  if (!model) return null;
  const { points, future, inView, lo, hi, x, y } = model;
  const path = (rows, key = 'v') => rows.map((p, i) => `${i ? 'L' : 'M'}${x(p.ms).toFixed(1)} ${y(p[key]).toFixed(1)}`).join('');
  const band = future.length ? `${path(future, 'hi')}L${[...future].reverse().map((p) => `${x(p.ms).toFixed(1)} ${y(p.lo).toFixed(1)}`).join('L')}Z` : '';
  const normal = series.normal;
  const anomalies = showAnomalies ? (series.anomalies || []).map((a) => ({ ...a, ms: Date.parse(a.t) })) : [];
  const onMove = (event) => {
    const box = svgRef.current.getBoundingClientRect();
    const ms = model.t0 + (((event.clientX - box.left) / box.width) * WIDTH - PAD.left) / (WIDTH - PAD.left - PAD.right) * (model.t1 - model.t0);
    const all = [...points, ...future.map((p) => ({ ...p, predicted: true }))];
    setHover(all.reduce((best, p) => (Math.abs(p.ms - ms) < Math.abs(best.ms - ms) ? p : best), all[0]));
  };
  const timeTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => model.t0 + f * (points.at(-1).ms - model.t0));
  // Longer than a day: show the date too, or two days' 12:30 look the same.
  const tickLabel = (ms) => (points.at(-1).ms - model.t0 > 20 * 3600000 ? shortDateTime : hhmmss)(new Date(ms).toISOString());
  return (
    <div className="ts-chart">
      <svg ref={svgRef} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${series.label} recorded readings`} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {niceTicks(lo, hi).map((t) => <g key={t}><line className="ts-grid" x1={PAD.left} x2={WIDTH - PAD.right} y1={y(t)} y2={y(t)} /><text className="ts-tick" x={PAD.left - 8} y={y(t) + 4} textAnchor="end">{fmt(t)}</text></g>)}
        {timeTicks.map((t) => <text key={t} className="ts-tick" x={x(t)} y={HEIGHT - 8} textAnchor="middle">{tickLabel(t)}</text>)}
        {normal && <rect className="ts-normal" x={PAD.left} width={WIDTH - PAD.left - PAD.right} y={y(Math.min(normal[1], hi))} height={Math.max(0, y(Math.max(normal[0], lo)) - y(Math.min(normal[1], hi)))} />}
        {normal && <text className="ts-note" x={WIDTH - PAD.right + 6} y={y((normal[0] + normal[1]) / 2) + 4}>Normal range</text>}
        {(series.gaps || []).map((g) => <rect key={g.from} className="ts-gap" x={x(Date.parse(g.from))} width={x(Date.parse(g.to)) - x(Date.parse(g.from))} y={PAD.top} height={HEIGHT - PAD.top - PAD.bottom}><title>No data for {g.seconds} s</title></rect>)}
        {inView.map((l) => <g key={l.label}><line className="ts-limit" x1={PAD.left} x2={WIDTH - PAD.right} y1={y(l.value)} y2={y(l.value)} /><text className="ts-note" x={WIDTH - PAD.right + 6} y={y(l.value) + 4}>{l.label.slice(0, 22)}</text></g>)}
        {band && <path className="ts-band" d={band} />}
        <path className="ts-line" d={path(points)} />
        {future.length > 0 && <path className="ts-forecast" d={path([points.at(-1), ...future])} />}
        {future.length > 0 && <g><line className="ts-now" x1={x(points.at(-1).ms)} x2={x(points.at(-1).ms)} y1={PAD.top} y2={HEIGHT - PAD.bottom} />
          <text className="ts-note" x={x(points.at(-1).ms) + 4} y={PAD.top + 10}>now · prediction →</text></g>}
        {forecast?.crossing && Number.isFinite(Date.parse(forecast.crossing.at)) && <g>
          <line className="ts-crossing" x1={x(Date.parse(forecast.crossing.at))} x2={x(Date.parse(forecast.crossing.at))} y1={PAD.top} y2={HEIGHT - PAD.bottom} />
          <text className="ts-note" x={x(Date.parse(forecast.crossing.at)) + 4} y={PAD.top + 24}>predicted crossing</text></g>}
        {anomalies.slice(0, 200).map((a) => <circle key={a.t} className="ts-anomaly" cx={x(a.ms)} cy={y(a.v)} r="3" />)}
        {hover && <g><line className="ts-cross" x1={x(hover.ms)} x2={x(hover.ms)} y1={PAD.top} y2={HEIGHT - PAD.bottom} /><circle className="ts-dot" cx={x(hover.ms)} cy={y(hover.v)} r="4.5" /></g>}
      </svg>
      <div className="ts-legend">
        <span><i className="line" /> Recorded</span>
        <span><i className="normal" /> Normal range</span>
        <span><i className="limit" /> Alarm limit</span>
        <span><i className="anomaly" /> Anomaly marker</span>
        {future.length > 0 && <span><i className="forecast" /> Prediction, not measured</span>}
        {future.length > 0 && <span><i className="band" /> 95 % prediction interval</span>}
        {forecast?.crossing && <span><i className="crossing" /> Estimated limit-crossing time</span>}
        {series.gaps?.length > 0 && <span><i className="gap" /> No data</span>}
        {hover && <b className="ts-readout">{tickLabel(hover.ms)} · {fmt(hover.v)} {series.unit}{hover.predicted ? ` (predicted ${fmt(hover.lo)} to ${fmt(hover.hi)})` : ''}</b>}
      </div>
    </div>
  );
}

function useTagList(incidentTags) {
  const [tags, setTags] = useState([]);
  const [tag, setTag] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    getTelemetryTags().then((d) => {
      setTags(d.tags);
      setTag((current) => current || (incidentTags || []).find((t) => d.tags.some((x) => x.tag === t && x.samples)) || d.tags.find((x) => x.samples)?.tag || d.tags[0]?.tag || '');
    }).catch((e) => setError(e.message));
  }, [incidentTags]);
  return { tags, tag, setTag, error };
}

function SensorPicker({ tags, tag, setTag, incidentTags }) {
  const groups = useMemo(() => {
    const out = {};
    tags.forEach((t) => { (out[t.equipment_id] = out[t.equipment_id] || []).push(t); });
    return out;
  }, [tags]);
  const own = tags.filter((t) => (incidentTags || []).includes(t.tag));
  return (
    <>
      <label>Sensor <select value={tag} onChange={(e) => setTag(e.target.value)}>
        {own.length > 0 && <optgroup label="Selected incident">{own.map((t) => <option key={t.tag} value={t.tag}>{t.label} ({t.tag})</option>)}</optgroup>}
        {Object.entries(groups).map(([eq, rows]) => <optgroup key={eq} label={eq}>{rows.map((t) => <option key={`${eq}-${t.tag}`} value={t.tag}>{t.label} ({t.tag}){t.samples ? '' : ' — no data'}</option>)}</optgroup>)}
      </select></label>
      {own.length > 0 && <div className="ts-chips" aria-label="Sensors in this incident">{own.map((t) => <button key={t.tag} type="button" aria-pressed={t.tag === tag} onClick={() => setTag(t.tag)}>{t.label}</button>)}</div>}
    </>
  );
}

const SourceChips = ({ series }) => [...new Set((series?.data_sources || []).map(sourceName))].map((name) => <span key={name} className="ov-chip amber">{name} DATA</span>);

/** Historical and live telemetry from backend records only. */
export default function TimeSeriesPage({ eventId, incidentTags, children, forecastOn = false }) {
  const { tags, tag, setTag, error: tagError } = useTagList(incidentTags);
  // Open on the incident, not the whole stored history (which can include a past month).
  const [minutes, setMinutes] = useState(60);
  const [series, setSeries] = useState(null);
  const [compare, setCompare] = useState(null);
  const [error, setError] = useState('');
  const [analysis, setAnalysis] = useState(forecastOn);
  const [forecast, setForecast] = useState(null);
  const [warnings, setWarnings] = useState(null);
  useEffect(() => {
    if (!analysis || !tag) { setForecast(null); return undefined; }
    let live = true;
    const load = () => {
      getForecast(tag).then((d) => live && setForecast(d)).catch(() => live && setForecast(null));
      getPredictiveWarnings().then((d) => live && setWarnings(d)).catch(() => live && setWarnings(null));
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => { live = false; window.clearInterval(timer); };
  }, [analysis, tag]);
  useEffect(() => {
    if (!tag) return undefined;
    let live = true;
    const load = () => {
      getTelemetrySeries(tag, { minutes }).then((d) => { if (live) { setSeries(d); setError(''); } }).catch((e) => live && setError(e.message));
      if (eventId) getTelemetryCompare(eventId, tag).then((d) => live && setCompare(d)).catch(() => live && setCompare(null));
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => { live = false; window.clearInterval(timer); };
  }, [tag, minutes, eventId]);
  return (
    <section className="con-section" aria-label="Time-series analytics">
      <header className="con-head"><div><h2>Time-series analytics</h2><p>Recorded telemetry from the backend. Refreshes every 5 s.{analysis ? ' Dotted values are predictions, not measurements, and not a confirmed fault.' : ''}</p></div></header>
      <div className="ts-controls">
        <SensorPicker tags={tags} tag={tag} setTag={setTag} incidentTags={incidentTags} />
        <label>Range <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>{RANGES.map(([label, value]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="ts-check"><input type="checkbox" checked={analysis} onChange={(e) => setAnalysis(e.target.checked)} /> Forecasting and anomaly detection</label>
        <SourceChips series={series} />
      </div>
      {(error || tagError) && <p role="alert" className="ov-error">{error || tagError}</p>}
      {!series && !error && <p className="ov-empty"><Loader2 size={13} className="spin" /> Loading…</p>}
      {series && !series.available && <p className="ov-empty">{series.message}</p>}
      {series?.available && <>
        <h3>{series.label} <small>{series.total_samples} samples · every {series.sample_interval_seconds} s · {series.gaps?.length || 0} gap(s) · {series.anomalies?.length || 0} anomaly marker(s)</small></h3>
        <Chart series={series} forecast={analysis ? forecast : null} showAnomalies={analysis} />
        {analysis && <ForecastCards series={series} forecast={forecast} warnings={warnings} />}
      </>}
      {compare && <section className="ts-compare" aria-label="Before and after the fix">
        <h3>Before and after the fix <small>{series?.label || ''}{series?.unit ? ` · ${series.unit}` : ''}</small></h3>
        <div className="ts-compare-grid">
          <StatCard title="During the incident" note={compare.incident_start ? `From the first alarm (${hhmmss(compare.incident_start)})${compare.alarm_cleared_at ? ' until the alarm cleared' : ' until now'}` : ''} row={compare.before_fix} />
          {compare.alarm_cleared_at
            ? <StatCard title="After the fix" note={`Since the alarm cleared (${hhmmss(compare.alarm_cleared_at)})`} row={compare.after_fix} before={compare.before_fix} />
            : <div className="ts-stat waiting"><h4>After the fix</h4><p>Not available yet: the alarm is still active.</p>
                <p className="op-muted">Apply the fix (Scenario Lab → Simulate the fix), wait for the alarm to clear, and this card fills with the readings since then.</p></div>}
        </div>
      </section>}
      {children}
    </section>
  );
}

/** Min, max, mean and last of one period; with `before`, the change in the mean. */
function StatCard({ title, note, row, before }) {
  const change = before && Number.isFinite(row?.mean) && Number.isFinite(before?.mean) ? row.mean - before.mean : null;
  return (
    <div className="ts-stat"><h4>{title}</h4>{note && <p className="op-muted">{note}</p>}
      {row?.count === 0 || !row ? <p className="op-muted">No readings in this period.</p> :
        <dl>{[['Min', row.min], ['Max', row.max], ['Mean', row.mean], ['Last', row.last]].map(([k, v]) => <div key={k}><dt>{k}</dt><dd className="mono">{fmt(v)}</dd></div>)}</dl>}
      {change != null && <p className="ts-change">Mean {change >= 0 ? 'up' : 'down'} {fmt(Math.abs(change))} compared with during the incident.</p>}
    </div>
  );
}

const STATE_CLS = { ACTIVE_BREACH: 'breach', ACTIVE_BREACH_WORSENING: 'breach', PREDICTED_LIMIT_CROSSING: 'predicted', WITHIN_LIMITS: 'ok', NO_LIMIT: 'ok' };

/** Forecast, early warnings and anomaly markers for the sensor on the chart.
 *  A reading already past a limit is an ACTIVE BREACH (measured), never an early warning. */
function ForecastCards({ series, forecast, warnings }) {
  const bt = forecast?.backtest;
  const anomalies = (series?.anomalies || []).slice(-6).reverse();
  const unreliable = bt && bt.skill != null && bt.skill < 0;
  const state = forecast?.limit_state;
  return (
    <div className="ts-cards">
      <div className="ts-card"><h3>Limit status</h3>
        {state ? <>
          <p className={`ts-state ${STATE_CLS[state.state] || 'ok'}`} data-testid="limit-state">{state.text}</p>
          {state.limit && <p>{state.state === 'PREDICTED_LIMIT_CROSSING' ? `Forecast to reach ${state.limit} in about ${state.in_seconds} s.` : `Past ${state.limit}: measured ${fmt(state.measured)} ${series?.unit || ''}.`}</p>}
          {state.note && <p className="muted">{state.note}</p>}
          {series?.normal && state.measured != null && (() => {
            const [lo, hi] = series.normal; const out = (lo != null && state.measured < lo) || (hi != null && state.measured > hi);
            return <p className={out ? 'ts-outside' : ''} data-testid="normal-state">{out ? 'Outside' : 'Within'} the normal range ({fmt(lo)} to {fmt(hi)} {series.unit || ''}): now {fmt(state.measured)}.</p>;
          })()}
        </> : <p>{forecast?.reason || 'No status available.'}</p>}
      </div>
      <div className="ts-card"><h3>Prediction</h3>
        {!forecast?.available && <p>{forecast?.reason || 'No forecast available.'}</p>}
        {forecast?.available && <>
          {unreliable && <p className="op-notice">Prediction not reliable for this sensor: on its own history it did worse than assuming no change. Treat the dotted line as a rough guide only.</p>}
          <p>Last measured {fmt(forecast.last_measured.v)} {series?.unit}. Next {forecast.horizon_seconds} s. Trend {forecast.fitted.trend_significant ? 'clear, extended with damping' : 'not clear, holding the recent mean'}. The shaded band is an approximate 95 % range.</p>
          <p className="muted">Method: {forecast.method}.</p>
          <p className="muted">{bt ? `Backtest: error ${bt.mae}, no-change error ${bt.naive_mae}, skill ${bt.skill} (above 0 beats "no change").` : 'Not enough history to backtest this sensor.'}</p>
        </>}
      </div>
      <div className="ts-card"><h3>Early warnings</h3>
        {!warnings && <p>Unavailable.</p>}
        {warnings && !warnings.warnings.length && <p>No reading within its limits is predicted to reach one in the next {warnings.horizon_seconds} s.</p>}
        {warnings?.warnings.slice(0, 5).map((w) => <p key={w.tag}><b>{w.label}</b>: PREDICTED LIMIT CROSSING, {w.crossing.limit} in about {w.crossing.in_seconds} s</p>)}
        {(warnings?.active_breaches || []).length > 0 && <p className="ts-breaches"><b>Already past a limit (measured, not warnings):</b> {warnings.active_breaches.slice(0, 5).map((b) => `${b.label} (${b.limit_state.text.toLowerCase()})`).join('; ')}</p>}
        <p className="muted">{warnings?.note}</p>
      </div>
      <div className="ts-card"><h3>Latest anomaly markers</h3>
        {!anomalies.length && <p>None for this sensor.</p>}
        {anomalies.map((a) => <p key={a.t}><span className="mono">{hhmmss(a.t)}</span> {fmt(a.v)} {series?.unit}: {a.reasons.join(', ')}</p>)}
        <p className="muted">{series?.anomaly_method}</p>
      </div>
    </div>
  );
}
