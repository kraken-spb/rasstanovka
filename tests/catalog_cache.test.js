const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/reference-cache.js'), 'utf8');
function setup() {
  let now = 0;
  const events = {};
  const window = {addEventListener: (name, fn) => {events[name] = fn;}};
  vm.runInNewContext(source, {window, Date: {now: () => now}, structuredClone});
  return {cache: window.catalogData, advance: ms => {now += ms;}, focus: () => events.focus()};
}
const deferred = () => {let resolve, reject;const promise = new Promise((yes, no) => {resolve = yes;reject = no;});return {promise, resolve, reject};};
test('one request serves concurrent opens, while each renderer gets independent rows', async () => {
  const {cache} = setup();let calls = 0;const gate = deferred();
  const read = () => {calls++;return gate.promise;};
  const a = cache.get('catalog', read), b = cache.get('catalog', read);
  await Promise.resolve();assert.equal(calls, 1);
  gate.resolve({rows: [{id: 1, name: 'Арматурщик'}]});
  const [first, second] = await Promise.all([a, b]);first.rows[0].name = 'Changed by renderer';
  assert.equal(second.rows[0].name, 'Арматурщик');
  assert.equal((await cache.get('catalog', read)).rows[0].name, 'Арматурщик');
  assert.equal(calls, 1);
});
test('expiry starts after success; explicit refresh and focus fetch current data', async () => {
  const {cache, advance, focus} = setup();let calls = 0;
  const read = async () => ({revision: ++calls});
  assert.equal((await cache.get('catalog', read)).revision, 1);
  advance(59999);assert.equal((await cache.get('catalog', read)).revision, 1);
  advance(1);assert.equal((await cache.get('catalog', read)).revision, 2);
  assert.equal((await cache.get('catalog', read, {refresh: true})).revision, 3);
  focus();assert.equal((await cache.get('catalog', read)).revision, 4);
});
test('failed requests are not cached or replaced by stale data', async () => {
  const {cache} = setup();const error = Error('Access revoked');
  await cache.get('catalog', async () => ({rows: [1]}));
  await assert.rejects(cache.get('catalog', async () => {throw error;}, {refresh:true}), error);
  assert.deepEqual(await cache.get('catalog', async () => ({rows: []})), {rows: []});
});
test('invalidation during a request cannot repopulate cache with old data', async () => {
  const {cache} = setup();const gate = deferred();
  const old = cache.get('catalog', () => gate.promise);
  cache.invalidate();
  await cache.get('catalog', async () => ({revision:2}));
  gate.resolve({revision:1});await old;
  assert.equal((await cache.get('catalog', () => {throw Error('Unexpected request');})).revision, 2);
});
test('keys are independent; a delayed old rejection cannot discard a refreshed entry', async () => {
  const {cache} = setup();const gate = deferred();
  const pending = cache.get('one', () => gate.promise);
  const rejected = assert.rejects(pending, /offline/);
  await cache.get('two', async () => ({key:2}));
  await cache.get('one', async () => ({key:1}), {refresh:true});
  gate.reject(Error('offline'));await rejected;
  for(const [key, value] of [['one',1],['two',2]]) assert.equal((await cache.get(key, () => {throw Error('Unexpected request');})).key, value);
});
test('every catalog writer invalidates data even if the write response is lost', async () => {
  for (const file of ['categories.js','smu.js','contractors.js','crew-catalog.js','locations.js','workforce-catalogs.js']) {
    const {cache} = setup();
    const text = fs.readFileSync(path.join(__dirname, '../static', file), 'utf8');
    const apiSource = text.match(/  async function api\([\s\S]*?\n  }/)[0];
    const api = vm.runInNewContext(apiSource + '\napi', {
      window: {catalogData: cache, readApiResponse: response => response.json()},
      root: {dataset: {csrf:'test-only'}},
      fetch: async (_, options) => {
        if (options.method === 'PATCH') throw Error('Response lost');
        return {ok: true, json: async () => ({})};
      }
    });
    let calls = 0;const read = async () => ({revision:++calls});
    await cache.get('catalog', read);
    await api('/reference');
    assert.equal((await cache.get('catalog', read)).revision, 1, file);
    await assert.rejects(api('/catalog/value', {method:'PATCH'}), /Response lost/);
    assert.equal((await cache.get('catalog', read)).revision, 2, file);
  }
});
