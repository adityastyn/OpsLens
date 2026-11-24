import React, { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { getKnowledgeGraph } from '../../api/client';

const PLANT_COLUMNS = ['plant', 'area', 'equipment', 'sensor', 'alarm', 'incident', 'cause', 'fix', 'sop'];
// The incident view follows the operator's question: what moved, what alarmed,
// why (hypotheses), and which SOP step fixes it. Plant and area become a breadcrumb.
const INCIDENT_COLUMNS = ['sensor', 'alarm', 'cause', 'fix', 'sop'];
const COLUMN_OF = { check: 'sop' };
const TITLES = { plant: 'Plant', area: 'Process area', equipment: 'Equipment', sensor: 'Sensor', alarm: 'Alarm', incident: 'Incident', cause: 'Possible cause', fix: 'Fix (SOP step)', sop: 'SOP and recovery', check: 'Recovery check' };
const INCIDENT_TITLES = { sensor: 'Readings that moved', alarm: 'Alarms', cause: 'Causes (hypotheses)', fix: 'SOP step that fixes it', sop: 'SOP and recovery check' };
const STATE_TEXT = { limit: 'Past an SOP limit', abnormal: 'Outside normal range', normal: 'Within normal range', no_data: 'No recorded data' };
const ROLE_TEXT = { leading: 'LEADING', contributing: 'CONTRIBUTING', chain: 'CHAIN LINK' };
const STATE_ORDER = { limit: 0, abnormal: 1, normal: 2, no_data: 3 };

const PLANT = { colW: 122, rowH: 30, nodeW: 112, nodeH: 24 };
const INCIDENT = { colW: 214, rowH: 52, nodeW: 192, nodeH: 42 };

const causeName = (label) => label.replace(/^(Possible cause|Leading hypothesis|Contributing hypothesis|Contributing|Related condition|Link in the chain): /, '');

/** Up to two lines of about `width` characters, cut at a word, "…" only when needed. */
function wrap(text, width, lines = 2) {
  const words = String(text).split(/\s+/);
  const out = [];
  let line = '';
  while (words.length && out.length < lines) {
    const next = line ? `${line} ${words[0]}` : words[0];
    if (next.length <= width || !line) { line = next.length > width ? `${next.slice(0, width - 1)}…` : next; words.shift(); }
    else { out.push(line); line = ''; }
  }
  if (line && out.length < lines) out.push(line);
  if (words.length) out[out.length - 1] = `${out[out.length - 1].replace(/…$/, '').slice(0, width - 1)}…`;
  return out;
}

/** Plant-wide layout: every configured node in nine columns. */
function plantLayout(graph) {
  const positions = {};
  let tallest = 0;
  PLANT_COLUMNS.forEach((kind, c) => {
    graph.nodes.filter((n) => (COLUMN_OF[n.kind] || n.kind) === kind).forEach((n, r) => {
      positions[n.id] = { x: 8 + c * PLANT.colW, y: 28 + r * PLANT.rowH, node: n };
      tallest = Math.max(tallest, r + 1);
    });
  });
  const edges = graph.edges.filter((e) => positions[e.source] && positions[e.target]);
  return { positions, edges, height: 40 + tallest * PLANT.rowH, path: new Set(graph.incident_path), size: PLANT, columns: PLANT_COLUMNS, titles: TITLES, hidden: 0 };
}

/** Incident layout: only what explains this incident, ordered so lines rarely cross. */
function incidentLayout(graph, showAll) {
  const byId = Object.fromEntries(graph.nodes.map((n) => [n.id, n]));
  const path = new Set(graph.incident_path);
  const incident = graph.nodes.find((n) => n.kind === 'incident' && path.has(n.id));
  const inIncident = new Set(graph.edges.filter((e) => incident && e.target === incident.id).map((e) => e.source));
  const candidates = graph.nodes.filter((n) => path.has(n.id) || inIncident.has(n.id));
  const sensorsAll = candidates.filter((n) => n.kind === 'sensor');
  const moved = (n) => n.state === 'limit' || n.state === 'abnormal';
  const sensors = showAll ? sensorsAll : sensorsAll.filter(moved);
  const causes = candidates.filter((n) => n.kind === 'cause').sort((a, b) => (a.rank || 9) - (b.rank || 9));
  const keep = new Set([...sensors, ...causes].map((n) => n.id));
  candidates.filter((n) => ['alarm', 'fix', 'sop', 'check'].includes(n.kind)).forEach((n) => keep.add(n.id));
  const edges = graph.edges.filter((e) => keep.has(e.source) && keep.has(e.target) && e.relation !== 'see_procedure');

  // Row of each cause, then pull the other columns toward the causes they link to.
  const row = {};
  causes.forEach((n, i) => { row[n.id] = i; });
  const linked = (id, kinds, dir) => edges.filter((e) => (dir === 'out' ? e.source === id : e.target === id))
    .map((e) => (dir === 'out' ? e.target : e.source)).filter((other) => kinds.includes(byId[other]?.kind));
  const avg = (ids) => { const rows = ids.map((i) => row[i]).filter((r) => r !== undefined); return rows.length ? rows.reduce((a, b) => a + b, 0) / rows.length : 99; };
  const best = (ids) => Math.min(99, ...ids.map((i) => row[i]).filter((r) => r !== undefined));
  const alarms = [...keep].map((id) => byId[id]).filter((n) => n.kind === 'alarm');
  // A reading leads to a cause directly, or through the alarm it raises.
  const causeRow = (n) => best([...linked(n.id, ['cause'], 'out'), ...linked(n.id, ['alarm'], 'out').flatMap((a) => linked(a, ['cause'], 'out'))]);
  sensors.sort((a, b) => causeRow(a) - causeRow(b) || (STATE_ORDER[a.state] ?? 9) - (STATE_ORDER[b.state] ?? 9) || a.label.localeCompare(b.label));
  sensors.forEach((n, i) => { row[n.id] = i; });
  const alarmKey = (n) => { const c = best(linked(n.id, ['cause'], 'out')); return c < 99 ? c - 0.5 : avg(linked(n.id, ['sensor'], 'in')); };
  alarms.sort((a, b) => alarmKey(a) - alarmKey(b));
  const fixes = [...keep].map((id) => byId[id]).filter((n) => n.kind === 'fix').sort((a, b) => avg(linked(a.id, ['cause'], 'in')) - avg(linked(b.id, ['cause'], 'in')));
  const sops = [...keep].map((id) => byId[id]).filter((n) => n.kind === 'sop' || n.kind === 'check').sort((a, b) => (a.kind === 'sop' ? 0 : 1) - (b.kind === 'sop' ? 0 : 1));
  const columns = { sensor: sensors, alarm: alarms, cause: causes, fix: fixes, sop: sops };

  const positions = {};
  let tallest = 0;
  INCIDENT_COLUMNS.forEach((kind, c) => {
    columns[kind].forEach((n, r) => {
      positions[n.id] = { x: 8 + c * INCIDENT.colW, y: 30 + r * INCIDENT.rowH, node: n };
      tallest = Math.max(tallest, r + 1);
    });
  });
  return {
    positions, edges: edges.filter((e) => positions[e.source] && positions[e.target]), height: 44 + tallest * INCIDENT.rowH,
    path, size: INCIDENT, columns: INCIDENT_COLUMNS, titles: INCIDENT_TITLES, hidden: sensorsAll.length - sensors.length,
  };
}

/** The chain through one node: everything that leads into it and everything it leads to. */
function chainOf(id, edges) {
  const seen = new Set([id]);
  const walk = (start, dir) => {
    const stack = [start];
    while (stack.length) {
      const at = stack.pop();
      edges.forEach((e) => {
        const [from, to] = dir === 'out' ? [e.source, e.target] : [e.target, e.source];
        if (from === at && !seen.has(to)) { seen.add(to); stack.push(to); }
      });
    }
  };
  walk(id, 'out');
  walk(id, 'in');
  return seen;
}

/** Generated by the backend from the catalog and this incident's records. */
export default function KnowledgeGraphPage({ eventId }) {
  const [graph, setGraph] = useState(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(null);
  const [hover, setHover] = useState(null);
  const [scope, setScope] = useState('incident');
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    setGraph(null);
    getKnowledgeGraph(eventId).then(setGraph).catch((e) => setError(e.message));
  }, [eventId]);

  const incidentView = scope === 'incident' && graph?.incident_path?.length > 0;
  const layout = useMemo(() => {
    if (!graph) return null;
    return incidentView ? incidentLayout(graph, showAll) : plantLayout(graph);
  }, [graph, incidentView, showAll]);

  const focus = incidentView && (hover || selected) && layout?.positions[hover || selected] ? chainOf(hover || selected, layout.edges) : null;
  const chosen = selected && layout?.positions[selected]?.node;
  const touching = chosen ? graph.edges.filter((e) => e.source === chosen.id || e.target === chosen.id) : [];
  const nameOf = (id) => graph.nodes.find((n) => n.id === id)?.label || id;
  const incidentNode = graph?.nodes.find((n) => n.kind === 'incident' && graph.incident_path.includes(n.id));
  const size = layout?.size || PLANT;
  const chars = Math.floor((size.nodeW - 14) / 6.2);

  return (
    <section className="con-section" aria-label="Knowledge graph">
      <header className="con-head"><div><h2>Knowledge graph</h2>
        <p>{incidentView ? 'How the readings, alarms, causes and SOP steps of this incident connect. Causes are hypotheses until checked in the field.' : 'How the plant\'s equipment, sensors, alarms and SOPs are configured.'}</p>
        {incidentView && <p className="kg-crumb">{[...(graph.breadcrumb || []), incidentNode?.label.replace(/^Incident: /, '')].filter(Boolean).join(' › ')}</p>}
      </div></header>
      <div className="ts-controls">
        <label>View <select value={scope} onChange={(e) => { setScope(e.target.value); setSelected(null); }}><option value="incident">This incident</option><option value="plant">Whole plant</option></select></label>
        {incidentView && <label className="kg-toggle"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show readings that stayed normal{layout?.hidden ? ` (${layout.hidden})` : ''}</label>}
        <span className="kg-key"><b className="kg-swatch limit" /> Past limit</span>
        <span className="kg-key"><b className="kg-swatch abnormal" /> Outside normal</span>
        {incidentView && <span className="kg-key"><b className="kg-swatch effect" /> Possible consequence</span>}
        <span className="kg-key"><i className="hypothesis" /> Hypothesis</span>
        <span className="kg-key"><i className="procedure" /> From the SOP</span>
        {!incidentView && <span className="kg-key"><i className="configured" /> Configured</span>}
      </div>
      {error && <p role="alert" className="ov-error">{error}</p>}
      {!graph && !error && <p className="ov-empty"><Loader2 size={13} className="spin" /> Building the graph…</p>}
      {layout && <div className="kg-body">
        <div className="ov-panel kg-canvas">
          <svg width={layout.columns.length * size.colW + 8} height={layout.height} role="img" aria-label="Knowledge graph" onMouseLeave={() => setHover(null)}>
            {layout.columns.map((kind, c) => <text key={kind} className="kg-col" x={8 + c * size.colW} y={16}>{layout.titles[kind]}</text>)}
            {layout.edges.map((e) => {
              const a = layout.positions[e.source]; const b = layout.positions[e.target];
              const forward = b.x > a.x;
              const x1 = forward ? a.x + size.nodeW : a.x + size.nodeW / 2; const y1 = forward ? a.y + size.nodeH / 2 : a.y + size.nodeH;
              const x2 = forward ? b.x : b.x + size.nodeW / 2; const y2 = forward ? b.y + size.nodeH / 2 : b.y;
              const mid = (x1 + x2) / 2;
              const onPath = layout.path.has(e.source) && layout.path.has(e.target);
              const faded = focus && !(focus.has(e.source) && focus.has(e.target));
              return <path key={`${e.source}-${e.target}-${e.relation}`} className={`kg-edge ${e.basis} ${onPath ? 'path' : ''} ${faded ? 'faded' : ''} ${focus && !faded ? 'lit' : ''}`}
                d={forward ? `M${x1} ${y1}C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}` : `M${x1} ${y1}L${x2} ${y2}`}><title>{e.relation.replaceAll('_', ' ')} ({e.basis})</title></path>;
            })}
            {Object.values(layout.positions).map(({ x, y, node }) => {
              const isCause = node.kind === 'cause';
              const label = isCause ? causeName(node.label) : node.label.replace(/^Incident: /, '');
              const lines = incidentView
                ? (isCause ? [`${ROLE_TEXT[node.role] || 'POSSIBLE'}${node.support_count ? ` · ${node.support_count} reading${node.support_count === 1 ? '' : 's'}` : ''}`, ...wrap(label, chars, 1)] : wrap(label, chars, 2))
                : [label.length > 17 ? `${label.slice(0, 16)}…` : label];
              return (
                <g key={node.id} className={`kg-node ${node.kind} ${node.state ? `state-${node.state}` : ''} ${node.role ? `role-${node.role}` : ''} ${layout.path.has(node.id) ? 'path' : ''} ${selected === node.id ? 'selected' : ''} ${focus && !focus.has(node.id) ? 'faded' : ''}`}
                  transform={`translate(${x} ${y})`} onMouseEnter={() => incidentView && setHover(node.id)}
                  onClick={() => setSelected(node.id)} role="button" tabIndex={0} aria-label={`${node.label}${node.state ? `, ${STATE_TEXT[node.state]}` : ''}`}
                  onKeyDown={(event) => { if (event.key === 'Enter') setSelected(node.id); }}>
                  <rect width={size.nodeW} height={size.nodeH} rx="5" />
                  {lines.map((line, i) => (
                    <text key={i} x="7" y={incidentView ? (lines.length === 1 ? size.nodeH / 2 + 4 : 16 + i * 15) : 16} className={isCause && incidentView && i === 0 ? 'kg-role' : undefined}>{line}</text>
                  ))}
                  <title>{node.label}{node.state ? ` (${STATE_TEXT[node.state]})` : ''}{node.details?.['possible consequence of'] ? ` · possible consequence of ${node.details['possible consequence of']}` : ''}{node.details?.['insufficient evidence'] ? ' · insufficient evidence' : ''}</title>
                </g>
              );
            })}
          </svg>
        </div>
        <aside className="ov-panel kg-detail">
          {!chosen && <p className="ov-empty">{incidentView ? 'Point at a cause to light up its evidence and fix. Select any box for its details.' : 'Select a node to see its details and links.'}</p>}
          {chosen && <>
            <h3>{isCauseLabel(chosen) ? causeName(chosen.label) : chosen.label}</h3>
            <p className="muted">{chosen.kind === 'cause' ? `${chosen.details?.role || 'Possible cause'} (hypothesis)` : TITLES[chosen.kind]}</p>
            {chosen.kind === 'cause' && <p className="ov-gap"><span>A hypothesis from the record. It has not been confirmed in the field.</span></p>}
            {chosen.kind === 'fix' && <p className="ov-gap"><span>Advisory SOP step. Follow the SOP's own conditions; OpsLens does not operate equipment.</span></p>}
            {chosen.state && <p><b>{STATE_TEXT[chosen.state]}</b>{chosen.details?.['possible consequence of'] ? ` · possible consequence of ${chosen.details['possible consequence of']}` : ''}{chosen.details?.['insufficient evidence'] ? ` · insufficient evidence: ${chosen.details['insufficient evidence']}` : ''}</p>}
            <dl>{Object.entries(chosen.details || {}).filter(([k, v]) => k !== 'role' && v !== null && v !== undefined && String(v) !== '').map(([k, v]) => <div key={k}><dt>{k.replaceAll('_', ' ')}</dt><dd>{Array.isArray(v) ? v.join('; ') || '—' : String(v)}</dd></div>)}</dl>
            <h4>Links</h4>
            <ul>{touching.slice(0, 14).map((e) => <li key={`${e.source}${e.target}${e.relation}`}><b>{e.relation.replaceAll('_', ' ')}</b> {e.source === chosen.id ? nameOf(e.target) : `from ${nameOf(e.source)}`} <span className="muted">({e.basis})</span></li>)}</ul>
          </>}
        </aside>
      </div>}
    </section>
  );
}

const isCauseLabel = (node) => node.kind === 'cause';
