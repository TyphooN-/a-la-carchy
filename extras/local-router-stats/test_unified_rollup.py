#!/usr/bin/env python3
"""Offline contract of the unified dated token roll-up: collector, consumer and bar.

Synthetic data and isolated paths only: no router, live state, credentials or
network. Run with `python3 -B -m pytest test_unified_rollup.py` or directly.
ROLLUP_TEST_SOURCES selects another source directory, for example preimages.
"""
import contextlib
import copy
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

os.environ['TZ'] = 'America/New_York'
time.tzset()
HERE = Path(__file__).resolve().parent
SOURCES = Path(os.environ.get('ROLLUP_TEST_SOURCES', HERE))
NODE = shutil.which('node') or '/home/typhoon/.hermes/tools/node-26.12.1-linux-x64/bin/node'
ZONE = ZoneInfo('America/New_York')
FIELDS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens', 'api_call_count')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load('rollup_collector', SOURCES/'collector.py')
stager = load('rollup_stager', SOURCES/'stage_plugin.py')


def at(text):
    return dt.datetime.fromisoformat(text).replace(tzinfo=ZONE).timestamp()


def counts(**values):
    row = dict.fromkeys(FIELDS, 0) | values
    prompt = row['input_tokens'] + row['cache_read_tokens'] + row['cache_write_tokens']
    return {**row, 'prompt_tokens': prompt, 'total_tokens': prompt + row['output_tokens']}


def history(days, observed='2026-10-31T16:00:00Z', zone='America/New_York', provider='openai-codex', covered=86000):
    """A hermes-observed-history/1 projection: two sessions behind one provider."""
    first, second = 'a' * 64, 'b' * 64
    buckets = []
    for key, row in days.items():
        cells = [{'id': first, 'counts': counts(**row), 'coveredSeconds': covered, 'intervals': 1}]
        cells.append({'id': second, 'counts': counts(), 'coveredSeconds': covered, 'intervals': 1})
        buckets.append({'key': key, 'sources': cells})
    return {'contract': 'hermes-observed-history/1', 'schemaVersion': 1, 'readOnly': True, 'timezone': zone,
            'startedAt': '2026-10-19T23:21:31Z', 'observedAt': observed, 'generation': 1,
            'sources': [{'id': first, 'provider': provider}, {'id': second, 'provider': provider}],
            'buckets': {'day': buckets, 'week': [], 'month': [], 'year': []}, 'events': [], 'coverage': {}}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # RollupPublisher.tick merges $XDG_STATE_HOME/omarchy/agents/usage/claude.json
        # (else ~/.local/state). Isolate it so live Claude usage never joins synthetic days.
        environment = mock.patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root/'state')})
        environment.start()
        self.addCleanup(environment.stop)
        self.ledger = collector.Ledger(self.root/'tokens.sqlite3')
        self.addCleanup(self.ledger.db.close)

    def local_day(self, day, model='heretic', inp=0, out=0, first=None, last=None):
        start, end = collector.day_span(day)
        with self.ledger.db:
            self.ledger.db.execute('INSERT INTO daily VALUES (?,?,?,?) ON CONFLICT(day,model) DO UPDATE SET input=excluded.input, output=excluded.output', (day, model, inp, out))
            self.ledger.db.execute('INSERT OR REPLACE INTO coverage VALUES (?,?,?)',
                                   (day, start + 5 if first is None else first, end - 5 if last is None else last))

    def days(self, now, feed=None):
        return self.ledger.rollup(now, feed or {'state': 'missing'})['days']


class ReportedDay(Base):
    """10-30: the calendar read a measured Cloud 0 while the router sampled 11.7M heretic tokens."""
    NOW = at('2026-10-31T12:00:00')

    def merged(self):
        self.local_day('2026-10-30', inp=11572053, out=132560, first=at('2026-10-30T07:38:14'), last=at('2026-10-30T23:59:56'))
        return self.ledger.merge_cloud(history({'2026-10-30': {}}, covered=62506), self.NOW)

    def test_local_tokens_join_the_measured_cloud_zero(self):
        self.assertEqual(self.merged()['state'], 'admitted')
        day = self.days(self.NOW)['2026-10-30']
        self.assertEqual(day['total'], 11704613)
        self.assertEqual(day['byModel'], {'heretic': {'input_tokens': 11572053, 'output_tokens': 132560, 'total_tokens': 11704613}})
        self.assertEqual(day['byProvider']['openai-codex']['total_tokens'], 0)

    def test_partial_recording_is_marked_partial_not_zero(self):
        self.merged()
        coverage = self.days(self.NOW)['2026-10-30']['coverage']
        self.assertTrue(coverage['partial'])
        self.assertTrue(coverage['local']['partial'])
        self.assertEqual(coverage['local']['basis'], 'samples')
        self.assertTrue(coverage['cloud']['partial'])

    def test_both_sources_sum_on_a_shared_day(self):
        self.local_day('2026-10-29', inp=3230872, out=85731)
        self.ledger.merge_cloud(history({'2026-10-29': {'input_tokens': 100, 'cache_read_tokens': 900, 'output_tokens': 50}}), self.NOW)
        day = self.days(self.NOW)['2026-10-29']
        self.assertEqual(day['total'], 3230872 + 85731 + 1050)
        self.assertEqual(day['byProvider']['openai-codex']['prompt_tokens'], 1000)

    def test_sessions_reduce_to_one_row_per_provider(self):
        self.ledger.merge_cloud(history({'2026-10-30': {'output_tokens': 7}}), self.NOW)
        rows = self.ledger.db.execute('SELECT COUNT(*) FROM cloud_daily').fetchone()[0]
        self.assertEqual(rows, 1)


class Unknowns(Base):
    NOW = at('2026-10-31T12:00:00')

    def test_date_with_no_record_is_absent(self):
        self.local_day('2026-10-29', inp=5)
        self.assertNotIn('2026-10-28', self.days(self.NOW))

    def test_cloud_only_day_has_unknown_local(self):
        self.ledger.merge_cloud(history({'2026-10-28': {'output_tokens': 9}}), self.NOW)
        day = self.days(self.NOW)['2026-10-28']
        self.assertIsNone(day['coverage']['local'])
        self.assertEqual(day['total'], 9)
        self.assertTrue(day['coverage']['partial'])

    def test_local_only_day_has_unknown_cloud_once_cloud_exists(self):
        self.local_day('2026-10-27', inp=5)
        self.ledger.merge_cloud(history({'2026-10-28': {'output_tokens': 9}}), self.NOW)
        day = self.days(self.NOW)['2026-10-27']
        self.assertIsNone(day['coverage']['cloud'])
        self.assertEqual(day['total'], 5)
        self.assertTrue(day['coverage']['partial'], 'unknown Cloud makes the sum a lower bound')

    def test_machine_without_cloud_is_not_partial_for_lacking_it(self):
        self.local_day('2026-10-27', inp=5)
        self.assertFalse(self.days(self.NOW)['2026-10-27']['coverage']['partial'])

    def test_idle_router_is_a_measured_zero(self):
        start, end = collector.day_span('2026-10-30')
        self.ledger.heartbeat(start + 3)
        for moment in range(int(start) + 60, int(end), 90):
            self.ledger.heartbeat(moment)
        day = self.days(self.NOW)['2026-10-30']
        self.assertEqual((day['total'], day['byModel']), (0, {}))
        self.assertEqual(day['coverage']['local']['basis'], 'collector')
        self.assertFalse(day['coverage']['partial'])

    def test_zero_token_model_rows_are_not_listed(self):
        self.local_day('2026-10-30', model='loaded-only', inp=0, out=0)
        self.assertEqual(self.days(self.NOW)['2026-10-30']['byModel'], {})


class CloudRetention(Base):
    NOW = at('2026-11-05T12:00:00')

    def test_day_survives_the_projection_pruning_it(self):
        self.ledger.merge_cloud(history({'2026-10-29': {'input_tokens': 217967547}}, observed='2026-10-30T01:00:00Z'), self.NOW)
        self.ledger.merge_cloud(history({'2026-11-05': {}}, observed='2026-11-05T16:00:00Z'), self.NOW)
        days = self.days(self.NOW)
        self.assertEqual(days['2026-10-29']['total'], 217967547)
        self.assertIn('2026-11-05', days)

    def test_day_grows_with_later_samples(self):
        self.ledger.merge_cloud(history({'2026-11-04': {'input_tokens': 10}}, covered=100), self.NOW)
        self.ledger.merge_cloud(history({'2026-11-04': {'input_tokens': 25}}, covered=200), self.NOW)
        day = self.days(self.NOW)['2026-11-04']
        self.assertEqual(day['total'], 25)
        self.assertEqual(day['coverage']['cloud']['coveredSeconds'], 200)

    def test_lower_value_after_a_history_reset_never_replaces_a_higher_one(self):
        self.ledger.merge_cloud(history({'2026-11-04': {'input_tokens': 500, 'api_call_count': 9}}, covered=900), self.NOW)
        self.ledger.merge_cloud(history({'2026-11-04': {'input_tokens': 3, 'api_call_count': 1}}, covered=100), self.NOW)
        day = self.days(self.NOW)['2026-11-04']
        self.assertEqual(day['total'], 500)
        self.assertEqual(day['byProvider']['openai-codex']['api_call_count'], 9)
        self.assertEqual(day['coverage']['cloud']['coveredSeconds'], 900)

    def test_older_snapshot_can_restore_a_pruned_day_without_lowering_others(self):
        self.ledger.merge_cloud(history({'2026-11-04': {'input_tokens': 40}}, observed='2026-11-04T20:00:00Z'), self.NOW)
        self.ledger.merge_cloud(history({'2026-10-26': {'input_tokens': 7}, '2026-11-04': {'input_tokens': 2}}, observed='2026-11-04T10:00:00Z'), self.NOW)
        days = self.days(self.NOW)
        self.assertEqual((days['2026-10-26']['total'], days['2026-11-04']['total']), (7, 40))

    def test_retention_prunes_after_a_year(self):
        old = str(collector.local_date(self.NOW) - dt.timedelta(days=400))
        with self.ledger.db:
            self.ledger.db.execute('INSERT INTO cloud_daily VALUES (?,?,?,1,0,0,0,0,0,10)', (old, 'openai-codex', 'America/New_York'))
            self.ledger.db.execute('INSERT INTO uptime VALUES (?,?,?,0)', (old, 1.0, 2.0))
        self.assertNotIn(old, self.days(self.NOW), 'an expired date is never published')
        self.ledger.merge_cloud(history({'2026-11-04': {}}), self.NOW)
        self.ledger.heartbeat(self.NOW)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM cloud_daily WHERE day=?', (old,)).fetchone()[0], 0)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM uptime WHERE day=?', (old,)).fetchone()[0], 0)

    def test_the_real_producer_projection_is_admitted(self):
        producer = load('rollup_usage_history', SOURCES/'usage_history.py')
        def snapshot(total):
            row = counts(input_tokens=total)
            source = {'id': 'c' * 64, 'provider': 'openai-codex', 'profile': 'default', 'attribution': 'unattributed', 'counts': row}
            return {'profile': 'default', 'record': producer.accounting_record([source], 'default'), 'sources': [source]}
        state = producer.observe(None, snapshot(100), observed_at='2026-11-04T16:00:00Z', timezone='America/New_York')
        state = producer.observe(state, snapshot(175), observed_at='2026-11-04T16:01:00Z', timezone='America/New_York')
        self.assertEqual(self.ledger.merge_cloud(producer.project_history(state), self.NOW)['state'], 'admitted')
        self.assertEqual(self.days(self.NOW)['2026-11-04']['total'], 75, 'the first counters are an undated baseline')


class ProducerCalendar(unittest.TestCase):
    """R5: America/St_Johns 2009-11-01 00:00:59 NDT rolled back to 10-31 23:01 NST.

    2009-11-01 starts at 02:30Z, drops back to 2009-10-31 at 02:31Z and starts
    again at 03:30Z. Endpoints that both read 11-01 can hide that excursion.
    """
    ZONE = ZoneInfo('America/St_Johns')
    EVERY = ['day', 'week', 'month', 'year']

    @classmethod
    def setUpClass(cls):
        cls.producer = load('rollup_producer_calendar', SOURCES/'usage_history.py')

    def grains(self, start, end, zone=None):
        instant = self.producer.instant
        return self.producer.allocated_grains(instant(start), instant(end), zone or self.ZONE, 'delta')

    def test_midnight_rollback_excursion_is_never_dated(self):
        for start, end, expected, why in (
                ('02:30:30', '03:30:30', ['week', 'year'], 'both ends read 11-01 around 59 minutes of 10-31'),
                ('02:30:30', '03:30:00', ['week', 'year'], 'ending on the repeated midnight still holds the excursion'),
                ('02:30:10', '02:30:50', self.EVERY, 'the first 11-01 minute alone is one day'),
                ('03:30:00', '04:30:00', self.EVERY, 'after the repeated midnight it is one day'),
                ('02:29:00', '03:00:00', ['week', 'year'], 'both ends read 10-31 around the first 11-01 minute'),
                ('03:00:00', '03:10:00', ['week', 'year'], 'inside the excursion: outside 10-31 UTC bounds')):
            with self.subTest(why, start=start, end=end):
                self.assertEqual(self.grains('2009-11-01T' + start + 'Z', '2009-11-01T' + end + 'Z'), expected)

    def test_midnight_repeat_inside_one_day_stays_dated(self):
        # America/Havana 2025-11-02 00:59:59 CDT -> 00:00 CST repeats midnight without leaving the day.
        havana = ZoneInfo('America/Havana')
        self.assertEqual(self.grains('2025-11-02T04:30:00Z', '2025-11-02T05:30:00Z', havana), self.EVERY)

    def test_observed_rollback_delta_is_unallocated_not_misdated(self):
        producer = self.producer
        def snapshot(total):
            source = {'id': 'c' * 64, 'provider': 'openai-codex', 'profile': 'default', 'attribution': 'unattributed',
                      'counts': counts(input_tokens=total)}
            return {'profile': 'default', 'record': producer.accounting_record([source], 'default'), 'sources': [source]}
        state = producer.observe(None, snapshot(100), observed_at='2009-11-01T02:30:30Z', timezone='America/St_Johns')
        state = producer.observe(state, snapshot(175), observed_at='2009-11-01T03:30:30Z', timezone='America/St_Johns')
        self.assertEqual(state['events'][-1]['allocated'], ['week', 'year'])
        self.assertEqual((state['buckets']['day'], state['buckets']['month']), ([], []), 'no 10-31 tokens on 11-01 or November')
        self.assertEqual([bucket['key'] for bucket in state['buckets']['week']], ['2009-10-26'])
        for grain, unallocated in (('day', 75), ('week', 0), ('month', 75), ('year', 0)):
            self.assertEqual(state['coverage']['unallocated'][grain]['total_tokens'], unallocated, grain)
        self.assertIs(producer.validate_state(state), state)


class HistoryInputStability(unittest.TestCase):
    def setUp(self):
        self.producer = load('stable_history_inputs', SOURCES/'usage_history.py')

    def source(self, identity, total=100):
        return {'id': identity * 64, 'provider': 'openai-codex', 'profile': 'default',
                'attribution': 'unattributed', 'counts': counts(input_tokens=total)}

    def snapshot(self, sources):
        return {'profile': 'default', 'record': self.producer.accounting_record(sources, 'default'),
                'sources': sources}

    def test_optional_lifetime_bounds_cannot_overflow_or_date_a_new_baseline(self):
        source = self.source('a')
        source.update(firstSeen=253402300798, lastSeen=253402300799)
        snapshot = self.snapshot([source])
        original = copy.deepcopy(snapshot)
        state = self.producer.observe(None, snapshot, observed_at='2026-10-30T16:00:00Z', timezone='Asia/Tokyo')
        self.assertEqual(snapshot, original)
        self.assertEqual(state['events'][0]['status'], 'baseline')
        self.assertEqual(state['coverage']['observed']['total_tokens'], 0)
        self.assertTrue(all(not buckets for buckets in state['buckets'].values()))
        self.assertIs(self.producer.validate_state(state), state)


    def test_valid_unsorted_sources_have_an_order_independent_snapshot_digest(self):
        sources = [self.source('b', 200), self.source('a', 100)]
        snapshot = self.snapshot(sources)
        self.producer.validate_snapshot(snapshot)
        first = self.producer.observe(None, snapshot, observed_at='2026-10-30T16:00:00Z', timezone='UTC')
        reordered = self.snapshot(list(reversed(sources)))
        repeated = self.producer.observe(first, reordered, observed_at='2026-10-30T16:00:00Z', timezone='UTC')
        self.assertEqual(repeated, first, 'reordering identical facts is an idempotent observation')
        updated = self.snapshot([self.source('b', 211), self.source('a', 107)])
        later = self.producer.observe(first, updated, observed_at='2026-10-30T16:01:00Z', timezone='UTC')
        self.assertEqual(later['coverage']['observed']['total_tokens'], 18)
        self.assertIs(self.producer.validate_state(later), later)


