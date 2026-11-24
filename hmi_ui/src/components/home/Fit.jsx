import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { unzoom } from '../../lib/uiScale.js';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';

const MORE_HEIGHT = 40; // the "+N more" button (32 px) and the gap above it

/** Full content in a pop-up, for anything that did not fit in its panel. */
export function MoreSheet({ title, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="hm-scrim center" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="hm-dialog fit-full" role="dialog" aria-modal="true" aria-label={title}>
        <header><h2>{title}</h2><button type="button" className="hm-icon-btn" aria-label="Close" onClick={onClose}><X size={22} /></button></header>
        <div className="fit-full-body">{children}</div>
      </section>
    </div>
  );
}

/**
 * Measure again whenever the space or the text may have changed: the box was
 * resized, the window was resized or re-scaled, or a web font finished loading
 * (text set in the fallback font first and then reflowed was the main cause of
 * cut-off panels). A slow check every 1.5 s also catches anything else, for
 * browsers without ResizeObserver or document.fonts (older embedded panels).
 */
function useRemeasure(ref, onCheck) {
  const check = useRef(onCheck);
  check.current = onCheck;
  useEffect(() => {
    const run = () => check.current(false);
    const fontsChanged = () => check.current(true); // text reflowed: measure again even if nothing sticks out
    const observer = ref.current && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(run) : null;
    if (observer) observer.observe(ref.current);
    window.addEventListener('resize', run);
    const fonts = typeof document !== 'undefined' ? document.fonts : null;
    if (fonts?.addEventListener) fonts.addEventListener('loadingdone', fontsChanged);
    if (fonts?.ready) fonts.ready.then(fontsChanged).catch(() => {});
    const timer = setInterval(run, 1500);
    return () => {
      if (observer) observer.disconnect();
      window.removeEventListener('resize', run);
      if (fonts?.removeEventListener) fonts.removeEventListener('loadingdone', fontsChanged);
      clearInterval(timer);
    };
  }, [ref]);
}

/** Bottom of each child, in layout pixels from the top of the box. */
function bottoms(box) {
  const top = box.getBoundingClientRect().top;
  return (el) => unzoom(el.getBoundingClientRect().bottom - top);
}

/** Something sticks out of the box, or the box changed size since it was measured. */
function needsMeasure(box, measured) {
  if (!box || !measured || measured.fresh || measured.h == null) return false;
  if (Math.abs(box.clientHeight - measured.h) > 1 || Math.abs(box.clientWidth - measured.w) > 1) return true;
  const bottom = bottoms(box);
  // One block taller than the whole box is shown anyway (its text is clamped); do not loop on it.
  if (measured.count <= 1) return false;
  return Array.from(box.children).some((el) => bottom(el) > box.clientHeight + 1);
}

/**
 * Shows as many blocks as fit in the panel, never a scrollbar. Hidden blocks
 * are counted on a "+N more" button, and every block, and any sentence cut
 * with "…", opens in full in a pop-up. Nothing is silently cut off.
 */
export function FitBody({ children, title, signature = '', moreText = 'more', className = '' }) {
  const ref = useRef(null);
  const items = React.Children.toArray(children).filter(Boolean);
  const [shown, setShown] = useState(Infinity);
  const [clamped, setClamped] = useState(false);
  const [open, setOpen] = useState(false);
  const measured = useRef(null);
  const text = useRef('');
  const remeasure = useCallback((force) => {
    if ((force && measured.current) || needsMeasure(ref.current, measured.current)) { measured.current = null; setShown(Infinity); }
  }, []);
  useRemeasure(ref, remeasure);
  useLayoutEffect(() => { measured.current = null; setShown(Infinity); }, [signature, items.length]);
  useLayoutEffect(() => {
    const box = ref.current;
    if (!box) return;
    if (shown !== Infinity) {
      if (measured.current?.fresh) { measured.current.fresh = false; text.current = box.textContent; return; }
      // Text changed without a new signature (an AI answer arriving, a label filled in): measure again.
      if (box.textContent !== text.current || needsMeasure(box, measured.current)) { measured.current = null; setShown(Infinity); }
      return;
    }
    const kids = Array.from(box.children).filter((el) => !el.classList.contains('fit-more'));
    const height = box.clientHeight;
    const bottom = bottoms(box);
    const isClamped = Array.from(box.querySelectorAll('.clamp')).some((el) => el.scrollHeight > el.clientHeight + 1);
    const fitsAll = kids.every((el) => bottom(el) <= height + 1);
    let count = kids.length;
    if (!fitsAll || isClamped) {
      const room = height - MORE_HEIGHT; // keep space for the "+N more" button
      count = 0;
      while (count < kids.length && bottom(kids[count]) <= room) count += 1;
    }
    const finalCount = Math.max(count, Math.min(1, kids.length));
    measured.current = { h: height, w: box.clientWidth, count: finalCount, fresh: true };
    setShown(finalCount);
    setClamped(isClamped);
  });
  const visible = shown === Infinity ? items : items.slice(0, shown);
  const hidden = items.length - visible.length;
  const onClick = (e) => {
    const target = e.target.closest('.clamp');
    if (target && target.scrollHeight > target.clientHeight + 1) setOpen(true);
  };
  return (
    <>
      <div ref={ref} className={`fit ${className}`} onClick={onClick}>
        {visible}
        {(hidden > 0 || clamped) && shown !== Infinity && (
          <button type="button" className="fit-more" onClick={() => setOpen(true)}>
            {hidden > 0 ? `+${hidden} ${moreText}` : 'Read all'} ›
          </button>
        )}
      </div>
      {open && <MoreSheet title={title} onClose={() => setOpen(false)}>{items}</MoreSheet>}
    </>
  );
}

