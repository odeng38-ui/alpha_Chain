const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// Transpile TypeScript moving-average.ts
const filePath = path.resolve(__dirname, '../src/lib/moving-average.ts');
const source = fs.readFileSync(filePath, 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});

const moduleObj = { exports: {} };
const fn = new Function('module', 'exports', transpiled.outputText);
fn(moduleObj, moduleObj.exports);

const { calculateSMA, calculateMultipleSMAs } = moduleObj.exports;

console.log('Testing calculateSMA...');

// Test 1: Empty candles
assert.deepEqual(calculateSMA([], 5), []);

// Test 2: Invalid period
const sample3 = [
  { date: '2026-11-01', open: 100, high: 110, low: 90, close: 100, volume: 10 },
  { date: '2026-11-02', open: 100, high: 110, low: 90, close: 110, volume: 10 },
];
assert.deepEqual(calculateSMA(sample3, 0), [
  { date: '2026-11-01', value: null },
  { date: '2026-11-02', value: null },
]);

// Test 3: Less candles than period
const sample4 = [
  { date: '2026-11-01', open: 100, high: 110, low: 90, close: 10, volume: 10 },
  { date: '2026-11-02', open: 100, high: 110, low: 90, close: 20, volume: 10 },
  { date: '2026-11-03', open: 100, high: 110, low: 90, close: 30, volume: 10 },
  { date: '2026-11-04', open: 100, high: 110, low: 90, close: 40, volume: 10 },
];
const result4 = calculateSMA(sample4, 5);
assert.equal(result4.length, 4);
assert.ok(result4.every((r) => r.value === null));

// Test 4: Exactly 5 candles with MA5
const sample5 = [
  ...sample4,
  { date: '2026-11-05', open: 100, high: 110, low: 90, close: 50, volume: 10 },
];
const result5 = calculateSMA(sample5, 5);
assert.equal(result5.length, 5);
assert.equal(result5[0].value, null);
assert.equal(result5[1].value, null);
assert.equal(result5[2].value, null);
assert.equal(result5[3].value, null);
// sum = 10 + 20 + 30 + 40 + 50 = 150 / 5 = 30
assert.equal(result5[4].value, 30);

// Test 5: 6 candles with MA5 (sliding window)
const sample6 = [
  ...sample5,
  { date: '2026-11-06', open: 100, high: 110, low: 90, close: 60, volume: 10 },
];
const result6 = calculateSMA(sample6, 5);
assert.equal(result6.length, 6);
// 6th candle sum = 20 + 30 + 40 + 50 + 60 = 200 / 5 = 40
assert.equal(result6[5].value, 40);

// Test 6: Unsorted input candles should be sorted chronologically
const unsorted = [
  { date: '2026-11-05', close: 50 },
  { date: '2026-11-01', close: 10 },
  { date: '2026-11-03', close: 30 },
  { date: '2026-11-02', close: 20 },
  { date: '2026-11-04', close: 40 },
];
const resultUnsorted = calculateSMA(unsorted, 5);
assert.equal(resultUnsorted[4].date, '2026-11-05');
assert.equal(resultUnsorted[4].value, 30);

// Test 7: calculateMultipleSMAs
const multi = calculateMultipleSMAs(sample6, [5, 10]);
assert.equal(multi[5].length, 6);
assert.equal(multi[10].length, 6);
// MA10 should all be null because length is 6 < 10
assert.ok(multi[10].every((p) => p.value === null));
assert.equal(multi[5][5].value, 40);

console.log('All calculateSMA tests passed successfully!');