class HistoryCapacity(unittest.TestCase):
    """B1/S1: a retained (not lifetime) source inventory, dense retention, explicit refusal."""
    source = HistoryInputStability.source
    snapshot = HistoryInputStability.snapshot

    def setUp(self):
        self.producer = load('history_capacity', SOURCES/'usage_history.py')

    def rows(self, start, stop, total=100):
        return [dict(self.source('a', total), id=format(i, '064x')) for i in range(start, stop)]

    def run_observations(self, state, rows, *instants):
        for instant in instants:
            state = self.producer.observe(state, self.snapshot(rows), observed_at=instant, timezone='UTC')
        return state

    def test_identities_outside_retention_retire_so_arrivals_are_admitted(self):
        cap = self.producer.MAX_HISTORY_SOURCES
        state = self.run_observations(None, self.rows(0, cap), '2026-11-05T16:00:00Z')
        state = self.run_observations(state, [], '2026-11-05T16:01:00Z')
        arrival = self.run_observations(state, self.rows(cap, cap + 2), '2026-11-05T16:02:00Z')
        self.assertEqual(len(arrival['sources']), cap, 'two journal-expired identities retire for two arrivals')
        self.assertNotIn(format(0, '064x'), arrival['sources'])
        self.assertIn(format(2, '064x'), arrival['sources'], 'identities with retained facts stay')
        self.assertTrue(arrival['sources'][format(cap + 1, '064x')]['present'])
        self.assertEqual(arrival['coverage']['events']['baseline'], cap + 2, 'retirement keeps lifetime coverage')
        self.assertEqual(arrival['coverage']['events']['missing'], cap)
        self.assertIs(self.producer.validate_state(arrival), arrival)

    def test_every_grain_keeps_the_dense_population_including_measured_zero(self):
        cap = self.producer.MAX_HISTORY_SOURCES
        state = self.run_observations(None, self.rows(0, cap), '2026-11-04T16:00:00Z', '2026-11-04T16:01:00Z',
                                      '2026-11-05T16:00:00Z', '2026-11-05T16:01:00Z')
        retained = {grain: [(bucket['key'], len(bucket['sources'])) for bucket in buckets]
                    for grain, buckets in state['buckets'].items()}
        self.assertEqual(retained, {'day': [('2026-11-04', cap), ('2026-11-05', cap)], 'week': [('2026-11-02', cap)],
                                    'month': [('2026-11', cap)], 'year': [('2026', cap)]})
        self.assertEqual(state['coverage']['pruned'], {grain: self.producer.zeros() for grain in self.producer.GRAINS},
                         'nothing inside the promised retention is pruned')
        self.assertEqual(state['coverage']['events']['measured_zero'], 2 * cap)

    def test_beyond_cap_arrival_is_refused_and_leaves_the_last_good_state(self):
        cap = self.producer.MAX_HISTORY_SOURCES
        state = self.run_observations(None, self.rows(0, cap), '2026-11-05T16:00:00Z', '2026-11-05T16:01:00Z')
        state = self.run_observations(state, [], '2026-11-05T16:02:00Z')
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, 'retained history source capacity'):
            self.run_observations(state, self.rows(cap, cap + 1), '2026-11-05T16:03:00Z')
        self.assertEqual(state, before, 'every identity still holds retained dated cells')

    def test_all_history_readers_share_the_finite_dense_budget(self):
        accounting = load('capacity_accounting', SOURCES/'hermes_accounting.py')
        observed = run_node('out.cells=H.historyCellLimit;out.bytes=H.historyByteLimit;')
        self.assertEqual(observed, {'cells': self.producer.MAX_CELLS,
                                    'bytes': self.producer.MAX_STATE_BYTES})
        self.assertEqual(accounting.MAX_OUTPUT, self.producer.MAX_STATE_BYTES)
        self.assertEqual(collector.HISTORY_LIMIT, self.producer.MAX_STATE_BYTES)
        self.assertEqual(collector.PUBLICATION_LIMIT, 4 * 1024 * 1024,
                         'unified roll-up publication is a separate unchanged contract')

    def test_retired_identity_restarts_without_backfill_and_legacy_state_still_loads(self):
        cap = self.producer.MAX_HISTORY_SOURCES
        state = self.run_observations(None, self.rows(0, cap), '2026-11-05T16:00:00Z')
        self.assertNotIn('retired', state, 'old journals need no new field or migration')
        restored = json.loads(json.dumps(state))
        self.assertIs(self.producer.validate_state(restored), restored)
        state = restored
        state = self.run_observations(state, [], '2026-11-05T16:01:00Z')
        state = self.run_observations(state, self.rows(cap, cap + 2), '2026-11-05T16:02:00Z')
        self.assertEqual(state['retired']['sources'], 2)
        state = self.producer.validate_state(json.loads(json.dumps(state)))
        state = self.run_observations(state, self.rows(0, 1, total=1000), '2026-11-05T16:03:00Z')
        source = state['sources'][format(0, '064x')]
        self.assertEqual(source['coverage']['events']['baseline'], 1)
        self.assertEqual(source['coverage']['observed']['total_tokens'], 0,
                         'return after retirement is a new baseline, not a guessed 900-token delta')
        state = self.run_observations(state, self.rows(0, 1, total=1010), '2026-11-05T16:03:01Z')
        public = self.producer.project_history(state)
        self.assertNotIn('retired', public, 'private lifetime summary is not a forged public source')
        self.assertEqual(public['coverage']['observed']['total_tokens'], 10)
        result = run_node('out.state=H.parseHistory(payload.text).state;', text=json.dumps(public))
        self.assertEqual(result['state'], 'observed')


class Admission(Base):
    NOW = at('2026-10-31T12:00:00')

    def refused(self, projection):
        with self.assertRaises((ValueError, TypeError)):
            self.ledger.merge_cloud(projection, self.NOW)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM cloud_daily').fetchone()[0], 0)

    def test_local_routes_cannot_be_summed_with_router_samples(self):
        for provider in ('custom', 'llama.cpp', 'custom:local-(127.0.0.1:8080)', 'auto', '', 'local', None, ['openai']):
            with self.subTest(provider=provider):
                self.refused(history({'2026-10-30': {'input_tokens': 5}}, provider=provider))

    def test_malformed_projections_are_never_merged(self):
        valid = history({'2026-10-30': {'input_tokens': 5}})
        def mutated(change):
            value = copy.deepcopy(valid)
            change(value)
            return value
        cell = lambda v: v['buckets']['day'][0]['sources'][0]
        for label, change in {
                'contract': lambda v: v.update(contract='other/1'),
                'schema': lambda v: v.update(schemaVersion=2),
                'writable': lambda v: v.update(readOnly=False),
                'zone': lambda v: v.update(timezone='localtime'),
                'unavailable zone': lambda v: v.update(timezone='Mars/Base'),
                'directory zone': lambda v: v.update(timezone='America'),
                'time': lambda v: v.update(observedAt='yesterday'),
                'buckets': lambda v: v['buckets'].update(day={}),
                'too many': lambda v: v['buckets'].update(day=v['buckets']['day'] * 32),
                'date': lambda v: v['buckets']['day'][0].update(key='2026-13-01'),
                'unknown source': lambda v: cell(v).update(id='d' * 64),
                'bad source id': lambda v: v['sources'][0].update(id='SECRET'),
                'duplicate source': lambda v: v['sources'].append(dict(v['sources'][0])),
                'duplicate cell': lambda v: v['buckets']['day'][0]['sources'].append(dict(cell(v))),
                'missing counter': lambda v: cell(v)['counts'].pop('output_tokens'),
                'float': lambda v: cell(v)['counts'].update(input_tokens=5.0, prompt_tokens=5.0, total_tokens=5.0),
                'bool': lambda v: cell(v)['counts'].update(api_call_count=True),
                'negative': lambda v: cell(v)['counts'].update(cache_write_tokens=-1),
                'huge': lambda v: cell(v)['counts'].update(input_tokens=2**53, prompt_tokens=2**53, total_tokens=2**53),
                'prompt sum': lambda v: cell(v)['counts'].update(prompt_tokens=4),
                'total sum': lambda v: cell(v)['counts'].update(total_tokens=4),
                'reasoning subset': lambda v: cell(v)['counts'].update(reasoning_tokens=1),
                'coverage': lambda v: cell(v).update(coveredSeconds=-1)}.items():
            with self.subTest(label):
                self.refused(mutated(change))

    def test_unusable_files_are_refused(self):
        good = self.root/'history.json'
        good.write_text(json.dumps(history({'2026-10-30': {}})))
        self.assertEqual(collector.read_history(good)['schemaVersion'], 1)
        for label, text in (('duplicate', '{"a":1,"a":2}'), ('nan', '{"a":NaN}'), ('infinity', '{"a":Infinity}')):
            with self.subTest(label):
                path = self.root/(label + '.json')
                path.write_text(text)
                with self.assertRaises(ValueError):
                    collector.read_history(path)
        link = self.root/'link.json'
        link.symlink_to(good)
        with self.assertRaises(OSError):
            collector.read_history(link)
        big = self.root/'big.json'
        big.write_text(' ' * (collector.HISTORY_LIMIT + 1))
        with self.assertRaises(ValueError):
            collector.read_history(big)

    def test_future_observation_is_withheld(self):
        feed = self.ledger.merge_cloud(history({'2026-10-30': {'input_tokens': 5}}, observed='2026-11-01T16:00:00Z'), self.NOW)
        self.assertEqual(feed, {'state': 'future'})
        self.assertEqual(self.days(self.NOW), {})

    def test_future_day_bucket_is_withheld(self):
        feed = self.ledger.merge_cloud(history({'2026-11-01': {'input_tokens': 5}}), self.NOW)
        self.assertEqual(feed, {'state': 'future'})
        self.assertEqual(self.days(self.NOW), {})

    def test_small_clock_skew_is_tolerated(self):
        moment = at('2026-10-31T12:00:00')
        observed = collector.utc_stamp(moment + collector.CLOCK_SLACK - 5)
        self.assertEqual(self.ledger.merge_cloud(history({'2026-10-31': {}}, observed=observed), moment)['state'], 'admitted')