/**
 * Steps in pages that fit the panel, with large page buttons instead of
 * scrolling. Page sizes follow the real height of each step.
 */
const PAGER_HEIGHT = 58;

export function Paged({ items, renderItem, signature = '', label = 'Steps' }) {
  const ref = useRef(null);
  const [start, setStart] = useState(0);
  const [count, setCount] = useState(null);
  const [history, setHistory] = useState([]);
  const measured = useRef(null);
  const text = useRef('');
  const remeasure = useCallback((force) => {
    if ((force && measured.current) || needsMeasure(ref.current, measured.current)) { measured.current = null; setCount(null); }
  }, []);
  useRemeasure(ref, remeasure);
  useLayoutEffect(() => { measured.current = null; setStart(0); setHistory([]); setCount(null); }, [signature]);
  useLayoutEffect(() => {
    const box = ref.current;
    if (!box) return;
    if (count !== null) {
      // First pass after measuring: the page buttons are on screen now, so remember the size and text from here.
      if (measured.current?.fresh) { measured.current = { h: box.clientHeight, w: box.clientWidth, count, fresh: false }; text.current = box.textContent; return; }
      if (box.textContent !== text.current || needsMeasure(box, measured.current)) { measured.current = null; setCount(null); }
      return;
    }
    const kids = Array.from(box.children);
    const bottom = bottoms(box);
    // The page buttons are not on screen while measuring and appear only when
    // steps are left over (or on a later page): keep room for them then.
    const paged = start > 0 || kids.some((el) => bottom(el) > box.clientHeight + 1);
    const room = box.clientHeight - (paged ? PAGER_HEIGHT : 0);
    let fit = 0;
    while (fit < kids.length && bottom(kids[fit]) <= room + 1) fit += 1;
    measured.current = { fresh: true };
    setCount(Math.max(1, fit));
  });
  const shown = count === null ? items.slice(start) : items.slice(start, start + count);
  const end = start + shown.length;
  const next = () => { setHistory((h) => [...h, start]); setStart(end); setCount(null); };
  const prev = () => { setHistory((h) => { const copy = h.slice(); setStart(copy.pop() ?? 0); return copy; }); setCount(null); };
  return (
    <>
      <ol ref={ref} className="paged">{shown.map(renderItem)}</ol>
      {items.length > 0 && (start > 0 || end < items.length) && count !== null && (
        <div className="pager" aria-label={`${label} pages`}>
          <button type="button" className="hm-btn" aria-label={`Previous ${label.toLowerCase()}`} disabled={start === 0} onClick={prev}><ChevronLeft size={22} /></button>
          <span>{label} {start + 1}–{end} of {items.length}</span>
          <button type="button" className="hm-btn" aria-label={`Next ${label.toLowerCase()}`} disabled={end >= items.length} onClick={next}><ChevronRight size={22} /></button>
        </div>
      )}
    </>
  );
}

/**
 * One line of text that steps its font size down (to `min` px) until it fits
 * its box, and only then ends in "…". For headings in fixed-height bars.
 */
export function FitLine({ as: Tag = 'span', text, min = 14, ...rest }) {
  const ref = useRef(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = () => setTick((t) => t + 1);
    window.addEventListener('resize', bump);
    const fonts = typeof document !== 'undefined' ? document.fonts : null;
    if (fonts?.addEventListener) fonts.addEventListener('loadingdone', bump);
    return () => {
      window.removeEventListener('resize', bump);
      if (fonts?.removeEventListener) fonts.removeEventListener('loadingdone', bump);
    };
  }, []);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.fontSize = '';
    let size = parseFloat(getComputedStyle(el).fontSize) || 16;
    while (el.scrollWidth >= el.clientWidth && size > min) { // ">=": sub-pixel overflow still shows "…"
      size -= 1;
      el.style.fontSize = `${size}px`;
    }
  }, [text, min, tick]);
  return <Tag ref={ref} title={text} {...rest}>{text}</Tag>;
}
