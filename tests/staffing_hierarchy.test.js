const {test} = require('node:test');
const assert = require('node:assert/strict');
const H = require('../static/staffing-hierarchy.js');
const crews = [{id: 1, name: 'Бригада 1'}, {id: 2, name: 'Бригада 2'}];
const rows = [
  {id: 1, crew_id: 1, itr_group_key: 'person-a', itr_group_label: 'Иванов', employee_shift: '1 смена', employer: 'Организация А'},
  {id: 2, crew_id: 1, itr_group_key: 'person-b', itr_group_label: 'Петров', employee_shift: '2 смена', employer: 'Организация А'},
  {id: 3, crew_id: 2, itr_group_key: 'person-a', itr_group_label: 'Иванов', employee_shift: '1 смена', employer: 'Организация Б'},
  {id: 4, crew_id: null, itr_group_key: 'none', itr_group_label: 'Линейный ИТР не указан', employee_shift: null}
];
test('ITR then crew partitions a shared crew and each employee occurs in exactly one leaf', () => {
  const tree=H.build(rows,['itr','crew'],crews);
  assert.equal(tree.roots.length,3);
  const ivanov=tree.roots.find(n=>n.label==='Иванов');
  assert.equal(ivanov.children.length,2);
  assert.deepEqual(ivanov.rows.map(r=>r.id),[1,3]);
  const leaves=tree.nodes.filter(n=>!n.children.length);
  assert.deepEqual(leaves.flatMap(n=>n.rows.map(r=>r.id)).sort(),[1,2,3,4]);
  assert.notEqual(tree.paths.get(1).at(-1),tree.paths.get(2).at(-1));
  assert.equal(tree.byId.get(tree.paths.get(4).at(-1)).label,'Без бригады');
});
test('reordering levels changes parentage without altering worker membership', () => {
  const original=structuredClone(rows);
  const tree=H.build(rows,['crew','itr'],crews);
  const first=tree.roots.find(n=>n.label==='Бригада 1');
  assert.equal(first.children.length,2);
  assert.deepEqual(first.rows.map(r=>r.id),[1,2]);
  assert.deepEqual(rows,original);
});
test('full ancestor paths enable parent counts, selection and expansion at arbitrary depth', () => {
  const tree=H.build(rows,['employer','shift','itr','crew'],crews);
  for(const row of rows) {
    const path=tree.paths.get(row.id);
    assert.equal(path.length,4);
    path.forEach((id,depth)=>{
      const node=tree.byId.get(id);
      assert.equal(node.depth,depth);
      assert.deepEqual(node.ancestorIds,path.slice(0,depth));
      assert(node.rows.includes(row));
    });
  }
  assert.equal(tree.nodes.filter(n=>n.field==='shift' && n.label==='Не определена').length,1);
});
test('same names with different ITR identities never merge, paths are stable and selector safe', () => {
  const input=[{...rows[0],itr_group_label:'Одно имя'}, {...rows[1],itr_group_label:'Одно имя'}];
  const first=H.build(input,['itr','crew'],crews), reversed=H.build([...input].reverse(),['itr','crew'],crews);
  assert.equal(first.roots.length,2);
  assert.deepEqual(first.paths.get(1),reversed.paths.get(1));
  assert(first.nodes.every(n=>!/["<>]/.test(n.id)));
});
test('duplicate worker input cannot double counts and invalid level lists are rejected', () => {
  const tree=H.build([...rows,rows[0]],['crew'],crews);
  assert.equal(tree.roots.reduce((sum,n)=>sum+n.rows.length,0),4);
  for(const invalid of [[],['crew','crew'],['unknown'],null,['__proto__']]) {
    assert.equal(H.valid(invalid),false);
    assert.throws(()=>H.build(rows,invalid,crews));
  }
});
