const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Exercise the actual routing block without loading unrelated application forms.
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('  // Section navigation');
const end = source.indexOf('  function setBusy', start);
assert.ok(start >= 0 && end > start);
const routing = source.slice(start, end);
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };

function app({workforce = true, hash = '#workforce', role = 'admin', entries = null, index = 0} = {}) {
  const calls = {activate:[], deactivate:0, guards:0, staffing:0, employees:0, locations:0, errors:[]};
  const hooks = {leave:true, activate:null};
  const listeners = new Map();
  const state = {busy:false, pendingPlans:0, planWaiters:[], planErrors:new Map(), planDrafts:new Set()};
  const views = ['staffing','employees','placement','dashboard','catalogs', ...(workforce ? ['workforce'] : [])].map(view => ({
    id:'view-' + view, active:false, classList:{toggle(_name, active) { this.active = active; }}
  }));
  const buttons = [...views.map(node => node.id.slice(5)), ...(workforce ? ['workforce/rotation','workforce/recruitment'] : [])].map(view => ({dataset:{view}, active:false,classList:{toggle(_name,active) {this.active=active;}}, addEventListener() {}}));
  const stack = entries ? structuredClone(entries) : [{state:null, hash}];
  let cursor = index;
  const location = {get hash() {return stack[cursor].hash;}};
  const window = {
    addEventListener(type, callback) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(callback); },
    staffingScreen:{canLeave:() => true, load:async () => {calls.staffing++;}, openFromCalendar:async () => {calls.staffing++;}},
    employeesScreen:{canLeave:() => true, load:async () => {calls.employees++;}}
  };
  if (workforce) window.workforceScreen = {
    routeView(route) {
      const match = /^workforce(?:\/(rotation|recruitment))?(?:\/people\/([1-9]\d*))?$/.exec(route);
      return match && (!match[2] || Number.isSafeInteger(Number(match[2]))) ? 'workforce' : null;
    },
    canLeave() {calls.guards++;return hooks.leave;},
    activate:async route => {calls.activate.push(route);if (hooks.activate) await hooks.activate(route);},
    deactivate() {calls.deactivate++;}
  };
  const dispatch = (type, value) => {for (const callback of listeners.get(type) || []) callback({type, state:value});};
  const history = {
    get state() {return stack[cursor].state;},
    pushState(value, _title, nextHash) {stack.splice(cursor + 1);stack.push({state:structuredClone(value), hash:nextHash});cursor++;},
    replaceState(value, _title, nextHash) {stack[cursor] = {state:structuredClone(value), hash:nextHash};},
    go(delta) {
      const next = cursor + delta;
      if (next < 0 || next >= stack.length) return;
      cursor = next;
      const target = structuredClone(stack[cursor]);
      queueMicrotask(() => {dispatch('popstate', target.state);dispatch('hashchange');});
    },
    back() {this.go(-1);}, forward() {this.go(1);}
  };
  const context = {window, history, location, role, readOnly:['hr_viewer','rotation','recruitment'].includes(role), state,
    all:selector => selector === '.view' ? views : selector === '[data-view]' ? buttons : [],
    $:selector => selector === '.brand' ? {addEventListener() {}} : null,
    toast:message => calls.errors.push(message), loadLocationReference:async () => {calls.locations++;},
    loadCrews:async () => {}, loadCalendar:async () => {}, loadManagement:async () => {}};
  vm.runInNewContext(routing, context, {filename:'app-navigation.js'});
  return {window, history, location, state, calls, hooks, stack, context, buttons, index:() => cursor,
    navigate:context.switchView, dispatch};
}

test('list opens a card in history; close/back/forward reuse activation and one guard per transition', async () => {
  const a = app();
  await a.navigate('workforce');
  a.calls.guards = 0;
  await a.window.openWorkforcePerson(12);
  assert.equal(a.location.hash, '#workforce/rotation/people/12');
  assert.equal(a.stack.length, 2);
  assert.equal(a.calls.guards, 1);
  a.window.closeWorkforcePerson();
  a.window.closeWorkforcePerson();
  await settle();
  assert.equal(a.location.hash, '#workforce/rotation');
  assert.equal(a.calls.guards, 2);
  a.history.forward();await settle();
  assert.equal(a.location.hash, '#workforce/rotation/people/12');
  assert.deepEqual(a.calls.activate, ['workforce/rotation','workforce/rotation/people/12','workforce/rotation','workforce/rotation/people/12']);
  assert.equal(a.calls.guards, 3);
  assert.equal(a.calls.locations, 0);
});

test('declined browser Back restores the original entry without activating or discarding the form', async () => {
  const a = app();await a.navigate('workforce');await a.window.openWorkforcePerson(12);
  const before = a.calls.activate.length;a.calls.guards = 0;a.hooks.leave = false;
  a.history.back();await settle();
  assert.equal(a.location.hash, '#workforce/rotation/people/12');
  assert.equal(a.index(), 1);
  assert.equal(a.calls.activate.length, before);
  assert.equal(a.calls.deactivate, 0);
  assert.equal(a.calls.guards, 1);
  a.hooks.leave = true;a.history.back();await settle();
  assert.equal(a.location.hash, '#workforce/rotation');
  assert.equal(a.calls.guards, 2);
});

