const assert=require('node:assert/strict');
const {test}=require('node:test');
const {objectsFor,filteredMonths}=require('../static/smg.js');
const nodes=[{id:'total'}], period='2026-09-01';
const part=(scope,value)=>({scope,label:scope,values:{staff:{total:value},rental:{total:'0'},outstaff:{total:null},total:{total:value}}});
const edition=(scope,objects)=>({scope,months:[{period,breakdown:{objects}}]});
function result(editions,kind='staff',objectKeys=null,periods=[period]){
 return filteredMonths(editions,new Set(editions.map(e=>e.scope)),new Set(objectKeys||editions.flatMap(objectsFor).map(o=>o.key)),periods,kind,nodes);
}
test('PPS and child objects contribute once; selected objects limit the total',()=>{
 const editions=[edition('ППС-19',[part('19.1','533'),part('19.2','457'),part('19.3','22')]),edition('ППС-15',[part('15','2658.0666666666666')])];
 assert.equal(result(editions)[0].values.total,3670.0666666666666);
 const keys=[objectsFor(editions[0])[0].key,objectsFor(editions[1])[0].key];
 assert.equal(result(editions,'staff',keys)[0].values.total,3191.0666666666666);
});
test('zero, missing cells, missing month and empty selection remain distinct',()=>{
 const e=[edition('ППС-19',[part('19.1','2')])];
 assert.equal(result(e,'rental')[0].values.total,0);
 assert.equal(result(e,'outstaff')[0].values.total,null);
 assert.equal(result(e,'staff',null,['2026-10-01'])[0].values.total,null);
 assert.equal(result(e,'staff',[])[0].values.total,null);
});
test('legacy monthly plans never invent rental data or object-level values',()=>{
 const e={scope:'ППС-19',months:[{period,values:{total:'100'}},{period:'2026-10-01',breakdown:{objects:[part('19.1','60'),part('19.2','40')]}}]};
 assert.equal(result([e])[0].values.total,100);
 assert.equal(result([e],'rental')[0].values.total,null);
 assert.equal(result([e],'staff',[objectsFor(e)[0].key])[0].values.total,null);
});
