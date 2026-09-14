const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../static/reference-cache.js'), 'utf8');

function setup() {
  let now = 1000;
  const calls = [];
  const context = {window: {}, Date: {now: () => now}, fetch: () => new Promise(resolve => calls.push(resolve))};
  vm.runInNewContext(code, context);
  return {api: context.window.appReference, calls, advance: ms => {now += ms;},
    respond: (index, id, ok = true) => calls[index]({ok, json: async () => ok ? {id} : {error: 'failed'}})};
}
test('concurrent consumers share one request; an expired catalog is fetched again', async () => {
  const s = setup();
  const first = s.api.get();
  assert.equal(s.api.get(), first);
  s.respond(0, 146);
  assert.equal((await first).id, 146);
  assert.equal(s.api.get(), first);
  s.advance(60001);
  const fresh = s.api.get();
  assert.notEqual(fresh, first);
  assert.equal(s.calls.length, 2);
  s.respond(1, 683);
  assert.equal((await fresh).id, 683);
});
test('explicit refresh bypasses the cache immediately', async () => {
  const s = setup(); const old = s.api.get(); s.respond(0, 146); await old;
  const fresh = s.api.get({refresh: true}); s.respond(1, 683);
  assert.equal((await fresh).id, 683);
});
test('a failed obsolete request does not discard a newer catalog', async () => {
  const s = setup(); const old = s.api.get(); old.catch(() => {});
  const fresh = s.api.get({refresh: true}); s.respond(1, 683); await fresh;
  s.respond(0, 0, false); await assert.rejects(old);
  assert.equal(s.api.get(), fresh);
  assert.equal(s.calls.length, 2);
});
test('failed current requests and explicit invalidation allow another fetch', async () => {
  const s = setup(); const bad = s.api.get(); s.respond(0, 0, false); await assert.rejects(bad);
  const good = s.api.get(); s.respond(1, 683); await good;
  s.api.invalidate(); const next = s.api.get(); s.respond(2, 684);
  assert.equal((await next).id, 684);
});