test('direct card link closes to the list with replace; refresh retains a known list return entry', async () => {
  const direct = app({hash:'#workforce/people/9'});
  await direct.navigate('workforce/people/9');
  await direct.window.closeWorkforcePerson();
  assert.equal(direct.location.hash, '#workforce/rotation');
  assert.equal(direct.stack.length, 1);
  const before = app();await before.navigate('workforce');await before.window.openWorkforcePerson(9);
  const refreshed = app({entries:before.stack,index:before.index()});
  await refreshed.navigate('workforce/rotation/people/9');
  refreshed.window.closeWorkforcePerson();await settle();
  assert.equal(refreshed.location.hash, '#workforce/rotation');
  assert.equal(refreshed.index(), 0);
});

test('sidebar guards run before URL/deactivation and successful departure uses the normal section loader', async () => {
  const a = app({hash:'#workforce/people/9'});await a.navigate('workforce/people/9');
  a.hooks.leave = false;
  assert.equal(await a.navigate('staffing'), false);
  assert.equal(a.location.hash, '#workforce/people/9');assert.equal(a.calls.deactivate, 0);
  a.hooks.leave = true;await a.navigate('staffing');
  assert.equal(a.location.hash, '#staffing');assert.equal(a.calls.deactivate, 1);assert.equal(a.calls.staffing, 1);
});

test('unsafe IDs and malformed hashes do not reach a CSS selector or the card API; non-HR baseline remains usable', async () => {
  const a = app();await a.navigate('staffing');
  for (const value of [0,-1,1.5,'1e2','1/x','9007199254740992']) assert.equal(await a.window.openWorkforcePerson(value), false);
  assert.equal(a.location.hash, '#staffing');
  await a.navigate('workforce/people/1]');assert.equal(a.location.hash, '#placement');
  const legacy = app({workforce:false, role:'viewer', hash:'#workforce/people/1'});
  await legacy.navigate('workforce/people/1');
  assert.equal(legacy.location.hash, '#dashboard');
  assert.equal(await legacy.window.openWorkforcePerson(1), false);
});

test('a superseded navigation waiting on plan saves does not replace the newer destination', async () => {
  const a = app();await a.navigate('workforce');
  a.state.pendingPlans = 1;
  const older = a.navigate('staffing'), newer = a.navigate('employees');
  a.state.pendingPlans = 0;a.state.planWaiters.forEach(resolve => resolve());
  assert.equal(await older, false);assert.equal(await newer, true);
  assert.equal(a.location.hash, '#employees');assert.equal(a.calls.staffing, 0);assert.equal(a.calls.employees, 1);
});

test('late activation failure cannot change or report an error over a newer card route', async () => {
  const a = app();await a.navigate('workforce');
  let rejectOlder;
  a.hooks.activate = route => route.endsWith('/1') ? new Promise((_resolve, reject) => {rejectOlder = reject;}) : undefined;
  const older = a.window.openWorkforcePerson(1);
  await a.window.openWorkforcePerson(2);rejectOlder(new Error('Old request failed'));await older;
  assert.equal(a.location.hash, '#workforce/rotation/people/2');assert.deepEqual(a.calls.errors, []);
});

test('same-card refresh replaces history and preserves the list return target', async () => {
  const a = app();await a.navigate('workforce');await a.window.openWorkforcePerson(7);
  await a.window.openWorkforcePerson(7);assert.equal(a.stack.length, 2);
  a.window.closeWorkforcePerson();await settle();
  assert.equal(a.location.hash, '#workforce/rotation');assert.equal(a.index(), 0);
});

test('both HR sections retain card context, highlight their own menu and restore on Back', async () => {
  for (const section of ['rotation','recruitment']) {
    const a=app(), route='workforce/'+section;
    await a.navigate(route);await a.window.openWorkforcePerson(24);
    assert.equal(a.location.hash,'#'+route+'/people/24');
    assert.equal(a.buttons.find(button => button.dataset.view===route).classList.active,true);
    a.window.closeWorkforcePerson();await settle();
    assert.equal(a.location.hash,'#'+route);
    a.history.forward();await settle();assert.equal(a.location.hash,'#'+route+'/people/24');
  }
});

test('direct recruitment cards close to recruitment and a rejected section switch preserves the draft route', async () => {
  const a=app({hash:'#workforce/recruitment/people/9'});
  await a.navigate('workforce/recruitment/people/9');a.hooks.leave=false;
  assert.equal(await a.navigate('workforce/rotation'),false);
  assert.equal(a.location.hash,'#workforce/recruitment/people/9');
  a.hooks.leave=true;await a.window.closeWorkforcePerson();
  assert.equal(a.location.hash,'#workforce/recruitment');assert.equal(a.stack.length,1);
  const recruiter=app({role:'recruitment'});await recruiter.navigate('workforce');
  assert.equal(recruiter.location.hash,'#workforce/recruitment');
});
