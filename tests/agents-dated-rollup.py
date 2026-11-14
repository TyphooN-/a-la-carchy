#!/usr/bin/env python3
"""Unified dated consumer proof: synthetic projections, no live DB or requests."""
import atexit
from copy import deepcopy
import datetime as dt
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXTRA = ROOT / 'extras/local-router-stats'
# Isolate the parent before importing any fixture/adapter, not just Node.
PRIVATE_HOME = tempfile.TemporaryDirectory(prefix='dated-', dir=os.environ['TMPDIR'])
atexit.register(PRIVATE_HOME.cleanup)
for key in ('HOME', 'HERMES_HOME', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME',
            'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR'):
    destination = Path(PRIVATE_HOME.name) / key.lower()
    destination.mkdir(mode=0o700)
    os.environ[key] = str(destination)
for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
    os.environ.pop(key, None)
SPEC = importlib.util.spec_from_file_location('dated_controls_fixture', ROOT / 'tests/agents-history-controls.py')
assert SPEC is not None and SPEC.loader is not None
FIXTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIXTURE)


def clock(day='2024-02-29'):
    date = dt.date.fromisoformat(day)
    return {'state': 'ready', 'timezone': 'UTC',
            'observedAt': day + 'T15:05:00Z',
            'expiresAt': str(date + dt.timedelta(days=1)) + 'T00:00:00Z',
            'keys': {'day': day, 'week': str(date - dt.timedelta(days=date.weekday())),
                     'month': day[:7], 'year': day[:4]},
            'localCalendarAligned': True}


UNSET = object()


def millis(stamp):
    return dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() * 1000


def local_days(*items):
    return {'id': 'local', 'readOnly': True, 'hasLocalStats': True,
            'recentDays': [{'date': date, 'messageCount': value, 'recorded': value is not None}
                           for date, value in items]}


