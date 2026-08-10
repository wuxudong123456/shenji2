#!/usr/bin/env node
/**
 * Phase9 U1 灰度开关验收 — 实模式/演示模式门禁（纯前端逻辑，node headless）
 *
 * 被测对象：frontend/js/analysis-wiz.js 的 _useRealApi/_api/_apiBlob 集中门禁
 *   + 15 处裸 fetch 全部路由到 _api/_apiBlob。
 *
 * 方法：整文件在 vm 沙箱（stub document/localStorage/fetch）加载——
 *   顶层仅 `var AW={...}`（纯定义）+ document.addEventListener（no-op，回调不触发），
 *   故加载即得 AW 对象；直调门禁方法断言行为；再对源码做静态断言
 *   （无漏网裸 fetch、调用点 ≥15）。
 *
 * 用法：cd frontend && node tests\test_u1_gray.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SRC_PATH = path.join(__dirname, '..', 'js', 'analysis-wiz.js');
const SRC = fs.readFileSync(SRC_PATH, 'utf8');

let PASS = 0, FAIL = 0;
function check(name, cond, detail) {
  if (cond) { PASS++; console.log('  ✅ ' + name); }
  else { FAIL++; console.log('  ❌ ' + name + '  ' + (detail || '')); }
}

function makeSandbox() {
  const ls = { _m: {} };
  const sandbox = {
    console,
    JSON, Promise, Error,
    document: { addEventListener: function () {} },   // DOMContentLoaded 回调不触发
    setTimeout: function () {},                        // 防御：顶层若有调用不执行
    window: {},
    localStorage: {
      getItem: (k) => (k in ls._m ? ls._m[k] : null),
      setItem: (k, v) => { ls._m[k] = String(v); },
      removeItem: (k) => { delete ls._m[k]; },
    },
    fetch: null,                                       // 每用例注入
  };
  vm.createContext(sandbox);
  return sandbox;
}

function buildAW(sandbox) {
  vm.runInContext(SRC, sandbox);
  return sandbox.AW;
}

async function main() {
  console.log('[test] Phase9 U1 灰度开关（实模式/演示模式门禁）\n');

  // ── ① _useRealApi 默认实模式；aw_lab_demomode='1' → 演示模式 ──
  {
    const sb = makeSandbox();
    const AW = buildAW(sb);
    check('_useRealApi() 默认 true（无 key = 实模式）', AW._useRealApi() === true);
    sb.localStorage.setItem('aw_lab_demomode', '1');
    check("_useRealApi() = false（aw_lab_demomode='1' = 演示模式）", AW._useRealApi() === false);
    sb.localStorage.setItem('aw_lab_demomode', '0');
    check("_useRealApi() = true（显式 '0' = 实模式）", AW._useRealApi() === true);
    sb.localStorage.removeItem('aw_lab_demomode');
    check('removeItem 后回实模式', AW._useRealApi() === true);
  }

  // ── ② 实模式：_api 转发 fetch，url=_apiBase+path，返回 .json() 结果 ──
  {
    const sb = makeSandbox();
    const calls = [];
    sb.fetch = (url, opts) => {
      calls.push({ url, opts });
      return Promise.resolve({ json: () => Promise.resolve({ success: true, rows: [1, 2, 3] }) });
    };
    const AW = buildAW(sb);
    const d1 = await AW._api('GET', '/knowledge/violations?per_page=100');
    check('实模式 GET：_api 返回 .json() 结果', d1 && d1.success === true && d1.rows.length === 3);
    check('实模式 GET：url = /api/audit + path', calls.length === 1 && calls[0].url === '/api/audit/knowledge/violations?per_page=100');
    check('实模式 GET：method=GET', calls[0].opts.method === 'GET');
    check('实模式 GET：headers.Content-Type=application/json', calls[0].opts.headers['Content-Type'] === 'application/json');

    await AW._api('POST', '/expression/execute', { violation_ids: [1], project_id: 'p1' });
    check('实模式 POST：body JSON 序列化', calls.length === 2 && calls[1].opts.body === JSON.stringify({ violation_ids: [1], project_id: 'p1' }));
  }

  // ── ③ 演示模式：_api/_apiBlob reject 且 err.demo===true ──
  {
    const sb = makeSandbox();
    sb.fetch = () => Promise.reject(new Error('演示模式不应调用 fetch'));
    sb.localStorage.setItem('aw_lab_demomode', '1');
    const AW = buildAW(sb);
    let e1 = null;
    try { await AW._api('GET', '/analysis/x'); } catch (e) { e1 = e; }
    check('演示模式 _api reject 且 err.demo=true', e1 && e1.demo === true, 'demo=' + (e1 && e1.demo));
    let e2 = null;
    try { await AW._apiBlob('POST', '/documents/export', {}); } catch (e) { e2 = e; }
    check('演示模式 _apiBlob reject 且 err.demo=true', e2 && e2.demo === true, 'demo=' + (e2 && e2.demo));
  }

  // ── ④ settings.html 集成：toggleDemo 持久化 + switch('lab') 恢复 ──
  console.log('\n── ④ settings.html 集成（灰度开关 UI）──');
  {
    const settingsSrc = fs.readFileSync(path.join(__dirname, '..', 'settings.html'), 'utf8');
    const blocks = [...settingsSrc.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
    check('settings.html 内联脚本仅 1 块且非空', blocks.length === 1 && blocks[0].length > 0, 'blocks=' + blocks.length);
    const ls = {};
    const els = {};
    const elStub = (id) => ({
      id, checked: false, value: '', textContent: '', innerHTML: '',
      style: {}, dataset: {},
      classList: { add() {}, remove() {}, toggle() {} },
      closest() { return null; }, remove() {}, appendChild() {},
    });
    const sbs = {
      console, JSON, Promise, Error,
      setTimeout: function () {}, clearTimeout: function () {},
      localStorage: {
        getItem: (k) => (k in ls ? ls[k] : null),
        setItem: (k, v) => { ls[k] = String(v); },
        removeItem: (k) => { delete ls[k]; },
      },
      window: { AuditWorkbench: { toast: function () {} } },
      AuditWorkbench: { toast: function () {} },
      document: {
        addEventListener: function () {},
        getElementById: (id) => (els[id] || (els[id] = elStub(id))),
        querySelectorAll: () => [],
        querySelector: () => null,
      },
    };
    vm.createContext(sbs);
    vm.runInContext(blocks[0], sbs);
    const SettingsTab = sbs.SettingsTab;
    check('SettingsTab 已定义（内联脚本可解析）', typeof SettingsTab === 'object' && typeof SettingsTab.toggleDemo === 'function');

    SettingsTab.toggleDemo({ checked: true });
    check("toggleDemo(true) 持久化 aw_lab_demomode='1'", ls['aw_lab_demomode'] === '1');
    SettingsTab.toggleDemo({ checked: false });
    check("toggleDemo(false) 持久化 aw_lab_demomode='0'", ls['aw_lab_demomode'] === '0');

    ls['aw_lab_demomode'] = '1';
    SettingsTab.switch('lab');
    check("switch('lab') 从 localStorage 恢复 u1-demomode=checked", els['u1-demomode'] && els['u1-demomode'].checked === true);
    ls['aw_lab_demomode'] = '0';
    SettingsTab.switch('lab');
    check("switch('lab') 恢复 u1-demomode=unchecked（'0'）", els['u1-demomode'] && els['u1-demomode'].checked === false);
    delete ls['aw_lab_demomode'];
    SettingsTab.switch('lab');
    check('switch(\'lab\') 无 key 时 u1-demomode=unchecked（默认实模式）', els['u1-demomode'] && els['u1-demomode'].checked === false);
  }

  // ── ⑤ 静态断言：无漏网裸 fetch、调用点 ≥15 ──
  console.log('\n── ⑤ 静态断言（源码）──');
  const fetchSites = (SRC.match(/fetch\(/g) || []).length;
  check('裸 fetch( 仅剩门禁内部 2 处（_api/_apiBlob 内）', fetchSites === 2, 'count=' + fetchSites);
  const gateFetchInside = ((SRC.match(/return fetch\(self\._apiBase/g) || []).length) === 2;
  check('2 处 fetch 均在门禁内（_api/_apiBlob 返回语句）', gateFetchInside);
  const apiCalls = (SRC.match(/\._api\('/g) || []).length;
  const blobCalls = (SRC.match(/\._apiBlob\('/g) || []).length;
  const totalCalls = apiCalls + blobCalls;
  check('_api/_apiBlob 调用点 ≥ 15 处', totalCalls >= 15, 'count=' + totalCalls);
  const leakedHardcoded = SRC.match(/fetch\('\/api\/audit/g);
  check('无遗留 fetch(\'/api/audit 硬编码调用点', !leakedHardcoded, JSON.stringify(leakedHardcoded));
  const leftoverJson = SRC.match(/return r\.json\(\)\)\.then/g);
  check('无残留双重 .json()（_api 已内置）', !leftoverJson, JSON.stringify(leftoverJson));

  console.log('\n' + '='.repeat(50));
  console.log('Phase9 U1 灰度开关：PASS=' + PASS + '  FAIL=' + FAIL);
  console.log('='.repeat(50));
  process.exit(FAIL ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(2); });
