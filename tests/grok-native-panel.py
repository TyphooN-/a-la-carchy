#!/usr/bin/env python3
"""Execute the real Agents provider admission with offline Grok records."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess


def block(text, anchor):
    start = text.index(anchor) + len(anchor)
    start = text.index('{', start)
    depth = 0
    quote = None
    comment = None
    i = start
    while i < len(text):
        c = text[i]
        pair = text[i:i + 2]
        if comment == '//':
            if c == '\n':
                comment = None
        elif comment == '/*':
            if pair == '*/':
                comment = None
                i += 1
        elif quote:
            if c == '\\':
                i += 1
            elif c == quote:
                quote = None
        elif pair in ('//', '/*'):
            comment = pair
            i += 1
        elif c in ('\"', "'", '`'):
            quote = c
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    raise ValueError('unbalanced source block: ' + anchor)


def function(text, name):
    anchor = 'function ' + name + '('
    start = text.index(anchor)
    return text[start:text.index('{', start)] + block(text, anchor)


def providers(main):
    native = {
        'id': 'grok', 'name': 'Grok', 'ready': True, 'tierLabel': 'fixture plan',
        'limits': [{'label': 'Weekly', 'percent': 1, 'resetsAt': '2026-10-18T12:00:00Z'}],
        'limitsFetchedAt': 1791734400000, 'hasLocalStats': True,
        'todayTotalTokens': 19, 'todayTokensByModel': {'fixture-grok-model': 19},
        'modelUsage': {'fixture-grok-model': {'inputTokens': 11, 'outputTokens': 5,
                       'cacheReadInputTokens': 2, 'cacheCreationInputTokens': 1}},
        'recentDays': [{'date': '2026-10-11', 'messageCount': 19}],
    }
    observed = {'id': 'grok', 'name': 'Grok', 'ready': False,
                'limits': [{'label': 'Subscription quota', 'percent': -1}],
                'usageStatusText': 'Waiting for fixture metadata', 'hasLocalStats': False}
    code = '''const vm = require('node:vm');
const seed = JSON.parse(process.argv[1]);
const ctx = {
  agents: [{record: seed.native}], grokObservedAgent: {record: seed.observed},
  localRouterAgent: {record: null}, dataRevision: 0, syncRevision: 0,
  providerOrder: [], aggregateData: {}, syncConfigured: () => false,
  providerEnabled: () => true, numberValue: v => Number(v) || 0
};
vm.createContext(ctx);
vm.runInContext(seed.functions, ctx, {timeout: 1000});
const rows = vm.runInContext('(function()' + seed.admission + ')()', ctx, {timeout: 1000});
console.log(JSON.stringify(rows));
'''
    funcs = ['balanceValue', 'displayProvider', 'providerHasData', 'orderedProviders', 'syncedStatsFor']
    if 'function recordedTokenCounters(' in main:
        funcs.append('recordedTokenCounters')
    seed = {'native': native, 'observed': observed,
            'functions': '\n'.join(function(main, name) for name in funcs),
            'admission': block(main, 'property var enabledProviders:')}
    p = subprocess.run([shutil.which('node') or 'node', '-e', code, json.dumps(seed)],
                       text=True, capture_output=True, timeout=5)
    if p.returncode:
        raise RuntimeError(p.stderr)
    return json.loads(p.stdout), native


def panel_quota(panel):
    now = '2026-10-11T16:00:00Z'
    source_functions = ['windowIsLong', 'windowSpanMs', 'windowTitle', 'limitWindow', 'limitWindows', 'scopedPart', 'displayWindows']
    for extra in ['grokQuotaFresh', 'grokQuotaText', 'grokAccountWindows']:
        if 'function ' + extra + '(' in panel:
            source_functions.append(extra)
    code = '''const vm = require('node:vm');
const seed = JSON.parse(process.argv[1]);
const ctx = {nowMs: Date.parse(seed.now), usage: {setting: (k, fallback) => fallback}};
ctx.root = ctx;
vm.createContext(ctx);
vm.runInContext(seed.functions, ctx, {timeout: 1000});
const fresh = {providerId: 'grok', limitsStale: false, limitsFetchedAt: ctx.nowMs,
               limits: [{label: 'Weekly', percent: 1, resetsAt: '2026-10-18T12:00:00Z'}]};
const stale = {...fresh, limitsFetchedAt: Date.parse('2026-10-03T12:00:00Z'),
               limits: [{label: 'Weekly', percent: 0, resetsAt: '2026-10-18T12:00:00Z'}]};
ctx.fresh = fresh; ctx.stale = stale;
const cases = {
  zero: {...fresh, limits: [{...fresh.limits[0], percent: 0}]},
  missing: {...fresh, limitsFetchedAt: 0},
  future: {...fresh, limitsFetchedAt: ctx.nowMs + 120000},
  flagged: {...fresh, limitsStale: true},
  expired: {...fresh, limits: [{...fresh.limits[0], resetsAt: '2026-10-10T12:00:00Z'}]},
  malformed: {...fresh, limits: [{...fresh.limits[0], percent: null}]},
  invalidPercent: {...fresh, limits: [{...fresh.limits[0], percent: 2}]}
};
const windows = {};
for (const name of Object.keys(cases)) windows[name] = ctx.limitWindows(cases[name]);
const result = {
  fresh: vm.runInContext('limitWindows(fresh)', ctx, {timeout: 1000}),
  stale: vm.runInContext('limitWindows(stale)', ctx, {timeout: 1000}),
  staleText: typeof ctx.grokQuotaText === 'function' ? ctx.grokQuotaText(stale) : '',
  cases: windows,
  account: typeof ctx.grokAccountWindows === 'function' ? ctx.grokAccountWindows(fresh, {limits: fresh.limits, fetchedAt: ctx.nowMs, stale: false}) : [],
  staleAccount: typeof ctx.grokAccountWindows === 'function' ? ctx.grokAccountWindows(fresh, {limits: fresh.limits, fetchedAt: stale.limitsFetchedAt, stale: false}) : []
};
console.log(JSON.stringify(result));
'''
    seed = {'now': now, 'functions': '\n'.join(function(panel, n) for n in source_functions)}
    p = subprocess.run([shutil.which('node') or 'node', '-e', code, json.dumps(seed)],
                       text=True, capture_output=True, timeout=5)
    if p.returncode:
        raise RuntimeError(p.stderr)
    result = json.loads(p.stdout)
    assert result['fresh'][0]['percent'] == 1, 'Fresh real 100% quota was changed'
    assert result['stale'] == [], 'Old fetched window must not be presented as fresh 0%'
    assert 'stale' in result['staleText'].lower(), 'Stale quota must be visibly identified'
    assert result['cases']['zero'][0]['percent'] == 0, 'A genuinely measured zero is valid'
    assert all(result['cases'][key] == [] for key in ['missing', 'future', 'flagged', 'expired', 'malformed', 'invalidPercent'])
    assert result['account'][0]['percent'] == 1 and result['staleAccount'] == []
    print('PASS fresh 100% quota retained; old synthetic 0% quota withheld and identified')


def panel_models(panel):
    names = ['grokQuotaFresh', 'grokQuotaText']
    if 'function grokUsageText(' in panel:
        names.append('grokUsageText')
    code = '''const vm = require('node:vm');
const seed = JSON.parse(process.argv[1]);
const ctx = {nowMs: Date.parse('2026-10-11T16:00:00Z'),
             usage: {setting: (k, fallback) => fallback, formatTokenCount: v => String(v)}};
ctx.root = ctx;
vm.createContext(ctx);
vm.runInContext(seed.functions, ctx, {timeout: 1000});
const record = {providerId: 'grok', limitsStale: false, limitsFetchedAt: ctx.nowMs,
 limits: [{label: 'Weekly', percent: 1, resetsAt: '2026-10-18T12:00:00Z'}], hasLocalStats: true,
 modelUsage: {'fixture/grok-model': {inputTokens: 11, outputTokens: 5,
 cacheReadInputTokens: 2, cacheCreationInputTokens: 1}}};
const unknown = {...record, hasLocalStats: false, modelUsage: {}};
const zero = {...record, modelUsage: {'fixture-zero': {inputTokens: 0, outputTokens: 0}}};
const format = v => typeof ctx.grokUsageText === 'function' ? ctx.grokUsageText(v) : '';
console.log(JSON.stringify({recorded: format(record), unknown: format(unknown), zero: format(zero)}));
'''
    seed = {'functions': '\n'.join(function(panel, name) for name in names)}
    p = subprocess.run([shutil.which('node') or 'node', '-e', code, json.dumps(seed)],
                       text=True, capture_output=True, timeout=5)
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout)
    for value in ['fixture/grok-model', 'In 11', 'Out 5', 'Cache read 2', 'Cache creation 1', 'not quota']:
        assert value in result['recorded'], 'Native per-model ledger must be rendered separately: ' + value
    assert 'unavailable' in result['unknown'].lower() and 'not zero' in result['unknown'].lower()
    assert 'fixture-zero' in result['zero'] and 'In 0' in result['zero']
    print('PASS native model input/output/cache buckets retained; unknown distinct from measured zero')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--main', type=Path, required=True)
    parser.add_argument('--panel', type=Path)
    args = parser.parse_args()
    rows, expected = providers(args.main.read_text())
    grok = [p for p in rows if p['providerId'] == 'grok']
    assert len(grok) == 1, f'Expected one Grok card, found {len(grok)}'
    assert grok[0]['limits'] == expected['limits'], 'Native quota was replaced by observer quota'
    assert grok[0]['modelUsage'] == expected['modelUsage'], 'Native model token ledger changed'
    assert grok[0]['todayTokensByModel'] == expected['todayTokensByModel']
    print('PASS one native Grok card retains its actual quota and model token ledger')
    if args.panel:
        panel_quota(args.panel.read_text())
        panel_models(args.panel.read_text())


if __name__ == '__main__':
    main()
