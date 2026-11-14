#!/usr/bin/env python3
"""Bounded shared-selector proof using actual canonical history projection.

The generated panel has no source picker: it always queries every admitted
source. Explicit `sources` filtering stays covered here as a backend API."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXTRA = ROOT / 'extras/local-router-stats'
METRICS = ('total_tokens', 'input_tokens', 'output_tokens', 'prompt_tokens',
           'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens', 'api_call_count')


def load(name):
    spec = importlib.util.spec_from_file_location(name, EXTRA / (name + '.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def projected(case='initial'):
    accounting, history = load('hermes_accounting'), load('usage_history')
    rows = [dict(session_id='synthetic-' + str(i), model='synthetic-' + str(i),
                 billing_provider=provider, billing_base_url=endpoint,
                 billing_mode='subscription_included', task='', first_seen=0, last_seen=1,
                 api_call_count=1, input_tokens=100, output_tokens=20,
                 cache_read_tokens=30, cache_write_tokens=5, reasoning_tokens=7)
            for i, (provider, endpoint) in enumerate([
                ('anthropic', 'https://api.anthropic.com'),
                ('openai-codex', 'https://api.openai.com/v1')])]
    initial = history.observe(None, accounting.canonical_snapshot(rows, 'default'),
                              observed_at='2024-02-29T15:00:00Z', timezone='UTC')
    changes = [dict(input_tokens=10, output_tokens=5, cache_read_tokens=2,
                    cache_write_tokens=1, reasoning_tokens=2, api_call_count=1),
               dict(input_tokens=20, output_tokens=7, cache_read_tokens=3,
                    cache_write_tokens=2, reasoning_tokens=1, api_call_count=1)]
    for row, delta in zip(rows, changes):
        for field, value in delta.items():
            previous = row[field]
            assert isinstance(previous, int)
            row[field] = previous + value
    final = history.observe(initial, accounting.canonical_snapshot(rows, 'default'),
                            observed_at='2024-02-29T15:05:00Z', timezone='UTC')
    if case == 'initial':
        return history.project_history(final)
    assert case in ('updated', 'removed', 'expanded', 'zero', 'partial')
    previous_input = rows[0]['input_tokens']
    assert isinstance(previous_input, int)
    rows[0]['input_tokens'] = previous_input + 10
    updated = history.observe(final, accounting.canonical_snapshot(rows, 'default'),
                              observed_at='2024-02-29T15:10:00Z', timezone='UTC')
    if case == 'updated':
        return history.project_history(updated)
    if case == 'removed':
        absent = history.observe(updated, accounting.canonical_snapshot(rows[1:], 'default'),
                                 observed_at='2024-02-29T15:15:00Z', timezone='UTC')
        return history.project_history(absent, source_ids=[s['id'] for s in absent['sources'].values() if s['present']])
    if case == 'expanded':
        rows.append(dict(rows[0], session_id='synthetic-new', model='synthetic-new',
                         billing_provider='google', billing_base_url='https://generativelanguage.googleapis.com'))
    if case == 'partial':
        rows = rows[:1]  # Second source has no cell on the next sampled day.
    next_day = history.observe(updated, accounting.canonical_snapshot(rows, 'default'),
                               observed_at='2024-03-01T15:00:00Z', timezone='UTC')
    final = history.observe(next_day, accounting.canonical_snapshot(rows, 'default'),
                            observed_at='2024-03-01T15:05:00Z', timezone='UTC')
    return history.project_history(final)


def inventory(count=313, changed=100, zero=100):
    """Actual backend projection with the live deployment's 313 identities.

    `changed` sources record a dated delta, `zero` a measured zero, and the
    rest are baseline-only (no dated cell: unknown, never zero)."""
    accounting, history = load('hermes_accounting'), load('usage_history')
    def row(i):
        provider, endpoint = (('anthropic', 'https://api.anthropic.com') if i % 10 == 9
                              else ('openai-codex', 'https://api.openai.com/v1'))
        return dict(session_id='synthetic-inventory-%03d' % i, model='synthetic', billing_provider=provider,
                    billing_base_url=endpoint, billing_mode='subscription_included', task='',
                    first_seen=0, last_seen=1, api_call_count=1, input_tokens=100, output_tokens=20,
                    cache_read_tokens=30, cache_write_tokens=5, reasoning_tokens=7)
    rows = [row(i) for i in range(changed + zero)]
    state = history.observe(None, accounting.canonical_snapshot(rows, 'default'),
                            observed_at='2024-02-29T15:00:00Z', timezone='UTC')
    for i, item in enumerate(rows[:changed]):
        item.update(input_tokens=110 + i % 7, output_tokens=23 + i % 5, api_call_count=2)
    rows += [row(i) for i in range(changed + zero, count)]
    state = history.observe(state, accounting.canonical_snapshot(rows, 'default'),
                            observed_at='2024-02-29T15:05:00Z', timezone='UTC')
    return history.project_history(state)


def generated_panel():
    """The maintained generator's Panel.qml for the synthetic limits-first source."""
    stager = load('stage_plugin')
    spec = importlib.util.spec_from_file_location('history_controls_fixture', ROOT / 'tests/agents_fixture.py')
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    with tempfile.TemporaryDirectory(prefix='hp-') as td:
        return stager.render(fixture.make_source(Path(td) / 'native', 'limits'), 'fixture.history')['Panel.qml']


