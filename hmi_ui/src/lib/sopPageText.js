/**
 * Lays out the text of one SOP PDF page for reading. The PDF extractor gives one
 * line per text box, so the title block comes out as "SOP No." / "SOP-LFT-001" /
 * "Revision" / "0" ... one word per line. This groups those into label/value
 * pairs and marks section headings ("1. Purpose").
 *
 * Words are never changed, dropped or reordered: every input line appears in the
 * output exactly once, in the same order. Only the layout changes.
 *
 * Block types:
 *   { type: 'title',   text }          first line that starts with the SOP id
 *   { type: 'meta',    pairs: [[label, value], ...] }
 *   { type: 'heading', text }          "1. Purpose", "4.2 Isolation"
 *   { type: 'text',    text }          consecutive ordinary lines, joined with \n
 *   { type: 'foot',    text }          "Page 12 of 29"
 */
const META_LABELS = [
  'SOP No.', 'SOP No', 'SOP Number', 'Document No.', 'Revision', 'Rev.', 'Process Area', 'Area',
  'Owner', 'Prepared by', 'Reviewed by', 'Approved by', 'Approval', 'Status',
  'Effective date', 'Issue date', 'Review date', 'Date',
];
const META = new Set(META_LABELS.map((l) => l.toLowerCase()));

const isMetaLabel = (line) => META.has(line.replace(/\s*:\s*$/, '').toLowerCase());
const isFoot = (line) => /^page\s+\d+\s+of\s+\d+$/i.test(line);
// Short numbered line without a closing full stop: a section heading, not a sentence.
const isHeading = (line) => /^\d+(?:\.\d+)*\.?\s+\S/.test(line) && line.length <= 60 && !/[.;:]$/.test(line);

export function sopPageBlocks(text, sopId = '') {
  const lines = String(text || '').split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const blocks = [];
  const push = (block) => {
    const last = blocks[blocks.length - 1];
    if (block.type === 'text' && last?.type === 'text') { last.text += `\n${block.text}`; return; }
    if (block.type === 'meta' && last?.type === 'meta') { last.pairs.push(...block.pairs); return; }
    blocks.push(block);
  };
  let titled = false;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const next = lines[i + 1];
    if (!titled && sopId && line.toUpperCase().startsWith(String(sopId).toUpperCase())) {
      titled = true;
      push({ type: 'title', text: line });
    } else if (isMetaLabel(line) && next != null && !isMetaLabel(next) && !isFoot(next)) {
      push({ type: 'meta', pairs: [[line.replace(/\s*:\s*$/, ''), next]] });
      i += 1;
    } else if (isFoot(line)) {
      push({ type: 'foot', text: line });
    } else if (isHeading(line)) {
      push({ type: 'heading', text: line });
    } else {
      push({ type: 'text', text: line });
    }
  }
  return blocks;
}

/** Every word of the page, in order: used by the test to prove nothing is lost. */
export function blocksText(blocks) {
  return blocks.flatMap((b) => (b.type === 'meta' ? b.pairs.flat() : b.text.split('\n'))).join('\n');
}
