import test from 'node:test';
import assert from 'node:assert/strict';
import { sopPageBlocks, blocksText } from './sopPageText.js';

const page12 = [
  'SOP-LFT-001: Lift Station High-High Level and Pump Clog', 'Response', 'SOP No.', 'SOP-LFT-001',
  'Revision', '0', 'Process Area', 'Lift station and wet well', 'Owner', 'Maintenance Manager',
  'SOP Manual – Wastewater Treatment Plant', 'Page 12 of 29',
].join('\n');

test('the title block becomes label/value pairs', () => {
  const blocks = sopPageBlocks(page12, 'SOP-LFT-001');
  assert.equal(blocks[0].type, 'title');
  const meta = blocks.find((b) => b.type === 'meta');
  assert.deepEqual(meta.pairs, [['SOP No.', 'SOP-LFT-001'], ['Revision', '0'], ['Process Area', 'Lift station and wet well'], ['Owner', 'Maintenance Manager']]);
  assert.equal(blocks[blocks.length - 1].type, 'foot');
});

test('every line is kept, in the same order, word for word', () => {
  const blocks = sopPageBlocks(page12, 'SOP-LFT-001');
  assert.equal(blocksText(blocks), page12);
});

test('numbered section titles are headings, numbered sentences are not', () => {
  const blocks = sopPageBlocks('1. Purpose\nTo prevent sewer overflow and flooding when the lift station pumps clog, trip or fail.\n2. Scope\n3. Stop P-101 before you open the panel.', 'SOP-LFT-001');
  assert.deepEqual(blocks.map((b) => b.type), ['heading', 'text', 'heading', 'text']);
});

test('a label with no value after it stays as text', () => {
  const blocks = sopPageBlocks('Owner', 'SOP-X');
  assert.deepEqual(blocks, [{ type: 'text', text: 'Owner' }]);
});

test('empty or missing text gives no blocks', () => {
  assert.deepEqual(sopPageBlocks(''), []);
  assert.deepEqual(sopPageBlocks(undefined), []);
});
