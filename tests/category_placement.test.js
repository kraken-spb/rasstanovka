'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync('static/category-placement.js', 'utf8'), context);
const build = context.window.categoryPlacement.build;
const person = (id, status, contractor) => ({id, status, contractor});
const data = {groups: [
  {category: 'Рабочие', pps: '15', total: 3, assigned: 1, unassigned: 1, absent: 1,
    companies: {'А': {total: 2, assigned: 1, unassigned: 0, absent: 1}, 'Б': {total: 1, assigned: 0, unassigned: 1, absent: 0}},
    people: [person(1, 'assigned', 'А'), person(2, 'absent', 'А'), person(3, 'unassigned', 'Б')]},
  {category: 'Рабочие', pps: '19', total: 1, assigned: 0, unassigned: 1, absent: 0,
    companies: {'А': {total: 1, assigned: 0, unassigned: 1, absent: 0}}, people: [person(4, 'unassigned', 'А')]},
  {category: '', pps: '19', total: 1, assigned: 1, unassigned: 0, absent: 0,
    companies: {'': {total: 1, assigned: 1, unassigned: 0, absent: 0}}, people: [person(5, 'assigned', '')]}
]};
const before = JSON.stringify(data), result = build(data);
assert.equal(result.categories.length, 2);
const workers = result.categories.find(item => item.category === 'Рабочие');
assert.deepEqual([workers.total, workers.assigned, workers.unassigned, workers.absent], [4, 1, 2, 1]);
assert.equal(workers.companies.get('А').total, 3);
assert.equal(workers.companies.get('Б').unassigned, 1);
assert.equal(result.overall.total, 5);
assert.equal(result.overall.companies.get('').assigned, 1);
for (const group of [...result.categories, result.overall]) {
  assert.equal(group.total, group.assigned + group.unassigned + group.absent);
  assert.equal(group.total, group.people.length);
  for (const [company, counts] of group.companies) for (const key of ['total', 'assigned', 'unassigned', 'absent']) {
    assert.equal(counts[key], group.people.filter(p => p.contractor === company && (key === 'total' || p.status === key)).length);
  }
}
assert.equal(JSON.stringify(data), before);
assert.equal(build({groups: []}).overall.total, 0);
assert.equal(build({groups: [data.groups[1]]}).categories[0].total, 1);
console.log('Category/PPS aggregation, contractor and status drill counts, missing category, filtered and empty results: OK');
