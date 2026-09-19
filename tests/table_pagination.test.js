const {test} = require('node:test');
const assert = require('node:assert/strict');
const {windowFor, groupWindow} = require('../static/table-pagination.js');
const hierarchy = require('../static/staffing-hierarchy.js');
test('empty results, exact boundary, last page and shrinking filters', () => {
  assert.deepEqual(windowFor(0, 8), {page:0, pages:1, start:0, end:0, numbers:[0]});
  assert.equal(windowFor(100, 1).end, 100);
  assert.equal(windowFor(101, 2).start, 100);
  assert.equal(windowFor(101, 2).end, 101);
  assert.equal(windowFor(4, 9).page, 0);
  assert.equal(windowFor(101, -1).page, 0);
});
test('all employees occur once across pages for each supported size and hierarchy', () => {
  const rows = Array.from({length:137}, (_, i) => ({id:i, crew_id:i % 3, itr_group_key:'itr' + (i % 2), itr_group_label:'ИТР ' + (i % 2), employee_shift:i % 2 ? '1 смена' : '2 смена'}));
  const crews = [0,1,2].map(id => ({id, name:'Бригада ' + id}));
  for (const levels of [['itr','crew'], ['shift','crew','itr']]) {
    const tree = hierarchy.build(rows, levels, crews);
    const ranks = new Map(tree.nodes.map((group, index) => [group.id, index]));
    const ordered = [...rows].sort((a,b) => ranks.get(tree.paths.get(a.id).at(-1)) - ranks.get(tree.paths.get(b.id).at(-1)));
    for (const size of [25,50,100]) {
      const ids = [];
      for (let page = 0; page < windowFor(rows.length, 0, size).pages; page++) {
        const bounds = windowFor(rows.length, page, size);
        ids.push(...ordered.slice(bounds.start, bounds.end).map(row => row.id));
      }
      assert.equal(new Set(ids).size, rows.length);
      assert.deepEqual(ids, ordered.map(row => row.id));
    }
  }
});
test('page navigation stays bounded on large lists and exposes both ends', () => {
  const info = windowFor(50000, 451, 25);
  assert.deepEqual(info.numbers, [0,450,451,452,1999]);
});

test('group pages never split a large crew and include crewless people', () => {
  const rows = Array.from({length:180}, (_,id) => ({id,crew_id:id < 120 ? 1 : id < 179 ? 2 : null}));
  const first = groupWindow(rows, [1,2,null], r => r.crew_id, 0, 1);
  assert.equal(first.total, 3);
  assert.equal(first.rows.length, 120);
  const second = groupWindow(rows, [1,2,null], r => r.crew_id, 1, 1);
  assert.equal(second.rows.length, 59);
  const last = groupWindow(rows, [1,2,null], r => r.crew_id, 9, 1);
  assert.equal(last.page, 2);
  assert.deepEqual(last.rows.map(r => r.id), [179]);
  assert.equal(groupWindow([], [1,2,null], r => r.crew_id, 9, 50).total, 0);
});

test('hierarchy pages keep every descendant under its top-level group', () => {
  const rows = Array.from({length:220}, (_,id) => ({id,crew_id:id % 7,itr_group_key:'itr'+(id % 3),itr_group_label:'ИТР '+(id % 3)}));
  for (const levels of [['itr','crew'], ['crew','itr']]) {
    const tree = hierarchy.build(rows, levels);
    const roots = tree.roots.map(g => g.id), key = row => tree.paths.get(row.id)[0];
    const seen = [];
    for (let page=0;page<roots.length;page++) {
      const result = groupWindow(rows, roots, key, page, 1);
      assert.equal(new Set(result.rows.map(key)).size, 1);
      assert.equal(result.rows.length, tree.byId.get(roots[page]).rows.length);
      seen.push(...result.rows.map(r => r.id));
    }
    assert.equal(new Set(seen).size, rows.length);
    assert.equal(seen.length, rows.length);
  }
});
