#!/usr/bin/env python3
"""Offline source + JS contract proof; no live services or records are read."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('token_summary_stager', ROOT / 'extras/local-router-stats/stage_plugin.py')
assert SPEC is not None and SPEC.loader is not None
STAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGER)


def render(today, cloud, local):
    windows = {'today': today, 'week': None, 'history': None}
    totals = {'total': windows, 'cloud': {'today': cloud}, 'local': {'today': local}}
    for key in ['week', 'history']:
        totals['cloud'][key] = {'value': None, 'known': 0, 'sources': cloud['sources'], 'overflow': False}
        totals['local'][key] = None
    functions = STAGER.TOKEN_PANEL_FUNCTIONS.split('  function recordedCount', 1)[1]
    functions = 'function recordedCount' + functions
    expressions = {}
    for name in ['recordedTokenBarText', 'recordedTokenCompactText']:
        expressions[name] = STAGER.TOKEN_PANEL_FUNCTIONS.split('readonly property string ' + name + ':', 1)[1].split('\n\n', 1)[0].strip()
    program = 'const recordedTotals = ' + json.dumps(totals) + ';\n'
    program += 'const usage = {formatTokenCount: n => String(n)};\n' + functions
    program += '\nconsole.log(JSON.stringify({bar: (' + expressions['recordedTokenBarText'] + '), compact: (' + expressions['recordedTokenCompactText'] + '), help: recordedTokenHelpText(), cloud: recordedCloudTotalsText(), local: recordedLocalTotalsText(), combined: recordedCombinedTotalsText()}));'
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node required for actual injected JavaScript contract proof')
    with tempfile.TemporaryDirectory(prefix='agents-summary-', dir=os.environ['TMPDIR']) as folder:
        env = dict(os.environ, HOME=folder, XDG_CONFIG_HOME=folder, XDG_STATE_HOME=folder, XDG_CACHE_HOME=folder)
        for key in ['DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS']:
            env.pop(key, None)
        child = subprocess.run([node, '-e', program], env=env, text=True, capture_output=True, timeout=10)
        if child.returncode:
            raise AssertionError(child.stderr)
        return json.loads(child.stdout)


def compose_dated(undated, view):
    # Execute the exact generated helpers, not a test copy of the compositor.
    functions = STAGER.RECORDED_TOKEN_FUNCTIONS.split('  // One reactive pass', 1)[0]
    program = functions + """
const p=JSON.parse(require('fs').readFileSync(0,'utf8'));
const before=JSON.stringify(p);
function freeze(v) {if (v && typeof v==='object') {Object.keys(v).forEach(k=>freeze(v[k]));Object.freeze(v);}}
freeze(p);
const result=typeof recordedDatedTokenTotals==='function' ? recordedDatedTokenTotals(p.undated,p.view) : {unimplemented:true};
console.log(JSON.stringify({result:result,unchanged:JSON.stringify(p)===before,
 sameUndated:result.undated===p.undated,
 sameHistory:!!result.cloud && result.cloud.history===p.undated.cloud.history,
 sameProviders:result.byProvider===p.undated.byProvider}));
