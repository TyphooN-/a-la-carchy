#!/usr/bin/env python3
"""Offline JS contract of persistent totals generated from native Agents QML."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STOCK = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/home/typhoon/omarchy/shell/plugins/agents'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    stager = load('totals_stager', ROOT / 'extras/local-router-stats/stage_plugin.py')
    extract = load('totals_extract', ROOT / 'tests/grok-native-panel.py')
    rendered = stager.render(STOCK, 'fixture.agents')
    source = rendered['Main.qml']
    assert 'function recordedTokenTotalsFor(' in source, 'Recorded totals must be available without the rotating hero'
    names = ['validRecordedTokens', 'recordedTokenCounters', 'recordedTokenWindows',
             'recordedTokenLabel', 'recordedTokenProviderText', 'recordedTokenTotalsFor']
    seed = {'functions': '\n'.join(extract.function(source, name) for name in names)}
    js = r'''const vm = require('node:vm');
const assert = require('node:assert/strict');
const ctx = {formatTokenCount: v => String(v)}; ctx.root = ctx;
vm.createContext(ctx);
vm.runInContext(JSON.parse(process.argv[1]).functions, ctx, {timeout: 1000});
function recorded(id, n, extra = {}) {
  return {providerId: id, providerName: id, hasLocalStats: true, todayTotalTokens: n,
    recentDays: [{date: '2026-10-12', messageCount: n}],
    modelUsage: {m: {inputTokens: n, outputTokens: 2, cacheReadInputTokens: 3, cacheCreationInputTokens: 4}}, ...extra};
}
function run(rows, codex = {}) { return ctx.recordedTokenTotalsFor(rows, codex); }
let cloud = recorded('claude', 11, {accounts: [recorded('a', 11), recorded('b', 11)]});
let local = recorded('local', 900, {recentDays: [{date:'2026-10-12', messageCount:900, recorded:true}]});
let quota = {providerId:'codex-hermes-1', providerName:'Codex (Hermes 1)', readOnly:true,
             hasLocalStats:false, todayTotalTokens:0, modelUsage:{}, limits:[{percent:0.5}]};
let t = run([cloud, local, quota, cloud], recorded('codex', 500));
assert.equal(t.cloud.today.value, 11, 'Aggregate and accounts, Local, and duplicate provider must not be added twice');
assert.equal(t.cloud.history.value, 20, 'History includes input/output/cache-read/cache-creation once');
assert.equal(t.total.today, 911, 'Known cloud plus Local once, excluding separate native CLI history');
assert.equal(t.total.history, t.cloud.history.value + t.local.history);
assert.equal(run([cloud, local, local, quota]).total.today, 911, 'Local duplicate row counted once');
let large = run([recorded('cloud-big',9007199254740990,{modelUsage:{}}), recorded('local',10,{recentDays:[{date:'2026-10-12',recorded:true,messageCount:10}],modelUsage:{}})]);
assert.equal(large.total.today, null, 'Combined unsafe sum must be unavailable');
assert.equal(t.cloud.today.sources, 2); assert.equal(t.cloud.today.known, 1);
assert.equal(t.local.today, 900); assert.equal(t.codex.history, 509, 'Native CLI must remain separately unattributed');
assert.match(t.byProvider.claude, /11 today/);
assert.match(t.byProvider.claude, /20 recorded/);
assert.equal(t.byProvider['codex-hermes-1'], '', 'Quota-only cards need no repeated token disclaimer');
console.log('PASS aggregate once; Local and unattributed Codex separate; quota coverage explicit');
let nativeClaude = ctx.recordedTokenCounters({id:'claude', hasLocalStats:true, todayTotalTokens:11,
  recentDays:[{date:'2026-10-12',messageCount:11}],modelUsage:{m:{inputTokens:11}}});
assert.equal(ctx.recordedTokenWindows(nativeClaude,false).week,null,
  'Claude merges can erase fallback message-count markers; absent unit provenance must not become token totals');
assert.equal(ctx.recordedTokenWindows(nativeClaude,false).today,11);
console.log('PASS unattributable Claude daily units withheld without hiding its recorded today/history');
let zero = run([recorded('zero', 0, {modelUsage:{m:{inputTokens:0,outputTokens:0}}}), quota]);
assert.equal(zero.cloud.today.value, 0); assert.equal(zero.cloud.today.known, 1);
let unknown = run([quota, {providerId:'missing', todayTotalTokens:undefined}]);
assert.equal(unknown.cloud.today.value, null); assert.equal(unknown.cloud.history.value, null);
assert.equal(unknown.cloud.today.known, 0);
assert.equal(unknown.total.today, null, 'No known counters must remain unavailable');
assert.equal(zero.total.today, 0, 'Recorded zero is not unavailable');
let mixed = run([quota, {providerId:'local', tokenCounters:{providerId:'local',hasLocalStats:true,todayTotalTokens:5,recentDays:[{date:'2026-10-12',recorded:true,messageCount:5}],modelUsage:{m:{inputTokens:5}}}}]);
assert.equal(mixed.total.today, 5, 'Missing cloud history is excluded, not treated as measured zero');
assert.equal(unknown.byProvider.missing, '', 'Unknown counters stay absent, not displayed as zero');
console.log('PASS unavailable differs from recorded zero');
let accounts = Array.from({length:137}, (_, i) => recorded('account-'+i, i, {label:'account '+i}));
let grouped = run([{providerId:'family', providerName:'family', hasLocalStats:false, accounts}]);
assert.equal(grouped.cloud.today.sources,137); assert.equal(grouped.cloud.today.known,137);
assert.equal(grouped.cloud.today.value, accounts.reduce((sum,a)=>sum+a.todayTotalTokens,0));
assert.match(grouped.byProvider.family, /account 136/);
console.log('PASS dynamic account fallback has no fixed business account count');
let days = Array.from({length:10}, (_,i)=>({date:'2026-10-'+String(i+1).padStart(2,'0'),messageCount:i+1,recorded:i!==8}));
let w = ctx.recordedTokenWindows(recorded('local', 0, {recentDays:days, modelUsage:{}}), true);
assert.equal(w.week, 43, 'Last seven recorded days, not an invented calendar-week sum');
assert.equal(w.recordedDays,7); assert.equal(w.today,0);
let gap = ctx.recordedTokenWindows(recorded('local', 0, {recentDays:[{date:'2026-10-12',messageCount:null,recorded:false}]}), true);
assert.equal(gap.today,null); assert.equal(gap.week,null);
let fallback = ctx.recordedTokenWindows(recorded('claude', 11, {recentDays:[{date:'2026-10-12',messageCount:3,sessionCount:1,toolCallCount:2}]}), false);
assert.equal(fallback.week,null,'Claude dailyActivity fallback contains messages, not tokens');
for (const value of [null, '0', -1, NaN, Infinity, 1.5, 9007199254740992]) {
  assert.equal(ctx.recordedTokenWindows(recorded('bad', value),false).today,null);
}
let malformed = ctx.recordedTokenWindows(recorded('bad', 1, {modelUsage:{m:{inputTokens:1,outputTokens:null}}}),false);
assert.equal(malformed.history,null);
let duplicateDay = ctx.recordedTokenWindows(recorded('bad', 1, {recentDays:[days[0],days[0]]}),false);
assert.equal(duplicateDay.week,null);
let oversized = ctx.recordedTokenWindows(recorded('bad', 1, {recentDays:Array(367).fill(days[0])}),false);
assert.equal(oversized.week,null);
let overflow = run([recorded('big',9007199254740991),recorded('one',1)]);
assert.equal(overflow.cloud.today.value,null); assert.equal(overflow.cloud.today.overflow,true);
console.log('PASS recorded-day gaps, fallback-message guard, invalid/oversized counters, overflow');
'''
    run = subprocess.run([shutil.which('node') or 'node', '-e', js, json.dumps(seed)], text=True,
                         capture_output=True, timeout=8)
    print(run.stdout + run.stderr)
    assert run.returncode == 0, f'JS contract exit {run.returncode}'
    print('Offline JS only; no credentials, requests, or live counters read.')


if __name__ == '__main__':
    main()
