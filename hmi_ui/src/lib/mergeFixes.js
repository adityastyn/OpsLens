/**
 * Earlier fixes as one entry per distinct fix. Two notes are the same fix when
 * their words match, ignoring letter case, punctuation, spacing and numbers
 * (dates and times written into the note). Newest first.
 */
export function fixKey(note) {
  return String(note || '').toLowerCase().normalize('NFKC').replace(/[^a-z]+/g, ' ').trim();
}

export function mergeFixes(earlier) {
  const fixes = [];
  (earlier || []).filter((e) => e && e.resolution_notes).forEach((e) => {
    const key = fixKey(e.resolution_notes);
    if (!key) return;
    const seen = fixes.find((f) => f.key === key);
    if (seen) { seen.when.push(e.when_text); if (e.event_id) seen.events.push(e.event_id); }
    else fixes.push({ key, text: String(e.resolution_notes).trim(), when: [e.when_text], events: e.event_id ? [e.event_id] : [], simulated: Boolean(e.seeded_history) });
  });
  return fixes;
}

/** "(fixed this way 2 times, last 07 Nov 14:25)" or "(07 Nov 14:25)". */
export function fixWhen(fix) {
  return fix.when.length > 1 ? `fixed this way ${fix.when.length} times, last ${fix.when[0]}` : fix.when[0];
}