def selected(data, options=None):
    program = (EXTRA / 'history_consumer.js').read_text() + '''
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log(JSON.stringify(typeof selectHistory === 'function'
    ? selectHistory(parseHistory(JSON.stringify(input.data)), input.options)
    : {unimplemented: true}));
'''
    node = shutil.which('node')
    assert node, 'Node is required'
    child = subprocess.run([node, '-e', program], input=json.dumps(dict(data=data, options=options)),
                           text=True, capture_output=True, timeout=10)
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


def generated_routes(panel, main):
    """Execute the generated binding and maintained options-object query seam.

    The dated selector is observed, not replaced with legacy accounting. Actual
    dated accounting has its own suite; the retained multiple route runs the
    real canonical-history selector here.
    """
    binding = re.search(r'  readonly property var historyView:([\s\S]*?)(?=\n  function historySourceColor\()', panel)
    method = re.search(r'  function datedHistoryFor\(options\) \{\n[\s\S]*?\n  \}', main)
    assert binding is not None and method is not None
    program = (EXTRA / 'history_consumer.js').read_text() + '''
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const observedHistory = parseHistory(JSON.stringify(input.data));
const root = {datedTokenView: {fixture: 'cached-dated'}, cloudObservedHistory: observedHistory,
              providerEnabled: function(id) {if (id !== 'local') throw Error('wrong provider'); return false},
              calendarClock: {fixture: 'clock'}, calendarNowMs: 1709219160000};
const localRouterAgent = {record: null};
let dated = [], legacy = 0;
const History = {
 selectDatedHistory: function(cloud, local, clock, options, now) {
  if (cloud !== observedHistory || local !== null || clock !== root.calendarClock || now !== root.calendarNowMs)
    throw Error('wrong dated-query authority');
  dated.push(options); return {fixture: 'other-dated'};
 },
 selectHistory: function(data, options) {
  if (data !== observedHistory) throw Error('wrong legacy history');
  legacy++; return selectHistory(data, options);
 }
};
''' + method.group(0) + '''
const usage = {datedHistoryFor: datedHistoryFor};
const rows = [];
for (const options of [{metric:'total_tokens', grain:'day', mode:'combined'},
                       {metric:'input_tokens', grain:'week', mode:'combined'},
                       {metric:'cache_write_tokens', grain:'day', mode:'multiple'}]) {
 const historyMetric = options.metric, historyGrain = options.grain, historyMode = options.mode;
 dated = []; legacy = 0;
 const view = (''' + binding.group(1) + ''');
 rows.push({cached:view === root.datedTokenView, dated:dated.length, legacy:legacy,
            value:view.bars ? view.bars[view.bars.length-1].value : null,
            options:dated.length ? dated[0] : null});
}
console.log(JSON.stringify(rows));
'''
    node = shutil.which('node')
    assert node, 'Node is required'
    child = subprocess.run([node, '-e', program], input=json.dumps(dict(data=projected())),
                           text=True, capture_output=True, timeout=10)
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