def bar_keys(day, grain):
    date = dt.date.fromisoformat(day)
    if grain == 'day':
        return [str(date - dt.timedelta(days=i)) for i in range(6, -1, -1)]
    if grain == 'week':
        monday = date - dt.timedelta(days=date.weekday())
        return [str(monday - dt.timedelta(weeks=i)) for i in range(6, -1, -1)]
    if grain == 'month':
        absolute = date.year * 12 + date.month - 1
        return ['%04d-%02d' % (value // 12, value % 12 + 1)
                for value in range(absolute - 6, absolute + 1)]
    return [str(year) for year in range(date.year - 4, date.year + 1)]


def query(data, local, date_clock=None, options=None, now=UNSET):
    program = (EXTRA / 'history_consumer.js').read_text() + '''
const p=JSON.parse(require('fs').readFileSync(0,'utf8'));
global.Intl=undefined; // Qt's tested JS runtime has no Intl timezone resolver.
const report=p.data===null ? null : parseHistory(JSON.stringify(p.data));
const inputs=[report,p.local,p.clock,p.options];
const before=JSON.stringify(inputs);
function freeze(value) {
  if (value && typeof value==='object') {Object.keys(value).forEach(k=>freeze(value[k]));Object.freeze(value);}
}
inputs.forEach(freeze);
const tick=p.nonfinite ? Number(p.nonfinite) : p.now;
const view=typeof selectDatedHistory === 'function'
  ? selectDatedHistory(report,p.local,p.clock,p.options,tick) : {unimplemented:true};
if (JSON.stringify(inputs)!==before) throw Error('dated consumer mutated an input');
console.log(JSON.stringify(typeof selectDatedHistory === 'function'
  ? view
  : {unimplemented:true}));
'''
    c = clock() if date_clock is None else date_clock
    if now is UNSET:
        try:
            now = millis(c['observedAt'])
        except (TypeError, KeyError, ValueError):
            now = millis(clock()['observedAt'])
    nonfinite = None
    if type(now) is float and not math.isfinite(now):
        nonfinite = 'NaN' if math.isnan(now) else 'Infinity' if now > 0 else '-Infinity'
        now = None
    p = subprocess.run(['node', '-e', program], input=json.dumps({'data': data, 'local': local,
                       'clock': c, 'options': options, 'now': now, 'nonfinite': nonfinite}),
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


class DatedTests(unittest.TestCase):
    def test_dated_view_keeps_the_chart_inventory_and_coverage_contract(self):
        data = FIXTURE.projected()
        view = query(data, local_days(('2024-02-29', 9)))
        self.assertIn('sources', view, 'Combined chart lost its admitted source inventory')
        ids = [s['id'] for s in data['sources']]
        self.assertEqual([s['id'] for s in view['sources']], ids)
        self.assertEqual(view['selectedSources'], ids)
        self.assertTrue(all(s['label'] and s['provider'] for s in view['sources']))
        for bar in view['bars']:
            self.assertEqual(bar['known'], bar['knownSources'])
        # Source metadata must not leak a partial inventory from refused views.
        expired = query(data, None, now=millis(clock()['expiresAt']))
        self.assertTrue(expired['selectionError'])
        self.assertEqual(expired['sources'], [])
        self.assertEqual(expired['selectedSources'], [])

    def test_cloud_and_local_use_one_dated_sum_for_summary_and_calendar(self):
        data = FIXTURE.projected()
        local = {'id': 'local', 'readOnly': True, 'hasLocalStats': True,
                 'recentDays': [{'date': '2024-02-29', 'messageCount': 9, 'recorded': True}]}
        expected_cloud = sum(cell['counts']['total_tokens'] for cell in data['buckets']['day'][-1]['sources'])
        view = query(data, local)
        self.assertNotIn('unimplemented', view, 'No unified dated consumer; header still uses legacy zeros')
        self.assertEqual(view['periods']['day']['cloud']['value'], expected_cloud)
        self.assertEqual(view['periods']['day']['local']['value'], 9)
        self.assertEqual(view['periods']['day']['value'], expected_cloud + 9)
        self.assertEqual(view['bars'][-1]['key'], '2024-02-29')
        self.assertEqual(view['bars'][-1]['value'], view['periods']['day']['value'])
        self.assertEqual(view['mode'], 'combined')

    def assert_rejected(self, view):
        self.assertTrue(view.get('selectionError'), view)
        self.assertEqual(view['bars'], [])
        self.assertTrue(all(value is None for value in view['periods'].values()))

    def test_clock_requires_a_finite_current_numeric_tick(self):
        data, c = FIXTURE.projected(), clock()
        for now in (millis(c['observedAt']), millis(c['expiresAt']) - 1):
            with self.subTest(valid_now=now):
                self.assertIsNotNone(query(data, None, c, now=now)['periods']['day'])
        for now in (millis(c['observedAt']) - 1, millis(c['expiresAt']),
                    millis(c['expiresAt']) + 1, None, True, False,
                    str(millis(c['observedAt'])), c['observedAt'],
                    float('nan'), float('inf'), float('-inf')):
            with self.subTest(invalid_now=repr(now)):
                self.assert_rejected(query(data, None, c, now=now))

    def test_clock_shape_keys_and_timezone_agreement_are_checked(self):
        data = FIXTURE.projected()
        mutations = [lambda c: c.update(state='waiting'),
                     lambda c: c.update(timezone='America/New_York'),
                     lambda c: c.update(timezone='localtime'),
                     lambda c: c.update(timezone='right/UTC'),
                     lambda c: c.update(timezone='../UTC'),
                     lambda c: c.update(timezone=''),
                     lambda c: c.update(localCalendarAligned=1),
                     lambda c: c.update(extra=True),
                     lambda c: c.update(observedAt='not-an-instant'),
                     lambda c: c.update(expiresAt=c['observedAt']),
                     lambda c: c.update(expiresAt='2024-02-29T15:04:59Z'),
                     lambda c: c['keys'].update(day='2024-02-30'),
                     lambda c: c['keys'].update(week='2024-02-27'),
                     lambda c: c['keys'].update(week='2024-02-19'),
                     lambda c: c['keys'].update(month='2024-03'),
                     lambda c: c['keys'].update(year='2023'),
                     lambda c: c['keys'].update(extra='2024'),
                     lambda c: c['keys'].pop('day')]
        for index, mutate in enumerate(mutations):
            c = clock()
            mutate(c)
            with self.subTest(mutation=index):
                self.assert_rejected(query(data, None, c, now=millis(clock()['observedAt'])))
        for c in (False, [], 'UTC', {}):
            with self.subTest(shape=c):
                self.assert_rejected(query(data, None, c))

    def test_aggregate_api_refuses_filters_and_unsupported_options(self):
        data = FIXTURE.projected()
        options = (True, [], 'day', {'metric': ''}, {'grain': False}, {'mode': 0},
                   {'mode': 'multiple'}, {'sources': None}, {'sources': []},
                   {'sources': [data['sources'][0]['id']]}, {'unexpected': True})
        for value in options:
            with self.subTest(options=value):
                self.assert_rejected(query(data, None, options=value))
        self.assertFalse(query(data, None, options={'mode': 'combined'}).get('selectionError'))

    def test_future_cloud_projection_is_withheld_for_every_grain_and_metric(self):
        accounting, history = FIXTURE.load('hermes_accounting'), FIXTURE.load('usage_history')
        row = dict(session_id='synthetic-future', model='synthetic',
                   billing_provider='openai-codex', billing_base_url='https://api.openai.com/v1',
                   billing_mode='subscription_included', task='', first_seen=0, last_seen=1,
                   api_call_count=1, input_tokens=100, output_tokens=20,
                   cache_read_tokens=30, cache_write_tokens=5, reasoning_tokens=7)
        state = history.observe(None, accounting.canonical_snapshot([row], 'default'),
                                observed_at='2024-03-01T15:00:00Z', timezone='UTC')
        row.update(api_call_count=2, input_tokens=110, output_tokens=25,
                   cache_read_tokens=31, cache_write_tokens=6, reasoning_tokens=9)
        state = history.observe(state, accounting.canonical_snapshot([row], 'default'),
                                observed_at='2024-03-01T15:05:00Z', timezone='UTC')
        tomorrow = history.project_history(state)
        early = clock()
        early['observedAt'] = '2024-02-29T15:04:00Z'
        for case, data, c in (('same-day', FIXTURE.projected(), early),
                              ('cross-date', tomorrow, clock())):
            # Genuine producer projections must be admitted at their own observation.
            admitted = query(data, None, clock(data['observedAt'][:10]))
            self.assertFalse(admitted.get('selectionError'))
            self.assertTrue(admitted['coverage']['cloudKnown'])
            for grain in ('day', 'week', 'month', 'year'):
                for metric in FIXTURE.METRICS:
                    with self.subTest(case=case, grain=grain, metric=metric):
                        view = query(data, local_days(('2024-02-29', 9)), c,
                                     options={'grain': grain, 'metric': metric})
                        self.assertFalse(view.get('selectionError'), 'Valid Local must survive future Cloud')
                        for period in view['periods'].values():
                            self.assertIsNone(period['cloud']['value'])
                            self.assertEqual(period['value'], 9 if metric == 'total_tokens' else None)
                        self.assertTrue(all(bar['cloud']['value'] is None for bar in view['bars']))
                        self.assertEqual(view['bars'][-1], view['periods'][grain])
                        self.assertFalse(view['coverage']['sourcePopulationKnown'])
                        self.assertIsNone(view['undatedCloud']['value'])
                        self.assertIsNone(view['recordedCloud']['value'])

    def test_midnight_rollback_interval_is_not_allocated_to_repeated_calendar(self):
        accounting, history = FIXTURE.load('hermes_accounting'), FIXTURE.load('usage_history')
        row = dict(session_id='rollback-synthetic', model='synthetic',
                   billing_provider='openai-codex', billing_base_url='https://api.openai.com/v1',
                   billing_mode='subscription_included', task='', first_seen=0, last_seen=1,
                   api_call_count=1, input_tokens=100, output_tokens=20,
                   cache_read_tokens=30, cache_write_tokens=5, reasoning_tokens=7)
        state = history.observe(None, accounting.canonical_snapshot([row], 'default'),
                                observed_at='2009-11-01T02:30:30Z', timezone='America/St_Johns')
        row.update(api_call_count=2, input_tokens=110, output_tokens=25,
                   cache_read_tokens=31, cache_write_tokens=6, reasoning_tokens=9)
        state = history.observe(state, accounting.canonical_snapshot([row], 'default'),
                                observed_at='2009-11-01T03:30:30Z', timezone='America/St_Johns')
        self.assertEqual([event['allocated'] for event in state['events']],
                         [[], ['week', 'year']],
                         'Day/month must stay unallocated across the ambiguous rollback boundary')
        self.assertEqual(state['buckets']['year'][0]['key'], '2009')
        self.assertEqual(state['buckets']['year'][0]['sources'][list(state['sources'])[0]]['counts']['total_tokens'], 17)
        projected = history.project_history(state)
        self.assertEqual([bar['key'] for bar in projected['buckets']['day']], [])
        self.assertEqual([bar['key'] for bar in projected['buckets']['week']], ['2009-10-26'])
        self.assertEqual([bar['key'] for bar in projected['buckets']['month']], [])
        self.assertEqual([bar['key'] for bar in projected['buckets']['year']], ['2009'])
        self.assertEqual(projected['buckets']['year'][0]['sources'][0]['counts']['total_tokens'], 17)
        c = {'state': 'ready', 'timezone': 'America/St_Johns',
             'observedAt': '2009-11-01T03:30:30Z',
             'expiresAt': '2009-11-02T00:00:00Z',
             'keys': {'day': '2009-11-01', 'week': '2009-10-26', 'month': '2009-11', 'year': '2009'},
             'localCalendarAligned': True}
        view = query(projected, None, c)
        self.assertFalse(view.get('selectionError'))
        self.assertIsNone(view['periods']['day']['cloud']['value'])
        self.assertEqual(view['periods']['week']['cloud']['value'], 17)
        self.assertIsNone(view['periods']['month']['cloud']['value'])
        self.assertEqual(view['periods']['year']['cloud']['value'], 17)
        self.assertEqual(view['bars'][-1]['key'], '2009-11-01')
        self.assertIsNone(view['bars'][-1]['value'])

    def test_future_cloud_timezone_does_not_poison_independent_local_clock(self):
        data = FIXTURE.projected()
        data['timezone'] = 'America/New_York'
        c = clock()
        c['observedAt'] = '2024-02-29T15:04:00Z'
        view = query(data, local_days(('2024-02-29', 9)), c)
        self.assertFalse(view.get('selectionError'))
        self.assertEqual(view['periods']['day']['value'], 9)
        self.assertIsNone(view['periods']['day']['cloud']['value'])
        self.assertEqual(view['bars'][-1], view['periods']['day'])
        # Do not relax the timezone check once the Cloud projection is no longer future.
        self.assert_rejected(query(data, None, c, now=millis(data['observedAt'])))

    def test_cloud_uses_current_keys_never_the_latest_retained_period(self):
        data = FIXTURE.projected()
        view = query(data, None, clock('2024-03-01'))
        self.assertIsNone(view['periods']['day']['value'])
        self.assertEqual(view['periods']['week']['value'], 50)
        self.assertIsNone(view['periods']['month']['value'])
        self.assertEqual(view['periods']['year']['value'], 50)
        self.assertEqual(view['bars'][-2]['value'], 50)
        self.assertIsNone(view['bars'][-1]['value'])
        for grain in ('day', 'week', 'month', 'year'):
            with self.subTest(grain=grain):
                c = clock('2024-04-03')
                selected = query(data, None, c, options={'grain': grain})
                self.assertEqual([bar['key'] for bar in selected['bars']],
                                 bar_keys(c['keys']['day'], grain))
                self.assertEqual(selected['bars'][-1], selected['periods'][grain])
                self.assertEqual(selected['periods'][grain]['key'], c['keys'][grain])
                self.assertEqual(selected['metric'], 'total_tokens')
                self.assertEqual(selected['grain'], grain)

    def test_local_today_is_an_exact_date_not_the_last_recorded_row(self):
        local = local_days(('2024-02-26', 2), ('2024-02-28', 3))
        view = query(FIXTURE.projected(), local)
        self.assertIsNone(view['periods']['day']['local']['value'])
        self.assertEqual(view['periods']['day']['value'], 50)
        self.assertEqual(view['periods']['week']['local']['value'], 5)
        self.assertEqual(view['bars'][-2]['value'], 3)
        self.assertEqual(view['bars'][-1]['value'], 50)

    def test_local_week_is_monday_based_not_the_last_seven_rows(self):
        local = local_days(('2024-02-20', 100), ('2024-02-25', 200),
                           ('2024-02-26', 2), ('2024-02-28', 3), ('2024-02-29', 4))
        view = query(FIXTURE.projected(), local, options={'grain': 'week'})
        self.assertEqual(view['periods']['week']['local']['value'], 9)
        self.assertEqual(view['periods']['week']['value'], 59)
        self.assertEqual(view['periods']['month']['local']['value'], 309)
        self.assertEqual(view['bars'][-2]['value'], 300)
        self.assertEqual(view['bars'][-1], view['periods']['week'])

    def test_local_only_calendar_periods_agree_across_leap_and_year_boundaries(self):
        cases = [('2024-03-01', [('2024-02-25', 99), ('2024-02-26', 2),
                                  ('2024-02-29', 3), ('2024-03-01', 5)], [5, 10, 5, 109]),
                 ('2024-01-01', [('2023-12-25', 11), ('2023-12-31', 13),
                                  ('2024-01-01', 17)], [17, 17, 17, 17]),
                 ('2025-01-01', [('2024-12-30', 2), ('2025-01-01', 3)], [3, 5, 3, 3]),
                 ('2024-02-29', [('2024-02-28', 2), ('2024-02-29', 3)], [3, 5, 5, 5]),
                 ('2023-03-01', [('2023-02-28', 2), ('2023-03-01', 3)], [3, 5, 3, 5])]
        for day, rows, expected in cases:
            for grain in ('day', 'week', 'month', 'year'):
                with self.subTest(day=day, grain=grain):
                    view = query(None, local_days(*reversed(rows)), clock(day), options={'grain': grain})
                    self.assertIsNotNone(view['periods']['day'])
                    self.assertEqual([view['periods'][g]['value'] for g in ('day', 'week', 'month', 'year')],
                                     expected)
                    self.assertTrue(all(view['periods'][g]['cloud']['value'] is None
                                        for g in ('day', 'week', 'month', 'year')))
                    self.assertEqual([bar['key'] for bar in view['bars']], bar_keys(day, grain))
                    self.assertEqual(view['bars'][-1], view['periods'][grain])
                    values = [bar['value'] for bar in view['bars'] if bar['value'] is not None]
                    self.assertEqual(view['maximum'], max(values))

    def test_malformed_cloud_report_does_not_erase_valid_local_dates(self):
        view = query({'not': 'public history'}, local_days(('2024-02-29', 9)))
        self.assertIsNotNone(view['periods']['day'])
        self.assertEqual(view['periods']['day']['value'], 9)
        self.assertIsNone(view['periods']['day']['cloud']['value'])

    def test_local_measured_zero_and_recorded_false_gaps_are_distinct(self):
        for data, expected in ((None, 0), (FIXTURE.projected(), 50)):
            with self.subTest(cloud=data is not None):
                view = query(data, local_days(('2024-02-29', 0)))
                self.assertEqual(view['periods']['day']['local']['value'], 0)
                self.assertEqual(view['periods']['day']['value'], expected)
                self.assertEqual(view['bars'][-1], view['periods']['day'])
                self.assertEqual(view['maximum'], expected)
        local = local_days(('2024-02-26', 2), ('2024-02-27', None),
                           ('2024-02-28', 0), ('2024-02-29', None))
        view = query(FIXTURE.projected(), local)
        self.assertIsNone(view['periods']['day']['local']['value'])
        self.assertEqual(view['periods']['week']['local']['value'], 2)
        self.assertEqual(view['periods']['month']['local']['value'], 2)
        self.assertEqual([bar['local']['value'] for bar in view['bars'][-4:]], [2, None, 0, None])
        missing = query(None, local_days(('2024-02-29', None)))
        self.assertTrue(all(p['value'] is None for p in missing['periods'].values()))
        self.assertEqual(missing['maximum'], 0)

    def test_local_total_units_never_enter_other_metrics(self):
        data, local = FIXTURE.projected(), local_days(('2024-02-29', 9))
        local.update(todayTotalTokens=999999, modelUsage={'synthetic': {'inputTokens': 999999}},
                     calendarWeek={'totalTokens': 999999})
        for metric in FIXTURE.METRICS:
            with self.subTest(metric=metric):
                expected = sum(cell['counts'][metric] for cell in data['buckets']['day'][-1]['sources'])
                view = query(data, local, options={'metric': metric})
                self.assertEqual(view['periods']['day']['cloud']['value'], expected)
                self.assertEqual(view['periods']['day']['local']['value'], 9 if metric == 'total_tokens' else None)
                self.assertEqual(view['periods']['day']['value'], expected + (9 if metric == 'total_tokens' else 0))
                self.assertEqual(view['bars'][-1], view['periods']['day'])
                if metric != 'total_tokens':
                    local_only = query(None, local, options={'metric': metric})
                    self.assertTrue(all(p['value'] is None for p in local_only['periods'].values()))

    def test_local_requires_the_aligned_collector_contract(self):
        data = FIXTURE.projected()
        good = local_days(('2024-02-29', 9))
        mutations = [lambda l: l.update(id='claude'),
                     lambda l: l.pop('id'),
                     lambda l: l.update(readOnly=False),
                     lambda l: l.update(hasLocalStats=False),
                     lambda l: l.update(recentDays={}),
                     lambda l: l['recentDays'].append(deepcopy(l['recentDays'][0])),
                     lambda l: l['recentDays'].append({'date': '2024-02-29', 'messageCount': None, 'recorded': False}),
                     lambda l: l['recentDays'][0].update(date='2024-02-30'),
                     lambda l: l['recentDays'][0].update(date='2024-2-29'),
                     lambda l: l['recentDays'][0].update(date='2024-03-01'),
                     lambda l: l['recentDays'][0].update(messageCount=True),
                     lambda l: l['recentDays'][0].update(messageCount='9'),
                     lambda l: l['recentDays'][0].update(messageCount=-1),
                     lambda l: l['recentDays'][0].update(messageCount=0.5),
                     lambda l: l['recentDays'][0].update(messageCount=None),
                     lambda l: l['recentDays'][0].update(messageCount=2**53),
                     lambda l: l['recentDays'][0].pop('recorded'),
                     lambda l: l['recentDays'][0].update(recorded=1),
                     lambda l: l['recentDays'][0].update(recorded=False),
                     lambda l: l['recentDays'][0].update(sessionCount=0),
                     lambda l: l['recentDays'][0].update(toolCallCount=0),
                     lambda l: l['recentDays'][0].update(extra=True),
                     lambda l: l['recentDays'].__setitem__(0, None)]
        for index, mutate in enumerate(mutations):
            local = deepcopy(good)
            mutate(local)
            with self.subTest(mutation=index):
                view = query(data, local)
                self.assertIsNotNone(view['periods']['day'], 'bad Local must not erase Cloud')
                self.assertIsNone(view['periods']['day']['local']['value'])
                self.assertEqual(view['periods']['day']['value'], 50)
                self.assertEqual(view['bars'][-1]['value'], 50)
        c = clock()
        c['localCalendarAligned'] = False
        unaligned = query(data, good, c)
        self.assertIsNone(unaligned['periods']['day']['local']['value'])
        self.assertEqual(unaligned['periods']['day']['value'], 50)
        for local in (None, {}, False, [], local_days()):
            with self.subTest(unavailable=local):
                view = query(data, local)
                self.assertIsNone(view['periods']['day']['local']['value'])
                self.assertEqual(view['periods']['day']['value'], 50)

    def test_local_row_bound_is_finite_and_does_not_truncate(self):
        end = dt.date(2024, 2, 29)
        rows = [(str(end - dt.timedelta(days=i)), 1) for i in range(366)]
        self.assertEqual(query(None, local_days(*rows))['periods']['day']['local']['value'], 1)
        too_many = local_days(*(rows + [('2023-02-28', 1)]))
        view = query(FIXTURE.projected(), too_many)
        self.assertIsNone(view['periods']['day']['local']['value'])
        self.assertEqual(view['periods']['day']['value'], 50)

    def test_overflow_is_refused_and_unobserved_deltas_remain_unknown(self):
        limit = 2**53 - 1
        for local in (local_days(('2024-02-29', limit)),
                      local_days(('2024-02-28', limit), ('2024-02-29', 1))):
            with self.subTest(rows=local['recentDays']):
                self.assert_rejected(query(FIXTURE.projected(), local))
        measured_limit = query(None, local_days(('2024-02-29', limit)))
        self.assertEqual(measured_limit['periods']['day']['value'], limit)
        self.assertEqual(measured_limit['bars'][-1], measured_limit['periods']['day'])
        baseline = query(FIXTURE.inventory(count=1, changed=0, zero=0), None)
        self.assertIsNone(baseline['recordedCloud']['value'])
        self.assertIsNotNone(baseline['undatedCloud']['value'])
        self.assertIsNone(baseline['periods']['day']['value'])
        measured_zero = query(FIXTURE.inventory(count=1, changed=0, zero=1), None)
        self.assertEqual(measured_zero['recordedCloud']['value'], 0)
        self.assertEqual(measured_zero['periods']['day']['value'], 0)

    def test_coverage_counts_measured_sources_not_missing_or_baseline_rows(self):
        local = local_days(('2024-02-29', 0))
        for count, partial in ((5, True), (3, False)):
            data = FIXTURE.inventory(count=count, changed=2, zero=1)
            view = query(data, local)
            day = view['periods']['day']
            self.assertEqual(day['cloud']['sources'], count)
            self.assertEqual(day['cloud']['knownSources'], 3)
            self.assertEqual(day['cloud']['partial'], partial)
            self.assertEqual(day['local']['sources'], 1)
            self.assertEqual(day['local']['knownSources'], 1)
            self.assertEqual(day['local']['recordedDays'], 1)
            self.assertEqual(day['local']['elapsedDays'], 1)
            self.assertEqual(day['local']['value'], 0)
            self.assertEqual(day['knownSources'], 4)
            self.assertEqual(day['sources'], count + 1)
            self.assertEqual(day['partial'], partial)
            self.assertTrue(day['lowerBound'], 'Sampling never proves full request history')
            self.assertEqual(view['bars'][-1], day)
        missing_local = query(FIXTURE.projected(), None)['periods']['day']
        self.assertEqual(missing_local['knownSources'], 2)
        self.assertEqual(missing_local['sources'], 3)
        self.assertTrue(missing_local['partial'])
        local_only = query(None, local)['periods']['day']
        self.assertEqual(local_only['value'], 0)
        self.assertEqual(local_only['knownSources'], 1)
        self.assertIsNone(local_only['sources'], 'Unavailable Cloud has unknown population')
        self.assertTrue(local_only['partial'])
        for grain, elapsed in (('week', 4), ('month', 29), ('year', 60)):
            selected = query(FIXTURE.projected(), local, options={'grain': grain})
            period = selected['periods'][grain]
            self.assertEqual(period['local']['recordedDays'], 1)
            self.assertEqual(period['local']['elapsedDays'], elapsed)
            self.assertTrue(period['local']['partial'])
            self.assertTrue(period['partial'])
            self.assertEqual(selected['bars'][-1], period)
        cloud_metric = query(FIXTURE.projected(), local, options={'metric': 'input_tokens'})['periods']['day']
        self.assertEqual(cloud_metric['sources'], 2)
        self.assertEqual(cloud_metric['knownSources'], 2)
        self.assertEqual(cloud_metric['local']['sources'], 0)
        self.assertFalse(cloud_metric['partial'])

    def test_retained_snapshots_and_recorded_deltas_are_not_lifetime_baselines(self):
        data = FIXTURE.projected()
        for metric in FIXTURE.METRICS:
            with self.subTest(metric=metric):
                view = query(data, local_days(('2024-02-29', 9)), options={'metric': metric})
                snapshot = sum(s['counts'][metric] for s in data['sources'])
                recorded = sum(s['coverage']['observed'][metric] for s in data['sources'])
                self.assertEqual(view['undatedCloud']['value'], snapshot)
                self.assertEqual(view['recordedCloud']['value'], recorded)
                self.assertEqual(view['undatedCloud']['metric'], metric)
                self.assertEqual(view['recordedCloud']['metric'], metric)
                self.assertIsNone(view['lifetime'], 'Current snapshots/deltas cannot establish lifetime usage')
                expected = recorded + (9 if metric == 'total_tokens' else 0)
                self.assertEqual(view['periods']['day']['value'], expected)
                self.assertIn('undated', view['help'].lower())
                self.assertNotIn('is undated coverage', view['help'])
        self.assertNotEqual(sum(s['counts']['total_tokens'] for s in data['sources']),
                            sum(s['coverage']['observed']['total_tokens'] for s in data['sources']))

    def test_real_synthetic_local_publisher_keeps_zero_and_gap_semantics(self):
        import time
        old_tz = os.environ.get('TZ')
        os.environ['TZ'] = 'UTC'
        time.tzset()
        collector = FIXTURE.load('collector')
        ledger = collector.Ledger(':memory:')
        try:
            now = millis(clock()['observedAt']) / 1000
            ledger.observe('synthetic', 'epoch', (100, 20), now - 300)
            ledger.observe('synthetic', 'epoch', (107, 22), now)
            local = ledger.record(now, 'synthetic offline fixture')
            self.assertEqual(len(local['recentDays']), 7)
            self.assertTrue(all(row['recorded'] is False and row['messageCount'] is None
                                for row in local['recentDays'][:-1]))
            self.assertEqual(local['recentDays'][-1]['messageCount'], 9)
            view = query(FIXTURE.projected(), local)
            self.assertEqual(view['periods']['day']['local']['value'], 9)
            self.assertEqual(view['bars'][-1]['value'], 59)
        finally:
            ledger.db.close()
            if old_tz is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = old_tz
            time.tzset()


if __name__ == '__main__':
    unittest.main(verbosity=2)
