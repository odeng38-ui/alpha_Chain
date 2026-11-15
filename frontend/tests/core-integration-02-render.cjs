// Isolated SSR checks of the selected dashboard's loaded UI states.
// Effects are disabled: no server, browser, network, or operational DB is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const frontend = path.resolve(__dirname, '..');
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function(request, ...args) {
  return originalResolve.call(this, request.startsWith('@/') ? path.join(frontend, 'src', request.slice(2)) : request, ...args);
};
for (const extension of ['.ts', '.tsx']) {
  require.extensions[extension] = (module, filename) => {
    const result = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      fileName: filename,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    });
    module._compile(result.outputText, filename);
  };
}
global.fetch = () => { throw new Error('network forbidden'); };
const { EventFirstDashboard } = require('../src/components/event-first-dashboard.tsx');
const originals = { useState: React.useState, useEffect: React.useEffect, useMemo: React.useMemo };
function render(values) {
  let index = 0;
  React.useState = (initial) => [index < values.length ? values[index++] : typeof initial === 'function' ? initial() : initial, () => {}];
  React.useEffect = () => {};
  React.useMemo = (factory) => factory();
  try {
    return renderToStaticMarkup(React.createElement(EventFirstDashboard, { asOf: '2026-11-15' }));
  } finally {
    Object.assign(React, originals);
  }
}

const output = path.resolve(frontend, '../backend/.core-integration-02-results');
fs.mkdirSync(output, { recursive: true });
const empty = render([[], null, null, 'ready', 'idle']);
assert.match(empty, /저장된 글로벌 이벤트 없음/);
assert.doesNotMatch(empty, /Micron|TC Bonder|SUPPLIES_TO/);
assert.doesNotMatch(empty, /type="checkbox"/);
fs.writeFileSync(path.join(output, 'empty-dashboard.html'), empty);

// Use the actual response written by the isolated HTTP boundary test.
const pending = JSON.parse(fs.readFileSync(path.join(output, 'pending-analysis.json'), 'utf8'));
const item = { id: pending.event.event_id, event_kind: 'CAPEX_EXPANSION', title: pending.event.title,
  occurred_at: pending.event.occurred_at, source_type: 'stored', is_demo: false };
const html = render([[item], item.id, pending, 'ready', 'ready']);
assert.match(html, /적격 공급망 후보 없음/);
assert.match(html, /event.not.verified/);
assert.doesNotMatch(html, /TC Bonder|\$150B|class="ef-candidate panel"/);
fs.writeFileSync(path.join(output, 'pending-dashboard.html'), html);

const unavailable = render([[], null, null, 'error', 'idle', 'event_first_storage_unavailable']);
assert.match(unavailable, /event_first_storage_unavailable/);
assert.doesNotMatch(unavailable, /저장된 글로벌 이벤트 없음/);
fs.writeFileSync(path.join(output, 'unavailable-dashboard.html'), unavailable);
console.log('3 SSR states passed: empty, API pending response, storage unavailable. No browser interaction tested.');