class ControlsTests(unittest.TestCase):
    def test_generated_week_labels_are_compact(self):
        panel = generated_panel()
        self.assertNotIn('Monday week', panel)
        self.assertIn('{id:"week", label:"Week"}', panel)
        self.assertIn('week:"Week"', panel)

    def test_chart_mode_controls_are_removed_but_backend_grouping_remains(self):
        panel = generated_panel()
        self.assertNotIn('label:"Combined"', panel)
        self.assertNotIn('label:"Per source"', panel)
        self.assertNotIn('objectName: "historyMode_"', panel)
        data = projected()
        combined = selected(data, dict(mode='combined'))
        multiple = selected(data, dict(mode='multiple'))
        self.assertEqual(combined['bars'][-1]['value'], multiple['bars'][-1]['value'])
        self.assertEqual(combined['bars'][-1]['series'], [])
        self.assertEqual(len(multiple['bars'][-1]['series']), 2)
        self.assertEqual(sum(s['value'] for s in multiple['bars'][-1]['series']),
                         combined['bars'][-1]['value'])

    def test_generated_panel_has_no_source_selector_and_charts_every_source(self):
        panel = generated_panel()
        for removed in ('historySourcesControl', 'historySource_', 'historySources', 'historyToggleSource',
                        'historySelectAll', 'model: root.historyView.sources', 'No sources selected'):
            self.assertFalse(removed in panel, 'source picker remnant: ' + removed)
        for control in ('historyMetric_', 'historyGrain_'):
            self.assertIn('objectName: "' + control + '" + modelData.id', panel)
        # The visible combined view uses the current options-object dated API.
        # The programmatic multiple route remains available for renderer tests;
        # it is not a source/mode picker and is never the default visible query.
        self.assertIn('property string historyMode: "combined"', panel)
        self.assertIn('usage.datedHistoryFor({metric: historyMetric, grain: historyGrain, mode: "combined"})', panel)
        main = load('stage_plugin').HISTORY_MAIN
        # Clock-payload admission, cached default view, and nondefault query.
        self.assertEqual(main.count('History.selectDatedHistory('), 3)
        self.assertIn('History.selectDatedHistory(null, null, candidate, {}, Date.parse(candidate.observedAt))', main)
        self.assertIn('readonly property var datedTokenView: History.selectDatedHistory(', main)
        self.assertIn('function datedHistoryFor(options)', main)
        self.assertIn('localRouterAgent.record', main)
        self.assertIn('root.calendarClock, {}, root.calendarNowMs)', main)
        self.assertIn('root.calendarClock, options, root.calendarNowMs)', main)
        self.assertNotIn('sources:', main)
        self.assertEqual(generated_routes(panel, main), [
            dict(cached=True, dated=0, legacy=0, value=None, options=None),
            dict(cached=False, dated=1, legacy=0, value=None,
                 options=dict(metric='input_tokens', grain='week', mode='combined')),
            dict(cached=False, dated=0, legacy=1, value=3, options=None)])
        data = inventory()
        self.assertEqual((len(data['sources']), len(data['events'])), (313, 512))
        latest = data['buckets']['day'][-1]
        cells = [cell['counts']['total_tokens'] for cell in latest['sources']]
        self.assertEqual((len(cells), cells.count(0)), (200, 100))
        for mode in ('combined', 'multiple'):
            with self.subTest(mode=mode):
                view = selected(data, dict(metric='total_tokens', grain='day', mode=mode))
                self.assertEqual(len(view['selectedSources']), 313)
                self.assertFalse(view['selectionEmpty'] or view['selectionError'])
                last = view['bars'][-1]
                self.assertEqual((last['key'], last['value'], last['known'], last['partial']),
                                 (latest['key'], sum(cells), 200, True))
                if mode == 'multiple':
                    values = [s['value'] for s in last['series']]
                    # Baseline-only sources stay unknown; measured zero stays a known zero.
                    self.assertEqual((len(values), values.count(None), values.count(0)), (313, 113, 100))

    def test_source_selection_queries_actual_canonical_projection(self):
        data = projected()
        source = next(s for s in data['sources'] if s['provider'] == 'anthropic')
        view = selected(data, dict(sources=[source['id']], metric='total_tokens',
                                   grain='day', mode='combined'))
        self.assertNotIn('unimplemented', view, 'shared source/metric/grain selector is missing')
        expected = sum((10, 5, 2, 1))
        self.assertEqual(view['bars'][-1]['value'], expected)
        self.assertEqual([s['value'] for s in view['summaries']], [expected] * 4)
        self.assertEqual(view['selectedSources'], [source['id']])
        self.assertNotIn('synthetic-', json.dumps(view))

    def test_metrics_and_multiple_bars_do_not_add_reasoning_twice(self):
        data = projected()
        both = selected(data)
        self.assertNotIn('unimplemented', both)
        self.assertEqual(both['bars'][-1]['value'], sum((10, 5, 2, 1, 20, 7, 3, 2)))
        for metric, expected in [('input_tokens', sum((10, 20))),
                                 ('output_tokens', sum((5, 7))),
                                 ('cache_read_tokens', sum((2, 3))),
                                 ('cache_write_tokens', sum((1, 2))),
                                 ('reasoning_tokens', sum((2, 1))),
                                 ('prompt_tokens', sum((10, 2, 1, 20, 3, 2))),
                                 ('api_call_count', 2)]:
            with self.subTest(metric=metric):
                view = selected(data, dict(metric=metric, mode='multiple'))
                self.assertEqual(view['bars'][-1]['value'], expected)
                self.assertEqual(sum(c['value'] for c in view['bars'][-1]['series']), expected)
                self.assertEqual(len(view['bars'][-1]['series']), 2)

    def test_calendar_grains_preserve_unknown_slots_and_explicit_empty_selection(self):
        data = projected()
        for grain, key in [('day', '2024-02-29'), ('week', '2024-02-26'),
                           ('month', '2024-02'), ('year', '2024')]:
            with self.subTest(grain=grain):
                view = selected(data, dict(grain=grain))
                self.assertNotIn('unimplemented', view)
                self.assertEqual(view['bars'][-1]['key'], key)
                self.assertTrue(all(b['value'] is None for b in view['bars'][:-1]))
        empty = selected(data, dict(sources=[]))
        self.assertTrue(empty['selectionEmpty'])
        self.assertEqual(empty['bars'], [])
        self.assertTrue(all(s['value'] is None for s in empty['summaries']))
        absent = selected(data, dict(sources=['0' * 64]))
        self.assertTrue(absent['selectionEmpty'])
        invalid = selected(data, dict(metric='not-a-counter'))
        self.assertEqual(invalid['bars'], [])
        self.assertEqual(invalid['selectionError'], True)


    def test_refresh_removed_new_sources_partial_and_measured_zero(self):
        initial = projected()
        first = next(s['id'] for s in initial['sources'] if s['provider'] == 'anthropic')
        options = dict(sources=[first], metric='total_tokens', grain='day', mode='multiple')
        self.assertEqual(selected(projected('updated'), options)['bars'][-1]['value'], 28)
        removed = selected(projected('removed'), options)
        self.assertTrue(removed['selectionEmpty'])
        self.assertEqual(removed['bars'], [])
        expanded = selected(projected('expanded'), options)
        self.assertEqual(len(expanded['sources']), 3)
        self.assertEqual(expanded['selectedSources'], [first])
        partial = selected(projected('partial'), dict(mode='multiple'))
        last = partial['bars'][-1]
        self.assertEqual(last['value'], 0)
        self.assertEqual(last['known'], 1)
        self.assertTrue(last['partial'])
        self.assertEqual(sorted((s['value'] for s in last['series']), key=lambda v: -1 if v is None else v), [None, 0])
        zero = selected(projected('zero'))
        self.assertEqual(zero['bars'][-1]['value'], 0)
        self.assertFalse(zero['bars'][-1]['partial'])
        for data in (projected('updated'), projected('removed'), projected('expanded')):
            empty = selected(data, dict(sources=[]))
            self.assertTrue(empty['selectionEmpty'])
            self.assertTrue(all(s['value'] is None for s in empty['summaries']))

    def test_invalid_options_are_not_silently_normalized(self):
        data = projected()
        source = data['sources'][0]['id']
        for options in (True, [], 'day', {'metric':''}, {'grain':False}, {'mode':0},
                        {'sources':{'length':0}}, {'sources':'all'}, {'sources':[source, source]},
                        {'sources':['bad']}, {'sources':[source]*129}, {'unexpected':True}):
            with self.subTest(options=options):
                view = selected(data, options)
                self.assertTrue(view['selectionError'], options)
                self.assertEqual(view['bars'], [])


if __name__ == '__main__':
    unittest.main()