"""
    child = subprocess.run(['node', '-e', program], input=json.dumps({'undated': undated, 'view': view}),
                           capture_output=True, text=True, timeout=10)
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


def undated_fixture():
    return {'cloud': {'today': {'value': 1000}, 'week': {'value': 2000},
                      'history': {'value': 3000, 'known': 1, 'sources': 2, 'overflow': False}},
            'local': {'today': 9, 'week': 9, 'history': 44, 'recordedDays': 1},
            'codex': {'today': None, 'week': None, 'history': None},
            'byProvider': {'claude': '1000 today', 'codex-hermes-test': ''},
            'total': {'today': 1009, 'week': 2009, 'history': 3044}}


class SummaryTests(unittest.TestCase):
    def test_calendar_metric_labels_keep_tokens_distinct_from_api_calls(self):
        import re
        helper = re.search(r'  function historyMetricUnit\(\) \{.*?\n  \}', STAGER.HISTORY_PANEL_FUNCTIONS, re.S)
        self.assertIsNotNone(helper, 'Calendar values have no selected-metric unit binding')
        program = 'var root={historyMetric:"output_tokens"};\n' + helper.group() + "; console.log(historyMetricUnit()); root.historyMetric='api_call_count'; console.log(historyMetricUnit());"
        child = subprocess.run(['node', '-e', program], capture_output=True, text=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.splitlines(), ['tokens', 'calls'])

    def test_header_always_shows_clock_keyed_week_and_keeps_recorded_scope(self):
        result = render(9, {'value': None, 'known': 0, 'sources': None, 'overflow': False}, 9)
        self.assertEqual(result['cloud'], 'Cloud · today — · week — · recorded —')
        self.assertEqual(result['local'], 'Local · today 9 · week — · recorded —')
        self.assertEqual(result['combined'], 'Total · today 9 · week — · recorded —')

    def test_unknown_cloud_population_is_not_a_null_or_zero_denominator(self):
        result = render(9, {'value': None, 'known': 0, 'sources': None, 'overflow': False}, 9)
        self.assertIn('Cloud today: 0/? sources recorded.', result['help'])
        self.assertNotIn('null', result['help'])

    def test_header_uses_dated_periods_without_recounting_undated_totals(self):
        periods = {}
        for grain, cloud, local, total in [('day', 5, 9, 14), ('week', 7, 9, 16),
                                          ('month', 5, 9, 14), ('year', 8, 9, 17)]:
            periods[grain] = {'value': total, 'cloud': {'value': cloud, 'knownSources': 1, 'sources': 2},
                              'local': {'value': local}}
        proof = compose_dated(undated_fixture(), {'selectionError': False, 'periods': periods})
        out = proof['result']
        self.assertNotIn('unimplemented', out, 'Native header still uses undated provider windows')
        for key, total in [('today', 14), ('week', 16), ('month', 14), ('year', 17)]:
            self.assertEqual(out['total'][key], total)
            self.assertEqual(out['local'][key], 9)
            self.assertEqual(out['cloud'][key]['known'], 1)
            self.assertEqual(out['cloud'][key]['sources'], 2)
        self.assertEqual(out['cloud']['today']['value'], 5)
        self.assertEqual(out['total']['history'], 3044)
        self.assertTrue(all(proof[k] for k in ('unchanged', 'sameUndated', 'sameHistory', 'sameProviders')))

    def test_expired_clock_clears_dated_windows_but_preserves_accounting_identity(self):
        # Deliberately keep old period values: selectionError must override them.
        view = {'selectionError': True, 'periods': {'day': {'value': 9999}}}
        proof = compose_dated(undated_fixture(), view)
        out = proof['result']
        self.assertNotIn('unimplemented', out, 'No shared clock expiry binding for the header')
        for key in ('today', 'week', 'month', 'year'):
            self.assertIsNone(out['total'][key])
            self.assertIsNone(out['local'][key])
            self.assertEqual(out['cloud'][key], {'value': None, 'known': 0, 'sources': None, 'overflow': False})
        self.assertEqual(out['total']['history'], 3044)
        self.assertTrue(all(proof[k] for k in ('unchanged', 'sameUndated', 'sameHistory', 'sameProviders')))

    def test_title_does_not_assert_partial_for_every_dataset(self):
        self.assertIn('text: "Recorded tokens"', STAGER.TOKEN_HEADER)
        self.assertNotIn('Recorded tokens · partial', STAGER.TOKEN_HEADER)
        self.assertIn('ToolTip.text: root.recordedTokenHelpText()', STAGER.TOKEN_HEADER)

    def test_bar_is_compact_without_hiding_coverage_details(self):
        result = render(100, {'value': 80, 'known': 1, 'sources': 2, 'overflow': False}, 20)
        self.assertEqual(result['bar'], 'Tokens Today 100')
        self.assertEqual(result['compact'], '100')
        self.assertIn('Cloud today: 1/2 sources recorded', result['help'])
        self.assertIn('Local today: recorded', result['help'])
        self.assertIn('Missing counters are not zero', result['help'])

    def test_complete_today_does_not_claim_complete_history(self):
        result = render(100, {'value': 80, 'known': 2, 'sources': 2, 'overflow': False}, 20)
        self.assertEqual(result['bar'], 'Tokens Today 100')
        self.assertIn('Cloud today: 2/2 sources recorded', result['help'])
        self.assertIn('Recording boundaries vary by source', result['help'])
        self.assertNotIn('complete', result['help'].lower())

    def test_unknown_is_distinct_from_recorded_zero(self):
        unknown = render(None, {'value': None, 'known': 0, 'sources': 2, 'overflow': False}, None)
        self.assertEqual(unknown['bar'], 'Tokens Today —')
        self.assertEqual(unknown['compact'], '—')
        self.assertIn('Local today: not recorded', unknown['help'])
        zero = render(0, {'value': 0, 'known': 2, 'sources': 2, 'overflow': False}, 0)
        self.assertEqual(zero['bar'], 'Tokens Today 0')
        self.assertIn('Local today: recorded', zero['help'])

    def test_overflow_remains_unavailable(self):
        result = render(None, {'value': None, 'known': 2, 'sources': 2, 'overflow': True}, 20)
        self.assertEqual(result['compact'], '—')
        self.assertIn('Cloud sum unavailable', result['help'])

    def test_tooltip_uses_same_reactive_coverage_on_bar_and_header(self):
        function = __import__('inspect').getsource(STAGER.transform_token_visibility)
        self.assertIn('tooltipText: root.recordedTokenHelpText()', function)
        self.assertNotIn('(* = partial coverage)', function)


if __name__ == '__main__':
    unittest.main(verbosity=2)
