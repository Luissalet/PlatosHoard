import test from 'node:test';
import assert from 'node:assert/strict';
import { arrangementOrder, importedNameOrder, mountImportedNameChain } from '../../static/editor/arrangement.mjs';

test('re-fitting an existing chain preserves its chosen outer silhouette', () => {
  const layers = [{ id: 'small', parent_id: 'middle' }, { id: 'outer' }, { id: 'middle', parent_id: 'outer' }];
  const result = arrangementOrder(layers, layer => layer.id === 'middle' ? 999 : 1);
  assert.deepEqual(result.map(layer => layer.id), ['outer', 'middle', 'small']);
});

test('separate roots are arranged by their displayed size', () => {
  const layers = [{ id: 'small', area: 10 }, { id: 'large', area: 100 }];
  assert.deepEqual(arrangementOrder(layers, layer => layer.area).map(layer => layer.id), ['large', 'small']);
  assert.equal(layers[0].id, 'small');
});

test('initial import uses national number descending regardless of arrival or area', () => {
  const layers = [
    { id: 'a', name: '0825 Sugimori Style', area: 999 },
    { id: 'b', name: '0826 Sugimori Style', area: 1 },
    { id: 'c', name: '0824 Sugimori Style', area: 500 },
  ];
  assert.deepEqual(importedNameOrder(layers).map(layer => layer.id), ['b', 'a', 'c']);
});

test('initial import falls back to natural reverse alphabetical order', () => {
  const layers = [{ name: 'alpha 9' }, { name: 'zeta' }, { name: 'alpha 10' }];
  assert.deepEqual(importedNameOrder(layers).map(layer => layer.name), ['zeta', 'alpha 10', 'alpha 9']);
});

test('screen-drop batch builds 0826 > 0825 > 0824 without touching prior layers', async () => {
  const layers = [
    { id: '825', name: '0825 Sugimori Style', area: 999, parent_id: null },
    { id: '826', name: '0826 Sugimori Style', area: 1, parent_id: null },
    { id: '824', name: '0824 Sugimori Style', area: 500, parent_id: null },
  ];
  const document = {
    marco: { id: 'marco', name: 'Marco', parent_id: null, order: 0, locked: true },
    prior: { id: 'prior', parent_id: 'prior-parent', order: 7 },
    ...Object.fromEntries(layers.map(layer => [layer.id, { ...layer }])),
  };
  const commands = [];
  const ordered = await mountImportedNameChain(layers, async (id, parentId) => {
    commands.push([id, parentId]);
    document[id].parent_id = parentId;
  });
  assert.deepEqual(ordered.map(layer => layer.id), ['826', '825', '824']);
  assert.equal(document['826'].parent_id, null);
  assert.equal(document['825'].parent_id, '826');
  assert.equal(document['824'].parent_id, '825');
  assert.deepEqual(document.marco, { id: 'marco', name: 'Marco', parent_id: null, order: 0, locked: true });
  assert.deepEqual(document.prior, { id: 'prior', parent_id: 'prior-parent', order: 7 });
  assert.deepEqual(commands, [['825', '826'], ['824', '825']]);
});
