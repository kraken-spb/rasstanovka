const assert = require('node:assert/strict');
const {test} = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../static/api-response.js'), 'utf8');

function setup() {
  const window = {};
  Object.defineProperty(window, 'location', {
    get() { assert.fail('The response reader must not inspect or navigate the page'); },
    set() { assert.fail('The response reader must not navigate away from drafts'); }
  });
  vm.runInNewContext(code, {window, URL, Error, JSON, fetch: () => assert.fail('The response reader must not retry requests')});
  return window.readApiResponse;
}
function response(body, status = 200, type = 'application/json') {
  return new Response(body, {status, headers: {'Content-Type': type}});
}

test('valid JSON objects, arrays and null are returned without reshaping', async () => {
  const read = setup();
  for (const value of [{rows: [{id: 1}]}, [{key: 'rotation', label: 'Перевахта'}], [], null]) {
    assert.deepEqual(await read(response(JSON.stringify(value))), value);
  }
});

for (const status of [400, 409]) test(`JSON ${status} preserves the server message and HTTP status`, async () => {
  const read = setup(), message = 'Данные изменены. Обновите карточку.';
  await assert.rejects(read(response(JSON.stringify({error: message}), status), 'Запасное сообщение.'), error => {
    assert.ok(error instanceof Error);
    assert.equal(error.message, message);
    assert.equal(error.status, status);
    return true;
  });
});

for (const status of [500, 502, 200]) test(`HTML ${status} becomes a safe Russian error without leaking its body`, async () => {
  const read = setup(), html = '<!doctype html><title>INTERNAL_PRIVATE_TRACE</title><body>secret-host:1234</body>';
  await assert.rejects(read(response(html, status, 'text/html'), 'Не удалось загрузить справочник.'), error => {
    assert.equal(error.status, status);
    assert.match(error.message, /Не удалось загрузить справочник/);
    assert.match(error.message, /Сервер вернул некорректный ответ/);
    assert.match(error.message, new RegExp(`HTTP ${status}`));
    assert.doesNotMatch(error.message + error.stack, /<!doctype|INTERNAL_PRIVATE_TRACE|secret-host|Unexpected token/);
    assert.equal(error.cause, undefined);
    return true;
  });
});

test('a redirect to the login page requests another login without navigation or retry', async () => {
  const read = setup(), result = response('<!doctype html><form>PRIVATE_LOGIN_CONTENT</form>', 200, 'text/html');
  Object.defineProperties(result, {redirected: {value: true}, url: {value: 'https://example.test/login?next=%2Fapi%2Freference'}});
  await assert.rejects(read(result), error => {
    assert.equal(error.status, 200);
    assert.match(error.message, /Требуется повторный вход/);
    assert.doesNotMatch(error.message + error.stack, /PRIVATE_LOGIN_CONTENT|next=/);
    return true;
  });
});

test('malformed JSON is sanitized even when the content type claims JSON', async () => {
  await assert.rejects(setup()(response('{"private":"INTERNAL_PRIVATE_VALUE",', 200)), error => {
    assert.equal(error.status, 200);
    assert.match(error.message, /HTTP 200/);
    assert.doesNotMatch(error.message + error.stack, /INTERNAL_PRIVATE_VALUE|Unexpected|SyntaxError/);
    return true;
  });
});

test('HTTP failures without a string error use the caller fallback, including array payloads', async () => {
  const read = setup();
  for (const value of [null, [], {error: {private: 'server internals'}}]) {
    await assert.rejects(read(response(JSON.stringify(value), 503), 'Не удалось прочитать данные.'), error => {
      assert.equal(error.status, 503);
      assert.equal(error.message, 'Не удалось прочитать данные.');
      return true;
    });
  }
});

test('a failed body read does not expose transport internals', async () => {
  const result = {status: 502, ok: false, text: async () => { throw new Error('PRIVATE_TRANSPORT_DETAIL'); }};
  await assert.rejects(setup()(result), error => {
    assert.equal(error.status, 502);
    assert.match(error.message, /HTTP 502/);
    assert.doesNotMatch(error.message + error.stack, /PRIVATE_TRANSPORT_DETAIL/);
    return true;
  });
});