class Calendars(Base):
    def tearDown(self):
        os.environ['TZ'] = 'America/New_York'
        time.tzset()

    def test_a_different_host_calendar_withholds_cloud_and_keeps_local(self):
        now = at('2026-10-31T12:00:00')
        self.ledger.merge_cloud(history({'2026-10-30': {'input_tokens': 5}}), now)
        self.local_day('2026-10-30', inp=11)
        os.environ['TZ'] = 'UTC'
        time.tzset()
        feed = self.ledger.merge_cloud(history({'2026-10-31': {'input_tokens': 9}}), now)
        self.assertEqual(feed, {'state': 'misaligned', 'timezone': 'America/New_York'})
        record = self.ledger.rollup(now, feed)
        self.assertEqual(record['days']['2026-10-30']['total'], 11, 'retained Cloud rows do not share a date with another calendar')
        self.assertEqual(record['timezone'], 'UTC')
        self.assertEqual([s['kind'] for s in record['sources']], ['local'])

    def test_zones_with_other_clock_change_dates_do_not_agree(self):
        now = at('2026-10-31T12:00:00')
        self.assertTrue(collector.zones_agree('America/New_York', now))
        self.assertTrue(collector.zones_agree('America/Toronto', now))
        self.assertFalse(collector.zones_agree('America/Havana', now))
        self.assertFalse(collector.zones_agree('UTC', now))

    def test_day_lengths_follow_clock_changes(self):
        self.assertEqual(self.ledger.rollup(at('2026-11-02T12:00:00'), {'state': 'missing'})['today'], '2026-11-02')
        start, end = collector.day_span('2026-11-01')
        self.assertEqual(end - start, 90000)
        start, end = collector.day_span('2026-03-08')
        self.assertEqual(end - start, 82800)

    def test_full_25_hour_day_is_not_partial(self):
        now = at('2026-11-02T12:00:00')
        start, end = collector.day_span('2026-11-01')
        self.local_day('2026-11-01', inp=1, first=start + 5, last=end - 5)
        self.ledger.merge_cloud(history({'2026-11-01': {}}, observed='2026-11-02T04:59:00Z', covered=89800), now)
        coverage = self.days(now)['2026-11-01']['coverage']
        self.assertEqual(coverage['local']['daySeconds'], 90000)
        self.assertFalse(coverage['partial'])

    def test_short_coverage_of_a_25_hour_day_is_partial(self):
        now = at('2026-11-02T12:00:00')
        self.ledger.merge_cloud(history({'2026-11-01': {}}, observed='2026-11-02T04:59:00Z', covered=86000), now)
        self.assertTrue(self.days(now)['2026-11-01']['coverage']['cloud']['partial'])

    def test_valid_until_stops_at_the_ttl(self):
        now = at('2026-10-31T12:00:00')
        self.assertEqual(collector.rollup_valid_until(now), now + collector.ROLLUP_TTL)

    def test_valid_until_stops_before_midnight(self):
        now = at('2026-10-31T23:58:00')
        self.assertEqual(collector.rollup_valid_until(now), at('2026-10-31T23:59:30'))

    def test_valid_until_finds_a_backward_date_change(self):
        os.environ['TZ'] = 'America/St_Johns'
        time.tzset()
        now = dt.datetime(2009, 11, 1, 2, 30, 0, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(str(collector.local_date(now)), '2009-11-01')
        self.assertEqual(str(collector.local_date(now + 60)), '2009-10-31')
        self.assertEqual(collector.rollup_valid_until(now), now + 30)


class Coverage(Base):
    NOW = at('2026-10-31T12:00:00')

    def state(self, **times):
        self.local_day('2026-10-30', inp=1, **times)
        return self.days(self.NOW)['2026-10-30']['coverage']['local']

    def test_recording_spanning_the_day_is_complete(self):
        local = self.state()
        self.assertFalse(local['partial'])
        self.assertEqual(local['daySeconds'], 86400)

    def test_late_start_is_partial(self):
        self.assertTrue(self.state(first=at('2026-10-30T07:38:00'))['partial'])

    def test_early_stop_is_partial(self):
        self.assertTrue(self.state(last=at('2026-10-30T20:43:00'))['partial'])

    def test_today_is_judged_up_to_now(self):
        start, _ = collector.day_span('2026-10-31')
        self.local_day('2026-10-31', inp=1, first=start + 5, last=self.NOW - 30)
        self.assertFalse(self.days(self.NOW)['2026-10-31']['coverage']['local']['partial'])

    def test_today_with_a_stopped_collector_is_partial(self):
        start, _ = collector.day_span('2026-10-31')
        self.local_day('2026-10-31', inp=1, first=start + 5, last=self.NOW - 3600)
        self.assertTrue(self.days(self.NOW)['2026-10-31']['coverage']['local']['partial'])

    def test_outage_inside_the_day_is_partial(self):
        start, end = collector.day_span('2026-10-30')
        self.ledger.heartbeat(start + 2)
        self.ledger.heartbeat(start + 3600)
        self.ledger.heartbeat(end - 10)
        local = self.days(self.NOW)['2026-10-30']['coverage']['local']
        self.assertTrue(local['partial'], 'a mid-day outage must not read as a complete day')
        self.assertGreater(local['gapSeconds'], 80000)
        self.assertLess(local['coveredSeconds'], 4000)

    def test_short_restart_is_not_an_outage(self):
        start, end = collector.day_span('2026-10-30')
        moment = start + 2
        while moment < end:
            self.ledger.heartbeat(moment)
            moment += 150
        self.ledger.heartbeat(end - 2)
        local = self.days(self.NOW)['2026-10-30']['coverage']['local']
        self.assertEqual(local['gapSeconds'], 0)
        self.assertFalse(local['partial'])

    def test_heartbeat_writes_are_throttled(self):
        statements = []
        self.ledger.db.set_trace_callback(lambda sql: statements.append(sql) if 'uptime' in sql else None)
        moment = at('2026-10-30T12:00:00')
        for step in range(0, 55, 5):
            self.ledger.heartbeat(moment + step)
        writes = [sql for sql in statements if sql.startswith(('INSERT', 'UPDATE'))]
        self.ledger.db.set_trace_callback(None)
        self.assertEqual(len(writes), 1)

    def test_day_change_writes_at_once(self):
        self.ledger.heartbeat(at('2026-10-30T23:59:58'))
        self.ledger.heartbeat(at('2026-10-31T00:00:03'))
        self.assertEqual(self.ledger.db.execute('SELECT first FROM uptime WHERE day=?', ('2026-10-31',)).fetchone()[0], at('2026-10-31T00:00:03'))

    def test_clock_stepping_back_invents_no_gap(self):
        moment = at('2026-10-30T12:00:00')
        self.ledger.heartbeat(moment)
        self.ledger.heartbeat(moment - 500)
        row = self.ledger.db.execute('SELECT first,last,gap FROM uptime').fetchone()
        self.assertEqual(row, (moment - 500, moment, 0))


class Publisher(Base):
    def publisher(self, history_path=None, collecting=True):
        self.output = self.root/'unified_daily.json'
        return collector.RollupPublisher(self.ledger, history_path or self.root/'absent.json', self.output, collecting)

    def published(self):
        return json.loads(self.output.read_text())

    def test_cadence_matches_the_cloud_feed(self):
        moment = at('2026-10-31T12:00:00')
        publisher = self.publisher()
        publisher.tick(moment)
        first = self.published()['observedAt']
        self.local_day('2026-10-31', inp=99)
        publisher.tick(moment + 5)
        self.assertEqual(self.published()['observedAt'], first, 'sampled tokens alone do not rebuild the chart')
        publisher.tick(moment + 61)
        self.assertNotEqual(self.published()['observedAt'], first)
        self.assertEqual(self.published()['days']['2026-10-31']['total'], 99)

    def test_date_change_publishes_at_once(self):
        publisher = self.publisher()
        publisher.tick(at('2026-10-31T23:59:58'))
        publisher.tick(at('2026-11-01T00:00:03'))
        self.assertEqual(self.published()['today'], '2026-11-01')

    def test_expiry_is_published(self):
        moment = at('2026-10-31T12:00:00')
        self.publisher().tick(moment)
        record = self.published()
        self.assertEqual(record['observedAt'], collector.utc_stamp(moment))
        self.assertEqual(record['validUntil'], collector.utc_stamp(moment + collector.ROLLUP_TTL))

    def test_clock_stepping_back_republishes(self):
        moment = at('2026-10-31T12:00:00')
        publisher = self.publisher()
        publisher.tick(moment)
        publisher.tick(moment - 600)
        self.assertEqual(self.published()['observedAt'], collector.utc_stamp(moment - 600))

    def test_missing_cloud_feed_publishes_local(self):
        self.local_day('2026-10-30', inp=5)
        self.publisher().tick(at('2026-10-31T12:00:00'))
        record = self.published()
        self.assertEqual(record['cloudFeed'], {'state': 'missing'})
        self.assertEqual(record['days']['2026-10-30']['total'], 5)

    def test_bad_projection_keeps_local_and_retained_cloud(self):
        path = self.root/'history.json'
        path.write_text(json.dumps(history({'2026-10-30': {'input_tokens': 7}})))
        self.local_day('2026-10-30', inp=5)
        moment = at('2026-10-31T12:00:00')
        publisher = self.publisher(path)
        publisher.tick(moment)
        self.assertEqual(self.published()['days']['2026-10-30']['total'], 12)
        path.write_text('{"contract":')
        publisher.tick(moment + 61)
        record = self.published()
        self.assertEqual(record['cloudFeed'], {'state': 'unusable'})
        self.assertEqual(record['days']['2026-10-30']['total'], 12)

    def test_unchanged_projection_is_not_reread(self):
        path = self.root/'history.json'
        path.write_text(json.dumps(history({'2026-10-30': {}})))
        moment = at('2026-10-31T12:00:00')
        publisher = self.publisher(path)
        publisher.tick(moment)
        reads = []
        original = collector.read_history
        collector.read_history = lambda *a: reads.append(a) or original(*a)
        try:
            publisher.tick(moment + 5)
            self.assertEqual(reads, [])
            path.write_text(json.dumps(history({'2026-10-30': {'output_tokens': 1}})))
            publisher.tick(moment + 10)
            self.assertEqual(len(reads), 1)
        finally:
            collector.read_history = original

    def test_failure_never_raises_and_is_reported_once_without_data(self):
        self.local_day('2026-10-30', model='secret-model', inp=2**53)
        publisher = self.publisher()
        moment = at('2026-10-31T12:00:00')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            publisher.tick(moment)
            publisher.tick(moment + 5)
        self.assertEqual(stderr.getvalue(), 'Unified roll-up unavailable (ValueError)\n')
        self.assertFalse(self.output.exists(), 'an unsafe sum is not published')
        with self.ledger.db:
            self.ledger.db.execute('DELETE FROM daily')
        publisher.tick(moment + 10)
        self.assertTrue(self.output.exists(), 'publication resumes by itself')

    def test_unwritable_output_never_raises(self):
        publisher = collector.RollupPublisher(self.ledger, self.root/'absent.json', self.root/'no-such-dir/out.json', True)
        with contextlib.redirect_stderr(io.StringIO()):
            publisher.tick(at('2026-10-31T12:00:00'))

    def test_render_only_invents_no_uptime(self):
        self.local_day('2026-10-30', inp=5)
        self.publisher(collecting=False).tick(at('2026-10-31T12:00:00'))
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM uptime').fetchone()[0], 0)

    def test_local_record_ignores_the_new_tables(self):
        moment = at('2026-10-31T12:00:00')
        self.local_day('2026-10-30', inp=5)
        before = self.ledger.record(moment, 'status')
        self.ledger.merge_cloud(history({'2026-10-30': {'input_tokens': 100}}), moment)
        self.ledger.heartbeat(moment)
        self.assertEqual(self.ledger.record(moment, 'status'), before)

    def test_sampling_still_reaches_the_rollup(self):
        moment = at('2026-10-30T10:00:00')
        self.assertEqual(self.ledger.observe('heretic', 'worker', (100, 10), moment), (0, 0))
        self.assertEqual(self.ledger.observe('heretic', 'worker', (11572153, 132570), moment + 5), (11572053, 132560))
        self.assertEqual(self.days(moment + 6)['2026-10-30']['total'], 11704613)


class CloudFeedFreshness(Base):
    NOW = at('2026-10-31T12:00:00')

    def publisher(self):
        self.path = self.root/'history.json'
        self.path.write_text(json.dumps(history({'2026-10-30': {'input_tokens': 7}},
                                                observed=collector.utc_stamp(self.NOW))))
        self.output = self.root/'unified_daily.json'
        self.local_day('2026-10-30', inp=5)
        return collector.RollupPublisher(self.ledger, self.path, self.output, False)

    def record(self):
        return json.loads(self.output.read_text())

    def test_unchanged_feed_expires_after_ten_minutes_without_another_read(self):
        publisher = self.publisher()
        with mock.patch.object(collector, 'read_history', wraps=collector.read_history) as reader:
            publisher.tick(self.NOW)
            publisher.tick(self.NOW + 600)
            self.assertEqual(self.record()['cloudFeed']['state'], 'admitted')
            publisher.tick(self.NOW + 601)
            self.assertEqual(self.record()['cloudFeed']['state'], 'stale')
            source_reads = [call for call in reader.call_args_list if Path(call.args[0]) == self.path]
            self.assertEqual(len(source_reads), 1, 'age checks must not reread an unchanged projection')
        self.assertEqual(self.record()['days']['2026-10-30']['total'], 12,
                         'staleness changes liveness, never retained dated counts')

    def test_stale_publication_is_accepted_by_the_real_consumer(self):
        publisher = self.publisher()
        publisher.tick(self.NOW)
        publisher.tick(self.NOW + 660)
        record = self.record()
        self.assertEqual(record['cloudFeed']['state'], 'stale')
        parsed = run_node('''
            const report = H.parseRollup(payload.text);
            out.state = report.state;
            out.help = report.help;
            out.view = H.selectHistory(report, {});
        ''', text=json.dumps(record))
        self.assertEqual(parsed['state'], 'observed')
        self.assertIn('feed stale', parsed['help'])
        self.assertEqual({row['key']: row['value'] for row in parsed['view']['bars']}['2026-10-30'], 12)

    def test_fresh_projection_recovers_without_recounting_retained_tokens(self):
        publisher = self.publisher()
        publisher.tick(self.NOW)
        publisher.tick(self.NOW + 660)
        self.assertEqual(self.record()['cloudFeed']['state'], 'stale')
        self.path.write_text(json.dumps(history({'2026-10-30': {'input_tokens': 8}},
                                                observed=collector.utc_stamp(self.NOW + 661))))
        publisher.tick(self.NOW + 661)
        record = self.record()
        self.assertEqual(record['cloudFeed']['state'], 'admitted')
        self.assertEqual(record['cloudFeed']['observedAt'], collector.utc_stamp(self.NOW + 661))
        self.assertEqual(record['days']['2026-10-30']['total'], 13)


class FeedDiagnostics(Base):
    def test_main_reports_stable_reason_codes_without_exception_payloads(self):
        feed = load('feed_diagnostics', SOURCES/'canonical_feed.py')
        argv = ['--source-db', str(self.root/'source.db'), '--profile', 'fixture', '--timezone', 'UTC',
                '--canonical-output', str(self.root/'canonical.json'), '--history-state', str(self.root/'state.json'),
                '--history-output', str(self.root/'history.json'), '--output', str(self.root/'usage.json')]
        cases = (
            (ValueError('retained history source capacity reached'), 'E_SOURCE_CAP'),
            (ValueError('too many canonical history sources'), 'E_SOURCE_CAP'),
            (ValueError('canonical source byte bound reached'), 'E_DB_SIZE'),
            (ValueError('canonical sidecar byte bound reached'), 'E_SIDECAR_SIZE'),
            (ValueError('canonical rows or bytes exceeded bound'), 'E_INPUT_LIMIT'),
            (ValueError('canonical ledger shape drifted'), 'E_SCHEMA'),
            (ValueError('backward or conflicting repeated observation'), 'E_CLOCK_BACKWARD'),
            (BlockingIOError('PRIVATE-ERROR-PAYLOAD'), 'E_LOCK_BUSY'),
            (sqlite3.OperationalError('PRIVATE-ERROR-PAYLOAD'), 'E_SQLITE'),
            (OSError('PRIVATE-ERROR-PAYLOAD'), 'E_IO'),
            (ValueError('PRIVATE-ERROR-PAYLOAD'), 'E_VALIDATION'),
        )
        for error, expected in cases:
            with self.subTest(code=expected, error=type(error).__name__):
                stderr = io.StringIO()
                with mock.patch.object(feed, 'run', side_effect=error), \
                     mock.patch.object(feed, 'cli_limits', return_value=contextlib.nullcontext()), \
                     contextlib.redirect_stderr(stderr):
                    self.assertEqual(feed.main(argv), 2)
                self.assertEqual(stderr.getvalue(), 'Canonical accounting feed refused [' + expected
                                 + ']; last-good publications retained.\n')
                self.assertNotIn('PRIVATE-ERROR-PAYLOAD', stderr.getvalue())
                self.assertNotIn(str(self.root), stderr.getvalue())


class LocalClockRollbackSafety(Base):
    NOW = at('2026-10-30T12:00:00')

    def parsed(self, now):
        record = {**self.ledger.rollup(now, {'state': 'missing'}),
                  'observedAt': collector.utc_stamp(now),
                  'validUntil': collector.utc_stamp(now + 300)}
        result = run_node('out.state = H.parseRollup(payload.text).state;', text=json.dumps(record))
        self.assertEqual(result['state'], 'observed')
        self.assertEqual(record['days']['2026-10-30']['total'], 7)
        return record['days']['2026-10-30']['coverage']['local']

    def test_large_backward_step_keeps_the_coverage_envelope_ordered(self):
        self.ledger.observe('model', 'worker', (100, 10), self.NOW)
        self.ledger.observe('model', 'worker', (107, 10), self.NOW + 5)
        self.ledger.observe('model', 'worker', (107, 10), self.NOW - 500)
        first, last = self.ledger.db.execute('SELECT first,last FROM coverage').fetchone()
        self.assertEqual((first, last), (self.NOW - 500, self.NOW + 5))
        self.parsed(self.NOW + 10)

    def test_legacy_inverted_coverage_does_not_hide_other_retained_days(self):
        self.local_day('2026-10-30', inp=7, first=self.NOW, last=self.NOW - 500)
        cover = self.parsed(self.NOW + 10)
        self.assertLessEqual(cover['first'], cover['last'])
        self.assertTrue(cover['partial'])
        self.assertEqual(self.ledger.db.execute('SELECT first,last FROM coverage').fetchone(),
                         (self.NOW, self.NOW - 500), 'rendering must not rewrite retained source rows')


class RollupPublicationBudget(Base):
    def test_unchanged_ticks_do_not_rebuild_the_full_dated_rollup(self):
        publisher = collector.RollupPublisher(self.ledger, self.root/'absent.json', self.root/'out.json', True)
        now = at('2026-10-31T12:00:00')
        with mock.patch.object(self.ledger, 'rollup', wraps=self.ledger.rollup) as build:
            publisher.tick(now)
            publisher.tick(now + 5)
            publisher.tick(now + 10)
            self.assertEqual(build.call_count, 1)
            publisher.tick(now + 60)
            self.assertEqual(build.call_count, 2)
        first, last = self.ledger.db.execute('SELECT first,last FROM uptime').fetchone()
        self.assertEqual((first, last), (now, now + 60), 'publication throttling must not throttle sampling coverage')

    def test_oversized_serialized_output_refuses_before_temp_creation(self):
        output = self.root/'rollup.json'
        output.write_bytes(b'{"lastGood":true}')
        before = set(self.root.iterdir())
        # Every temporary-creation primitive (mkstemp included) goes through
        # os.open; a refused publication must not even open its directory.
        with mock.patch.object(collector.os, 'open', wraps=os.open) as create:
            with self.assertRaisesRegex(ValueError, 'publication exceeded byte bound'):
                collector.publish(output, {'payload': 'x' * (4 * 1024 * 1024)})
            self.assertEqual(create.call_count, 0)
        self.assertEqual(output.read_bytes(), b'{"lastGood":true}')
        self.assertEqual(set(self.root.iterdir()), before)

    def test_exact_transport_byte_limit_is_accepted(self):
        output = self.root/'at-limit.json'
        overhead = len(json.dumps({'payload': ''}, allow_nan=False).encode())
        record = {'payload': 'x' * (4 * 1024 * 1024 - overhead)}
        collector.publish(output, record)
        self.assertEqual(output.stat().st_size, 4 * 1024 * 1024)
        self.assertEqual(json.loads(output.read_text()), record)


# S7 crash model: SIGKILL at a publication step skips every finally/atexit
# cleanup, exactly like a host crash after that step's earlier effects.
CRASH_PUBLISH = '''
import importlib.util, os, signal, sys
spec = importlib.util.spec_from_file_location('crash_collector', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def crash(*args, **kwargs):
    os.kill(os.getpid(), signal.SIGKILL)
if sys.argv[3] == 'rename':
    os.replace = os.rename = crash
else:
    os.fsync = crash
module.publish(sys.argv[2], {'attempt': sys.argv[4]})
raise SystemExit('publication completed without reaching the crash point')
'''


class PublicationAliasPreflight(unittest.TestCase):
    """The real CLI must refuse aliases before reading inputs or acquiring state."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def invoke(self, state, output, rollup, history_input, *, cwd=None):
        events = self.root/'forbidden-events.jsonl'
        # The observer writes before raising, so a broad production catch cannot
        # disguise a forbidden input read or SQLite acquisition as safe refusal.
        script = r'''
import json, os, runpy, sys
source, events, history_input, *arguments = sys.argv[1:]
input_path = os.path.realpath(history_input)
def audit(event, args):
    forbidden = event == 'sqlite3.connect'
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        forbidden = os.path.realpath(os.fsdecode(args[0])) == input_path
    if forbidden:
        with open(events, 'a') as handle:
            handle.write(json.dumps({'event': event}) + '\n')
        raise RuntimeError('fixture observed forbidden acquisition')
sys.addaudithook(audit)
sys.argv = [source, *arguments]
runpy.run_path(source, run_name='__main__')
'''
        command = [sys.executable, '-I', '-B', '-c', script,
                   str(SOURCES/'collector.py'), str(events), str(history_input),
                   '--render-only', '--state-dir', str(state), '--output', str(output),
                   '--rollup-output', str(rollup), '--hermes-history', str(history_input)]
        result = subprocess.run(command, cwd=cwd or self.root, capture_output=True,
                                text=True, timeout=20)
        attempted = events.read_text().splitlines() if events.exists() else []
        return result, attempted

    def test_history_aliases_are_refused_before_any_acquisition(self):
        for spelling in ('absolute', 'relative', 'hardlink'):
            with self.subTest(spelling=spelling):
                directory = self.root/spelling
                directory.mkdir()
                output, rollup = directory/'local.json', directory/'unified.json'
                reserved = collector.publication_temporary(output)
                reserved.write_text('{"private_input":true}')
                reserved.chmod(0o600)
                history_input = reserved
                if spelling == 'relative':
                    history_input = Path(spelling)/reserved.name
                elif spelling == 'hardlink':
                    history_input = directory/'history.json'
                    os.link(reserved, history_input)
                state = directory/'new-state'
                before = (reserved.read_bytes(), reserved.stat().st_ino,
                          sorted(p.name for p in directory.iterdir()))
                result, attempted = self.invoke(state, output, rollup, history_input)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('publication path aliases protected storage', result.stderr)
                self.assertEqual(attempted, [], 'refuse before history reads or SQLite acquisition')
                self.assertFalse(state.exists(), 'refuse before creating state directories')
                self.assertEqual(before, (reserved.read_bytes(), reserved.stat().st_ino,
                                         sorted(p.name for p in directory.iterdir())))

    def test_ledger_and_lock_aliases_are_refused_before_acquisition(self):
        for name in ('tokens.sqlite3', 'tokens.sqlite3-wal', 'tokens.sqlite3-shm',
                     'tokens.sqlite3-journal', 'collector.lock'):
            with self.subTest(name=name):
                directory = self.root/name
                directory.mkdir()
                state = directory/'state'
                state.mkdir()
                protected = state/name
                protected.write_text('preserved private storage')
                output = directory/'output.json'
                os.link(protected, output)
                before = (protected.read_bytes(), protected.stat().st_ino)
                result, attempted = self.invoke(state, output, directory/'rollup.json',
                                                directory/'unread-history.json')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('publication path aliases protected storage', result.stderr)
                self.assertEqual(attempted, [])
                self.assertEqual(before, (protected.read_bytes(), protected.stat().st_ino))
                self.assertFalse((state/'tokens.sqlite3').exists() and name != 'tokens.sqlite3')

    def test_two_outputs_cannot_share_a_destination_or_temporary(self):
        for shared_temporary in (False, True):
            with self.subTest(shared_temporary=shared_temporary):
                directory = self.root/str(shared_temporary)
                directory.mkdir()
                output = directory/'local.json'
                rollup = collector.publication_temporary(output) if shared_temporary else output
                result, attempted = self.invoke(directory/'new-state', output, rollup,
                                                directory/'unread-history.json')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('publication path aliases protected storage', result.stderr)
                self.assertEqual(attempted, [])
                self.assertEqual(list(directory.iterdir()), [])

    def test_future_ledger_names_are_reserved_without_creating_state(self):
        # Characterization of the pathname branch, alongside the hardlink RED/GREEN.
        for name in ('tokens.sqlite3', 'tokens.sqlite3-wal', 'tokens.sqlite3-shm',
                     'tokens.sqlite3-journal', 'collector.lock'):
            with self.subTest(name=name):
                directory = self.root/name
                directory.mkdir()
                state = directory/'new-state'
                result, attempted = self.invoke(state, state/name, directory/'rollup.json',
                                                directory/'unread-history.json')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('publication path aliases protected storage', result.stderr)
                self.assertEqual(attempted, [])
                self.assertEqual(list(directory.iterdir()), [])

    def test_nonaliasing_render_only_publication_still_runs(self):
        state = self.root/'state'
        output, rollup = self.root/'local.json', self.root/'unified.json'
        command = [sys.executable, '-I', '-B', str(SOURCES/'collector.py'),
                   '--render-only', '--state-dir', str(state), '--output', str(output),
                   '--rollup-output', str(rollup), '--hermes-history', str(self.root/'missing.json')]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text())['id'], 'local')
        self.assertIs(json.loads(output.read_text())['readOnly'], True)
        self.assertEqual(json.loads(rollup.read_text())['contract'], collector.ROLLUP_CONTRACT)
        self.assertTrue((state/'tokens.sqlite3').is_file())
        self.assertFalse(collector.publication_temporary(output).exists())
        self.assertFalse(collector.publication_temporary(rollup).exists())


class CrashSafePublication(Base):
    """S7: crashes cannot accumulate temporaries, and a completed rename is durable."""

    def crash(self, target, point, attempt):
        result = subprocess.run([sys.executable, '-I', '-B', '-c', CRASH_PUBLISH, str(SOURCES/'collector.py'),
                                 str(target), point, str(attempt)], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr[-2000:])

    def leftovers(self, target):
        return sorted(path.name for path in target.parent.iterdir() if path.name != target.name)

    def test_repeated_crashes_leave_at_most_one_deterministic_temporary(self):
        directory = self.root/'agents'
        directory.mkdir()
        target = directory/'local.json'
        collector.publish(target, {'attempt': 'good'})
        names = set()
        for attempt, point in enumerate(('rename', 'fsync', 'rename', 'rename')):
            self.crash(target, point, attempt)
            left = self.leftovers(target)
            self.assertLessEqual(len(left), 1, f'crash {attempt} at {point} accumulated temporaries: {left}')
            names.update(left)
            self.assertEqual(json.loads(target.read_text()), {'attempt': 'good'},
                             'a crash before the rename never exposes a partial or newer target')
        self.assertEqual(len(names), 1, f'one temporary per target, not one per crash: {sorted(names)}')
        collector.publish(target, {'attempt': 'after'})
        self.assertEqual(self.leftovers(target), [], 'the next publication replaces the crash leftover')
        self.assertEqual(json.loads(target.read_text()), {'attempt': 'after'})
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_rename_is_made_durable_by_a_parent_directory_fsync(self):
        target = self.root/'unified_daily.json'
        target.write_bytes(b'{}')
        parent = target.parent.stat()
        events = []
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(fd):
            info = os.fstat(fd)
            events.append(('fsync', stat.S_ISDIR(info.st_mode), (info.st_dev, info.st_ino)))
            return real_fsync(fd)

        def replace(*args, **kwargs):
            events.append(('replace',))
            return real_replace(*args, **kwargs)

        with mock.patch.object(collector.os, 'fsync', fsync), mock.patch.object(collector.os, 'replace', replace):
            collector.publish(target, {'durable': True})
        final = target.stat()
        self.assertEqual(json.loads(target.read_text()), {'durable': True})
        self.assertEqual(events.count(('replace',)), 1)
        renamed = events.index(('replace',))
        self.assertIn(('fsync', False, (final.st_dev, final.st_ino)), events[:renamed],
                      'the renamed inode itself is fsynced before the rename')
        self.assertIn(('fsync', True, (parent.st_dev, parent.st_ino)), events[renamed + 1:],
                      'the parent directory entry is fsynced after the rename')

    def test_one_temporary_per_target_name_in_its_directory(self):
        local, rollup = self.root/'local.json', self.root/'unified_daily.json'
        temporary = collector.publication_temporary(local)
        self.assertEqual(temporary.parent, local.parent)
        self.assertTrue(temporary.name.startswith('.local-'))
        self.assertNotEqual(temporary, collector.publication_temporary(rollup))
        self.assertEqual(collector.publication_temporary(self.root/'x'/'..'/'local.json').name, temporary.name)

    def test_only_an_owned_private_crash_leftover_is_replaced(self):
        (self.root/'agents').mkdir()
        target = self.root/'agents'/'local.json'
        target.write_bytes(b'{"lastGood":true}')
        temporary = collector.publication_temporary(target)
        victim = self.root/'victim'
        victim.write_bytes(b'victim')

        def shared():
            temporary.write_bytes(b'{}')
            temporary.chmod(0o644)

        obstacles = (('symlink', lambda: temporary.symlink_to(victim)), ('hardlink', lambda: os.link(victim, temporary)),
                     ('directory', temporary.mkdir), ('shared', shared))
        for name, make in obstacles:
            with self.subTest(name):
                make()
                with self.assertRaises(collector.PublicationTemporaryError):
                    collector.publish(target, {'replaced': True})
                self.assertEqual(target.read_bytes(), b'{"lastGood":true}')
                self.assertEqual(victim.read_bytes(), b'victim')
                self.assertTrue(os.path.lexists(temporary), 'an unsafe temporary is never followed or removed')
                if temporary.is_dir() and not temporary.is_symlink():
                    temporary.rmdir()
                else:
                    temporary.unlink()
        self.assertEqual(victim.stat().st_nlink, 1)
        # A genuine crash leftover: owned, private, singly linked and bounded.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, b'{"partial"')
        os.close(fd)
        collector.publish(target, {'replaced': True})
        self.assertEqual(json.loads(target.read_text()), {'replaced': True})
        self.assertFalse(os.path.lexists(temporary))
        self.assertEqual(self.leftovers(target), [])
        self.assertEqual(victim.read_bytes(), b'victim')


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {'HOME': str(self.root/'home'), 'XDG_STATE_HOME': str(self.root/'xdg'), 'PATH': '/usr/bin:/bin',
                    'TZ': 'America/New_York', 'PYTHONDONTWRITEBYTECODE': '1'}

    def run_collector(self, *args):
        return subprocess.run([sys.executable, '-B', str(SOURCES/'collector.py'), *args], env=self.env,
                              capture_output=True, text=True, timeout=30)

    def old_ledger(self, state):
        """A ledger exactly as the previous collector left it: no roll-up tables."""
        state.mkdir(parents=True)
        db = sqlite3.connect(state/'tokens.sqlite3')
        db.executescript('''
            CREATE TABLE snapshots (model TEXT PRIMARY KEY, epoch TEXT NOT NULL, input INTEGER NOT NULL, output INTEGER NOT NULL, sampled REAL NOT NULL);
            CREATE TABLE daily (day TEXT NOT NULL, model TEXT NOT NULL, input INTEGER NOT NULL, output INTEGER NOT NULL, PRIMARY KEY(day, model));
            CREATE TABLE coverage (day TEXT PRIMARY KEY, first REAL NOT NULL, last REAL NOT NULL);''')
        today = dt.date.today()
        day = str(today - dt.timedelta(days=2))
        start = dt.datetime.combine(dt.date.fromisoformat(day), dt.time()).timestamp()
        db.execute('INSERT INTO daily VALUES (?,?,?,?)', (day, 'heretic', 11572053, 132560))
        db.execute('INSERT INTO coverage VALUES (?,?,?)', (day, start + 27000, start + 86000))
        db.commit()
        db.close()
        return day

    def test_render_only_upgrades_an_old_ledger_and_publishes(self):
        state = self.root/'state'
        day = self.old_ledger(state)
        snapshot = self.root/'snapshot.json'
        snapshot.write_text(json.dumps(history({day: {'input_tokens': 5}}, observed=collector.utc_stamp(time.time() - 3600))))
        output = self.root/'out/unified_daily.json'
        result = self.run_collector('--render-only', '--state-dir', str(state), '--rollup-output', str(output), '--hermes-history', str(snapshot))
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        local = json.loads(result.stdout)
        self.assertEqual(local['id'], 'local')
        record = json.loads(output.read_text())
        self.assertEqual(record['cloudFeed']['state'], 'stale', 'an hour-old projection retains facts, not liveness')
        self.assertEqual(record['days'][day]['total'], 11704618)
        self.assertEqual(record['days'][day]['byModel']['heretic']['total_tokens'], 11704613)
        self.assertEqual(os.stat(output).st_mode & 0o077, 0, 'private like local.json')

    def test_publish_writes_the_path_the_panel_reads(self):
        state = self.root/'xdg/omarchy/local-router'
        day = self.old_ledger(state)
        accounting = self.root/'xdg/omarchy/hermes-token-accounting'
        accounting.mkdir(parents=True)
        (accounting/'history.json').write_text(json.dumps(history({day: {'input_tokens': 5}}, observed=collector.utc_stamp(time.time() - 3600))))
        result = self.run_collector('--render-only', '--publish')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertTrue((state/'agents/local.json').exists())
        self.assertEqual(json.loads((state/'agents/unified_daily.json').read_text())['days'][day]['total'], 11704618)
        self.assertIn('"/omarchy/local-router/agents/unified_daily.json"', stager.HISTORY_MAIN)

    def test_without_a_rollup_destination_nothing_extra_is_written(self):
        state = self.root/'state'
        self.old_ledger(state)
        result = self.run_collector('--render-only', '--state-dir', str(state))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(sorted(p.name for p in state.iterdir() if not p.name.startswith('tokens.sqlite3')), ['collector.lock'])

    def test_native_usage_directory_is_refused(self):
        target = self.root/'xdg/omarchy/agents/usage'
        target.mkdir(parents=True)
        result = self.run_collector('--render-only', '--state-dir', str(self.root/'state'), '--rollup-output', str(target/'unified_daily.json'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('refusing to publish', result.stderr)
        self.assertFalse((target/'unified_daily.json').exists())

    def test_new_cli_state_is_private_despite_permissive_caller_umask(self):
        state = self.root/'xdg/omarchy/local-router'
        files = [state/name for name in ('tokens.sqlite3', 'tokens.sqlite3-wal',
                                        'tokens.sqlite3-shm', 'collector.lock',
                                        'agents/local.json', 'agents/unified_daily.json')]
        command = [sys.executable, '-I', '-B', str(SOURCES/'collector.py'),
                   '--render-only', '--run', '--interval', '2', '--publish']
        child = subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True,
                                 start_new_session=True, umask=0o022)
        file_modes = None
        directory_modes = None
        try:
            deadline = time.monotonic() + 10
            while not all(path.is_file() for path in files):
                self.assertIsNone(child.poll(), 'render-only collector exited before publication')
                self.assertLess(time.monotonic(), deadline, 'private fixture publication deadline')
                time.sleep(0.02)
            # Inspect the real producer while SQLite still holds WAL/SHM open.
            file_modes = {str(path.relative_to(self.root)): path.stat().st_mode & 0o777
                          for path in files}
            directories = [self.root/'xdg', self.root/'xdg/omarchy', state, state/'agents']
            directory_modes = {str(path.relative_to(self.root)): path.stat().st_mode & 0o777
                               for path in directories}
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                stdout, stderr = child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                stdout, stderr = child.communicate(timeout=5)
        self.assertEqual((child.returncode, stderr), (0, ''))
        self.assertEqual(stdout, '')
        self.assertIsNotNone(file_modes)
        self.assertIsNotNone(directory_modes)
        self.assertEqual(file_modes, dict.fromkeys(file_modes, 0o600))
        self.assertEqual(directory_modes, dict.fromkeys(directory_modes, 0o700))

    def test_cli_does_not_chmod_existing_state_database_or_lock(self):
        state = self.root/'existing-state'
        self.old_ledger(state)
        lock = state/'collector.lock'
        lock.write_text('fixture preimage\n')
        modes = {state: 0o755, state/'tokens.sqlite3': 0o644, lock: 0o640}
        for path, mode in modes.items():
            path.chmod(mode)  # Disposable fixture only, never live metadata.
        before = {path: (path.stat().st_ino, path.stat().st_mode & 0o777) for path in modes}
        result = self.run_collector('--render-only', '--state-dir', str(state))
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assertEqual(before, {path: (path.stat().st_ino, path.stat().st_mode & 0o777)
                                  for path in modes})

    def test_staged_service_declares_a_private_creation_umask(self):
        import configparser
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(SOURCES/'local-router-stats.service')
        self.assertEqual(unit.get('Service', 'UMask', fallback=None), '0077')


NODE_PRELUDE = r'''
const fs = require('node:fs'), vm = require('node:vm');
const payload = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const H = {}; vm.createContext(H);
vm.runInContext(fs.readFileSync(payload.consumer, 'utf8'), H);
const out = {};
'''


def run_node(script, **payload):
    with tempfile.NamedTemporaryFile('w', suffix='.json') as stream:
        json.dump({'consumer': str(SOURCES/'history_consumer.js'), **payload}, stream)
        stream.flush()
        result = subprocess.run([NODE, '-e', NODE_PRELUDE + script + '\nconsole.log(JSON.stringify(out));', stream.name],
                                capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class JsonStructureGuard(unittest.TestCase):
    """Characterization of the guards retained by the native-memory repair."""

    def accepted(self, texts):
        return run_node('''
            out.accepted=payload.texts.map(function(text) {
                try { H.uniqueJson(text); JSON.parse(text); return true; }
                catch (error) { return false; }
            });
        ''', texts=texts)['accepted']

    def test_quoted_structure_escaped_duplicates_and_grammar(self):
        cases = [
            (json.dumps({'quote"\\[]{},:': ['{["', {'a': '\\"},[]'}]}), True),
            ('{"x":{"a":0},"y":{"a":1}}', True),
            ('{"constructor":0,"toString":1}', True),
            ('{"a":0,"a":1}', False),
            (r'{"a":0,"\u0061":1}', False),
            ('{"__proto__":0,"__proto__":1}', False),
            ('{"a":1,,"b":2}', False),
            ('{"a":"unterminated}', False),
            (r'{"\u00ZZ":1}', False),
            ('[', False),
            ('{"a":1} {"b":2}', False),
            ('{"a":NaN}', False),
        ]
        self.assertEqual(self.accepted([text for text, _ in cases]),
                         [expected for _, expected in cases])

    def test_depth_boundary_does_not_count_quoted_braces(self):
        texts = ['['*depth + '0' + ']'*depth for depth in (1, 31, 32, 33)]
        texts.append(json.dumps({'quoted': '[{'*100 + '}]'*100}))
        self.assertEqual(self.accepted(texts), [True, True, True, False, True])


class ClaudeMergeCoverage(Base):
    NOW = at('2026-10-31T12:00:00')

    def complete_record(self, hermes=False):
        for day, tokens in (('2026-10-29', 100), ('2026-10-30', 200)):
            self.local_day(day, inp=tokens)
        if hermes:
            self.ledger.merge_cloud(history({'2026-10-29': {'input_tokens': 10},
                                             '2026-10-30': {'input_tokens': 20}},
                                            covered=86390), self.NOW)
        return {**self.ledger.rollup(self.NOW, {'state': 'admitted' if hermes else 'missing'}),
                'observedAt': collector.utc_stamp(self.NOW),
                'validUntil': collector.utc_stamp(self.NOW + 300)}

    def parsed(self, record):
        return run_node('''
            const report = H.parseRollup(payload.text);
            out.state = report.state;
            out.view = H.selectHistory(report, {});
        ''', text=json.dumps(record))

    def published_provider_metrics(self, *, claude=None, canonical=None):
        self.local_day('2026-10-30', inp=200)
        feed = self.root/'canonical-history.json'
        if canonical is not None:
            feed.write_text(json.dumps(canonical))
        if claude is not None:
            path = Path(os.environ['XDG_STATE_HOME'])/'omarchy/agents/usage/claude.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(claude))
        publisher = collector.RollupPublisher(self.ledger, feed, self.root/'published-rollup.json', collecting=False)
        publisher.tick(self.NOW)
        self.assertIsNone(publisher.failure, 'the actual publisher must reach successful publication')
        record = json.loads(publisher.output.read_text())
        result = run_node('''
            const report = H.parseRollup(payload.text);
            out.state = report.state;
            out.metrics = {};
            if (report.data) {
                const source = report.data.sources.find(s => s.provider === 'anthropic');
                out.providerPresent = !!source;
                if (source) for (const metric of payload.metrics) {
                    const view = H.selectHistory(report, {sources:[source.id], metric:metric, mode:'multiple'});
                    const bar = view.bars.find(row => row.key === '2026-10-30');
                    out.metrics[metric] = bar ? {value:bar.value, known:bar.known,
                        seriesValue:bar.series[0].value, selectionError:view.selectionError} : null;
                }
            }
        ''', text=json.dumps(record), metrics=list(FIELDS)+['prompt_tokens', 'total_tokens'])
        self.assertEqual(result['state'], 'observed', 'published Local and optional history must be consumable')
        self.assertTrue(result.get('providerPresent'), 'the intended provider must reach the real consumer')
        self.assertEqual(record['days']['2026-10-30']['byModel']['heretic']['total_tokens'], 200)
        return result['metrics']

    def test_total_only_claude_keeps_unmeasured_breakdowns_unknown(self):
        metrics = self.published_provider_metrics(claude={
            'recentDaysUnit': 'tokens', 'recentDays': [{'date':'2026-10-30', 'messageCount':17}]})
        self.assertEqual(metrics['total_tokens'],
                         {'value':17, 'known':1, 'seriesValue':17, 'selectionError':False})
        for metric in (*FIELDS, 'prompt_tokens'):
            with self.subTest(metric=metric):
                self.assertEqual(metrics[metric],
                                 {'value':None, 'known':0, 'seriesValue':None, 'selectionError':False},
                                 'a total-only observation cannot establish a measured breakdown')

    def test_actual_measured_zero_breakdowns_remain_measured_zero(self):
        metrics = self.published_provider_metrics(canonical=history(
            {'2026-10-30': {}}, provider='anthropic'))
        for metric in (*FIELDS, 'prompt_tokens', 'total_tokens'):
            with self.subTest(metric=metric):
                self.assertEqual(metrics[metric],
                                 {'value':0, 'known':1, 'seriesValue':0, 'selectionError':False})

    def test_total_only_wire_keeps_strict_shape_and_provider_admission(self):
        base = collector.merge_claude_days(self.complete_record(), {'2026-10-30':17})
        self.assertEqual(self.parsed(base)['state'], 'observed')
        for metric in (*FIELDS, 'prompt_tokens'):
            with self.subTest(partially_known=metric):
                broken = copy.deepcopy(base)
                broken['days']['2026-10-30']['byProvider']['anthropic'][metric] = 0
                self.assertEqual(self.parsed(broken)['state'], 'malformed',
                                 'total-only admission must not allow mixed known/unknown breakdowns')
        ordinary = copy.deepcopy(base)
        for source in ordinary['sources']:
            if source['provider'] == 'anthropic':
                source.update(collector.rollup_source('cloud', 'openai'))
        for day in ordinary['days'].values():
            if 'anthropic' in day['byProvider']:
                day['byProvider']['openai'] = counts(input_tokens=17)
                del day['byProvider']['anthropic']
                cover = day['coverage']['cloud']['byProvider']
                cover['openai'] = cover.pop('anthropic')
        self.assertEqual(self.parsed(ordinary)['state'], 'observed', 'the other-provider setup must be valid')
        ordinary['days']['2026-10-30']['byProvider']['openai'] = copy.deepcopy(
            base['days']['2026-10-30']['byProvider']['anthropic'])
        self.assertEqual(self.parsed(ordinary)['state'], 'malformed',
                         'the Claude total-only form must not relax other provider contracts')

    def optional_aggregate_refusal(self, dates):
        for day in dates:
            self.local_day(day, inp=1)
        publisher = collector.RollupPublisher(self.ledger, self.root/'absent-history.json',
                                             self.root/'aggregate-rollup.json', collecting=False)
        publisher.tick(self.NOW)
        baseline = json.loads(publisher.output.read_text())
        self.assertEqual(self.parsed(baseline)['state'], 'observed', 'the Local baseline must be consumable')
        before = tuple(self.ledger.db.iterdump())
        path = Path(os.environ['XDG_STATE_HOME'])/'omarchy/agents/usage/claude.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'recentDaysUnit':'tokens', 'recentDays':[
            {'date':day, 'messageCount':collector.MAX_SAFE - 1} for day in dates]}))
        publisher.tick(self.NOW + collector.ROLLUP_REFRESH)
        record = json.loads(publisher.output.read_text())
        self.assertEqual(self.parsed(record)['state'], 'observed',
                         'optional aggregate overflow must not replace a consumable Local report')
        self.assertIsNone(publisher.failure, 'optional refusal must remain isolated from Local publication')
        self.assertEqual(tuple(self.ledger.db.iterdump()), before, 'optional input must not alter retained history')
        for day in dates:
            self.assertEqual(record['days'][day]['total'], 1)
            self.assertEqual(record['days'][day]['byProvider'], {})

    def test_optional_same_period_overflow_preserves_consumable_local_publication(self):
        self.optional_aggregate_refusal(('2026-10-29', '2026-10-30'))

    def test_optional_cross_year_retained_overflow_is_isolated(self):
        self.optional_aggregate_refusal(('2025-12-31', '2026-10-30'))

    def test_two_date_optional_totals_within_budget_are_consumable(self):
        for day in ('2026-10-29', '2026-10-30'):
            self.local_day(day, inp=1)
        path = Path(os.environ['XDG_STATE_HOME'])/'omarchy/agents/usage/claude.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'recentDaysUnit':'tokens', 'recentDays':[
            {'date':'2026-10-29', 'messageCount':17}, {'date':'2026-10-30', 'messageCount':19}]}))
        publisher = collector.RollupPublisher(self.ledger, self.root/'absent-history.json',
                                             self.root/'ordinary-rollup.json', collecting=False)
        publisher.tick(self.NOW)
        self.assertIsNone(publisher.failure)
        record = json.loads(publisher.output.read_text())
        parsed = self.parsed(record)
        self.assertEqual(parsed['state'], 'observed')
        bars = {row['key']:row['value'] for row in parsed['view']['bars']}
        for day, tokens in (('2026-10-29', 17), ('2026-10-30', 19)):
            self.assertEqual(record['days'][day]['total'], 1 + tokens)
            self.assertEqual(bars[day], 1 + tokens)
            self.assertEqual(record['days'][day]['byProvider']['anthropic']['total_tokens'], tokens)

    def test_claude_merge_preserves_local_calendar_without_hermes(self):
        record = self.complete_record()
        self.assertEqual(self.parsed(record)['state'], 'observed')
        merged = collector.merge_claude_days(record, {'2026-10-30': 37})
        parsed = self.parsed(merged)
        self.assertEqual(parsed['state'], 'observed')
        bars = {row['key']: row for row in parsed['view']['bars']}
        self.assertEqual(bars['2026-10-29']['value'], 100)
        self.assertEqual(bars['2026-10-30']['value'], 237)
        self.assertTrue(merged['days']['2026-10-29']['coverage']['partial'],
                        'new Cloud inventory changes even days without Claude samples')
        self.assertTrue(merged['days']['2026-10-30']['coverage']['partial'])

    def test_claude_merge_marks_added_unknown_coverage_partial(self):
        record = self.complete_record(hermes=True)
        self.assertFalse(record['days']['2026-10-30']['coverage']['cloud']['partial'])
        merged = collector.merge_claude_days(record, {'2026-10-30': 37})
        self.assertEqual(self.parsed(merged)['state'], 'observed')
        day = merged['days']['2026-10-30']
        self.assertEqual(day['total'], 257)
        self.assertTrue(day['coverage']['cloud']['partial'])
        self.assertTrue(day['coverage']['partial'])
        saved = copy.deepcopy(merged)
        self.assertEqual(collector.merge_claude_days(merged, {'2026-10-30': 37}), saved,
                         'an already-admitted anthropic source must never be counted twice')

    def test_no_in_window_claude_days_do_not_change_inventory(self):
        for days in ({'2026-11-01': 37}, {'2025-01-01': 37}, {}):
            with self.subTest(days=days):
                record = self.complete_record()
                before = copy.deepcopy(record)
                merged = collector.merge_claude_days(record, days)
                self.assertEqual(merged, before)
                self.assertEqual(self.parsed(merged)['state'], 'observed')

    def test_claude_merge_overflow_leaves_valid_record_unchanged(self):
        record = self.complete_record()
        before = copy.deepcopy(record)
        with self.assertRaisesRegex(ValueError, 'overflow'):
            collector.merge_claude_days(record, {'2026-10-30': collector.MAX_SAFE})
        self.assertEqual(record, before)
        self.assertEqual(self.parsed(record)['state'], 'observed')


class RollupIngestRecovery(Base):
    NOW = at('2026-10-31T12:00:00')

    def publisher(self):
        self.local_day('2026-10-30', inp=100)
        path = self.root/'cloud-history.json'
        path.write_text(json.dumps(history({'2026-10-30': {'input_tokens': 17}})))
        return collector.RollupPublisher(self.ledger, path, self.root/'rollup.json', collecting=False)

    def test_stat_permission_failure_still_publishes_local_history(self):
        publisher = self.publisher()
        real_stat = collector.os.stat

        def stat(path, *args, **kwargs):
            if os.fspath(path) == os.fspath(publisher.history):
                raise PermissionError('synthetic source failure')
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(collector.os, 'stat', side_effect=stat):
            publisher.tick(self.NOW)
        self.assertTrue(publisher.output.is_file(), 'optional Cloud failure must not stop Local publication')
        record = json.loads(publisher.output.read_text())
        self.assertEqual(record['cloudFeed']['state'], 'unusable')
        self.assertEqual(record['days']['2026-10-30']['total'], 100)
        parsed = run_node('out.state = H.parseRollup(payload.text).state;', text=json.dumps(record))
        self.assertEqual(parsed['state'], 'observed')
        self.assertIsNone(publisher.stamp)

    def test_database_failure_retries_the_same_source_stamp(self):
        publisher = self.publisher()
        original = self.ledger.merge_cloud
        calls = []

        def merge(value, now):
            calls.append(now)
            if len(calls) == 1:
                raise sqlite3.OperationalError('synthetic transient transaction failure')
            return original(value, now)

        with mock.patch.object(self.ledger, 'merge_cloud', side_effect=merge):
            publisher.ingest(self.NOW)
            self.assertEqual(publisher.feed['state'], 'unusable')
            self.assertIsNone(publisher.stamp, 'a failed transaction must not consume the projection')
            publisher.ingest(self.NOW + 5)
            self.assertEqual(publisher.feed['state'], 'admitted')
            publisher.ingest(self.NOW + 10)
        self.assertEqual(len(calls), 2, 'successful unchanged projections remain cached')
        self.assertEqual(self.ledger.rollup(self.NOW, publisher.feed)['days']['2026-10-30']['total'], 117)

    def test_source_read_failure_retries_without_mtime_change(self):
        publisher = self.publisher()
        original = collector.read_history
        calls = []

        def read(path):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError('synthetic temporary read failure')
            return original(path)

        with mock.patch.object(collector, 'read_history', side_effect=read):
            publisher.ingest(self.NOW)
            self.assertEqual(publisher.feed['state'], 'unusable')
            publisher.ingest(self.NOW + 5)
        self.assertEqual(len(calls), 2)
        self.assertEqual(publisher.feed['state'], 'admitted')
        self.assertEqual(self.ledger.rollup(self.NOW, publisher.feed)['days']['2026-10-30']['total'], 117)


class ClaudeInputSafety(Base):
    def setUp(self):
        super().setUp()
        self.path = self.root/'claude.json'
        self.payload = {'recentDaysUnit': 'tokens',
                        'recentDays': [{'date': '2026-10-30', 'messageCount': 17}]}

    def write(self, payload=None):
        self.path.write_text(json.dumps(self.payload if payload is None else payload))

    def test_valid_bounded_regular_file(self):
        self.write()
        self.assertEqual(collector.claude_token_days(self.path), {'2026-10-30': 17})

    def test_symlink_is_refused_without_reading_its_target(self):
        target = self.root/'original.json'
        target.write_text(json.dumps(self.payload))
        original = target.read_bytes()
        self.path.symlink_to(target)
        self.assertEqual(collector.claude_token_days(self.path), {})
        self.assertEqual(target.read_bytes(), original)

    def test_oversized_input_is_refused(self):
        self.write()
        with self.path.open('ab') as stream:
            stream.write(b' ' * collector.HISTORY_LIMIT)
        self.assertGreater(self.path.stat().st_size, collector.HISTORY_LIMIT)
        self.assertEqual(collector.claude_token_days(self.path), {})

    def test_exact_byte_limit_remains_admitted(self):
        raw = json.dumps(self.payload).encode()
        self.path.write_bytes(raw + b' ' * (collector.HISTORY_LIMIT - len(raw)))
        self.assertEqual(self.path.stat().st_size, collector.HISTORY_LIMIT)
        self.assertEqual(collector.claude_token_days(self.path), {'2026-10-30': 17})

    def test_duplicate_member_is_refused(self):
        self.path.write_text('{"recentDaysUnit": "tokens", "recentDays": [], "recentDays": '
                             + json.dumps(self.payload['recentDays']) + '}')
        self.assertEqual(collector.claude_token_days(self.path), {})

    def test_nonfinite_values_are_refused_even_outside_the_days(self):
        for literal in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(literal=literal):
                self.path.write_text('{"recentDaysUnit": "tokens", "ignored": ' + literal + ', "recentDays": '
                                     + json.dumps(self.payload['recentDays']) + '}')
                self.assertEqual(collector.claude_token_days(self.path), {})

    def test_wrong_shape_and_missing_file_contribute_nothing(self):
        self.assertEqual(collector.claude_token_days(self.path), {})
        for value in (None, [], 'not a mapping', {'recentDays': {}}):
            with self.subTest(value=value):
                self.path.write_text(json.dumps(value))
                self.assertEqual(collector.claude_token_days(self.path), {})


class ClaudeTokenUnits(Base):
    def record(self, unit='tokens'):
        value = {'providerId': 'claude', 'hasLocalStats': True, 'todayTotalTokens': 17,
                 'recentDays': [{'date': '2026-10-30', 'messageCount': 17}]}
        if unit is not None:
            value['recentDaysUnit'] = unit
        return value

    def calendar(self, value):
        path = self.root/'claude.json'
        path.write_text(json.dumps(value))
        return collector.claude_token_days(path)

    def bar(self, value):
        functions = '\n'.join(qml_function(stager.RECORDED_TOKEN_FUNCTIONS, name) for name in
                              ('recordedTokenCounters', 'validRecordedTokens', 'recordedTokenWindows'))
        return run_node('''
            const ctx = {}; vm.createContext(ctx); vm.runInContext(payload.functions, ctx);
            const raw = ctx.recordedTokenCounters(payload.record, 'claude');
            out.unit = raw.recentDaysUnit === undefined ? null : raw.recentDaysUnit;
            out.windows = ctx.recordedTokenWindows(raw, false);
        ''', functions=functions, record=value)

    def test_missing_or_other_units_cannot_establish_daily_tokens(self):
        for unit in (None, 'messages', 'Tokens', '', True, 1, ['tokens']):
            with self.subTest(unit=unit):
                self.assertEqual(self.calendar(self.record(unit)), {})

    def test_explicit_tokens_are_accepted_by_calendar_and_generated_bar(self):
        value = self.record()
        self.assertEqual(self.calendar(value), {'2026-10-30': 17})
        result = self.bar(value)
        self.assertEqual(result['unit'], 'tokens', 'normalization must preserve the explicit unit')
        self.assertEqual(result['windows']['week'], 17)
        self.assertEqual(result['windows']['recordedDays'], 1)

    def test_untyped_days_do_not_erase_explicit_today_tokens_in_the_bar(self):
        value = self.record(None)
        self.assertEqual(self.calendar(value), {})
        windows = self.bar(value)['windows']
        self.assertEqual(windows['today'], 17)
        self.assertIsNone(windows['week'])

    def test_activity_markers_cannot_be_overridden_by_a_tokens_label(self):
        for marker in ('sessionCount', 'toolCallCount'):
            with self.subTest(marker=marker):
                value = self.record()
                value['recentDays'][0][marker] = 1
                self.assertEqual(self.calendar(value), {})
                self.assertIsNone(self.bar(value)['windows']['week'])

    def test_typed_zero_and_unrecorded_rows_do_not_fabricate_cloud_coverage(self):
        value = self.record()
        value['recentDays'] += [{'date': '2026-10-29', 'messageCount': 0},
                                {'date': '2026-10-28', 'messageCount': None, 'recorded': False}]
        self.assertEqual(self.calendar(value), {'2026-10-30': 17})


class OfflineSnapshotSafety(Base):
    def setUp(self):
        super().setUp()
        self.adapter = load('offline_snapshot_adapter', SOURCES/'hermes_accounting.py')

    def snapshot(self, name='snapshot.db', oversized=False, writable=False):
        path = self.root/name
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE session_model_usage ('
                       + ','.join('"' + col + '" TEXT' for col in self.adapter.COLUMNS) + ')')
        if oversized:
            with path.open('r+b') as stream:
                stream.truncate(self.adapter.MAX_INPUT + 4096)
        if not writable:
            path.chmod(0o400)
        return path

    def invoke(self, path, *, refused):
        output = self.root/'accounting.json'
        output.write_text('{"lastGood":true}')
        original = output.read_bytes()
        attempts, connect = [], self.adapter.sqlite3.connect

        def observed(database, *args, **kwargs):
            if database != ':memory:':
                attempts.append(str(database))
            return connect(database, *args, **kwargs)

        with contextlib.redirect_stderr(io.StringIO()), \
             mock.patch.object(self.adapter.sqlite3, 'connect', side_effect=observed):
            code = self.adapter.main(['--sqlite-snapshot', str(path), '--profile', 'default',
                                      '--output', str(output)])
        self.assertEqual(attempts, [], 'offline snapshots must never be opened by SQLite pathname')
        self.assertEqual(code, 2 if refused else 0)
        if refused:
            self.assertEqual(output.read_bytes(), original, 'refusal preserves last-good publication')
        return output

    def test_small_standalone_readonly_snapshot_is_deserialized(self):
        path = self.snapshot()
        original = path.read_bytes()
        output = self.invoke(path, refused=False)
        self.assertEqual(json.loads(output.read_text())['contract'], 'hermes-token-accounting/1')
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')))

    def test_oversized_snapshot_never_switches_to_a_pathname_reader(self):
        path = self.snapshot(oversized=True)
        self.invoke(path, refused=True)

    def test_legacy_helper_cannot_bypass_the_offline_bound(self):
        path = self.snapshot(oversized=True)
        with self.assertRaises(ValueError):
            self.adapter.read_snapshot_vacuumed(path)

    def test_special_characters_cannot_create_or_open_another_database(self):
        for name in ('snap#one.db', 'snap?two.db', 'snap%3fthree.db'):
            with self.subTest(name=name):
                path = self.snapshot(name, oversized=True)
                before = set(self.root.iterdir()) | {self.root/'accounting.json'}
                self.invoke(path, refused=True)
                self.assertEqual(set(self.root.iterdir()), before)

    def test_symlink_input_never_reaches_sqlite(self):
        path = self.snapshot(oversized=True)
        link = self.root/'alias.db'
        link.symlink_to(path)
        self.invoke(link, refused=True)
        self.assertTrue(link.is_symlink())

    def test_sidecars_are_refused_on_small_standalone_snapshots(self):
        for suffix in ('-wal', '-shm', '-journal'):
            with self.subTest(suffix=suffix):
                path = self.snapshot('sidecar' + suffix + '.db')
                sidecar = Path(str(path) + suffix)
                sidecar.write_bytes(b'owned synthetic sidecar')
                self.invoke(path, refused=True)
                self.assertEqual(sidecar.read_bytes(), b'owned synthetic sidecar')

    def test_writable_standalone_snapshot_is_refused(self):
        self.invoke(self.snapshot(writable=True), refused=True)

    def test_vacuum_destination_is_not_an_accepted_ignored_option(self):
        path = self.snapshot()
        output, vacuum = self.root/'accounting.json', self.root/'vacuum.db'
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self.adapter.main(['--sqlite-snapshot', str(path), '--profile', 'default',
                                   '--output', str(output), '--vacuum-into', str(vacuum)])
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(output.exists())
        self.assertFalse(vacuum.exists())

    def typed_snapshot(self, name, rows=(), generated=None):
        path = self.root/name
        kinds = {field: 'TEXT' for field in self.adapter.KEY_FIELDS}
        kinds.update({field: 'INTEGER' for field in self.adapter.COUNTERS}, first_seen='REAL', last_seen='REAL')
        columns = ['"%s" %s' % (field, kinds[field]) + (' AS (%s)' % generated if field == 'input_tokens' and generated else '')
                   for field in self.adapter.COLUMNS]
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE session_model_usage (' + ','.join(columns) + ')')
            names = [field for field in self.adapter.COLUMNS if not (generated and field == 'input_tokens')]
            for row in rows:
                db.execute('INSERT INTO session_model_usage (' + ','.join(names) + ') VALUES ('
                           + ','.join('?' * len(names)) + ')', [row[field] for field in names])
            db.commit()
        path.chmod(0o400)
        return path

    def test_typed_rows_round_trip_through_the_bounded_reader(self):
        row = dict(session_id='s1', model='m', billing_provider='openai-codex', billing_base_url='',
                   billing_mode='', task='', input_tokens=100, output_tokens=20, cache_read_tokens=5,
                   cache_write_tokens=1, reasoning_tokens=3, api_call_count=2, first_seen=None, last_seen=None)
        output = self.invoke(self.typed_snapshot('rows.db', [row]), refused=False)
        record = json.loads(output.read_text())
        self.assertEqual([(s['provider'], s['windows']['history']) for s in record['sources']], [('openai-codex', 126)])

    def test_generated_columns_are_refused_not_evaluated(self):
        row = dict(session_id='s1', model='m', billing_provider='openai-codex', billing_base_url='',
                   billing_mode='', task='', output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
                   reasoning_tokens=0, api_call_count=0, first_seen=None, last_seen=None)
        path = self.typed_snapshot('generated.db', [row], generated='length(hex(zeroblob(1024)))')
        self.invoke(path, refused=True)

    def test_dangling_sidecar_symlink_is_refused(self):
        path = self.snapshot('dangling.db')
        sidecar = Path(str(path) + '-journal')
        sidecar.symlink_to(self.root/'absent-target')
        self.invoke(path, refused=True)
        self.assertTrue(sidecar.is_symlink())
        self.assertFalse((self.root/'absent-target').exists())


class CollectorLoopRecovery(Base):
    NOW = at('2026-10-31T12:00:00')

    class StopLoop(Exception):
        pass

    def run_loop(self, **patches):
        output = self.root/'local-output.json'
        argv = ['collector.py', '--render-only', '--run', '--state-dir', str(self.root/'loop-state'),
                '--output', str(output)]
        sleeps, errors = [], io.StringIO()

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 2:
                raise self.StopLoop()

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, 'argv', argv))
            stack.enter_context(mock.patch.object(collector.signal, 'signal'))
            stack.enter_context(mock.patch.object(collector.time, 'time', return_value=self.NOW))
            stack.enter_context(mock.patch.object(collector.time, 'sleep', side_effect=sleep))
            stack.enter_context(contextlib.redirect_stderr(errors))
            for name, replacement in patches.items():
                stack.enter_context(mock.patch.object(collector, name, side_effect=replacement))
            with self.assertRaises(self.StopLoop):
                collector.main()
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(output.is_file(), 'the next tick must recover publication')
        self.assertEqual(json.loads(output.read_text())['id'], 'local')
        self.assertNotIn('PRIVATE-ERROR-PAYLOAD', errors.getvalue())
        return errors.getvalue()

    def test_publish_error_does_not_exit_the_running_collector(self):
        publish = collector.publish
        attempts = []

        def flaky(path, record):
            attempts.append(path)
            if len(attempts) == 1:
                raise OSError('PRIVATE-ERROR-PAYLOAD')
            return publish(path, record)

        error = self.run_loop(publish=flaky)
        self.assertEqual(len(attempts), 2)
        self.assertIn('OSError', error)

    def test_publication_byte_limit_does_not_exit_the_running_collector(self):
        publish = collector.publish
        attempts = []

        def oversized_once(path, record):
            attempts.append(path)
            if len(attempts) == 1:
                return publish(path, {'payload': 'x' * (4 * 1024 * 1024)})
            return publish(path, record)

        error = self.run_loop(publish=oversized_once)
        self.assertEqual(len(attempts), 2)
        self.assertIn('PublicationLimitError', error)

    def test_unsafe_publication_temporary_does_not_exit_the_running_collector(self):
        publish = collector.publish
        attempts = []

        def obstructed_once(path, record):
            attempts.append(path)
            temporary = collector.publication_temporary(path)
            if len(attempts) == 1:
                temporary.symlink_to(self.root/'never-written')
            else:
                temporary.unlink()
            return publish(path, record)

        error = self.run_loop(publish=obstructed_once)
        self.assertEqual(len(attempts), 2)
        self.assertIn('PublicationTemporaryError', error)
        self.assertFalse(os.path.lexists(self.root/'never-written'), 'the obstacle is never followed')

    def test_single_shot_byte_limit_still_returns_failure(self):
        argv = ['collector.py', '--render-only', '--state-dir', str(self.root/'limit-state'),
                '--output', str(self.root/'limit-output.json')]
        publish = collector.publish
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(collector.signal, 'signal'), \
             mock.patch.object(collector, 'publish', side_effect=lambda path, record:
                               publish(path, {'payload': 'x' * (4 * 1024 * 1024)})):
            with self.assertRaisesRegex(ValueError, 'publication exceeded byte bound'):
                collector.main()
        self.assertFalse((self.root/'limit-output.json').exists())

    def test_record_error_does_not_exit_the_running_collector(self):
        record = collector.Ledger.record
        attempts = []

        def flaky(ledger, now, status):
            attempts.append(now)
            if len(attempts) == 1:
                raise sqlite3.OperationalError('PRIVATE-ERROR-PAYLOAD')
            return record(ledger, now, status)

        with mock.patch.object(collector.Ledger, 'record', autospec=True, side_effect=flaky):
            error = self.run_loop()
        self.assertEqual(len(attempts), 2)
        self.assertIn('OperationalError', error)

    def test_single_shot_publication_failure_is_not_reported_as_success(self):
        argv = ['collector.py', '--render-only', '--state-dir', str(self.root/'one-state'),
                '--output', str(self.root/'one-output.json')]
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(collector.signal, 'signal'), \
             mock.patch.object(collector, 'publish', side_effect=OSError('synthetic write failure')):
            with self.assertRaises(OSError):
                collector.main()
        self.assertFalse((self.root/'one-output.json').exists())


class Published(Base):
    """Real collector output for the week of 10-26, read through the real consumer."""
    NOW = at('2026-11-01T17:00:00')

    def record(self, tweak=None):
        self.local_day('2026-10-28', inp=9109682, out=169463)
        self.local_day('2026-10-30', inp=11572053, out=132560, first=at('2026-10-30T07:38:14'), last=at('2026-10-30T23:59:56'))
        self.local_day('2026-10-31', inp=5827255, out=116894)
        start, _ = collector.day_span('2026-11-01')
        self.local_day('2026-11-01', inp=5034339, out=34629, first=start + 5, last=self.NOW - 10)
        self.ledger.merge_cloud(history({
            '2026-10-29': {'input_tokens': 2000, 'cache_read_tokens': 217000000, 'output_tokens': 965547, 'reasoning_tokens': 11, 'api_call_count': 40},
            '2026-10-30': {}, '2026-10-31': {}, '2026-11-01': {'input_tokens': 30, 'output_tokens': 70}},
            observed=collector.utc_stamp(self.NOW - 60), covered=86000), self.NOW)
        value = {**self.ledger.rollup(self.NOW, {'state': 'admitted', 'timezone': 'America/New_York', 'observedAt': collector.utc_stamp(self.NOW - 60)}),
                 'observedAt': collector.utc_stamp(self.NOW), 'validUntil': collector.utc_stamp(self.NOW + 300)}
        return value


class CalendarConsumer(Published):
    def select(self, **options):
        return run_node('''
            const report = H.parseRollup(payload.text);
            out.report = {state: report.state, help: report.help, timezone: report.data && report.data.timezone};
            out.view = H.selectHistory(report, payload.options);
        ''', text=json.dumps(self.record()), options=options)

    def bar(self, view, key):
        return next(b for b in view['bars'] if b['key'] == key)

    def test_the_reported_day_renders_its_local_tokens(self):
        view = self.select()['view']
        day = self.bar(view, '2026-10-30')
        self.assertEqual(day['value'], 11704613)
        self.assertTrue(day['partial'], 'recorded for part of the day: a lower bound, shown with +')
        self.assertEqual(view['state'], 'observed')

    def test_seven_slots_end_on_today_with_unknowns_unknown(self):
        view = self.select()['view']
        self.assertEqual([b['key'] for b in view['bars']],
                         ['2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31', '2026-11-01'])
        self.assertIsNone(self.bar(view, '2026-10-26')['value'], 'no record stays ?, never 0')
        self.assertEqual(self.bar(view, '2026-11-01')['value'], 5034339 + 34629 + 100)
        self.assertEqual(view['maximum'], 217967547)

    def test_a_cloud_only_day_stays_a_lower_bound(self):
        day = self.bar(self.select()['view'], '2026-10-29')
        self.assertEqual(day['value'], 217967547)
        self.assertTrue(day['partial'])

    def test_a_local_only_day_stays_a_lower_bound(self):
        day = self.bar(self.select()['view'], '2026-10-28')
        self.assertEqual(day['value'], 9109682 + 169463)
        self.assertTrue(day['partial'])

    def test_counters_local_cannot_measure_stay_unknown(self):
        for metric, expected in (('cache_read_tokens', 0), ('cache_write_tokens', 0), ('reasoning_tokens', 0),
                                 ('api_call_count', 0), ('prompt_tokens', 0)):
            with self.subTest(metric):
                view = self.select(metric=metric, mode='multiple')['view']
                day = self.bar(view, '2026-10-30')
                self.assertEqual(day['value'], expected)
                self.assertTrue(day['partial'])
                self.assertEqual(sorted(str(s['value']) for s in day['series']), ['0', 'None'])

    def test_counters_local_measures_add_up(self):
        for metric, expected in (('input_tokens', 11572053), ('output_tokens', 132560), ('total_tokens', 11704613)):
            with self.subTest(metric):
                self.assertEqual(self.bar(self.select(metric=metric)['view'], '2026-10-30')['value'], expected)

    def test_cloud_counters_keep_their_own_meaning(self):
        view = self.select(metric='cache_read_tokens')['view']
        self.assertEqual(self.bar(view, '2026-10-29')['value'], 217000000)
        self.assertEqual(self.bar(self.select(metric='api_call_count')['view'], '2026-10-29')['value'], 40)

    def test_both_sources_are_selectable(self):
        view = self.select(mode='multiple')['view']
        self.assertEqual(sorted(s['provider'] for s in view['sources']), ['local', 'openai-codex'])
        self.assertEqual(len(view['selectedSources']), 2)
        series = {s['id']: s['value'] for s in self.bar(view, '2026-10-30')['series']}
        self.assertEqual(sorted(series.values()), [0, 11704613])

    def test_periods_are_monday_weeks_months_and_years_of_retained_days(self):
        total = 9109682 + 169463 + 217967547 + 11704613 + 5827255 + 116894 + 5034339 + 34629 + 100
        november = 5034339 + 34629 + 100
        # Missing days make a period a lower bound, not a total. November has one
        # elapsed day so far, and it is fully recorded.
        for grain, key, last, expected, partial in (('week', '2026-10-26', '2026-10-26', total, True),
                                                    ('month', '2026-10', '2026-11', total - november, True),
                                                    ('month', '2026-11', '2026-11', november, False),
                                                    ('year', '2026', '2026', total, True)):
            with self.subTest(grain=grain, key=key):
                view = self.select(grain=grain)['view']
                self.assertEqual(view['bars'][-1]['key'], last)
                self.assertEqual(self.bar(view, key)['value'], expected)
                self.assertEqual(self.bar(view, key)['partial'], partial)

    def test_summaries_use_the_same_numbers_as_the_bars(self):
        view = self.select()['view']
        summary = {s['grain']: s for s in view['summaries']}
        self.assertEqual(summary['day']['key'], '2026-11-01')
        self.assertEqual(summary['day']['value'], self.bar(view, '2026-11-01')['value'])
        self.assertEqual(summary['week']['key'], '2026-10-26')

    def test_help_states_what_the_numbers_are(self):
        report = self.select()['report']
        self.assertEqual(report['timezone'], 'America/New_York')
        for phrase in ('sampled', 'cached input is not counted', 'counted once', 'lower bound', 'Not full request accounting'):
            self.assertIn(phrase, report['help'])


class CompletePeriods(Base):
    def test_fully_recorded_week_is_exact(self):
        now = at('2026-11-01T17:00:00')
        days = [str(dt.date(2026, 10, 26) + dt.timedelta(days=n)) for n in range(7)]
        for index, day in enumerate(days):
            start, end = collector.day_span(day)
            last = min(end - 5, now - 10)
            self.local_day(day, inp=100 + index, first=start + 5, last=last)
        self.ledger.merge_cloud(history({day: {'output_tokens': 1} for day in days}, observed=collector.utc_stamp(now - 60), covered=89900), now)
        record = {**self.ledger.rollup(now, {'state': 'admitted'}), 'observedAt': collector.utc_stamp(now), 'validUntil': collector.utc_stamp(now + 300)}
        out = run_node('''
            const report = H.parseRollup(payload.text);
            out.week = H.selectHistory(report, {grain:'week'}).bars.pop();
            out.day = H.selectHistory(report, {grain:'day'}).bars[0];
        ''', text=json.dumps(record))
        self.assertEqual(out['week']['key'], '2026-10-26')
        self.assertEqual(out['week']['value'], sum(100 + i + 1 for i in range(7)))
        self.assertFalse(out['week']['partial'])
        self.assertFalse(out['day']['partial'])


class ConsumerAdmission(Published):
    def test_valid_record_is_admitted(self):
        out = run_node('out.state = H.parseRollup(payload.text).state;', text=json.dumps(self.record()))
        self.assertEqual(out['state'], 'observed')

    def test_malformed_records_are_rejected_whole(self):
        valid = self.record()
        day = '2026-10-30'
        def chain(change):
            value = copy.deepcopy(valid)
            change(value)
            return value
        local = next(s for s in valid['sources'] if s['kind'] == 'local')
        cases = {
            'contract': lambda v: v.update(contract='hermes-observed-history/1'),
            'schema': lambda v: v.update(schemaVersion=2),
            'writable': lambda v: v.update(readOnly=False),
            'extra field': lambda v: v.update(surprise=1),
            'missing field': lambda v: v.pop('validUntil'),
            'zone': lambda v: v.update(timezone='../etc'),
            'today': lambda v: v.update(today='2026-02-30'),
            'expiry order': lambda v: v.update(validUntil='2020-01-01T00:00:00Z'),
            'instant': lambda v: v.update(observedAt='2026-11-01 12:00'),
            'feed state': lambda v: v.update(cloudFeed={'state': 'trust-me'}),
            'loopback provider': lambda v: v['sources'].insert(0, {'id': 'e' * 64, 'kind': 'cloud', 'provider': 'custom'}),
            'second local': lambda v: v['sources'].append({'id': 'e' * 64, 'kind': 'local', 'provider': 'local'}),
            'no local': lambda v: v.update(sources=[s for s in v['sources'] if s['kind'] != 'local']),
            'duplicate id': lambda v: v['sources'].append(dict(v['sources'][0])),
            'bad id': lambda v: v['sources'][0].update(id='nothex'),
            'future date': lambda v: v['days'].update({'2026-11-02': copy.deepcopy(v['days'][day])}),
            'impossible date': lambda v: v['days'].update({'2026-02-30': copy.deepcopy(v['days'][day])}),
            'date order': lambda v: v.update(days=dict(reversed(list(v['days'].items())))),
            'wrong total': lambda v: v['days'][day].update(total=1),
            'model total': lambda v: v['days'][day]['byModel']['heretic'].update(total_tokens=1),
            'model field': lambda v: v['days'][day]['byModel']['heretic'].update(extra=1),
            'fractional': lambda v: v['days'][day]['byModel']['heretic'].update(input_tokens=1.5),
            'unsafe': lambda v: v['days'][day]['byModel']['heretic'].update(input_tokens=2**53),
            'cloud sum': lambda v: v['days'][day]['byProvider']['openai-codex'].update(total_tokens=1),
            'unlisted provider': lambda v: v['days'][day]['byProvider'].update(groq=v['days'][day]['byProvider']['openai-codex']),
            'partial flag': lambda v: v['days'][day]['coverage'].update(partial=False),
            'no coverage': lambda v: v['days'][day]['coverage'].update(local=None, cloud=None),
            'models without coverage': lambda v: v['days'][day]['coverage'].update(local=None),
            'cloud without coverage': lambda v: v['days'][day]['coverage'].update(cloud=None),
            'coverage basis': lambda v: v['days'][day]['coverage']['local'].update(basis='guess'),
            'coverage cell': lambda v: v['days'][day]['coverage']['cloud'].update(byProvider={}),
        }
        for label, change in cases.items():
            with self.subTest(label):
                out = run_node('const r = H.parseRollup(payload.text); out.state = r.state; out.data = !!r.data; out.rollup = !!r.rollup;',
                               text=json.dumps(chain(change)))
                self.assertEqual(out, {'state': 'malformed', 'data': False, 'rollup': False})
        self.assertIsNotNone(local)

    def test_unusable_text_is_rejected(self):
        duplicate = json.dumps(self.record()).replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1')
        for label, text in (('empty', ''), ('truncated', '{"contract":'), ('array', '[]'), ('duplicate key', duplicate),
                            ('oversize', ' ' * 4194305)):
            with self.subTest(label):
                self.assertEqual(run_node('out.state = H.parseRollup(payload.text).state;', text=text)['state'], 'malformed')


class BarWindows(Published):
    """The bar's Hermes windows: the same days the calendar shows."""
    def windows(self, nowOffset=1000, provider='openai-codex'):
        return run_node('''
            const report = H.parseRollup(payload.text), observed = report.rollup.observedAtMs;
            out.fresh = H.rollupFresh(report, observed + payload.offset);
            out.windows = H.rollupCloudWindows(report, payload.provider);
        ''', text=json.dumps(self.record()), offset=nowOffset, provider=provider)

    def test_today_and_seven_days_come_from_the_shared_days(self):
        out = self.windows()
        self.assertTrue(out['fresh'])
        self.assertEqual(out['windows'], {'today': 100, 'week': 217967547 + 100})

    def test_expired_or_future_publications_are_not_fresh(self):
        self.assertFalse(self.windows(nowOffset=300 * 1000)['fresh'], 'expires exactly at validUntil')
        self.assertTrue(self.windows(nowOffset=-299 * 1000)['fresh'], 'small clock skew is tolerated')
        self.assertFalse(self.windows(nowOffset=-301 * 1000)['fresh'], 'a publication from the future is withheld')

    def test_reports_without_a_rollup_are_not_fresh(self):
        out = run_node('''
            out.values = [H.rollupFresh(null, 1), H.rollupFresh({state:'loading'}, 1), H.rollupFresh(H.parseRollup('bad'), 1)];
            out.number = H.rollupFresh(H.parseRollup(payload.text), NaN);
        ''', text=json.dumps(self.record()))
        self.assertEqual(out, {'values': [False, False, False], 'number': False})

    def test_unknown_provider_has_unknown_windows(self):
        self.assertEqual(self.windows(provider='anthropic')['windows'], {'today': None, 'week': None})

    def test_yesterday_never_stands_in_for_today(self):
        self.ledger.merge_cloud(history({'2026-10-30': {'input_tokens': 9}}), at('2026-10-31T12:00:00'))
        self.local_day('2026-10-30', inp=1)
        now = at('2026-10-31T12:00:00')
        record = {**self.ledger.rollup(now, {'state': 'admitted'}), 'observedAt': collector.utc_stamp(now), 'validUntil': collector.utc_stamp(now + 300)}
        out = run_node('out.windows = H.rollupCloudWindows(H.parseRollup(payload.text), "openai-codex");', text=json.dumps(record))
        self.assertEqual(out['windows'], {'today': None, 'week': 9})


def qml_function(source, name):
    start = source.index('function ' + name + '(')
    depth, position, seen = 0, source.index('{', start), False
    quote = None
    while True:
        char = source[position]
        if quote:
            if char == '\\':
                position += 1
            elif char == quote:
                quote = None
        elif source.startswith('//', position):
            position = source.index('\n', position)
        elif char in '"\'':
            quote = char
        elif char == '{':
            depth += 1
            seen = True
        elif char == '}':
            depth -= 1
            if seen and depth == 0:
                return source[start:position + 1]
        position += 1


class BarTotals(Published):
    """The generated QML function behind 'Tokens today', with the real consumer behind it."""
    NAMES = ('validRecordedTokens', 'recordedTokenCounters', 'recordedTokenWindows', 'recordedTokenLabel',
             'recordedTokenProviderText', 'recordedTokenTotalsFor')

    def totals(self, script, **payload):
        functions = '\n'.join(qml_function(stager.RECORDED_TOKEN_FUNCTIONS, name) for name in self.NAMES)
        prelude = '''
            const ctx = {formatTokenCount: v => String(v), History: H}; ctx.root = ctx;
            vm.createContext(ctx);
            vm.runInContext(payload.functions, ctx);
            const hermes = provider => ({id:'hermes-token-accounting', contract:'hermes-token-accounting/1', schemaVersion:1, readOnly:true,
                sources:[{id:'f'.repeat(64), provider, profile:'default', attribution:'unattributed', windows:{today:null, week:null, history:4760038722}}]});
            const local = {providerId:'local', hasLocalStats:true, todayTotalTokens:5068968,
                recentDays:[{date:'2026-11-01', messageCount:5068968, recorded:true}], modelUsage:{heretic:{inputTokens:5034339, outputTokens:34629}}};
            const claude = {providerId:'claude', hasLocalStats:true, todayTotalTokens:40, modelUsage:{m:{inputTokens:40}}};
            const report = H.parseRollup(payload.text);
        '''
        return run_node(prelude + script, functions=functions, text=json.dumps(self.record()), **payload)

    def test_hermes_today_reaches_the_bar_once(self):
        out = self.totals('out.t = ctx.recordedTokenTotalsFor([local, claude], null, hermes("openai-codex"), report);')['t']
        self.assertEqual(out['cloud']['today']['value'], 100 + 40)
        self.assertEqual(out['cloud']['today']['known'], 2)
        self.assertEqual(out['total']['today'], 5068968 + 100 + 40)
        self.assertEqual(out['cloud']['history']['value'], 4760038722 + 40, 'the undated ledger keeps its own window')

    def test_without_a_rollup_the_bar_is_exactly_as_before(self):
        out = self.totals('''
            out.absent = ctx.recordedTokenTotalsFor([local, claude], null, hermes("openai-codex"));
            out.expired = ctx.recordedTokenTotalsFor([local, claude], null, hermes("openai-codex"), null);
        ''')
        self.assertEqual(out['absent'], out['expired'])
        self.assertEqual(out['absent']['total']['today'], 5068968 + 40)
        self.assertEqual(out['absent']['cloud']['today']['known'], 1)

    def test_nous_stays_with_the_native_hermes_record(self):
        out = self.totals('out.t = ctx.recordedTokenTotalsFor([local], null, hermes("nous"), report);')['t']
        self.assertIsNone(out['cloud']['today']['value'], 'dating Nous here would count it twice')
        self.assertEqual(out['total']['today'], 5068968)

    def test_local_is_not_read_from_the_rollup_as_well(self):
        out = self.totals('out.t = ctx.recordedTokenTotalsFor([local], null, hermes("openai-codex"), report);')['t']
        self.assertEqual(out['local']['today'], 5068968)
        self.assertEqual(out['total']['today'], 5068968 + 100)

    def test_calendar_today_equals_the_bar_with_no_native_providers(self):
        record = self.record()
        out = run_node('''
            const ctx = {formatTokenCount: v => String(v), History: H}; ctx.root = ctx;
            vm.createContext(ctx); vm.runInContext(payload.functions, ctx);
            const report = H.parseRollup(payload.text);
            const today = payload.today;
            const local = {providerId:'local', hasLocalStats:true, todayTotalTokens:payload.localToday,
                recentDays:[{date:today, messageCount:payload.localToday, recorded:true}], modelUsage:{}};
            const hermes = {id:'hermes-token-accounting', contract:'hermes-token-accounting/1', schemaVersion:1, readOnly:true,
                sources:[{id:'f'.repeat(64), provider:'openai-codex', profile:'default', attribution:'unattributed', windows:{today:null, week:null, history:1}}]};
            out.bar = ctx.recordedTokenTotalsFor([local], null, hermes, report).total.today;
            out.calendar = H.selectHistory(report, {}).bars.pop().value;
        ''', functions='\n'.join(qml_function(stager.RECORDED_TOKEN_FUNCTIONS, name) for name in self.NAMES),
            text=json.dumps(record), today=record['today'],
            localToday=sum(m['total_tokens'] for m in record['days'][record['today']]['byModel'].values()))
        self.assertEqual(out['bar'], out['calendar'])


class GeneratedWiring(unittest.TestCase):
    def test_calendar_prefers_the_rollup_and_keeps_cloud_history_as_fallback(self):
        main = stager.HISTORY_MAIN
        self.assertIn('History.parseRollup(content)', main)
        self.assertIn('History.parseHistory(text())', main)
        self.assertIn('readonly property var observedHistory: root.tokenRollup.data ? root.tokenRollup : root.cloudObservedHistory', main)

    def test_bar_expires_with_shared_dated_clock_not_the_legacy_rollup(self):
        # The native ten-stage clock proof and summary regressions exercise
        # expiry. Pin their actual shared wiring without relabelling undated
        # accounting history as a dated roll-up or rescanning it every tick.
        self.assertIn('recordedTokenTotalsFor(enabledProviders, null, hermesAccountingAgent.record, null)', stager.RECORDED_TOKEN_FUNCTIONS)
        self.assertIn('recordedTokenTotals: recordedDatedTokenTotals(undatedTokenTotals, datedTokenView)', stager.RECORDED_TOKEN_FUNCTIONS)
        self.assertIn('History.selectDatedHistory(root.cloudObservedHistory,', stager.HISTORY_MAIN)
        self.assertIn('root.calendarClock, {}, root.calendarNowMs)', stager.HISTORY_MAIN)
        self.assertIn('onTriggered: root.calendarNowMs = Date.now()', stager.HISTORY_MAIN)
        self.assertIn('dated.selectionError === false', stager.RECORDED_TOKEN_FUNCTIONS)


class CloudAggregateAdmission(Base):
    """V7-R1: genuine canonical projections must not poison retained Local views."""
    NOW = at('2026-10-30T12:02:00')

    def projection(self, day, *, tokens=0, api_calls=0):
        accounting = load('aggregate_accounting', SOURCES/'hermes_accounting.py')
        producer = load('aggregate_history', SOURCES/'usage_history.py')
        def snapshot(total, calls):
            row = {key: '' for key in accounting.KEY_FIELDS}
            row.update(session_id='synthetic-cloud', model='synthetic-model', billing_provider='openai-codex')
            row.update({key: 0 for key in accounting.COUNTERS})
            row.update(input_tokens=total, api_call_count=calls, first_seen=None, last_seen=None)
            return accounting.canonical_snapshot([row], 'default')
        state = producer.observe(None, snapshot(0, 0), observed_at=day+'T16:00:00Z', timezone='America/New_York')
        state = producer.observe(state, snapshot(tokens, api_calls), observed_at=day+'T16:01:00Z', timezone='America/New_York')
        projection = producer.project_history(state)
        admitted = collector.cloud_days(projection)[2][day]['openai-codex']
        self.assertEqual((admitted['input_tokens'], admitted['api_call_count']), (tokens, api_calls),
                         'the actual canonical writer must produce an individually admissible projection')
        return projection

    def setup_publisher(self, dates=('2026-10-29', '2026-10-30')):
        for day in dates:
            moment = at(day+'T12:00:00')
            self.assertEqual(self.ledger.observe('heretic', 'worker-'+day, (0, 0), moment), (0, 0))
            self.assertEqual(self.ledger.observe('heretic', 'worker-'+day, (1, 0), moment+1), (1, 0))
        feed, output = self.root/'canonical-history.json', self.root/'rollup.json'
        publisher = collector.RollupPublisher(self.ledger, feed, output, collecting=False)
        publisher.tick(self.NOW-30)
        self.assertEqual(self.consumer_state(output), 'observed', 'Local baseline must reach the real consumer')
        return publisher

    def consumer_state(self, path):
        return run_node('out.state = H.parseRollup(payload.text).state;', text=path.read_text())['state']

    def publish_projection(self, publisher, projection, moment):
        publisher.history.write_text(json.dumps(projection))
        publisher.tick(moment)
        self.assertIsNone(publisher.failure, 'optional rejection must stay isolated from Local publication')
        return self.consumer_state(publisher.output)

    def token_case(self, delta, *, refusal, dates=('2026-10-29', '2026-10-30')):
        publisher = self.setup_publisher(dates)
        before = tuple(self.ledger.db.iterdump())
        states = [self.publish_projection(publisher, self.projection('2026-10-30', tokens=delta), self.NOW)]
        after = tuple(self.ledger.db.iterdump())
        stamp_after_input = publisher.stamp
        publisher.tick(self.NOW+collector.ROLLUP_REFRESH)
        states.append(self.consumer_state(publisher.output))
        publisher.history.unlink()
        publisher.tick(self.NOW+2*collector.ROLLUP_REFRESH)
        states.append(self.consumer_state(publisher.output))
        if refusal:
            states.append(self.publish_projection(publisher, self.projection('2026-10-30', tokens=17),
                                                  self.NOW+3*collector.ROLLUP_REFRESH))
        self.assertEqual(states, ['observed']*len(states),
                         'valid Cloud input must not replace or retain a consumer-rejected combined report')
        if refusal:
            self.assertEqual(after, before, 'refused input must roll back the complete Cloud transaction')
            self.assertIsNone(stamp_after_input, 'refusal must not consume the input stamp')
            self.assertEqual(self.ledger.db.execute('SELECT input_tokens FROM cloud_daily').fetchall(), [(17,)],
                             'a later healthy projection must recover without deleting retained history')
        else:
            record = json.loads(publisher.output.read_text())
            self.assertEqual(sum(row['total'] for row in record['days'].values()), 2+delta)
            self.assertEqual(record['days']['2026-10-30']['byProvider']['openai-codex']['input_tokens'], delta)

    def api_case(self, second, *, refusal):
        publisher = self.setup_publisher()
        first = collector.MAX_SAFE-1
        self.assertEqual(self.publish_projection(publisher,
            self.projection('2026-10-29', api_calls=first), self.NOW-20), 'observed')
        before = tuple(self.ledger.db.iterdump())
        states = [self.publish_projection(publisher, self.projection('2026-10-30', api_calls=second), self.NOW)]
        after = tuple(self.ledger.db.iterdump())
        stamp_after_input = publisher.stamp
        publisher.history.unlink()
        publisher.tick(self.NOW+collector.ROLLUP_REFRESH)
        states.append(self.consumer_state(publisher.output))
        if refusal:
            states.append(self.publish_projection(publisher, self.projection('2026-10-30', api_calls=1),
                                                  self.NOW+2*collector.ROLLUP_REFRESH))
        self.assertEqual(states, ['observed']*len(states), 'API calls have an independent consumer aggregation bound')
        if refusal:
            self.assertEqual(after, before, 'reject before committing a second individually valid API-count row')
            self.assertIsNone(stamp_after_input)
        record = json.loads(publisher.output.read_text())
        self.assertEqual(sum(row['byProvider']['openai-codex']['api_call_count']
                             for row in record['days'].values()), collector.MAX_SAFE)
        self.assertEqual(sum(row['total'] for row in record['days'].values()), 2,
                         'the independent API counter must not fabricate token usage')

    def test_ordinary_cloud_control_survives_input_removal(self):
        self.token_case(17, refusal=False)

    def test_combined_token_overflow_rolls_back_and_recovers(self):
        self.token_case(collector.MAX_SAFE-1, refusal=True)

    def test_exact_combined_token_limit_is_admitted(self):
        self.token_case(collector.MAX_SAFE-2, refusal=False)

    def test_cross_year_retained_overflow_is_rejected_without_losing_history(self):
        self.token_case(collector.MAX_SAFE-1, refusal=True, dates=('2025-12-31', '2026-10-30'))

    def test_retained_api_call_overflow_is_transactional_and_recovers(self):
        self.api_case(2, refusal=True)

    def test_exact_retained_api_call_limit_is_admitted(self):
        self.api_case(1, refusal=False)


class CloudApiRetention(Base):
    """Retain independent API observations through the real publisher and UI consumer."""
    NOW = CloudAggregateAdmission.NOW
    setup_publisher = CloudAggregateAdmission.setup_publisher
    consumer_state = CloudAggregateAdmission.consumer_state
    publish_projection = CloudAggregateAdmission.publish_projection
    projection = CloudAggregateAdmission.projection

    def genuine_pair(self):
        accounting = load('retention_accounting', SOURCES/'hermes_accounting.py')
        producer = load('retention_history', SOURCES/'usage_history.py')
        def snapshot(tokens, calls):
            row = {key: '' for key in accounting.KEY_FIELDS}
            row.update(session_id='synthetic-replay', model='synthetic-model', billing_provider='openai-codex')
            row.update({key: 0 for key in accounting.COUNTERS})
            row.update(input_tokens=tokens, api_call_count=calls, first_seen=None, last_seen=None)
            return accounting.canonical_snapshot([row], 'default')
        state = producer.observe(None, snapshot(0, 0), observed_at='2026-10-30T16:00:00Z', timezone='America/New_York')
        state = producer.observe(state, snapshot(17, 1), observed_at='2026-10-30T16:01:00Z', timezone='America/New_York')
        older = producer.project_history(state)
        state = producer.observe(state, snapshot(17, 2), observed_at='2026-10-30T16:02:00Z', timezone='America/New_York')
        newer = producer.project_history(state)
        self.assertEqual(state['events'][-1]['status'], 'measured_zero')
        self.assertEqual([s['id'] for s in older['sources']], [s['id'] for s in newer['sources']])
        for payload, calls in ((older, 1), (newer, 2)):
            row = collector.cloud_days(payload)[2]['2026-10-30']['openai-codex']
            self.assertEqual((row['input_tokens'], row['api_call_count']), (17, calls))
        return older, newer

    def retain_check(self, publisher, *, tokens=17, calls=2, sources=None, local=None):
        with self.subTest(boundary='ledger'):
            self.assertEqual(self.ledger.db.execute(
                'SELECT input_tokens,api_call_count FROM cloud_daily WHERE day=? AND provider=?',
                ('2026-10-30', 'openai-codex')).fetchone(), (tokens, calls))
        record = json.loads(publisher.output.read_text())
        cloud_id = next(s['id'] for s in record['sources'] if s['kind'] == 'cloud')
        local_id = next(s['id'] for s in record['sources'] if s['kind'] == 'local')
        observed = run_node('''
            const report = H.parseRollup(payload.text);
            out.state = report.state;
            out.calls = []; out.tokens = []; out.localCalls = [];
            for (const grain of ['day', 'week', 'month', 'year']) {
                const opts = {sources:[payload.cloudId], grain, mode:'multiple'};
                const api = H.selectHistory(report, {...opts, metric:'api_call_count'});
                const tokens = H.selectHistory(report, {...opts, metric:'total_tokens'});
                const local = H.selectHistory(report, {sources:[payload.localId], grain, metric:'api_call_count'});
                if (api.selectionError || tokens.selectionError || local.selectionError) throw Error('selection failed');
                out.calls.push(api.bars.at(-1).value);
                out.tokens.push(tokens.bars.at(-1).value);
                out.localCalls.push(local.bars.at(-1).value);
            }
        ''', text=publisher.output.read_text(), cloudId=cloud_id, localId=local_id)
        with self.subTest(boundary='consumer'):
            self.assertEqual(observed, {'state': 'observed', 'calls': [calls]*4,
                                       'tokens': [tokens]*4, 'localCalls': [None]*4})
        with self.subTest(boundary='identity_and_local'):
            if sources is not None:
                self.assertEqual(record['sources'], sources)
            if local is not None:
                self.assertEqual(self.ledger.db.execute('SELECT * FROM daily ORDER BY day,model').fetchall(), local)
            self.assertEqual(record['days']['2026-10-30']['byModel']['heretic']['total_tokens'], 1)
            self.assertEqual(record['days']['2026-10-30']['total'], tokens+1)
        return record['sources']

    def test_equal_token_older_replay_preserves_api_calls_and_recovers(self):
        older, newer = self.genuine_pair()
        publisher = self.setup_publisher(('2026-10-30',))
        local = self.ledger.db.execute('SELECT * FROM daily ORDER BY day,model').fetchall()
        self.assertEqual(self.publish_projection(publisher, newer, self.NOW), 'observed')
        sources = self.retain_check(publisher, local=local)
        self.assertEqual(self.publish_projection(publisher, older, self.NOW+30), 'observed')
        self.retain_check(publisher, sources=sources, local=local)
        publisher.tick(self.NOW+60)
        self.retain_check(publisher, sources=sources, local=local)
        publisher.history.unlink()
        publisher.tick(self.NOW+90)
        self.retain_check(publisher, sources=sources, local=local)
        self.assertEqual(self.publish_projection(publisher, newer, self.NOW+120), 'observed')
        self.retain_check(publisher, sources=sources, local=local)

    def test_genuine_api_only_growth_is_admitted_without_recounting(self):
        older, newer = self.genuine_pair()
        publisher = self.setup_publisher(('2026-10-30',))
        self.assertEqual(self.publish_projection(publisher, older, self.NOW), 'observed')
        sources = self.retain_check(publisher, calls=1)
        self.assertEqual(self.publish_projection(publisher, newer, self.NOW+30), 'observed')
        self.retain_check(publisher, sources=sources)
        publisher.tick(self.NOW+60)
        self.retain_check(publisher, sources=sources)

    def test_token_growth_does_not_lower_retained_api_calls(self):
        publisher = self.setup_publisher(('2026-10-30',))
        self.assertEqual(self.publish_projection(publisher, self.projection('2026-10-30', tokens=17, api_calls=2), self.NOW), 'observed')
        sources = self.retain_check(publisher)
        # A restarted history still produces an admitted projection, not a reset of retained facts.
        self.assertEqual(self.publish_projection(publisher, self.projection('2026-10-30', tokens=23, api_calls=1), self.NOW+collector.ROLLUP_REFRESH), 'observed')
        self.retain_check(publisher, tokens=23, sources=sources)

    def test_lower_token_projection_can_advance_the_independent_api_metric(self):
        publisher = self.setup_publisher(('2026-10-30',))
        self.assertEqual(self.publish_projection(publisher, self.projection('2026-10-30', tokens=17, api_calls=2), self.NOW), 'observed')
        sources = self.retain_check(publisher)
        self.assertEqual(self.publish_projection(publisher, self.projection('2026-10-30', tokens=3, api_calls=5), self.NOW+collector.ROLLUP_REFRESH), 'observed')
        self.retain_check(publisher, calls=5, sources=sources)


if __name__ == '__main__':
    unittest.main(verbosity=1)
