/* Run with node --test tests/js/test_kpi_dashboard_charts.cjs. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../../static/js/people-kpi-dashboard.js'), 'utf8');

test('charts initialize once, dispose before HTMX cleanup, and rebuild after swaps/theme changes', () => {
  const events = {};
  let created = 0; let destroyed = 0; let themeChanged;
  const canvas = {dataset: {kpiChart: 'coverage', kpiConfig: '{"labels":["Recorded","Missing"],"data":[2,1]}'}};
  const document = {readyState: 'loading', querySelectorAll: () => [canvas], addEventListener: (name, fn) => {events[name] = fn;}, documentElement: {contains: () => true}};
  const window = {Chart: function(_canvas, config) {created++; assert.equal(config.type, 'doughnut'); this.destroy = () => {destroyed++;};}};
  const context = {window, document, Map, JSON, Number, MutationObserver: class {constructor(fn) {themeChanged=fn;} observe() {}}};
  vm.runInNewContext(code, context);
  events.DOMContentLoaded(); events['htmx:afterSwap']();
  assert.equal(created, 1);
  events['htmx:beforeCleanupElement']({detail: {elt: {contains: () => true}}});
  assert.equal(destroyed, 1);
  events['htmx:afterSwap'](); assert.equal(created, 2);
  themeChanged(); assert.equal(destroyed, 2); assert.equal(created, 3);
  vm.runInNewContext(code, context); assert.equal(created, 3);
});

test('invalid payloads leave accessible fallback text without throwing', () => {
  let created = 0;
  const context = {window: {Chart: function() {created++;}}, document: {readyState: 'complete', querySelectorAll: () => [{dataset: {kpiConfig: '<invalid>'}}], addEventListener() {}, documentElement: {}}, Map, JSON, Number, MutationObserver: class {observe() {}}};
  vm.runInNewContext(code, context);
  assert.equal(created, 0);
});
