const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/workforce.js'),'utf8');
const start = source.indexOf('  function categoryAssignment(row) {');
const end = source.indexOf('\n  }',start)+4;
const render = vm.runInNewContext('(' + source.slice(start,end) + ')', {
  E:(tag,attrs,...children)=>({tag,attrs,children}),
  displayDate:value=>new Date(value+'T12:00:00Z').toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'})
});
test('no invented attribution without a binding',()=>assert.equal(render({}),null));
test('name and Moscow assignment date appear under category',()=>{
  const node = render({category_assignment:{assigned_by:'Иванов Иван Иванович',assigned_at:'2026-09-18 22:30:00+00'}});
  assert.equal(node.tag,'small');
  assert.equal(node.children[0].children[0],'Иванов Иван Иванович');
  assert.equal(node.children[1].children[0],'19.09.2026');
});
test('date-only and ISO dates are supported',()=>{
  for(const assigned_at of ['2026-09-19','2026-09-19T07:30:00Z','2026-09-19 07:30:00.123456+00']) {
    assert.equal(render({category_assignment:{assigned_at}}).children[1].children[0],'19.09.2026');
  }
});
test('missing name and invalid legacy timestamp remain explicit',()=>{
  const node=render({category_assignment:{assigned_at:'now'}});
  assert.equal(node.children[0].children[0],'ФИО не указано');
  assert.equal(node.children[1].children[0],'Дата не указана');
});
