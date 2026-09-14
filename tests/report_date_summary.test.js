'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync('static/report-date-summary.js', 'utf8'), context);
const build = args => JSON.parse(JSON.stringify(context.window.reportDateSummary.build(args)));
const fixture = {
  data: {dates: ['2026-09-13'], facts: [
    {work_date: '2026-09-13', subobject_id: 11, employer: 'ЛГСС', day_count: 2, night_count: 1},
    {work_date: '2026-09-13', subobject_id: 11, employer: 'Подрядчик', day_count: 3, night_count: 2},
    {work_date: '2026-09-13', subobject_id: 12, employer: 'ЛГСС', day_count: 4, night_count: 0},
    {work_date: '2026-09-13', subobject_id: 21, employer: '', day_count: 0, night_count: 2},
    {work_date: '2026-09-14', subobject_id: 11, employer: 'ЛГСС', day_count: 100, night_count: 100},
  ]},
  objects: [{id: 1, name: 'Объект 1'}, {id: 2, name: 'Объект 2'}],
  subobjects: [{id: 11, object_id: 1, name: 'Узел А'}, {id: 12, object_id: 1, name: 'Подстанция'}, {id: 21, object_id: 2, name: 'Склад'}]
};
const report = build(fixture);
assert.deepEqual([report.overall.day, report.overall.night, report.overall.count], [9, 5, 14]);
assert.equal(report.employers.reduce((sum, item) => sum + item.count, 0), 14);
assert.equal(report.objects.reduce((sum, item) => sum + item.count, 0), 14);
const lgss = report.employers.find(item => item.name === 'ЛГСС');
assert.deepEqual([lgss.day, lgss.night, lgss.count, lgss.sites], [6, 1, 7, [11, 12]]);
assert.deepEqual([report.objects[0].day, report.objects[0].night, report.objects[0].count], [9, 3, 12]);
assert.equal(report.objects[0].subobjects.find(item => item.id === 11).count, 8);
assert.equal(report.employers.find(item => item.name === '').count, 2);
const filtered = build({...fixture, query: 'объект 1 подстанция'});
assert.deepEqual([filtered.overall.day, filtered.overall.night, filtered.overall.count, filtered.overall.sites], [4, 0, 4, [12]]);
assert.equal(build({...fixture, query: 'Не существует'}).overall.count, 0);
assert.equal(build({...fixture, data: {dates: ['2026-09-15'], facts: []}}).objects.length, 0);
assert.throws(() => build({...fixture, subobjects: []}), /Справочник объектов изменился/);
console.log('Report-date totals, employer/site breakdown, date isolation, filters and empty state: OK');
