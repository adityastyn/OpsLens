import React, { useState, useEffect, useRef, useCallback } from 'react';
import TimeSeriesPage from './components/hmi/TimeSeriesPage';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import PlantOverview from './components/hmi/PlantOverview';
import './hmi-overview.css';
import './hmi-operator.css';
import './home.css';
import AskOpsLensDrawer from './components/AskOpsLensDrawer';
import SopModal from './components/SopModal';
import { TopBar, NotificationBar, BottomBar, TABS } from './components/home/Chrome';
import HomeMain from './components/home/HomeMain';
import { AlarmsPage, IncidentsPage, RootCausePage, ShiftsPage, SopsPage, priorityOf } from './components/home/Pages';
import InvestigationPath, { stepFor } from './components/home/InvestigationPath';
import { ScenarioSidebar, ConfirmResolutionDialog, RelatedIncidents } from './components/home/Overlays';
import { severityOf, hhmmss } from './lib/format';
import { describeAi } from './lib/aiStatus';
import { useDiagnosis } from './lib/useDiagnosis';
import { resolveEventEquipment } from './lib/equipmentPresentation';
import { isAlarmActive } from './lib/alarmState';
import { ALARM_LABELS, ALARM_PRIORITY } from './lib/plantTags.generated.js';
import { workflowActionFailureMessage, workflowActionSuccessMessage, getWorkflowSubmission } from './lib/operatorWorkflow';
import {
  subscribeToEventStream, getFullEvent, getAlarmHistory, getCurrentHandover, getRankedIncidents,
  logOperatorAction, markIncidentUnresolved, getDataSource, getAiStatus, getOperatorBrief,
  acknowledgeIncident, getIncidentLifecycle, getIncidentInsight, getIncidentTimeline,
} from './api/client';

const OPERATOR_KEY = 'opslens.operator';
const DEFAULT_OPERATOR = 'Ravi';
const OLD_DEFAULT_OPERATOR = 'Control room operator';  // replaced by the named operator
const PAGES = ['Home', ...TABS, 'SOPs'];

function readOperator() {
  try {
    const saved = window.localStorage.getItem(OPERATOR_KEY);
    return saved && saved !== OLD_DEFAULT_OPERATOR ? saved : DEFAULT_OPERATOR;
  } catch (_) { return DEFAULT_OPERATOR; }
}
function saveOperator(name) {
  try { window.localStorage.setItem(OPERATOR_KEY, name); } catch (_) { /* not remembered */ }
}
const isOpen = (i) => i.is_open !== false && i.status !== 'RESOLVED';

