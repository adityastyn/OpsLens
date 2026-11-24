import { useCallback, useEffect, useRef, useState } from 'react';
import { streamDiagnosis } from '../api/client';

const SETTLE_MS = 1500;

export const STAGE_TEXT = {
  checking_model: 'Checking the local model',
  queued: 'Waiting for the model to be free',
  loading_model: 'Loading the model into memory',
  retrying: 'The answer was cut off; asking again with shorter reasoning',
  generating: 'Writing the assessment',
};

export const SOURCE_STYLE = {
  ai_generated: { cls: 'good', text: 'AI-GENERATED' },
  deterministic_fallback: { cls: 'warn', text: 'DETERMINISTIC FALLBACK' },
  sop_retrieval_only: { cls: 'warn', text: 'SOP RETRIEVAL ONLY' },
  ai_unavailable: { cls: 'bad', text: 'AI UNAVAILABLE' },
};

/**
 * Follows one incident's diagnosis. The model runs in a backend job, so leaving
 * the page or remounting does not start a second generation: the next request
 * re-attaches to the same job. retry() is the only thing that forces a new one.
 */
export function useDiagnosis(eventId, refreshKey) {
  const [draft, setDraft] = useState('');
  const [result, setResult] = useState(null);
  const [meta, setMeta] = useState(null);
  const [stage, setStage] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [nonce, setNonce] = useState(0);
  const forceRef = useRef(false);

  useEffect(() => {
    if (!eventId) { setDraft(''); setResult(null); setMeta(null); setError(''); setLoading(false); return undefined; }
    const controller = new AbortController();
    const started = performance.now();
    const refresh = forceRef.current;
    forceRef.current = false;
    setDraft(''); setResult(null); setMeta(null); setError(''); setStage(''); setLoading(true); setElapsed(0);
    const tick = window.setInterval(() => setElapsed((performance.now() - started) / 1000), 250);
    // An incident gains alarms in a burst. Wait for it to settle before asking.
    const settle = window.setTimeout(() => {
      streamDiagnosis(eventId, {
        signal: controller.signal,
        refresh,
        onMeta: setMeta,
        onStatus: (s) => { setStage(s.stage); if (s.stage === 'retrying') setDraft(''); },
        onToken: (t) => setDraft((d) => d + t),
        onDone: (r) => { if (r.failure_kind !== 'superseded') setResult(r); },
        onError: (e) => setError(e.detail || 'Diagnosis failed.'),
      }).catch((e) => { if (e.name !== 'AbortError') setError(`Diagnosis unavailable: ${e.message}`); })
        .finally(() => { window.clearInterval(tick); if (!controller.signal.aborted) setLoading(false); });
    }, refresh ? 0 : SETTLE_MS);
    return () => { window.clearTimeout(settle); controller.abort(); window.clearInterval(tick); };
  }, [eventId, refreshKey, nonce]);

  const retry = useCallback(() => { forceRef.current = true; setNonce((n) => n + 1); }, []);
  return { draft, result, meta, stage, error, loading, elapsed, retry };
}
