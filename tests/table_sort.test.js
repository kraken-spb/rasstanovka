const {test}=require('node:test');
const assert=require('node:assert/strict');
const {compare}=require('../static/table-sort.js');
test('nested natural ordering, nulls last in both directions and stable ties',()=>{
  const rows=[{id:4,name:'Г',smu:'СМУ 10',tab:'2'},{id:3,name:'В',smu:'СМУ 2',tab:'10'},
    {id:2,name:'Б',smu:'СМУ 2',tab:'2'},{id:1,name:'А',smu:null,tab:'1'}];
  const value=(r,k)=>r[k];
  const levels=[{field:'smu',direction:'asc'},{field:'tab',direction:'asc'}];
  assert.deepEqual([...rows].sort((a,b)=>compare(a,b,levels,value)).map(r=>r.id),[2,3,4,1]);
  levels[0].direction='desc';
  assert.deepEqual([...rows].sort((a,b)=>compare(a,b,levels,value)).map(r=>r.id),[4,2,3,1]);
  assert(compare({id:2,name:'Иван'},{id:1,name:'Иван'},[],value)>0);
});
test('sorting uses full lightweight population before slicing or hydration',()=>{
  const rows=Array.from({length:120},(_,i)=>({id:i+1,search_fields:['Имя '+(120-i)]}));
  const value=(row,key)=>key==='name'?(row.search_fields?row.search_fields[0]:row.full_name):null;
  const levels=[{field:'name',direction:'asc'}];
  const ordered=[...rows].sort((a,b)=>compare(a,b,levels,value));
  assert.equal(ordered[0].id,120);assert.equal(ordered[50].id,70);
  const before=ordered.map(r=>r.id);
  for(const r of ordered.slice(0,50)){r.full_name=r.search_fields[0];delete r.search_fields;}
  assert.deepEqual([...rows].sort((a,b)=>compare(a,b,levels,value)).map(r=>r.id),before);
});
