/**
 * The HMI is laid out for a 1280 × 800 panel. On a larger panel (for example
 * 1920 × 1200) the whole interface is scaled up evenly so it keeps the same
 * layout and fills the screen, instead of showing small text with empty space.
 *
 * Scale = the smaller of width / 1280 and height / 800, between 1 and 2, in
 * steps of 0.05 (1920 × 1200 → 1.5). Below 1280 × 800 nothing changes.
 * Override with ?scale=1 (off) or ?scale=1.25 in the address bar.
 */
export const BASE = { width: 1280, height: 800 };

export function scaleFor(width, height, override) {
  const forced = Number(override);
  if (override != null && override !== '' && Number.isFinite(forced) && forced > 0) return Math.min(2, Math.max(0.5, forced));
  const raw = Math.min(width / BASE.width, height / BASE.height);
  if (!(raw > 1.04)) return 1;
  return Math.min(2, Math.floor(raw * 20) / 20);
}

/** Apply CSS zoom to the page; --ui-zoom lets viewport-height styles undo it. */
export function applyUiScale(win = window) {
  const override = new URLSearchParams(win.location.search).get('scale');
  const set = () => {
    const z = scaleFor(win.innerWidth, win.innerHeight, override);
    const root = win.document.documentElement;
    root.style.zoom = z === 1 ? '' : String(z);
    root.style.setProperty('--ui-zoom', String(z));
    win.__opslensZoom = z;
    rectRatio = null; // re-measure on the next use
  };
  set();
  win.addEventListener('resize', set);
  return () => win.removeEventListener('resize', set);
}

/**
 * How many getBoundingClientRect pixels make one layout (CSS) pixel.
 *
 * Browsers do not agree on this under CSS zoom: Chrome 128 and later report
 * rects zoomed (1.5 on a 1920 × 1200 panel), older Chromium builds such as the
 * ones embedded in EcoStruxure Operator Terminal Expert and many kiosk
 * browsers report them unzoomed (1). Dividing by the zoom on those browsers made
 * every panel think it had half again as much room as it did, so content was
 * cut off. Measure it instead of assuming it.
 */
let rectRatio = null;
export function rectScale(doc = typeof document !== 'undefined' ? document : null) {
  if (rectRatio != null) return rectRatio;
  if (!doc?.body) return 1;
  const probe = doc.createElement('div');
  probe.style.cssText = 'position:absolute;left:0;top:0;width:100px;height:100px;visibility:hidden;pointer-events:none;padding:0;border:0;margin:0';
  doc.body.appendChild(probe);
  const ratio = probe.getBoundingClientRect().width / (probe.offsetWidth || 100);
  probe.remove();
  rectRatio = Number.isFinite(ratio) && ratio > 0 ? Math.round(ratio * 1000) / 1000 : 1;
  return rectRatio;
}
export const resetRectScale = () => { rectRatio = null; };

/** Convert a getBoundingClientRect value to layout pixels (for heights and position: fixed under the zoom). */
export const unzoom = (px) => px / rectScale();

/** Width of the window in layout pixels. */
export const layoutViewportWidth = () => (typeof window === 'undefined' ? BASE.width : window.innerWidth / ((window.__opslensZoom) || 1));
