// Isolated SSR verification for CORE-DASHBOARD-01:
// Validates candidate card rendering with market_reaction under normal, insufficient, and missing states.
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

function renderDashboard(values) {
  let index = 0;
  React.useState = (initial) => [index < values.length ? values[index++] : typeof initial === 'function' ? initial() : initial, () => {}];
  React.useEffect = () => {};
  React.useMemo = (factory) => factory();
  try {
    return renderToStaticMarkup(React.createElement(EventFirstDashboard, {
      asOf: '2025-06-13',
      onOpenChain: (id) => console.log('chain opened', id)
    }));
  } finally {
    Object.assign(React, originals);
  }
}

function makeAnalysis(reactionData) {
  return {
    event: {
      event_id: 101,
      title: 'Micron Technology Expansion',
      event_kind: 'CAPEX_EXPANSION',
      occurred_at: '2025-06-12T12:40:40Z',
      available_at: '2025-06-12T12:40:40Z',
    },
    anchor: {
      company_id: 1,
      company_name: 'Micron Technology',
      cik: '0000723125',
    },
    analysis_context: {
      mode: 'stored_data',
      as_of: '2025-06-13T23:59:59Z',
      score_horizon: '20d',
      score_version: 'v1.0',
    },
    candidates: [
      {
        company_id: 10,
        company_name: '한미반도체',
        market: 'KOSPI',
        ticker: '042700',
        security_id: 427,
        relationship_depth: 1,
        classification: 'supply_chain_candidate',
        eligibility_status: 'ready_for_analysis',
        project_link_status: 'unverified',
        valid_from: '2024-01-01',
        valid_to: null,
        evidence_status: 'verified',
        evidence: [
          {
            id: 1,
            excerpt: '한미반도체는 마이크론에 고대역폭메모리용 듀얼 TC 본더를 공급하는 공식 계약을 체결함',
            source_document: 'DART 단일판매·공급계약체결',
            source_url: 'https://dart.fss.or.kr',
            published_at: '2024-04-11T09:00:00Z',
          }
        ],
        paths: [
          {
            tier: 1,
            relationships: [
              {
                direction: 'SUPPLIES_TO',
                type: 'SUPPLY_CONTRACT',
                status: 'verified',
                evidence: []
              }
            ]
          }
        ],
        price: {
          status: 'available',
          trade_date: '2025-06-13',
          age_days: 0,
          open: 156000,
          high: 165000,
          low: 155000,
          close: 160000,
          volume: 2000000,
        },
        alpha_score: {
          status: 'available',
          as_of_date: '2025-06-13',
          horizon: '20d',
          version: 'v1.0',
          score: 84.5,
          completeness: 0.88,
          components: { supply_chain: 0.85, momentum: 0.80 },
        },
        market_reaction: reactionData,
        analysis_states: {},
      }
    ],
    excluded_candidates: [],
    event_evidence: [],
    uncertainties: [],
    empty_reasons: [],
    policy: {
      investment_recommendation: false,
      analysis_completed_is_fact_verified: false,
      future_information_excluded: true,
    }
  };
}

const item = {
  id: 101,
  title: 'Micron Technology Expansion',
  event_kind: 'CAPEX_EXPANSION',
  occurred_at: '2025-06-12T12:40:40Z',
  source_type: 'stored',
  is_demo: false
};

// 1. Normal State: full market reaction data present
console.log('Testing Case 1: Normal market_reaction rendering...');
const normalReaction = {
  status: 'available',
  reaction_timing: 'next_trading_day',
  base_trade_date: '2025-06-12',
  base_price: 152000,
  base_volume: 1000000,
  reaction_trade_date: '2025-06-13',
  reaction_price: 160000,
  reaction_volume: 2000000,
  return_pct: 5.26,
  volume_change_pct: 100.0,
  causality_note: '본 가격·거래량 변동은 사건 시점 전후의 단순 관찰값이며 공급망 연결에 따른 직접적인 수혜 또는 인과관계를 입증하지 않습니다.',
};
const html1 = renderDashboard([[item], item.id, makeAnalysis(normalReaction), 'ready', 'ready']);

assert.match(html1, /한미반도체/);
assert.match(html1, /042700/);
assert.match(html1, /1차 공급망/);
assert.match(html1, /기존 체인맵 열기/);
assert.match(html1, /EVENT REACTION/);
assert.match(html1, /\+5\.26%/);
assert.match(html1, /익영업일 첫 반응/);
assert.match(html1, /2025-06-12/);
assert.match(html1, /2025-06-13/);
assert.match(html1, /152,000 → 160,000원/);
assert.match(html1, /\+100\.0%/);
assert.match(html1, /인과관계를 입증하지 않습니다/);
console.log('  -> Case 1 PASSED: all 8 items and non-causal note verified.');

// 2. Insufficient Market Data State
console.log('Testing Case 2: Insufficient market data rendering...');
const insufficientReaction = {
  status: 'insufficient_market_data',
  reaction_timing: 'next_trading_day',
  base_trade_date: '2025-06-12',
  reaction_trade_date: null,
  causality_note: '사건 시점 전후의 가격 또는 거래량 데이터가 충분하지 않아 반응을 산출할 수 없습니다.',
};
const html2 = renderDashboard([[item], item.id, makeAnalysis(insufficientReaction), 'ready', 'ready']);
assert.match(html2, /시장 데이터 부족/);
assert.match(html2, /데이터가 충분하지 않아 반응을 산출할 수 없습니다/);
// Must NOT display fake 0.00%
assert.doesNotMatch(html2, /0\.00%/);
console.log('  -> Case 2 PASSED: safe empty state rendered without substituting 0.');

// 3. Not Available / Missing State
console.log('Testing Case 3: Missing/not_available market reaction...');
const missingReaction = {
  status: 'not_available'
};
const html3 = renderDashboard([[item], item.id, makeAnalysis(missingReaction), 'ready', 'ready']);
assert.match(html3, /시장 반응 데이터 없음/);
console.log('  -> Case 3 PASSED: clean not_available empty state rendered.');

// 4. Existing ChainMap and Selection preserved
console.log('Testing Case 4: ChainMap integration button...');
assert.match(html1, /<button>기존 체인맵 열기<\/button>/);
console.log('  -> Case 4 PASSED: onOpenChain button preserved.');

console.log('All 4 SSR UI test cases passed successfully!');