export default function App() {
  const [fullEvent, setFullEvent] = useState(null);
  const [currentEventId, setCurrentEventId] = useState(null);
  const [handover, setHandover] = useState(null);
  const [alarmHistory, setAlarmHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [rankedIncidents, setRankedIncidents] = useState([]);
  const [dataSource, setDataSource] = useState(null);
  const [aiStatus, setAiStatus] = useState(null);
  const [rcOpen, setRcOpen] = useState(null);
  const [serviceError, setServiceError] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');
  const [banner, setBanner] = useState(null);
  const [page, setPageState] = useState('Home');
  const [incidentDetail, setIncidentDetail] = useState(false);
  const [operator, setOperatorState] = useState(readOperator);
  const [operatorOpen, setOperatorOpen] = useState(false);
  const [lifecycle, setLifecycle] = useState(null);
  const [brief, setBrief] = useState(null);
  const [insight, setInsight] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [clock, setClock] = useState(() => new Date());
  const [overlay, setOverlay] = useState(null); // 'scenarios' | 'resolve' | 'related' | 'sop'
  const [chatOpen, setChatOpen] = useState(false);

  const currentEventIdRef = useRef(null);
  const pinnedRef = useRef(false);
  const submitRef = useRef(false);

  // Counts page changes, so a slow incident load can't pull the operator back after they moved on.
  const navSeq = useRef(0);
  const setPage = (p) => { if (PAGES.includes(p)) { navSeq.current += 1; setPageState(p); if (p !== 'Incidents') setIncidentDetail(false); } };
  const setOperator = (name) => { setOperatorState(name); saveOperator(name || DEFAULT_OPERATOR); };

  useEffect(() => {
    let live = true;
    const refresh = async () => {
      try { const s = await getDataSource(); if (live) setDataSource(s); } catch (_) { if (live) setDataSource(null); }
      try { const a = await getAiStatus(); if (live) setAiStatus(a); } catch (_) { if (live) setAiStatus(null); }
    };
    refresh();
    const t = setInterval(refresh, 5000);
    return () => { live = false; clearInterval(t); };
  }, []);

  const refreshHandover = useCallback(async () => { try { setHandover(await getCurrentHandover()); } catch (_) { /* keeps the last copy */ } }, []);
  const refreshAlarmHistory = useCallback(async () => {
    try { const r = await getAlarmHistory(); setAlarmHistory(r.alarms || []); } catch (_) { /* keeps the last copy */ } finally { setHistoryLoading(false); }
  }, []);

  const refreshActiveIncident = useCallback(async () => {
    try {
      const ranking = await getRankedIncidents(20);
      if (!ranking || !Array.isArray(ranking.ranked_incidents)) throw new Error('Alarm list unavailable');
      const incidents = ranking.ranked_incidents;
      setRankedIncidents(incidents);
      setServiceError('');
      const current = incidents.find((i) => i.event_id === currentEventIdRef.current);
      const top = incidents[0];
      const follow = !pinnedRef.current && top && (!current || (current.alarm_inputs_active === false && top.alarm_inputs_active !== false));
      const eventId = follow ? top.event_id : currentEventIdRef.current || top?.event_id;
      if (!eventId) { setFullEvent(null); setCurrentEventId(null); return; }
      if (eventId !== currentEventIdRef.current) { currentEventIdRef.current = eventId; setCurrentEventId(eventId); }
      const full = await getFullEvent(eventId);
      // The operator may have picked another incident while this was loading:
      // never put the old one back on screen.
      if (eventId !== currentEventIdRef.current) return;
      if (full) setFullEvent(full);
      else { currentEventIdRef.current = null; pinnedRef.current = false; setCurrentEventId(null); setFullEvent(null); }
    } catch (_) { setServiceError('Active alarms are temporarily unavailable. Retrying…'); }
  }, []);

  const selectIncident = useCallback(async (eventId) => {
    try {
      const full = await getFullEvent(eventId);
      if (!full) { setServiceError('This incident is no longer available.'); return false; }
      currentEventIdRef.current = eventId;
      pinnedRef.current = true;
      setCurrentEventId(eventId);
      setFullEvent(full);
      setServiceError(''); setActionError(''); setBanner(null);
      return true;
    } catch (_) { setServiceError('This incident could not be loaded. Try again.'); return false; }
  }, []);

  // A scenario sends events in bursts: handle the newest one per second.
  const liveRef = useRef({ timer: null });
  const handleLiveEvent = useCallback((payload) => {
    if (!(payload.event_id || payload.data?.event_id) || liveRef.current.timer) return;
    liveRef.current.timer = window.setTimeout(() => { liveRef.current.timer = null; refreshActiveIncident(); refreshHandover(); }, 1000);
  }, [refreshActiveIncident, refreshHandover]);

  useEffect(() => {
    const unsub = subscribeToEventStream(handleLiveEvent, () => {});
    refreshHandover(); refreshAlarmHistory(); refreshActiveIncident();
    const a = setInterval(refreshActiveIncident, 2500);
    const b = setInterval(refreshAlarmHistory, 15000);
    const c = setInterval(refreshHandover, 15000);
    return () => { clearInterval(a); clearInterval(b); clearInterval(c); unsub(); };
  }, [handleLiveEvent, refreshActiveIncident, refreshAlarmHistory, refreshHandover]);
  useEffect(() => { const t = setInterval(() => setClock(new Date()), 1000); return () => clearInterval(t); }, []);

  // Everything about the selected incident refreshes when its facts change.
  const liveAlarmKey = `${fullEvent?.status}-${fullEvent?.runtime_alarm_state?.active}-${(fullEvent?.individual_alarms || []).filter(isAlarmActive).length}`;
  const factsKey = `${fullEvent?.event_id}-${liveAlarmKey}-${(fullEvent?.actions || []).length}-${fullEvent?.story?.steps?.length || 0}`;
  useEffect(() => {
    const id = fullEvent?.event_id;
    if (!id) { setBrief(null); setInsight(null); setTimeline(null); return undefined; }
    let live = true;
    getOperatorBrief(id).then((b) => live && setBrief(b)).catch(() => live && setBrief(null));
    getIncidentInsight(id).then((d) => live && setInsight(d)).catch(() => live && setInsight(null));
    getIncidentTimeline(id).then((d) => live && setTimeline(d)).catch(() => live && setTimeline(null));
    return () => { live = false; };
  }, [factsKey]);

  const refreshLifecycle = useCallback(async (id = currentEventIdRef.current) => {
    if (!id) { setLifecycle(null); return null; }
    try { const s = await getIncidentLifecycle(id); if (id === currentEventIdRef.current) setLifecycle(s); return s; }
    catch (_) { if (id === currentEventIdRef.current) setLifecycle(null); return null; }
  }, []);
  useEffect(() => {
    setLifecycle(null);
    if (!currentEventId) return undefined;
    refreshLifecycle(currentEventId);
    const t = setInterval(() => refreshLifecycle(currentEventId), 2500);
    return () => clearInterval(t);
  }, [currentEventId, refreshLifecycle]);

  // One diagnosis stream for the selected incident, shared by every panel.
  const diagnosis = useDiagnosis(fullEvent?.event_id, liveAlarmKey);

  const runAction = async (actionType, notes = '', id = currentEventIdRef.current) => {
    if (!id || submitRef.current) return false;
    submitRef.current = true; setBusy(true); setActionError('');
    try {
      let message = workflowActionSuccessMessage(actionType);
      const submission = getWorkflowSubmission(actionType, notes);
      if (actionType === 'ACKNOWLEDGE') message = (await acknowledgeIncident(id, operator)).message;
      else if (submission.kind === 'status') await markIncidentUnresolved({ eventId: id, operator, reason: submission.reason });
      else await logOperatorAction({ eventId: id, operator, actionType: submission.actionType, notes: submission.notes });
      if (actionType === 'RESOLVE') pinnedRef.current = false;
      await Promise.all([refreshHandover(), refreshAlarmHistory(), refreshActiveIncident(), refreshLifecycle(id)]);
      setBanner({ cls: 'ok', text: message });
      return true;
    } catch (error) {
      const text = workflowActionFailureMessage(actionType, error);
      setActionError(text);
      if (actionType === 'ACKNOWLEDGE') setBanner({ cls: 'bad', text });
      return false;
    } finally { submitRef.current = false; setBusy(false); }
  };

  const acknowledgeOne = async (id) => { if (id !== currentEventIdRef.current) await selectIncident(id); await runAction('ACKNOWLEDGE', '', id); };

  // Re-reads the equipment state and readings, then says plainly whether the fix worked.
  const verifyStatus = async () => {
    const id = currentEventIdRef.current;
    if (!id) return;
    setBusy(true);
    setBanner({ cls: 'info', text: 'Checking the equipment status and reloading the readings…' });
    try {
      const [state, full] = await Promise.all([refreshLifecycle(id), getFullEvent(id)]);
      if (full) setFullEvent(full);
      const [b, ins] = await Promise.all([getOperatorBrief(id).catch(() => null), getIncidentInsight(id).catch(() => null)]);
      if (b) setBrief(b);
      if (ins) setInsight(ins);
      const failing = (state?.criteria || []).filter((c) => !c.passed && c.id !== 'acknowledged');
      const at = hhmmss(new Date().toISOString());
      if (!state) setBanner({ cls: 'bad', text: 'The status could not be checked. Try again.' });
      else if (!failing.length) setBanner({ cls: 'ok', text: `${at} · Readings are back to normal and the alarms have cleared.${state.can_resolve ? ' Use Confirm Resolution to log it.' : ` Still needed: ${(state.blocking || []).join('; ')}.`}` });
      else setBanner({ cls: 'bad', text: `${at} · Not fixed yet: ${failing.map((c) => c.label).join('; ')}. Carry out the Next Best Action, then press Verify Status again.` });
    } finally { setBusy(false); }
  };

  // Messages float over the screen and go away by themselves; they never move the panels.
  useEffect(() => {
    if (!banner || banner.cls === 'info') return undefined;
    const t = window.setTimeout(() => setBanner(null), banner.cls === 'bad' ? 12000 : 6000);
    return () => window.clearTimeout(t);
  }, [banner]);

  const submitResolution = async (choice, note) => { if (await runAction(choice, note)) setOverlay(null); };

  // Derived values for the bars.
  const open = rankedIncidents.filter(isOpen);
  const counts = { high: 0, med: 0, low: 0 };
  let activeCount = 0;
  // Each active alarm counts under its own configured priority (HIGH / MEDIUM / LOW);
  // an alarm without one takes its incident's priority.
  const bucket = (p) => ({ HIGH: 'high', CRITICAL: 'high', MEDIUM: 'med', MED: 'med', LOW: 'low' }[String(p || '').toUpperCase()]);
  open.forEach((i) => {
    (i.individual_alarms || []).filter(isAlarmActive).forEach((a) => {
      activeCount += 1;
      counts[bucket(a.priority) || bucket(ALARM_PRIORITY[a.tag]) || bucket(priorityOf(i))] += 1;
    });
  });
  const selected = rankedIncidents.find((i) => i.event_id === currentEventId);
  const navIndex = Math.max(0, open.findIndex((i) => i.event_id === currentEventId));
  const step = (d) => { if (open.length) selectIncident(open[(navIndex + d + open.length) % open.length].event_id); };
  const sev = severityOf(selected, brief);
  // Top-bar line: the viewed incident's priority, green once resolved, dark blue if logged unresolved.
  const viewedStatus = String(selected?.status || fullEvent?.status || '').toUpperCase();
  const topLine = !fullEvent ? (open.length ? bucket(priorityOf(open[0])) : 'ok')
    : viewedStatus === 'RESOLVED' ? 'resolved' : viewedStatus === 'UNRESOLVED' ? 'unresolved'
    : (lifecycle && (lifecycle.alarm_inputs || []).length && lifecycle.active_count === 0) ? 'resolved'
    : bucket(priorityOf(selected || {})) || 'high';
  const rankOrder = open.map((i) => ({ id: i.event_id, title: ALARM_LABELS[i.primary_tag] || i.primary_tag, equipment: i.display_equipment || i.equipment_id || '',
    prio: priorityOf(i), facts: i.ranking_facts, vsNext: i.ranking_vs_next }));
  const equipmentId = resolveEventEquipment(fullEvent);
  const sourceText = String(fullEvent?.runtime_alarm_state?.data_source || lifecycle?.alarm_inputs?.[0]?.data_source || dataSource?.mode || '').toLowerCase();
  const sourceTag = /simulat|synthetic/.test(sourceText) ? 'SIM' : /stale/.test(sourceText) ? 'STALE' : /opc|plc|live/.test(sourceText) ? 'LIVE PLC' : fullEvent ? 'RECORDED' : 'NO DATA';
  const ai = describeAi(aiStatus);
  // A resolved incident opened from the records still shows; its state tag says Resolved.
  const incidentInfo = fullEvent ? {
    sev: { cls: sev.cls, label: sev.label === '—' ? 'ALARM' : sev.label },
    // Rule-based urgency 0–100 from the backend (deviation, rate, evidence, persistence, data quality).
    urgency: Number.isFinite(Number(fullEvent.urgency?.urgency_score)) && fullEvent.urgency?.urgency_score != null
      ? { score: Math.round(Number(fullEvent.urgency.urgency_score)), band: fullEvent.urgency.urgency_band, reasons: fullEvent.urgency.urgency_reasons || [] } : null,
    title: brief?.problem?.alarm_description || ALARM_LABELS[fullEvent.primary_tag] || fullEvent.primary_tag,
    prio: (bucket(priorityOf(selected || {})) || 'high'),
    // When the group of alarms behind this incident began: its earliest alarm.
    startedAt: (fullEvent.individual_alarms || []).map((a) => a.first_seen_timestamp).filter(Boolean).sort()[0]
      || brief?.problem?.first_triggered_at || fullEvent.trigger_timestamp || null,
    equipment: brief?.problem?.equipment || equipmentId || '—',
    alarmTag: `${brief?.problem?.alarm_tag || fullEvent.primary_tag} · ${fullEvent.event_id}`,
    alarmCount: (fullEvent.individual_alarms || []).filter(isAlarmActive).length || (lifecycle?.alarm_inputs || []).length,
    occurrences: selected?.repeat_count || fullEvent.repeat_count || 1,
    // After the fix: every alarm input is back to normal (lifecycle), or the operator confirmed resolution.
    resolved: String(fullEvent.status || selected?.status || '').toUpperCase() === 'RESOLVED' || lifecycle?.state === 'RESOLVED',
    recovered: Boolean(lifecycle && (lifecycle.inputs_total ?? (lifecycle.alarm_inputs || []).length) > 0 && lifecycle.active_count === 0),
    canResolve: Boolean(lifecycle?.can_resolve),
  } : null;
  const current = { fullEvent, brief, incident: selected, lifecycle };
  const openIncidentPage = async (id, detail = false) => {
    const seq = navSeq.current;
    if (await selectIncident(id) && navSeq.current === seq) { setPage('Incidents'); setIncidentDetail(detail); }
  };
  // Investigation path: go to a step of the same investigation.
  const goStep = (to) => {
    if (to.verify) { verifyStatus(); return; }
    if (to.view) setRcOpen((o) => ({ view: to.view, n: (o?.n || 0) + 1 }));
    setPage(to.page);
    if (to.detail) setIncidentDetail(true);
  };
  const goHome = async (id) => { const seq = navSeq.current; if (await selectIncident(id) && navSeq.current === seq) setPage('Home'); };
  const bannerEl = (banner || serviceError) && (
    <div role="status" className={`hm-banner hm-toast ${serviceError && !banner ? 'bad' : banner.cls}`}>
      <span>{banner?.text || serviceError}</span>
      {banner && <button type="button" className="hm-icon-btn" aria-label="Dismiss message" onClick={() => setBanner(null)}>×</button>}
    </div>
  );

  return (
    <div className={`hmi-app hm-app ${page === 'Home' ? 'is-home' : 'is-tab'} ${chatOpen ? 'chat-open' : ''}`}>
      <TopBar home={page === 'Home'} page={page} setPage={setPage} onLogo={() => setOverlay('scenarios')} counts={counts} activeCount={activeCount}
        clock={clock} sourceTag={sourceTag} ai={ai} handover={handover} line={topLine}
        operatorProps={{ operator, setOperator, open: operatorOpen, setOpen: setOperatorOpen, defaultName: DEFAULT_OPERATOR }} />
      {page === 'Home' && <NotificationBar incident={incidentInfo} confidence={insight?.confidence} component={insight?.component} sop={insight?.sop} lifecycle={lifecycle}
        nav={{ index: navIndex, count: open.length, prev: () => step(-1), next: () => step(1), reason: selected?.ranking_reason, order: rankOrder, currentId: currentEventId, pick: (id) => selectIncident(id) }} busy={busy}
        onAcknowledge={() => runAction('ACKNOWLEDGE')} onDetails={() => { setPage('Incidents'); setIncidentDetail(true); }}
        onViewSop={() => setOverlay('sop')} onChat={() => setChatOpen((o) => !o)} onRelated={() => setOverlay('related')}
        onResolve={() => { setActionError(''); setOverlay('resolve'); }} />}
      <div className="hm-body">
        {page === 'Home' && (incidentInfo
          ? <ErrorBoundary label="Home" resetKey={`${currentEventId}`}><HomeMain brief={brief} insight={insight} lifecycle={lifecycle} timeline={timeline} diagnosis={diagnosis} banner={bannerEl}
            onFullStory={() => { setPage('Incidents'); setIncidentDetail(true); }} onViewAlarms={() => setPage('Alarms')}
            onRootCause={() => { setRcOpen((o) => ({ view: 'Knowledge graph', n: (o?.n || 0) + 1 })); setPage('Root Cause & Analysis'); }} onTrends={() => setPage('Time-Series & Prediction')} /></ErrorBoundary>
          : <main className="hm-main empty">{bannerEl}<div className="hm-idle"><h2>Plant normal</h2><p>No open incident. {activeCount ? '' : 'Readings and alarms are being watched.'}</p>
            <button type="button" className="hm-btn" onClick={() => setPage('Incidents')}>View past incidents</button></div></main>)}
        {page !== 'Home' && <div className="hm-tab-body">
          {bannerEl}
          <ErrorBoundary label={page} resetKey={`${page}${currentEventId}`}>
          {fullEvent && page !== 'Root Cause & Analysis' && <InvestigationPath current={stepFor(page, null, incidentDetail)} onGo={goStep} />}
          {page === 'Alarms' && <AlarmsPage incidents={rankedIncidents} alarmHistory={alarmHistory} loading={historyLoading} busy={busy} onAcknowledge={acknowledgeOne} onOpenIncident={goHome} />}
          {page === 'Incidents' && <IncidentsPage incidents={rankedIncidents} detail={incidentDetail} setDetail={setIncidentDetail} onOpenIncident={openIncidentPage} current={current} refreshKey={factsKey} />}
          {page === 'Root Cause & Analysis' && <RootCausePage current={current} refreshKey={factsKey} open={rcOpen} onGo={fullEvent ? goStep : null} />}
          {page === 'Time-Series & Prediction' && <div className="hm-page"><TimeSeriesPage forecastOn eventId={fullEvent?.event_id} incidentTags={brief?.scenario?.incident_tags}><details><summary>Whole-plant readings</summary><PlantOverview /></details></TimeSeriesPage></div>}
          {page === 'Shifts History' && <ShiftsPage handover={handover} onSelectIncident={goHome} onOpenAlarms={() => setPage('Alarms')} onRefresh={refreshHandover} />}
          {page === 'SOPs' && <SopsPage suggested={insight?.sop?.sop_id} />}
          </ErrorBoundary>
        </div>}
      </div>
      <BottomBar page={page} hasIncident={Boolean(fullEvent)} busy={busy} chatOpen={chatOpen}
        onVerify={verifyStatus} onSops={() => setPage('SOPs')} onChat={() => setChatOpen((o) => !o)}
        onResolve={() => { setActionError(''); setOverlay('resolve'); }} onShift={() => setPage('Shifts History')} />

      <AskOpsLensDrawer event={fullEvent} equipment={equipmentId} isOpen={chatOpen} onClose={() => setChatOpen(false)} />
      <SopModal isOpen={overlay === 'sop'} onClose={() => setOverlay(null)} sopData={fullEvent ? { ...(fullEvent.sop_match || {}), sop_resolution: fullEvent.sop_resolution, resolved_sop_sources: fullEvent.resolved_sop_sources } : null} />
      {overlay === 'scenarios' && <ScenarioSidebar eventId={fullEvent?.event_id} onClose={() => setOverlay(null)} />}
      {overlay === 'related' && <RelatedIncidents history={insight?.history} onClose={() => setOverlay(null)} onOpen={(id) => { setOverlay(null); openIncidentPage(id, true); }} />}
      {overlay === 'resolve' && fullEvent && <ConfirmResolutionDialog eventId={fullEvent.event_id} title={incidentInfo?.title || fullEvent.primary_tag} operator={operator}
        lifecycle={lifecycle} busy={busy} error={actionError} onSubmit={submitResolution} onClose={() => setOverlay(null)} />}
    </div>
  );
}
