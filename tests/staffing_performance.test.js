const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/staffing.js'), 'utf8');
const start = source.indexOf('  function pendingRows(');
const end = source.indexOf('  function selectedRows(', start);
assert.ok(start >= 0 && end > start);

function fixture(count=251, change=null) {
  const rows = Array.from({length:count}, (_, i) => ({id:i+1,crew_id:7,group_token:'same',search_fields:['Name']}));
  const state = {request:1,date:'2026-09-18',imported:{id:9},rows,crews:[{id:7}]};
  const calls = {active:0,maximum:0,requests:0,hydrated:[],render:0,reload:0};
  const context = {state, Map, Number, Promise,
    calendarQuery:()=>'',
    load:()=>{calls.reload++;},render:()=>{calls.render++;},
    hydrateRow:(row,fresh)=>{calls.hydrated.push(row.id);Object.assign(row,fresh);delete row.search_fields;},
    api:async url=>{
      calls.active++;calls.maximum=Math.max(calls.maximum,calls.active);calls.requests++;
      const ids=new URL('http://local'+url).searchParams.get('worker_ids').split(',').map(Number);
      await new Promise(setImmediate);
      const result={import:{id:9},rows:ids.map(id=>({id,crew_id:7,group_token:'same',assignment_id:12}))};
      if(change)change(result,state,ids);
      calls.active--;return result;
    }};
  vm.createContext(context);vm.runInContext(source.slice(start,end),context);
  return {rows,state,calls,context};
}

test('hydration bounds request concurrency and merges all batches only after validation',async()=>{
  const f=fixture();await f.context.loadPageRows(f.rows);
  assert.equal(f.calls.maximum,2);assert.equal(f.calls.requests,3);
  assert.equal(f.calls.hydrated.length,251);assert.equal(new Set(f.calls.hydrated).size,251);
  assert.ok(f.rows.every(row=>!row.search_fields));
});

test('a conflicting late batch cannot leave a partially hydrated selection',async()=>{
  const f=fixture(251,(data,_state,ids)=>{if(ids.includes(251))data.rows.at(-1).group_token='changed';});
  await assert.rejects(f.context.loadPageRows(f.rows),/Состав сотрудников изменился/);
  assert.equal(f.calls.hydrated.length,0);
});

test('responses from a previous report-date request are ignored',async()=>{
  const f=fixture(50,(_data,state)=>{state.request++;});
  await f.context.loadPageRows(f.rows);assert.equal(f.calls.hydrated.length,0);
});

test('targeted refresh falls back to the authoritative list when membership changes',async()=>{
  const f=fixture(120,(data,_state,ids)=>{if(ids.includes(120))data.rows.pop();});
  await f.context.refreshWorkers(f.rows);
  assert.equal(f.calls.reload,1);assert.equal(f.calls.hydrated.length,0);assert.equal(f.calls.render,0);
});

test('targeted refresh recomputes assignment totals without directory reloads',async()=>{
  const f=fixture(4);await f.context.refreshWorkers(f.rows.slice(0,2));
  assert.equal(f.calls.requests,1);assert.equal(f.calls.reload,0);assert.equal(f.calls.render,1);
  assert.equal(f.state.crews[0].assigned,2);
});

test('report drill-through membership is refreshed as a whole after a write',async()=>{
  const f=fixture(3);f.state.calendarFilter={kind:'changes'};
  await f.context.refreshWorkers(f.rows);
  assert.equal(f.calls.reload,1);assert.equal(f.calls.requests,0);
});
