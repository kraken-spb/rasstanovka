const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/view-modules.js'),'utf8');
const tick=()=>new Promise(setImmediate);
function setup(){
 const appended=[];
 const markers=[['catalogs','/first.js'],['catalogs','/second.js'],['logs','/logs.js']].map(([viewModule,src])=>({dataset:{viewModule,src}}));
 const window={};const document={querySelectorAll:()=>markers,createElement:()=>({remove(){this.removed=true;}}),head:{append:s=>appended.push(s)}};
 vm.runInNewContext(source,{window,document,Map,Promise,Error});return {window,appended};
}
test('section modules load once in dependency order and unrelated sections stay unloaded',async()=>{
 const {window,appended}=setup();await window.loadViewModules('workforce');assert.equal(appended.length,0);
 const a=window.loadViewModules('catalogs'),b=window.loadViewModules('catalogs');
 assert.deepEqual(appended.map(s=>s.src),['/first.js']);appended[0].onload();await tick();
 assert.deepEqual(appended.map(s=>s.src),['/first.js','/second.js']);appended[1].onload();await Promise.all([a,b]);
 await window.loadViewModules('catalogs');assert.equal(appended.length,2);
});
test('failed section loading is explicit and a later open retries',async()=>{
 const {window,appended}=setup();const first=window.loadViewModules('logs');
 appended[0].onerror();await assert.rejects(first,/Не удалось загрузить раздел/);assert.equal(appended[0].removed,true);
 const second=window.loadViewModules('logs');assert.equal(appended.length,2);appended[1].onload();await second;
});
