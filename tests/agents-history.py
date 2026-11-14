#!/usr/bin/env python3
"""Bounded history integration; all counters and dates are synthetic/offline."""
import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / 'extras/local-router-stats/hermes_accounting.py'


def load(name):
    spec = importlib.util.spec_from_file_location(name, ADAPTER.parent / (name + '.py'))
    if spec is None or spec.loader is None:
        raise AssertionError('owned test module is unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(**changes):
    result = dict(session_id='PRIVATE_SESSION_SENTINEL', model='PRIVATE_MODEL_SENTINEL',
                  billing_provider='openai-codex', billing_base_url='https://api.openai.com/v1',
                  billing_mode='subscription_included', task='PRIVATE_TASK_SENTINEL',
                  input_tokens=100, output_tokens=20, cache_read_tokens=30, cache_write_tokens=5,
                  reasoning_tokens=7, api_call_count=1, first_seen=0, last_seen=1)
    result.update(changes)
    return result


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hist-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.input = self.folder / 'snapshot.json'
        self.output = self.folder / 'accounting.json'
        self.state = self.folder / 'history-state.json'
        self.history = self.folder / 'history.json'

    def cli(self, rows, when, timezone='America/New_York', **kwargs):
        self.input.write_text(json.dumps({'contract': 'hermes-session-model-usage/1',
                                         'counterSemantics': 'hermes-canonical/1', 'rows': rows}))
        argv = [sys.executable, str(ADAPTER), '--json', str(self.input), '--profile', 'default',
                '--output', str(self.output), '--history-state', str(self.state),
                '--history-output', str(self.history), '--timezone', timezone, '--observed-at', when]
        env = dict(os.environ, HOME=str(self.folder), XDG_STATE_HOME=str(self.folder),
                   XDG_CONFIG_HOME=str(self.folder), XDG_CACHE_HOME=str(self.folder),
                   PYTHONDONTWRITEBYTECODE='1')
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
            env.pop(key, None)
        child = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10, **kwargs)
        self.assertEqual(child.returncode, 0, child.stderr)
        return json.loads(self.history.read_text())

    def test_calendar_crossings_and_both_dst_transitions_do_not_split(self):
        adapter, history = load('hermes_accounting'), load('usage_history')
        def pair(start, end, zone='UTC'):
            state = history.observe(None, adapter.canonical_snapshot([row()], 'default'),
                                    observed_at=start, timezone=zone)
            return history.project_history(history.observe(
                state, adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                observed_at=end, timezone=zone))
        for start, end, allocated in [
            ('2023-12-31T23:55:00Z', '2024-01-01T00:05:00Z', set()),
            ('2024-12-31T23:55:00Z', '2025-01-01T00:05:00Z', {'week'}),
            ('2024-02-29T23:55:00Z', '2024-03-01T00:05:00Z', {'week', 'year'}),
            ('2024-03-03T23:55:00Z', '2024-03-04T00:05:00Z', {'month', 'year'}),
            ('2024-03-04T23:55:00Z', '2024-03-05T00:00:00Z', {'week', 'month', 'year'}),
        ]:
            with self.subTest(start=start, end=end):
                public = pair(start, end)
                for grain in ('day', 'week', 'month', 'year'):
                    self.assertEqual(bool(public['buckets'][grain]), grain in allocated)
                    self.assertEqual(public['coverage']['unallocated'][grain]['total_tokens'],
                                     0 if grain in allocated else 10)
        for start, end, seconds in [
            ('2024-03-10T01:55:00-05:00', '2024-03-10T03:05:00-04:00', 600),
            ('2024-11-03T01:30:00-04:00', '2024-11-03T01:30:00-05:00', 3600),
        ]:
            with self.subTest(start=start):
                public = pair(start, end, 'America/New_York')
                cell = public['buckets']['day'][0]['sources'][0]
                self.assertEqual(cell['counts']['total_tokens'], 10)
                self.assertEqual(cell['coveredSeconds'], seconds)
                self.assertEqual(public['coverage']['unallocated']['day']['total_tokens'], 0)
        state = history.observe(None, adapter.canonical_snapshot([row()], 'default'),
                                observed_at='2024-02-29T23:50:00-05:00', timezone='America/New_York')
        state = history.observe(state, adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                                observed_at='2024-02-29T23:55:00-05:00', timezone='America/New_York')
        self.assertEqual(history.project_history(state)['buckets']['day'][0]['key'], '2024-02-29')

    def test_mixed_sources_zero_reset_gap_disappearance_and_reappearance(self):
        adapter, history = load('hermes_accounting'), load('usage_history')
        zero = row(session_id='zero', input_tokens=0, output_tokens=0, cache_read_tokens=0,
                   cache_write_tokens=0, reasoning_tokens=0, api_call_count=0)
        excluded = row(session_id='local', billing_base_url='http://127.0.0.1:8000/v1')
        def observe(state, rows, time):
            return history.observe(state, adapter.canonical_snapshot(rows, 'default'),
                                   observed_at='2024-03-04T' + time + 'Z', timezone='UTC')
        state = observe(None, [row(), zero, excluded], '10:00:00')
        state = observe(state, [row(), zero, excluded], '10:10:00')
        public = history.project_history(state)
        self.assertEqual(len(public['sources']), 2)
        self.assertEqual(public['coverage']['events']['measured_zero'], 2)
        self.assertEqual([c['counts']['total_tokens'] for c in public['buckets']['day'][0]['sources']], [0, 0])
        decreased = row(input_tokens=10, output_tokens=2, cache_read_tokens=0,
                        cache_write_tokens=0, reasoning_tokens=1, api_call_count=0)
        state = observe(state, [decreased], '10:20:00')
        self.assertEqual(state['coverage']['events']['reset'], 1)
        self.assertEqual(state['coverage']['events']['missing'], 1)
        self.assertEqual(state['coverage']['observed']['total_tokens'], 0)
        state = observe(state, [row(input_tokens=15, output_tokens=3, cache_read_tokens=0,
                                    cache_write_tokens=0, reasoning_tokens=1, api_call_count=1), zero], '10:30:00')
        self.assertEqual(state['coverage']['events']['reappeared'], 1)
        self.assertEqual(state['coverage']['observed']['total_tokens'], 6)
        state = history.observe(state, adapter.canonical_snapshot([row(input_tokens=25, output_tokens=3,
                                cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=1, api_call_count=1), zero], 'default'),
                                observed_at='2024-03-05T10:30:00Z', timezone='UTC')
        self.assertEqual(state['coverage']['events']['gap'], 2)
        self.assertEqual(state['coverage']['observed']['total_tokens'], 16)
        for grain in ('day', 'week', 'month', 'year'):
            self.assertEqual(state['coverage']['unallocated'][grain]['total_tokens'], 10)
        self.assertTrue(any(event['status'] == 'gap' and event['counts']['total_tokens'] == 10
                            for event in state['events']))
        self.assertIsNone(next(event['counts'] for event in state['events'] if event['status'] == 'reset'))
        self.assertTrue(history.project_history(state)['coverage']['missingHistory'])
        empty = history.project_history(observe(None, [], '10:00:00'))
        self.assertEqual(empty['sources'], [])
        self.assertEqual(empty['coverage']['status'], 'unknown')
        with self.assertRaises(ValueError):
            adapter.canonical_snapshot([row(), row()], 'default')

    def test_time_identity_and_invalid_timestamps_fail_without_mutation(self):
        adapter, history = load('hermes_accounting'), load('usage_history')
        snapshot = adapter.canonical_snapshot([row()], 'default')
        state = history.observe(None, snapshot, observed_at='2024-03-04T10:00:00Z', timezone='UTC')
        encoded = json.dumps(state, sort_keys=True)
        repeated = history.observe(state, snapshot, observed_at='2024-03-04T11:00:00+01:00', timezone='UTC')
        self.assertEqual(repeated, state)
        for when, zone, candidate in [
            ('2024-03-04T09:59:59Z', 'UTC', snapshot),
            ('2024-03-04T10:00:00Z', 'UTC', adapter.canonical_snapshot([row(input_tokens=101)], 'default')),
            ('2024-03-04T10:10:00Z', 'America/New_York', snapshot),
            ('2024-03-04T10:10:00Z', 'UTC', adapter.canonical_snapshot([row()], 'work')),
        ]:
            with self.subTest(when=when, zone=zone), self.assertRaises(ValueError):
                history.observe(state, candidate, observed_at=when, timezone=zone)
            self.assertEqual(json.dumps(state, sort_keys=True), encoded)
        for when in ('2024-03-04T10:00:00', '2024-02-30T10:00:00Z', '2024-01-01T25:00:00Z',
                     '2024-01-01T00:00:00.1Z', '2024-01-01T00:00:60Z', '1969-12-31T23:59:59Z',
                     '9999-12-31T23:59:59Z', '2024-01-01T00:00:00+24:00', '2024-01-01T00:00:00-00:00'):
            with self.subTest(when=when), self.assertRaises(ValueError):
                history.observe(None, snapshot, observed_at=when, timezone='UTC')
        for zone in ('../UTC', '/etc/passwd', 'NO_SUCH_ZONE', '', 'UTC\n'):
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                history.observe(None, snapshot, observed_at='2024-01-01T00:00:00Z', timezone=zone)

    def test_retention_source_cell_and_event_limits_preserve_coverage(self):
        from datetime import datetime, timedelta, timezone
        adapter, history = load('hermes_accounting'), load('usage_history')
        many = [row(session_id=str(i)) for i in range(adapter.MAX_HISTORY_SOURCES + 1)]
        with self.assertRaises(ValueError):
            adapter.canonical_snapshot(many, 'default')
        def snapshot(n, sources=1):
            return adapter.canonical_snapshot([row(session_id=str(i), input_tokens=n, output_tokens=0,
                            cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
                            api_call_count=n) for i in range(sources)], 'default')
        state, now = None, datetime(2020, 1, 1, 10, tzinfo=timezone.utc)
        for index in range(70):
            year, month = 2020 + index // 12, 1 + index % 12
            now = now.replace(year=year, month=month)
            state = history.observe(state, snapshot(index * 2), observed_at=now.isoformat(), timezone='UTC')
            state = history.observe(state, snapshot(index * 2 + 1),
                                    observed_at=(now + timedelta(minutes=10)).isoformat(), timezone='UTC')
        for grain, limit in history.MAX_BUCKETS.items():
            self.assertEqual(len(state['buckets'][grain]), limit)
            self.assertGreater(state['coverage']['pruned'][grain]['total_tokens'], 0)
        state, now = None, datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
        for index in range(26):
            state = history.observe(state, snapshot(index * 2, 128),
                                    observed_at=(now + timedelta(days=index)).isoformat(), timezone='UTC')
            state = history.observe(state, snapshot(index * 2 + 1, 128),
                                    observed_at=(now + timedelta(days=index, minutes=10)).isoformat(), timezone='UTC')
        self.assertLessEqual(sum(len(bucket['sources']) for buckets in state['buckets'].values() for bucket in buckets),
                             history.MAX_CELLS)
        self.assertEqual(len(state['events']), history.MAX_EVENTS)
        self.assertGreater(state['coverage']['prunedEvents'], 0)
        for grain in history.GRAINS:
            retained = sum(cell['counts']['total_tokens'] for bucket in state['buckets'][grain]
                           for cell in bucket['sources'].values())
            self.assertEqual(retained + state['coverage']['unallocated'][grain]['total_tokens'] +
                             state['coverage']['pruned'][grain]['total_tokens'],
                             state['coverage']['observed']['total_tokens'])
        self.assertLess(len(json.dumps(state).encode()), history.MAX_STATE_BYTES)

    def test_projection_filters_do_not_mutate_ledger_or_turn_unknown_into_zero(self):
        adapter, history = load('hermes_accounting'), load('usage_history')
        baseline = adapter.canonical_snapshot([row(), row(session_id='other', billing_provider='anthropic',
                                                         billing_base_url='https://api.anthropic.com')], 'default')
        later = adapter.canonical_snapshot([row(input_tokens=110), row(session_id='other', billing_provider='anthropic',
                                    billing_base_url='https://api.anthropic.com', input_tokens=150)], 'default')
        state = history.observe(None, baseline, observed_at='2024-01-01T10:00:00Z', timezone='UTC')
        state = history.observe(state, later, observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        before = json.dumps(state, sort_keys=True)
        filtered = history.project_history(state, providers=['openai-codex'], grain='day')
        self.assertEqual(len(filtered['sources']), 1)
        self.assertEqual(filtered['coverage']['observed']['total_tokens'], 10)
        self.assertEqual(filtered['buckets']['day'][0]['sources'][0]['counts']['total_tokens'], 10)
        self.assertEqual(set(filtered['buckets']), {'day'})
        filtered['sources'][0]['counts']['input_tokens'] = 999
        filtered['buckets']['day'].clear()
        self.assertEqual(json.dumps(state, sort_keys=True), before)
        absent = history.project_history(state, providers=['groq'])
        self.assertEqual(absent['coverage']['status'], 'unknown')
        self.assertEqual(absent['buckets']['day'], [])
        with self.assertRaises(ValueError):
            history.project_history(state, providers=['SECRET_PROVIDER'])

    def test_malformed_persistence_and_lifetime_overflow_fail_closed(self):
        from copy import deepcopy
        adapter, history = load('hermes_accounting'), load('usage_history')
        self.cli([row()], '2024-01-01T10:00:00Z', timezone='UTC')
        state = json.loads(self.state.read_text())
        first = self.output.read_bytes(), self.history.read_bytes()
        cases = [None, [], {'contract': 'bad'}, dict(state, schemaVersion=True),
                 dict(state, PRIVATE_ENDPOINT_SENTINEL='https://secret'), dict(state, generation=-1),
                 dict(state, timezone='../UTC'), dict(state, observedAt='bad')]
        invalid = deepcopy(state)
        next(iter(invalid['sources'].values()))['counts']['total_tokens'] = 9007199254740992
        cases.append(invalid)
        invalid = deepcopy(state)
        next(iter(invalid['sources'].values()))['provider'] = 'PRIVATE_CREDENTIAL_SENTINEL'
        cases.append(invalid)
        invalid = deepcopy(state)
        invalid['coverage']['observed']['total_tokens'] = 1
        cases.append(invalid)
        for candidate in cases:
            with self.subTest(candidate_type=type(candidate).__name__):
                data = json.dumps(candidate).encode()
                self.state.write_bytes(data)
                with self.assertRaises(ValueError):
                    adapter.record_history(adapter.canonical_snapshot([row(input_tokens=101)], 'default'),
                              state_path=self.state, history_output=self.history, output=self.output,
                              observed_at='2024-01-01T10:10:00Z', timezone='UTC')
                self.assertEqual(self.state.read_bytes(), data)
                self.assertEqual((self.output.read_bytes(), self.history.read_bytes()), first)
        self.state.write_bytes(b' ' * (history.MAX_STATE_BYTES + 1))
        with self.assertRaises(ValueError):
            adapter.record_history(adapter.canonical_snapshot([row()], 'default'), state_path=self.state,
                    history_output=self.history, output=self.output, observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        def snap(n):
            return adapter.canonical_snapshot([row(input_tokens=n, output_tokens=0, cache_read_tokens=0,
                                    cache_write_tokens=0, reasoning_tokens=0, api_call_count=0)], 'default')
        state = history.observe(None, snap(0), observed_at='2024-01-01T10:00:00Z', timezone='UTC')
        state = history.observe(state, snap(history.MAX_SAFE), observed_at='2024-01-01T10:01:00Z', timezone='UTC')
        state = history.observe(state, snap(0), observed_at='2024-01-01T10:02:00Z', timezone='UTC')
        before = json.dumps(state, sort_keys=True)
        with self.assertRaises(ValueError):
            history.observe(state, snap(1), observed_at='2024-01-01T10:03:00Z', timezone='UTC')
        self.assertEqual(json.dumps(state, sort_keys=True), before)

    def test_transaction_failure_recovery_and_retry_never_double_count(self):
        from unittest.mock import patch
        adapter = load('hermes_accounting')
        self.cli([row()], '2024-01-01T10:00:00Z', timezone='UTC')
        before_state, before_history = self.state.read_bytes(), self.history.read_bytes()
        snapshot = adapter.canonical_snapshot([row(input_tokens=110)], 'default')
        publish = adapter.publish
        def fail_state(record, output, **kwargs):
            if Path(output) == self.state:
                raise OSError('injected pre-commit storage failure')
            return publish(record, output, **kwargs)
        with patch.object(adapter, 'publish', fail_state), self.assertRaises(OSError):
            adapter.record_history(snapshot, state_path=self.state, history_output=self.history,
                    output=self.output, observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        self.assertEqual(self.state.read_bytes(), before_state)
        self.assertEqual(self.history.read_bytes(), before_history)
        def fail_history(record, output, **kwargs):
            if Path(output) == self.history:
                raise OSError('injected post-commit publication failure')
            return publish(record, output, **kwargs)
        with patch.object(adapter, 'publish', fail_history), self.assertRaises(OSError):
            adapter.record_history(snapshot, state_path=self.state, history_output=self.history,
                    output=self.output, observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        state = json.loads(self.state.read_text())
        self.assertEqual(state['coverage']['observed']['total_tokens'], 10)
        self.assertEqual(state['generation'], 2)
        self.assertEqual(self.history.read_bytes(), before_history)
        argv = [sys.executable, str(ADAPTER), '--recover-history', '--output', str(self.output),
                '--history-state', str(self.state), '--history-output', str(self.history)]
        child = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(self.history.read_text())['coverage']['observed']['total_tokens'], 10)
        recovered = self.state.read_bytes()
        adapter.record_history(snapshot, state_path=self.state, history_output=self.history,
                output=self.output, observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        self.assertEqual(self.state.read_bytes(), recovered)
        later = adapter.canonical_snapshot([row(input_tokens=120)], 'default')
        adapter.record_history(later, state_path=self.state, history_output=self.history,
                output=self.output, observed_at='2024-01-01T10:20:00Z', timezone='UTC')
        self.assertEqual(json.loads(self.history.read_text())['coverage']['observed']['total_tokens'], 20)
        self.assertFalse(list(self.folder.glob('.hermes-accounting-*')))

    def test_cli_input_aliases_to_internal_history_paths_refuse_before_writes(self):
        adapter = load('hermes_accounting')
        payload = json.dumps({'contract': 'hermes-session-model-usage/1',
                              'counterSemantics': 'hermes-canonical/1', 'rows': [row()]}).encode()
        for target in ('state', 'accounting', 'history', 'lock'):
            for spelling in ('absolute', 'relative', 'hardlink'):
                with self.subTest(target=target, spelling=spelling), tempfile.TemporaryDirectory(
                        prefix='input-alias-', dir=self.folder) as directory:
                    directory = Path(directory)
                    paths = {key: directory / (key + '.json') for key in ('state', 'accounting', 'history')}
                    internal = (paths['state'].with_name(paths['state'].name + '.lock')
                                if target == 'lock' else adapter.temporary_path(paths[target]))
                    internal.write_bytes(payload)
                    internal.chmod(0o600)
                    source = internal
                    if spelling == 'hardlink':
                        source = directory / 'input.json'
                        os.link(internal, source)
                    argument = source.name if spelling == 'relative' else str(source)
                    before = {p.name: p.read_bytes() for p in directory.iterdir()}
                    identity = source.stat()
                    argv = [sys.executable, '-B', str(ADAPTER), '--json', argument, '--profile', 'default',
                            '--output', str(paths['accounting']), '--history-state', str(paths['state']),
                            '--history-output', str(paths['history']), '--timezone', 'UTC',
                            '--observed-at', '2024-01-01T10:00:00Z']
                    child = subprocess.run(argv, cwd=directory, capture_output=True, text=True, timeout=10)
                    after = {p.name: p.read_bytes() for p in directory.iterdir()}
                    self.assertEqual(after, before, 'Input aliases must be refused before cleanup or publication')
                    self.assertEqual(child.returncode, 2, child.stderr)
                    current = source.stat()
                    self.assertEqual((current.st_dev, current.st_ino, current.st_mode, current.st_mtime_ns),
                                     (identity.st_dev, identity.st_ino, identity.st_mode, identity.st_mtime_ns))
                    self.assertEqual(child.stderr, 'Hermes accounting rejected input or publication.\n')

    def test_path_alias_symlink_and_changed_export_preimage_refuse(self):
        from unittest.mock import patch
        adapter = load('hermes_accounting')
        self.cli([row()], '2024-01-01T10:00:00Z', timezone='UTC')
        before = self.state.read_bytes(), self.output.read_bytes(), self.history.read_bytes()
        snapshot = adapter.canonical_snapshot([row(input_tokens=110)], 'default')
        alias = self.folder / 'alias.json'
        alias.symlink_to(self.history)
        hardlink = self.folder / 'hardlink.json'
        os.link(self.history, hardlink)
        for target in (alias, self.state, self.output, hardlink, self.folder / 'absent' / 'out.json'):
            with self.subTest(target=target), self.assertRaises((ValueError, OSError)):
                adapter.record_history(snapshot, state_path=self.state, history_output=target,
                        output=self.output, observed_at='2024-01-01T10:10:00Z', timezone='UTC')
            self.assertEqual((self.state.read_bytes(), self.output.read_bytes(), self.history.read_bytes()), before)
        hardlink.unlink()
        publish = adapter.publish
        def changed_preimage(record, output, **kwargs):
            if Path(output) == self.output:
                self.history.write_bytes(b'PRIVATE_EXTERNAL_CHANGE_SENTINEL')
            return publish(record, output, **kwargs)
        with patch.object(adapter, 'publish', changed_preimage), self.assertRaises(ValueError):
            adapter.record_history(snapshot, state_path=self.state, history_output=self.history,
                    output=self.output, observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        state = json.loads(self.state.read_text())
        self.assertEqual(state['coverage']['observed']['total_tokens'], 10)
        changed = self.state.read_bytes(), self.output.read_bytes(), self.history.read_bytes()
        with self.assertRaises(ValueError):
            adapter.recover_history(state_path=self.state, history_output=self.history, output=self.output)
        self.assertEqual((self.state.read_bytes(), self.output.read_bytes(), self.history.read_bytes()), changed)
        self.history.write_bytes(before[2])
        adapter.recover_history(state_path=self.state, history_output=self.history, output=self.output)
        self.assertEqual(json.loads(self.history.read_text())['coverage']['observed']['total_tokens'], 10)
        self.assertNotIn('PRIVATE_EXTERNAL_CHANGE_SENTINEL', self.history.read_text())

    def test_hidden_midnight_rollback_is_not_calendar_containment(self):
        adapter, history = load('hermes_accounting'), load('usage_history')
        state = history.observe(None, adapter.canonical_snapshot([row()], 'default'),
                    observed_at='2009-11-01T02:59:00Z', timezone='America/Goose_Bay')
        state = history.observe(state, adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                    observed_at='2009-11-01T03:29:00Z', timezone='America/Goose_Bay')
        public = history.project_history(state)
        # Both endpoints are Oct 31, but an interior instant is Nov 1.
        self.assertEqual(public['buckets']['day'], [])
        self.assertEqual(public['buckets']['month'], [])
        self.assertEqual(public['coverage']['unallocated']['day']['total_tokens'], 10)
        self.assertEqual(public['buckets']['week'][0]['sources'][0]['counts']['total_tokens'], 10)

    def test_persisted_event_and_snapshot_consistency_and_strict_offset(self):
        from copy import deepcopy
        adapter, history = load('hermes_accounting'), load('usage_history')
        snapshot = adapter.canonical_snapshot([row()], 'default')
        state = history.observe(None, snapshot, observed_at='2024-01-01T10:00:00Z', timezone='UTC')
        state = history.observe(state, adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                                observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        candidates = []
        bad = deepcopy(state)
        bad['events'][-1]['counts'].update(input_tokens=11, prompt_tokens=11, total_tokens=11)
        candidates.append(bad)
        bad = deepcopy(state)
        next(iter(bad['sources'].values()))['counts'].update(input_tokens=111, prompt_tokens=146, total_tokens=166)
        candidates.append(bad)
        bad = deepcopy(state)
        bad['events'].append(deepcopy(bad['events'][-1]))
        candidates.append(bad)
        bad = deepcopy(state)
        bad['coverage']['prunedEvents'] = 1
        next(iter(bad['sources'].values()))['coverage']['prunedEvents'] = 1
        candidates.append(bad)
        for candidate in candidates:
            with self.subTest(candidate=candidates.index(candidate)), self.assertRaises(ValueError):
                history.project_history(candidate)
        for when in ('2024-01-01T10:00:00+00:60', '2024-01-01T10:00:00+05:99'):
            with self.subTest(when=when), self.assertRaises(ValueError):
                history.observe(None, snapshot, observed_at=when, timezone='UTC')
        for zone in ('localtime', 'posixrules', 'posix/UTC', 'right/UTC'):
            with self.subTest(zone=zone), self.assertRaises(ValueError):
                history.observe(None, snapshot, observed_at='2024-01-01T10:00:00Z', timezone=zone)

    def test_recovery_syncs_matching_exports_and_replaces_owned_stale_temp(self):
        from unittest.mock import patch
        import stat
        adapter = load('hermes_accounting')
        public_dir = self.folder / 'public'
        public_dir.mkdir(mode=0o700)
        self.history = public_dir / 'history.json'
        self.cli([row()], '2024-01-01T10:00:00Z', timezone='UTC')
        synced_dirs = set()
        fsync = adapter.os.fsync
        def record_sync(fd):
            details = os.fstat(fd)
            if stat.S_ISDIR(details.st_mode):
                synced_dirs.add((details.st_dev, details.st_ino))
            return fsync(fd)
        with patch.object(adapter.os, 'fsync', record_sync):
            adapter.recover_history(state_path=self.state, history_output=self.history, output=self.output)
        details = public_dir.stat()
        self.assertIn((details.st_dev, details.st_ino), synced_dirs)
        temporary = public_dir / ('.hermes-accounting-' + __import__('hashlib').sha256(str(self.history).encode()).hexdigest()[:24] + '.tmp')
        temporary.write_bytes(b'incomplete owned publication')
        temporary.chmod(0o600)
        adapter.record_history(adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                    state_path=self.state, history_output=self.history, output=self.output,
                    observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        self.assertFalse(temporary.exists())
        self.assertEqual(json.loads(self.history.read_text())['coverage']['observed']['total_tokens'], 10)
        self.assertFalse(list(public_dir.glob('.hermes-accounting-*')))

    def test_owned_stale_publication_temp_is_reused_not_accumulated(self):
        import hashlib
        adapter = load('hermes_accounting')
        self.cli([row()], '2024-01-01T10:00:00Z', timezone='UTC')
        temporary = self.folder / ('.hermes-accounting-' + hashlib.sha256(str(self.history).encode()).hexdigest()[:24] + '.tmp')
        temporary.write_bytes(b'incomplete owned publication')
        temporary.chmod(0o600)
        adapter.record_history(adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                    state_path=self.state, history_output=self.history, output=self.output,
                    observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        self.assertFalse(temporary.exists())
        self.assertFalse(list(self.folder.glob('.hermes-accounting-*')))

    def test_zero_token_call_is_measured_zero_and_late_fsync_commit_recovers(self):
        from unittest.mock import patch
        import stat
        adapter, history = load('hermes_accounting'), load('usage_history')
        state = history.observe(None, adapter.canonical_snapshot([row()], 'default'),
                                observed_at='2024-01-01T10:00:00Z', timezone='UTC')
        state = history.observe(state, adapter.canonical_snapshot([row(api_call_count=2)], 'default'),
                                observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        self.assertEqual(state['coverage']['events']['measured_zero'], 1)
        self.assertEqual(state['coverage']['observed']['api_call_count'], 1)
        self.cli([row()], '2024-01-01T10:00:00Z', timezone='UTC')
        before = self.state.read_bytes()
        fsync = adapter.os.fsync
        def late_failure(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode) and self.state.read_bytes() != before:
                raise OSError('injected directory fsync failure after replace')
            return fsync(fd)
        with patch.object(adapter.os, 'fsync', late_failure), self.assertRaises(OSError):
            adapter.record_history(adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                    state_path=self.state, history_output=self.history, output=self.output,
                    observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        self.assertEqual(json.loads(self.state.read_text())['coverage']['observed']['total_tokens'], 10)
        adapter.recover_history(state_path=self.state, history_output=self.history, output=self.output)
        self.assertEqual(json.loads(self.history.read_text())['coverage']['observed']['total_tokens'], 10)

    def test_recorded_bucket_survives_backward_local_date_without_backfill(self):
        from copy import deepcopy
        adapter, history = load('hermes_accounting'), load('usage_history')
        state = history.observe(None, adapter.canonical_snapshot([row()], 'default'),
                    observed_at='2009-11-01T03:00:10Z', timezone='America/Goose_Bay')
        state = history.observe(state, adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                    observed_at='2009-11-01T03:00:30Z', timezone='America/Goose_Bay')
        state = history.observe(state, adapter.canonical_snapshot([row(input_tokens=120)], 'default'),
                    observed_at='2009-11-01T03:29:00Z', timezone='America/Goose_Bay')
        public = history.project_history(state)
        self.assertEqual(public['buckets']['day'][0]['key'], '2009-11-01')
        self.assertEqual(public['buckets']['day'][0]['sources'][0]['counts']['total_tokens'], 10)
        self.assertEqual(public['coverage']['unallocated']['day']['total_tokens'], 10)
        invalid = deepcopy(state)
        invalid['buckets']['day'][0]['key'] = '1999-01-01'
        with self.assertRaises(ValueError):
            history.project_history(invalid)

    def test_bucket_before_recording_range_is_rejected(self):
        from copy import deepcopy
        adapter, history = load('hermes_accounting'), load('usage_history')
        state = history.observe(None, adapter.canonical_snapshot([row()], 'default'),
                    observed_at='2024-01-01T10:00:00Z', timezone='UTC')
        state = history.observe(state, adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                    observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        invalid = deepcopy(state)
        invalid['buckets']['day'][0]['key'] = '1999-01-01'
        with self.assertRaises(ValueError):
            history.project_history(invalid)

    def test_sqlite_history_cli_lock_and_generic_refusals(self):
        import fcntl
        import sqlite3
        adapter = load('hermes_accounting')
        snapshot_file = self.folder / 'offline.sqlite'
        columns = adapter.COLUMNS
        with closing(sqlite3.connect(snapshot_file)) as db, db:
            db.execute('CREATE TABLE session_model_usage (' + ','.join(
                field + (' INTEGER' if field in adapter.COUNTERS else
                         ' REAL' if field in ('first_seen', 'last_seen') else ' TEXT')
                for field in columns) + ')')
            usage = row()
            db.execute('INSERT INTO session_model_usage VALUES (' + ','.join('?' for _ in columns) + ')',
                       tuple(usage[field] for field in columns))
        snapshot_file.chmod(0o400)
        original = snapshot_file.read_bytes()
        argv = [sys.executable, str(ADAPTER), '--sqlite-snapshot', str(snapshot_file), '--profile', 'default',
                '--output', str(self.output), '--history-state', str(self.state), '--history-output', str(self.history),
                '--timezone', 'UTC', '--observed-at', '2024-01-01T10:00:00Z']
        child = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(snapshot_file.read_bytes(), original)
        self.assertEqual(json.loads(self.history.read_text())['buckets']['day'], [])
        before = self.state.read_bytes(), self.output.read_bytes(), self.history.read_bytes()
        lock = self.state.with_name(self.state.name + '.lock')
        fd = os.open(lock, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            child = subprocess.run(argv[:-1] + ['2024-01-01T10:10:00Z'], capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 2)
            self.assertEqual(child.stderr, 'Hermes accounting rejected input or publication.\n')
        finally:
            os.close(fd)
        self.assertEqual((self.state.read_bytes(), self.output.read_bytes(), self.history.read_bytes()), before)
        rows = adapter.canonical_snapshot([row(input_tokens=110)], 'default')
        state = adapter.record_history(rows, state_path=self.state, history_output=self.history,
                    output=self.output, observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        self.assertEqual(state['coverage']['observed']['total_tokens'], 10)
        self.input.write_text(json.dumps({'contract': 'hermes-session-model-usage/1',
                'counterSemantics': 'hermes-canonical/1', 'rows': [row(input_tokens=True)]}))
        argv[2:4] = ['--json', str(self.input)]
        child = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 2)
        self.assertNotIn('PRIVATE_', child.stderr)
        self.assertEqual(json.loads(self.state.read_text())['generation'], 2)

    def test_gap_edge_reasoning_revision_and_lifetime_source_bound_controls(self):
        from copy import deepcopy
        adapter, history = load('hermes_accounting'), load('usage_history')
        self.assertEqual(adapter.CLOUD_PROVIDERS, history.PROVIDERS)
        first = history.observe(None, adapter.canonical_snapshot([row()], 'default'),
                                observed_at='2024-01-01T10:00:00Z', timezone='UTC')
        inclusive = history.observe(first, adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                                observed_at='2024-01-01T16:00:00Z', timezone='UTC')
        self.assertEqual(inclusive['coverage']['events']['delta'], 1)
        gap = history.observe(first, adapter.canonical_snapshot([row(input_tokens=110)], 'default'),
                                observed_at='2024-01-01T16:00:01Z', timezone='UTC')
        self.assertEqual(gap['coverage']['events']['gap'], 1)
        revision = history.observe(first, adapter.canonical_snapshot([row(reasoning_tokens=8)], 'default'),
                                observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        self.assertEqual(revision['coverage']['events']['reset'], 1)
        self.assertEqual(revision['coverage']['observed']['total_tokens'], 0)
        sources = adapter.canonical_snapshot([row(session_id=str(i)) for i in range(history.MAX_HISTORY_SOURCES)], 'default')
        state = history.observe(None, sources, observed_at='2024-01-01T10:00:00Z', timezone='UTC')
        # Capacity counts retained facts, not a lifetime union. Give every ID a
        # dated cell before requiring a new identity to be refused without loss.
        measured = adapter.canonical_snapshot([row(session_id=str(i), input_tokens=110)
                                               for i in range(history.MAX_HISTORY_SOURCES)], 'default')
        state = history.observe(state, measured, observed_at='2024-01-01T10:05:00Z', timezone='UTC')
        self.assertEqual(len(state['buckets']['day'][-1]['sources']), history.MAX_HISTORY_SOURCES)
        before = deepcopy(state)
        with self.assertRaises(ValueError):
            history.observe(state, adapter.canonical_snapshot([row(session_id='new')], 'default'),
                            observed_at='2024-01-01T10:10:00Z', timezone='UTC')
        self.assertEqual(state, before)
        source = next(iter(inclusive['sources']))
        self.assertEqual(len(history.project_history(inclusive, source_ids=[source], profiles=['default'])['sources']), 1)
        self.assertEqual(history.project_history(inclusive, source_ids=[])['coverage']['status'], 'unknown')

    def test_exact_journal_and_payload_byte_bounds_preserve_last_good(self):
        adapter, history = load('hermes_accounting'), load('usage_history')
        self.assertEqual(adapter.MAX_OUTPUT,16*1024*1024)
        self.assertEqual(history.MAX_STATE_BYTES,adapter.MAX_OUTPUT)
        snapshot=adapter.canonical_snapshot([row(session_id=str(i)) for i in range(129)],'default')
        options=dict(state_path=self.state,history_output=self.history,output=self.output,
                     observed_at='2024-01-01T10:00:00Z',timezone='UTC')
        adapter.record_history(snapshot,**options)
        original=self.state.read_bytes()
        exact=original+b' '*(history.MAX_STATE_BYTES-len(original))
        self.state.write_bytes(exact)
        loaded,_=adapter.read_history_state(history,self.state)
        self.assertEqual(len(loaded['sources']),129)
        before={p.name:p.read_bytes() for p in (self.state,self.history,self.output)}
        self.state.write_bytes(exact+b' ')
        over={p.name:p.read_bytes() for p in (self.state,self.history,self.output)}
        with self.assertRaises(ValueError): adapter.record_history(snapshot,**options)
        self.assertEqual({p.name:p.read_bytes() for p in (self.state,self.history,self.output)},over)
        self.state.write_bytes(before[self.state.name])
        # The exact-cap private preimage remains readable for the next valid commit.
        options['observed_at']='2024-01-01T10:05:00Z'
        adapter.record_history(snapshot,**options)
        self.assertEqual(json.loads(self.state.read_text())['generation'],2)
        # Envelope-size failure before publish cannot overwrite an existing good file.
        target=self.folder/'payload.json'
        envelope=len(adapter.payload_bytes({'padding':''}))
        record={'padding':'x'*(adapter.MAX_OUTPUT-envelope)}
        self.assertEqual(len(adapter.payload_bytes(record)),adapter.MAX_OUTPUT)
        adapter.publish(record,target)
        saved=target.read_bytes(); self.assertEqual(adapter.fingerprint(target)['st_size'],adapter.MAX_OUTPUT)
        with self.assertRaisesRegex(ValueError,'output exceeded bound'):
            adapter.publish({'padding':record['padding']+'x'},target)
        self.assertEqual(target.read_bytes(),saved)
        print('CAPACITY_STORAGE_BYTES_PROOF='+json.dumps({'identities':129,'exact':adapter.MAX_OUTPUT,
            'beyond':adapter.MAX_OUTPUT+1,'journal_refused_without_writes':True,'exact_preimage_next_commit':True,
            'payload_refused_without_overwrite':True}))

    def test_cli_baselines_old_totals_then_dates_only_observed_delta(self):
        first = self.cli([row()], '2024-02-29T15:00:00Z')
        self.assertEqual(first['contract'], 'hermes-observed-history/1')
        self.assertEqual(first['buckets']['day'], [])
        self.assertEqual(first['coverage']['events']['baseline'], 1)
        public = self.cli([row(input_tokens=110, output_tokens=23, cache_read_tokens=32,
                               cache_write_tokens=6, reasoning_tokens=8, api_call_count=2)],
                          '2024-02-29T15:10:00Z')
        bucket = public['buckets']['day'][0]
        self.assertEqual(bucket['key'], '2024-02-29')
        counted = bucket['sources'][0]
        self.assertEqual(counted['counts']['prompt_tokens'], 13)
        self.assertEqual(counted['counts']['total_tokens'], 16)
        self.assertEqual(counted['counts']['reasoning_tokens'], 1)
        self.assertEqual(counted['coveredSeconds'], 600)
        self.assertEqual(counted['intervals'], 1)
        self.assertEqual(public['buckets']['week'][0]['key'], '2024-02-26')
        self.assertEqual(public['buckets']['month'][0]['key'], '2024-02')
        self.assertEqual(public['buckets']['year'][0]['key'], '2024')
        record = json.loads(self.output.read_text())
        self.assertEqual(record['sources'][0]['windows'], {'today': None, 'week': None, 'history': 171})
        for path in (self.state, self.history, self.output):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for private in ('PRIVATE_SESSION_SENTINEL', 'PRIVATE_MODEL_SENTINEL',
                            'PRIVATE_TASK_SENTINEL', 'api.openai.com', 'subscription_included'):
                self.assertNotIn(private, path.read_text())


if __name__ == '__main__':
    unittest.main(verbosity=2)
