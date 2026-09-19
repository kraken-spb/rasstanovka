'use strict';
// Trusted implementation only. Input supplies a RegExp pattern and text, never code.
global.window = {};
require('../static/search-regex.js');
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
try {
  const pattern = new RegExp(window.SearchRegex.source(input.query), 'iu');
  const ids = input.rows.filter(row => row.fields.some(value => pattern.test(value))).map(row => row.id);
  process.stdout.write(JSON.stringify({ids}));
} catch (_) {
  process.stdout.write(JSON.stringify({error: 'invalid_regex'}));
}
