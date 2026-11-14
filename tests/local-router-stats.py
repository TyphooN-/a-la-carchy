#!/usr/bin/env python3
"""Offline fixtures: never accesses the real router or desktop."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]/'extras/local-router-stats'
def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE/(name+'.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
c = load('collector')
p = load('stage_plugin')
_spec = importlib.util.spec_from_file_location('agents_fixture', Path(__file__).with_name('agents_fixture.py'))
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)


def tree(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(Path(root).rglob('*')) if path.is_file()}


def epoch(day, hour=12):
    return dt.datetime(2026, 10, day, hour).timestamp()


# Frozen current-native UsageNote interface. These are fixture inputs, not
# generated from stage_plugin's recognition constants.
USAGE_NOTE_FUNCTION = '''  function usageNote(item) {
    if (!item || needsSignIn(item)) return ""
    var status = String(item.usageStatusText || "")
    var stale = item.limitsStale === true || item.stale === true
    if (status === "" && !(stale && String(item.authHelpText || "") !== "")) return ""
    return helpDisplayText(item.authHelpText) || statusDisplayText(status)
  }
'''
USAGE_NOTE_COMPONENT = '''  component UsageNote: Text {
    id: noteMark
    property var record: null
    readonly property string message: root.usageNote(record)
    visible: message !== ""
    text: "󰋽"
    textFormat: Text.PlainText
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption

    HoverHandler { id: noteHover }

    PanelToolTip {
      visible: noteHover.hovered && noteMark.visible
      // Capped through implicitWidth, which the tooltip centers itself by.
      implicitWidth: Math.min(implicitContentWidth + leftPadding + rightPadding, Style.space(320))
      text: noteMark.message
    }
  }
'''
USAGE_NOTE_PROVIDER = '''      UsageNote {
        anchors.left: sectionPlan.visible ? sectionPlan.right : sectionName.right
        anchors.leftMargin: Style.space(8)
        anchors.verticalCenter: sectionName.verticalCenter
        visible: !section.multi && message !== ""
        record: section.provider
      }
'''
USAGE_NOTE_ACCOUNT = '''        UsageNote {
          id: accountNote
          record: head.account
        }
'''


def current_limits_fixture(root):
    """Complete synthetic limits scaffold with the current note interface."""
    source = fixture.make_source(root, 'limits')
    text = (source/'Panel.qml').read_text()
    begin = text.index('  function otherTrouble(item) {')
    end = text.index('  function planLabel(p) {', begin)
    text = text[:begin] + USAGE_NOTE_FUNCTION + '\n' + text[end:]
    begin = text.index('    Text {\n      visible: !section.multi && root.otherTrouble(')
    end = text.index('    Column {\n      visible: !section.multi', begin)
    text = text[:begin] + text[end:]
    header = '        text: section.provider ? section.provider.providerName : ""\n      }\n'
    assert text.count(header) == 1
    text = text.replace(header, header + USAGE_NOTE_PROVIDER, 1)
    component = ('  component AccountHeader: Item {\n    id: head\n'
                 '    property var account: null\n    Row {\n' + USAGE_NOTE_ACCOUNT + '    }\n  }\n')
    text = text[:-2] + component + USAGE_NOTE_COMPONENT + '}\n'
    (source/'Panel.qml').write_text(text)
    return source


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = c.Ledger(self.root/'stats.sqlite3')

    def tearDown(self):
        self.ledger.db.close()
        self.tmp.cleanup()

    def test_daily_accumulation_overflow_rolls_back_all_retained_rows(self):
        maximum = 2**63 - 1
        for column in (0, 1):
            for increment in (1, 2):
                with self.subTest(column=column, increment=increment):
                    ledger = c.Ledger(self.root/f'overflow-{column}-{increment}.sqlite3')
                    try:
                        large = [0, 0]
                        large[column] = maximum
                        step = [0, 0]
                        step[column] = increment
                        ledger.observe('model', 'old-worker', (0, 0), epoch(2))
                        ledger.observe('model', 'old-worker', tuple(large), epoch(2)+5)
                        ledger.observe('model', 'new-worker', (0, 0), epoch(2)+10)
                        before = list(ledger.db.iterdump())
                        with self.assertRaisesRegex(ValueError, 'accumulated counters'):
                            ledger.observe('model', 'new-worker', tuple(step), epoch(2)+15)
                        self.assertEqual(list(ledger.db.iterdump()), before,
                                         'refusal preserves daily rows, snapshots, coverage and retention')
                        self.assertEqual(tuple(ledger.db.execute('SELECT typeof(input),typeof(output) FROM daily').fetchone()),
                                         ('integer', 'integer'))
                    finally:
                        ledger.db.close()

    def test_daily_accumulation_exact_boundary_remains_integer(self):
        maximum = 2**63 - 1
        for column in (0, 1):
            with self.subTest(column=column):
                ledger = c.Ledger(self.root/f'boundary-{column}.sqlite3')
                try:
                    large, step = [0, 0], [0, 0]
                    large[column], step[column] = maximum-2, 2
                    ledger.observe('model', 'old-worker', (0, 0), epoch(2))
                    ledger.observe('model', 'old-worker', tuple(large), epoch(2)+5)
                    ledger.observe('model', 'new-worker', (0, 0), epoch(2)+10)
                    self.assertEqual(ledger.observe('model', 'new-worker', tuple(step), epoch(2)+15), tuple(step))
                    row = ledger.db.execute('SELECT input,output,typeof(input),typeof(output) FROM daily').fetchone()
                    expected = [0, 0]
                    expected[column] = maximum
                    self.assertEqual(tuple(row), (*expected, 'integer', 'integer'))
                finally:
                    ledger.db.close()

    def test_noninteger_retained_daily_value_is_not_advanced_or_erased(self):
        for field in ('input', 'output'):
            with self.subTest(field=field):
                ledger = c.Ledger(self.root/f'corrupt-{field}.sqlite3')
                try:
                    ledger.observe('model', 'worker', (0, 0), epoch(2))
                    with ledger.db:
                        # Fixed fixture column names only; never interpolated user input.
                        ledger.db.execute('UPDATE daily SET '+field+'=1.5')
                    before = list(ledger.db.iterdump())
                    with self.assertRaisesRegex(ValueError, 'accumulated counters'):
                        ledger.observe('model', 'worker', (1, 1), epoch(2)+5)
                    self.assertEqual(list(ledger.db.iterdump()), before)
                finally:
                    ledger.db.close()

    def test_counter_validation(self):
        # Integral Prometheus floats/scientific notation remain supported.
        self.assertEqual(c.counters('llamacpp:prompt_tokens_total 1e2\nllamacpp:tokens_predicted_total 4'), (100, 4))
        for index, name in enumerate(c.NAMES):
            for raw, expected in [('0', 0), ('+0', 0), ('-0', 0), ('.0', 0),
                                  ('100', 100), ('+100', 100), ('100.0', 100),
                                  ('1e2', 100), ('1.00E+2', 100),
                                  (str(2**63 - 1), 2**63 - 1)]:
                with self.subTest(name=name, valid=raw):
                    lines = [f'{key} {raw if key == name else "4"}' for key in c.NAMES]
                    values = [4, 4]
                    values[index] = expected
                    self.assertEqual(c.counters('\n'.join(lines)), tuple(values))
            for bad in ['NaN', 'Infinity', '-1', '0.5', str(2**63),
                        '\u0661', '1_000', '1e-10000', '1e10000', '1e', '+']:
                with self.subTest(name=name, bad=bad), self.assertRaises(ValueError):
                    c.counters('\n'.join(f'{key} {bad if key == name else "4"}' for key in c.NAMES))
        for bad in ['', 'llamacpp:prompt_tokens_total 1', 'llamacpp:prompt_tokens_total 1\nllamacpp:prompt_tokens_total 2\nllamacpp:tokens_predicted_total 4']:
            with self.assertRaises(ValueError): c.counters(bad)

    def test_invalid_counter_sample_preserves_snapshots_and_daily_totals(self):
        self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2))
        self.ledger.observe('heretic', 'worker1', (120, 25), epoch(2)+5)
        before = list(self.ledger.db.iterdump())
        listing = json.dumps({'data': [{'id': 'heretic', 'status': {'value': 'loaded'}}]})
        for name in c.NAMES:
            for bad in ('\u0661', '1_000', '1e-10000', '1e10000', 'NaN', 'inf', str(2**63)):
                with self.subTest(name=name, bad=bad):
                    metrics = '\n'.join(f'{key} {bad if key == name else "130"}' for key in c.NAMES)
                    with patch.object(c, 'get', side_effect=[listing, metrics]) as get, \
                            patch.object(c, 'worker', return_value=('worker1', 9001)), \
                            patch.object(c, 'router_pid', return_value=123):
                        status = c.sample(self.ledger, epoch(2)+10)
                    self.assertIn('Collection unavailable (ValueError)', status)
                    self.assertEqual(get.call_args_list[1].args, (9001, '/metrics'))
                    self.assertEqual(list(self.ledger.db.iterdump()), before)
        metrics = 'llamacpp:prompt_tokens_total 1.30e2\nllamacpp:tokens_predicted_total +30'
        with patch.object(c, 'get', side_effect=[listing, metrics]), \
                patch.object(c, 'worker', return_value=('worker1', 9001)), \
                patch.object(c, 'router_pid', return_value=123):
            self.assertIn('Sampled tokens', c.sample(self.ledger, epoch(2)+15))
        self.assertEqual(self.ledger.record(epoch(2)+15, 'sampled')['todayTotalTokens'], 40)

    def test_first_sample_is_baseline_not_backfill(self):
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2)), (0, 0))
        record = self.ledger.record(epoch(2), 'sampled')
        self.assertEqual(record['todayTotalTokens'], 0)
        self.assertIsNone(record['recentDays'][0]['messageCount'])
        self.assertFalse(record['recentDays'][0]['recorded'])
        self.assertTrue(record['recentDays'][-1]['recorded'])

    def test_incremental_and_duplicate_poll(self):
        self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2))
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (120, 25), epoch(2)+5), (20, 5))
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (120, 25), epoch(2)+10), (0, 0))
        self.assertEqual(self.ledger.record(epoch(2)+10, 'sampled')['todayTotalTokens'], 25)

    def test_shared_worker_alias_order_does_not_double_count(self):
        # Exercise real sampling and the durable SQLite ledger; only router I/O
        # is mocked. A poll-local seen set cannot retire the other alias's
        # comparison baseline when the loaded listing changes order.
        orders = [('A', 'B'), ('B', 'A'), ('A', 'B'), ('B', 'A')]
        for i, aliases in enumerate(orders):
            listing = json.dumps({'data': [
                {'id': alias, 'status': {'value': 'loaded'}} for alias in aliases]})
            metrics = ('llamacpp:prompt_tokens_total %d\n'
                       'llamacpp:tokens_predicted_total %d') % (100 + 10*i, 10 + 3*i)
            with patch.object(c, 'get', side_effect=[listing, metrics]) as get, \
                    patch.object(c, 'worker', return_value=('shared', 9001)), \
                    patch.object(c, 'router_pid', return_value=123):
                c.sample(self.ledger, epoch(2) + 5*i)
                self.assertEqual(get.call_count, 2)  # one metrics read per worker
        before = self.ledger.record(epoch(2)+15, 'sampled')['todayTotalTokens']
        self.assertLessEqual(before, (130-100) + (19-10),
                             'aliases credited overlapping worker intervals')
        # A conservative ownership handoff may leave gaps, but a stable owner
        # must still credit its next real increment rather than disable totals.
        self.assertEqual(self.ledger.observe('B', 'shared', (140, 22), epoch(2)+20), (10, 3))
        self.assertEqual(self.ledger.record(epoch(2)+20, 'sampled')['todayTotalTokens'], before+13)

    def test_shared_alias_handoff_refuses_older_sample_without_mutation(self):
        self.ledger.observe('A', 'shared', (100, 10), epoch(2))
        self.ledger.observe('A', 'shared', (120, 20), epoch(2)+10)
        before = list(self.ledger.db.iterdump())
        with self.assertRaisesRegex(ValueError, 'non-monotonic sample time'):
            self.ledger.observe('B', 'shared', (110, 15), epoch(2)+9)
        self.assertEqual(list(self.ledger.db.iterdump()), before)
        self.assertEqual(self.ledger.observe('A', 'shared', (130, 23), epoch(2)+15), (10, 3))

    def test_alias_membership_handoffs_persist_without_erasing_history(self):
        self.ledger.observe('A', 'shared', (100, 10), epoch(2))
        self.ledger.observe('A', 'shared', (110, 13), epoch(2)+5)
        self.ledger.observe('C', 'independent', (1000, 50), epoch(2))
        self.ledger.observe('C', 'independent', (1007, 52), epoch(2)+5)
        self.ledger.db.close()
        self.ledger = c.Ledger(self.root/'stats.sqlite3')
        self.assertEqual(self.ledger.observe('B', 'shared', (120, 16), epoch(2)+10), (0, 0))
        self.assertEqual(self.ledger.observe('B', 'shared', (130, 19), epoch(2)+15), (10, 3))
        self.ledger.db.close()
        self.ledger = c.Ledger(self.root/'stats.sqlite3')
        self.assertEqual(self.ledger.observe('A', 'shared', (140, 22), epoch(2)+20), (0, 0))
        self.assertEqual(self.ledger.observe('A', 'shared', (150, 25), epoch(2)+25), (10, 3))
        self.assertEqual(self.ledger.observe('C', 'independent', (1008, 53), epoch(2)+25), (1, 1))
        record = self.ledger.record(epoch(2)+25, 'sampled')
        self.assertEqual(record['todayTokensByModel'], {'A':13*2, 'B':13, 'C':9+2})
        self.assertEqual(record['todayTotalTokens'], 13*3+9+2)
        self.assertEqual(self.ledger.db.execute('SELECT model FROM snapshots WHERE epoch=?', ('shared',)).fetchall(), [('A',)])

    def test_legacy_overlapping_alias_baselines_are_retired_not_backfilled(self):
        with self.ledger.db:
            self.ledger.db.executemany('INSERT INTO snapshots VALUES (?,?,?,?,?)', [
                ('A', 'shared', 100, 10, epoch(2)),
                ('B', 'shared', 110, 13, epoch(2)+5)])
            self.ledger.db.executemany('INSERT INTO daily VALUES (?,?,?,?)', [
                ('2026-10-02', 'A', 5, 0), ('2026-10-02', 'B', 7, 0)])
        self.ledger.db.close()
        self.ledger = c.Ledger(self.root/'stats.sqlite3')
        self.assertEqual(self.ledger.observe('A', 'shared', (120, 16), epoch(2)+10), (0, 0))
        self.assertEqual(self.ledger.record(epoch(2)+10, 'sampled')['todayTotalTokens'], 5+7)
        self.assertEqual(self.ledger.observe('A', 'shared', (130, 19), epoch(2)+15), (10, 3))
        record = self.ledger.record(epoch(2)+15, 'sampled')
        self.assertEqual(record['todayTokensByModel'], {'A':5+13, 'B':7})
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 2)
        self.assertEqual(self.ledger.db.execute('SELECT model FROM snapshots WHERE epoch=?', ('shared',)).fetchall(), [('A',)])

    def test_alias_transfer_preserves_retained_model_identity_bound(self):
        self.ledger.observe('A', 'shared', (100, 10), epoch(2))
        with self.ledger.db:
            self.ledger.db.execute('INSERT INTO snapshots VALUES (?,?,?,?,?)',
                                   ('B', 'old-worker-B', 0, 0, epoch(2)))
            self.ledger.db.executemany('INSERT INTO snapshots VALUES (?,?,?,?,?)',
                                      [(f'm{i}', f'worker{i}', 0, 0, epoch(2)) for i in range(254)])
        self.assertEqual(self.ledger.observe('B', 'shared', (110, 13), epoch(2)+5), (0, 0))
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0], 256)
        self.assertEqual(self.ledger.observe('m0', 'worker0', (7, 2), epoch(2)+5), (7, 2))
        # Known aliases may rotate without losing the bounded identity registry.
        self.assertEqual(self.ledger.observe('A', 'shared', (120, 16), epoch(2)+10), (0, 0))
        before = list(self.ledger.db.iterdump())
        for identity in ('shared', 'new-worker'):
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(ValueError, 'retained model bound exceeded'):
                    self.ledger.observe('overflow', identity, (130, 19), epoch(2)+15)
                self.assertEqual(list(self.ledger.db.iterdump()), before)

    def test_far_future_alias_owner_can_rebaseline_under_clock_policy(self):
        self.ledger.observe('A', 'shared', (100, 10), epoch(2)+c.CLOCK_SLACK+10)
        self.assertEqual(self.ledger.observe('B', 'shared', (110, 13), epoch(2)), (0, 0))
        self.assertEqual(self.ledger.observe('B', 'shared', (120, 16), epoch(2)+5), (10, 3))

    def test_worker_restart_even_with_larger_counters(self):
        self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2))
        self.assertEqual(self.ledger.observe('heretic', 'worker2', (900, 100), epoch(2)+5), (0, 0))
        self.assertEqual(self.ledger.observe('heretic', 'worker2', (910, 103), epoch(2)+10), (10, 3))

    def test_counter_regression_rebaselines_both(self):
        self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2))
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (50, 30), epoch(2)+5), (0, 0))

    def test_calendar_and_model_breakdown(self):
        for model in ['heretic', 'qwopus']:
            self.ledger.observe(model, model, (0, 0), epoch(2))
        self.ledger.observe('heretic', 'heretic', (12, 5), epoch(2)+5)
        # A day-long gap cannot be attributed to one calendar day. Rebaseline
        # before measuring a fresh, dated interval on the second day.
        self.ledger.observe('qwopus', 'qwopus', (30, 9), epoch(3))
        self.ledger.observe('qwopus', 'qwopus', (60, 18), epoch(3)+5)
        record = self.ledger.record(epoch(3)+5, 'sampled')
        self.assertEqual(record['todayTotalTokens'], 39)
        self.assertEqual(record['recentDays'][-2]['messageCount'], 17)
        self.assertEqual(record['modelUsage']['heretic']['inputTokens'], 12)
        self.assertEqual(len(record['recentDays']), 7)
        self.assertEqual(record['limits'], [])
        self.assertFalse(record['hasPromptStats'])

    def test_persistence(self):
        self.ledger.observe('heretic', 'worker1', (0, 0), epoch(2))
        self.ledger.observe('heretic', 'worker1', (20, 5), epoch(2)+5)
        self.ledger.db.close()
        self.ledger = c.Ledger(self.root/'stats.sqlite3')
        self.assertEqual(self.ledger.record(epoch(2), 'sampled')['todayTotalTokens'], 25)
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (30, 7), epoch(2)+10), (10, 2))

    def test_invalid_event_no_change(self):
        for model, vals in [('', (1, 2)), ('bad\nmodel', (1, 2)), ('heretic', (-1, 2)), ('heretic', (True, 2))]:
            with self.assertRaises(ValueError): self.ledger.observe(model, 'pid', vals, epoch(2))
        self.assertFalse(self.ledger.record(epoch(2), 'pending')['hasLocalStats'])

    def test_out_of_order_sample(self):
        self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2))
        with self.assertRaises(ValueError): self.ledger.observe('heretic', 'worker1', (200, 30), epoch(2)-1)
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (120, 25), epoch(2)+5), (20, 5))

    def test_no_loaded_models_no_metrics_or_systemctl(self):
        with patch.object(c, 'get', return_value=json.dumps({'data':[{'id':'heretic','status':{'value':'unloaded'}}]})) as get, patch.object(c.subprocess, 'run') as run:
            self.assertIn('No loaded model', c.sample(self.ledger, epoch(2)))
            get.assert_called_once_with(c.ROUTER_PORT, '/models')
            run.assert_not_called()

    def test_metrics_use_worker_listener_and_identity_checked(self):
        listing = json.dumps({'data':[{'id':'model/alias','status':{'value':'loaded'}}]})
        metrics = 'llamacpp:prompt_tokens_total 100\nllamacpp:tokens_predicted_total 20'
        class Result: stdout = '123\n'
        with patch.object(c, 'get', side_effect=[listing, metrics]) as get, patch.object(c, 'worker', return_value=('stable', 9001)), patch.object(c.subprocess, 'run', return_value=Result()):
            c.sample(self.ledger, epoch(2))
            self.assertEqual(get.call_args_list[1].args, (9001, '/metrics'))
        with patch.object(c, 'get', side_effect=[listing, metrics]), patch.object(c, 'worker', side_effect=[('old', 9001), ('new', 9001)]), patch.object(c.subprocess, 'run', return_value=Result()):
            self.assertIn('ValueError', c.sample(self.ledger, epoch(2)+5))
            self.assertEqual(self.ledger.record(epoch(2)+5, 'sampled')['todayTotalTokens'], 0)

    def test_atomic_publication_and_no_prompt_fields(self):
        record = self.ledger.record(epoch(2), 'pending')
        target = self.root/'local.json'
        target.write_text('old')
        c.publish(target, record)
        self.assertEqual(json.loads(target.read_text()), record)
        self.assertEqual(list(self.root.glob('.local-*')), [])
        for key in ['prompt', 'messages', 'response', 'credentials', 'api_key']:
            self.assertNotIn(key, record)

    def test_history_file_collision_is_refused(self):
        source = fixture.make_source(self.root/'collision', 'limits')
        (source/'History.js').write_text('// native source must not be overwritten')
        with self.assertRaises(ValueError):
            p.stage(source, self.root/'never-history', 'fixture.agents')
        self.assertFalse((self.root/'never-history').exists())

    def test_clock_file_collision_is_refused_before_staging(self):
        source = fixture.make_source(self.root/'clock-collision', 'limits')
        (source/'CalendarClock.py').write_text('native helper must not be overwritten')
        before = tree(source)
        with self.assertRaises(ValueError):
            p.stage(source, self.root/'never-clock', 'fixture.agents')
        self.assertFalse((self.root/'never-clock').exists())
        self.assertEqual(tree(source), before)

    def test_native_local_marks_are_kept_not_replaced(self):
        native = {'local.svg': b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><title>dark</title></svg>\n',
                  'local-light.svg': b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><title>light</title></svg>\n'}
        for shipped in (('local.svg', 'local-light.svg'), ('local.svg',), ('local-light.svg',)):
            with self.subTest(shipped=shipped):
                source = fixture.make_source(self.root/('marks-' + str(len(shipped)) + shipped[0])/'agents', 'limits')
                for name in shipped:
                    (source/'assets'/name).write_bytes(native[name])
                before = tree(source)
                self.assertEqual(p.render(source, 'fixture.agents')['assets'], {}, 'no clone mark beside a native one')
                target = source.parent/'staged'
                p.stage(source, target, 'fixture.agents')
                after = tree(target)
                self.assertEqual(tree(source), before)
                self.assertEqual({name for name in after if name.startswith('assets/local')}, {'assets/' + n for n in shipped})
                for name in shipped:
                    self.assertEqual(after['assets/' + name], native[name], 'native Local mark replaced: ' + name)
        # A native mark that appears after rendering is refused, never overwritten.
        source = fixture.make_source(self.root/'marks-race'/'agents', 'limits')
        rendered = p.render(source, 'fixture.agents')
        self.assertEqual(rendered['assets'], p.LOCAL_ASSETS)
        (source/'assets/local-light.svg').write_bytes(native['local-light.svg'])
        before = tree(source)
        target = source.parent/'staged'
        with self.assertRaises(FileExistsError):
            p.write(source, target, rendered)
        self.assertFalse(target.exists())
        self.assertEqual(sorted(path.name for path in target.parent.iterdir()), ['agents'], 'no staging leftovers')
        self.assertEqual(tree(source), before)

    def test_staged_calendar_clock_executes_as_an_isolated_helper(self):
        import datetime
        import subprocess
        import sys
        source = fixture.make_source(self.root/'clock-source', 'limits')
        target = self.root/'clock-target'
        before = tree(source)
        p.stage(source, target, 'fixture.agents')
        moment = datetime.datetime(2026, 11, 5, 12, tzinfo=datetime.timezone.utc)
        environment = dict(os.environ, TZ='America/New_York')
        result = subprocess.run([sys.executable, '-I', '-B', str(target/'CalendarClock.py'),
                                 '--timezone', 'America/New_York', '--now', str(moment.timestamp())],
                                env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['state'], 'ready')
        self.assertEqual(value['timezone'], 'America/New_York')
        self.assertEqual(value['keys'], {'day': '2026-11-05', 'week': '2026-11-02',
                                         'month': '2026-11', 'year': '2026'})
        self.assertEqual(value['observedAt'], moment.strftime('%Y-%m-%dT%H:%M:%SZ'))
        self.assertEqual(value['expiresAt'], (moment + datetime.timedelta(seconds=45)).strftime('%Y-%m-%dT%H:%M:%SZ'))
        self.assertIs(value['localCalendarAligned'], True)
        self.assertEqual(tree(source), before, 'native source stays untouched')
        self.assertEqual((target/'CalendarClock.py').read_bytes(), (BASE/'calendar_clock.py').read_bytes())

    def test_ui_patch_fails_closed(self):
        with self.assertRaises(ValueError): p.transform_main('unsupported')
        with self.assertRaises(ValueError): p.transform_panel('unsupported')
        with self.assertRaises(ValueError): p.stage(self.root, self.root, 'typhoon.agents')

    def test_worker_identity_and_environment_redaction(self):
        proc = self.root/'proc'
        (proc/'10/task/10').mkdir(parents=True)
        (proc/'10/task/10/children').write_text('')
        (proc/'10/task/11').mkdir()
        (proc/'10/task/11/children').write_text('20')
        (proc/'20').mkdir()
        (proc/'20/cmdline').write_bytes(b'llama-server\0--alias\0heretic\0--port\09001\0')
        # No environ file exists: the observer must not inspect environments.
        (proc/'20/stat').write_text('20 (llama server) ' + ' '.join(['S'] + ['0']*18 + ['888']))
        (proc/'sys/kernel/random').mkdir(parents=True)
        (proc/'sys/kernel/random/boot_id').write_text('fixture-boot')
        identity, port = c.worker({'id':'heretic','status':{}}, 10, proc)
        self.assertEqual((identity, port), ('fixture-boot:20:888', 9001))
        with self.assertRaises(ValueError): c.worker({'id':'other','status':{}}, 10, proc)

    def test_undated_cross_day_gap_is_not_backfilled(self):
        self.ledger.observe('heretic', 'pid', (0, 0), epoch(2))
        self.assertEqual(self.ledger.observe('heretic', 'pid', (1000, 500), epoch(3)), (0, 0))
        self.assertEqual(self.ledger.observe('heretic', 'pid', (1010, 502), epoch(3)+5), (10, 2))
        self.assertEqual(self.ledger.record(epoch(3)+5, 'sampled')['todayTotalTokens'], 12)

    def test_metrics_disabled_is_explicit_and_preserves_history(self):
        import urllib.error
        listing = json.dumps({'data':[{'id':'heretic','status':{'value':'loaded'}}]})
        from email.message import Message
        error = urllib.error.HTTPError('http://fake/DO_NOT_LOG', 501, 'DO_NOT_LOG', Message(), None)
        self.addCleanup(error.close)
        with patch.object(c, 'get', side_effect=[listing, error]), patch.object(c, 'worker', return_value=('stable', 9001)), patch.object(c, 'router_pid', return_value=123):
            status = c.sample(self.ledger, epoch(2))
            self.assertIn('Metrics disabled', status)
            self.assertNotIn('DO_NOT_LOG', status)
            self.assertFalse(self.ledger.record(epoch(2), status)['hasLocalStats'])

    def test_retention_and_model_bound(self):
        self.ledger.observe('old', 'pid', (0, 0), epoch(2))
        future = epoch(2) + 366*86400
        self.ledger.observe('new', 'pid2', (0, 0), future)
        self.assertEqual(list(self.ledger.record(future, 'sampled')['modelUsage']), ['new'])
        with self.ledger.db:
            self.ledger.db.executemany('INSERT INTO snapshots VALUES (?,?,?,?,?)', [(f'm{i}', f'p{i}', 0, 0, future) for i in range(255)])
        with self.assertRaises(ValueError): self.ledger.observe('overflow', 'pid3', (0, 0), future+5)
        self.assertNotIn('overflow', self.ledger.record(future, 'sampled')['modelUsage'])

    def test_writer_lock_and_render_only_cli(self):
        import fcntl, subprocess, sys
        state = self.root/'state'
        state.mkdir()
        args = [sys.executable, str(BASE/'collector.py'), '--state-dir', str(state), '--render-only']
        with (state/'collector.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(args, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Another local-router collector', result.stderr)
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['usageStatusText'], 'Activation pending · not collecting')

    def test_redirects_are_refused(self):
        self.assertIsNone(c.NoRedirect().redirect_request(None, None, None))

    TABS = ['dayRow.day && dayRow.day.recorded === false ? "—"', 'return p && p.providerId === "local" ? rows : rows.slice(0, 4)',
            'name: p && p.providerId === "local" ? id : usage.friendlyModelName(id),', '" · cached input not counted"',
            'if (day && day.recorded === false) return "Not recorded — not zero usage"']
    LIMITS = ['if (item && item.providerId === "local") return ""', 'if (p.providerId === "local") continue',
              'function localRouterText(p)', 'visible: !!section.provider && section.provider.providerId === "local"',
              'text: visible ? root.localRouterText(section.provider) : ""', 'Missing counters are not zero', 'cached input not counted']
    MAIN = ['!hermesId(name.slice(0, -5)) || hermesEntry(name.slice(0, -5))', '/omarchy/local-router/agents/local.json',
            'id: localRouterAgent', 'result.push(displayProvider(localRouterRecord))', 'if (syncedId === "local-transcripts") continue',
            'if (String(record.id) === "local" && !nativeLocalRecordAdmitted(record)) continue', 'if (!hermesRecordAdmitted(record)) continue',
            'if (providerId === "local" && nativeLocal !== true) return null']

    def assert_staged(self, source, target, layout):
        before = tree(source)
        p.stage(source, target, 'fixture.agents')
        self.assertEqual(tree(source), before, 'packaged source untouched')
        main, panel = (target/'Main.qml').read_text(), (target/'Panel.qml').read_text()
        for snippet in self.MAIN:
            self.assertEqual(main.count(snippet), 1, snippet)
        for snippet in (self.TABS if layout == 'tabs' else self.LIMITS):
            self.assertIn(snippet, panel)
        manifest = json.loads((target/'manifest.json').read_text())
        self.assertEqual(manifest['id'], 'fixture.agents')
        self.assertEqual(manifest['name'], 'Agents + Local')
        self.assertEqual(manifest['barWidget']['displayName'], 'Agents + Local')
        self.assertEqual(manifest['omarchy'], {'clonedFrom': 'omarchy.agents', 'alaCarchy': 'local-router-stats/1',
                                               'alaCarchyLayout': layout, 'alaCarchySource': p.fingerprint(source)})
        self.assertEqual((source/'Agent.qml').read_bytes(), (target/'Agent.qml').read_bytes())
        after = tree(target)
        marks = {'assets/local.svg', 'assets/local-light.svg'}
        # Native Local marks (current Omarchy ships both) stay native; the clone adds its own only when none ship.
        native_marks = marks & set(before)
        additions = {'History.js', 'CalendarClock.py'} | (set() if native_marks else marks)
        if b'import "../panels/elsewhen/Model.js" as Reorder\n' in before['Panel.qml']:
            additions.add('AgentsReorder.js')
            self.assertIn('import "AgentsReorder.js" as Reorder', panel)
            self.assertNotIn('../panels/elsewhen/Model.js', panel)
            self.assertIn(b'function dragTargetFor(', after['AgentsReorder.js'])
        if b'import "../panels/tailscale" as Tailscale\n' in before['Panel.qml']:
            import hashlib
            self.assertEqual(hashlib.sha256(before['Panel.qml']).hexdigest(),
                             '4fe2114698f1fe5b960c9bdbe72c2be3e8f9e78fc88996c86d7cd095d6684c89')
            additions.add('AgentsTailscaleIcon.qml')
            self.assertEqual(hashlib.sha256(after['AgentsTailscaleIcon.qml']).hexdigest(),
                             '05142bf1863a671c3b6b39f4a2455d556e2a75b4a373aa1a559c36c918aa8329')
            self.assertIn('AgentsTailscaleIcon {', panel)
            self.assertNotIn('../panels/tailscale', panel)
        self.assertEqual(set(after), set(before) | additions)
        self.assertEqual(after['CalendarClock.py'], (BASE/'calendar_clock.py').read_bytes())
        for name, data in before.items():
            if name not in ('Main.qml', 'Panel.qml', 'manifest.json'):
                self.assertEqual(after[name], data, 'native file changed: ' + name)
        if not native_marks:
            for name, color in (('local.svg', b'#f5f5f5'), ('local-light.svg', b'#202020')):
                self.assertIn(b'viewBox="0 0 24 24"', after['assets/' + name])
                self.assertIn(color, after['assets/' + name])
        self.assertEqual([path.name for path in target.parent.iterdir() if path.name.startswith('.')], [], 'no staging leftovers')

    def test_both_supported_panel_layouts_are_patched(self):
        for layout in ('tabs', 'limits'):
            with self.subTest(layout=layout):
                source = fixture.make_source(self.root/layout/'source', layout)
                self.assertEqual(p.patch_panel((source/'Panel.qml').read_text())[0], layout)
                self.assert_staged(source, self.root/layout/'staged/agents', layout)

    def test_limits_layout_keeps_local_out_of_the_subscription_summary(self):
        source = fixture.make_source(self.root/'source', 'limits')
        panel = p.render(source, 'fixture.agents')['Panel.qml']
        loop = panel.split('var p = providers[i]\n', 1)[1]
        self.assertTrue(loop.startswith('      if (p.providerId === "local") continue'), 'Local is skipped before any token is summed')
        trouble = panel.split('function otherTrouble(item) {\n', 1)[1]
        self.assertTrue(trouble.startswith('    if (item && item.providerId === "local") return ""'))

    def test_ambiguous_or_unknown_panels_are_refused_without_writing(self):
        source = fixture.make_source(self.root/'source', 'tabs')
        both = fixture.PANEL_TABS + fixture.PANEL_LIMITS
        listing = sorted(path.name for path in self.root.iterdir())
        for label, panel in [('both layouts', both), ('unknown', 'import QtQuick\nItem {}\n'),
                             ('doubled anchor', fixture.PANEL_TABS.replace('    return rows.slice(0, 4)', '    return rows.slice(0, 4)\n    return rows.slice(0, 4)'))]:
            with self.subTest(label):
                (source/'Panel.qml').write_text(panel)
                with self.assertRaises(ValueError): p.patch_panel(panel)
                with self.assertRaises(ValueError): p.render(source, 'fixture.agents')
                with self.assertRaises(ValueError): p.stage(source, self.root/'never', 'fixture.agents')
                self.assertFalse((self.root/'never').exists())
                self.assertEqual(sorted(path.name for path in self.root.iterdir()), listing, 'no staging leftovers')
        (source/'Panel.qml').write_text(fixture.PANEL_TABS)
        (source/'Main.qml').write_text(fixture.MAIN.replace('  Instantiator {', '  Loader {'))
        with self.assertRaises(ValueError): p.render(source, 'fixture.agents')

    def test_render_validates_identity_and_writes_nothing(self):
        source = fixture.make_source(self.root/'source', 'limits')
        before = tree(self.root)
        rendered = p.render(source, 'fixture.agents')
        self.assertEqual(tree(self.root), before)
        self.assertEqual(rendered['layout'], 'limits')
        for bad in ('omarchy.agents', 'agents', '../escape.agents', 'fixture.agents/x', ''):
            with self.subTest(bad=bad), self.assertRaises(ValueError): p.render(source, bad)
        manifest = json.loads((source/'manifest.json').read_text())
        (source/'manifest.json').write_text(json.dumps(manifest | {'id': 'someone.else'}))
        with self.assertRaises(ValueError): p.render(source, 'fixture.agents')
        (source/'manifest.json').write_text('[]')
        with self.assertRaises(ValueError): p.render(source, 'fixture.agents')

    def test_fingerprint_follows_the_packaged_bytes(self):
        source = fixture.make_source(self.root/'source', 'limits')
        first = p.fingerprint(source)
        self.assertEqual(p.fingerprint(source), first)
        self.assertRegex(first, r'^[0-9a-f]{64}$')
        (source/'assets/claude.svg').write_text('<svg/>')
        second = p.fingerprint(source)
        self.assertNotEqual(second, first)
        (source/'assets/claude.svg').rename(source/'assets/other.svg')
        self.assertNotEqual(p.fingerprint(source), second, 'a renamed file is a different source')

    def test_native_providers(self):
        self.assertEqual(p.native_providers(json.loads((fixture.make_source(self.root/'a', 'tabs')/'manifest.json').read_text())), {'claude', 'codex', 'fireworks'})
        self.assertIn('grok', p.native_providers(json.loads((fixture.make_source(self.root/'b', 'limits')/'manifest.json').read_text())))
        for odd in ({}, [], None, {'barWidget': []}, {'barWidget': {'defaults': {'providers': ['grok']}}}):
            self.assertEqual(p.native_providers(odd), set())

    def test_current_usage_note_layout_is_staged_without_rewriting_native_notes(self):
        source = current_limits_fixture(self.root/'usage-note-source')
        original = (source/'Panel.qml').read_text()
        self.assertEqual(p.patch_panel(original)[0], 'limits')
        self.assert_staged(source, self.root/'usage-note-staged/agents', 'limits')
        rendered = p.render(source, 'fixture.agents')['Panel.qml']
        for exact in (USAGE_NOTE_COMPONENT, USAGE_NOTE_PROVIDER, USAGE_NOTE_ACCOUNT):
            self.assertEqual(rendered.count(exact), 1, 'native note interface retained')
        self.assertNotIn('function otherTrouble(', rendered)
        self.assertIn('function usageNote(item) {\n    if (item && item.providerId === "local") return ""', rendered)
        self.assertIn('objectName: "localTaskRates"', rendered)

    def test_usage_note_scope_recognizer_handles_literals_and_refuses_unknown_syntax(self):
        declaration = '  component Probe: Item {'
        body = ('\n    property string value: ' + json.dumps('} // { " \\') + '\n'
                '    // } ignored comment\n    /* { ignored block } */\n'
                "    property string single: '} /* not a comment */'\n"
                '    Item { property int count: 1 }\n  ')
        self.assertEqual(p.limits_component_body(declaration + body + '}\n', declaration), body)
        for label, value in {
            'unclosed object': declaration + '\n    Item { }',
            'unclosed quote': declaration + '\n    property string value: "}',
            'unclosed line comment': declaration + ' // }',
            'unclosed block comment': declaration + ' /* }',
            'unknown template syntax': declaration + ' property var value: `x` }',
            'unknown slash syntax': declaration + ' property var value: /x/ }',
            'duplicate declaration': declaration + '}\n' + declaration + '}',
        }.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                p.limits_component_body(value, declaration)

    def test_usage_note_scope_recognizer_accepts_native_division_not_regexes(self):
        declaration = '  component Probe: Item {'
        body = ('\n    readonly property real elapsed: model ? Math.max(0, (now - Number(model.since_ms || now)) / 1000) : 0\n'
                '    readonly property real memory: section.memory / Number(usage.localModel.ram_total_gib)\n'
                '    readonly property real balance: section.balance.remaining / section.balance.funded\n  ')
        self.assertEqual(p.limits_component_body(declaration + body + '}', declaration), body)
        for expression in ('property var value: /[}]/',
                           'function value() { return /[}]/ }',
                           'function value() { if (ready) /[}]/.test(text) }'):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                p.limits_component_body(declaration + expression + '}', declaration)

    def test_current_model_heading_keeps_native_names_and_routes_only_credentials(self):
        import subprocess
        source = current_limits_fixture(self.root/'usage-note-source')
        panel = (source/'Panel.qml').read_text()
        old = '        text: section.provider ? section.provider.providerName : ""'
        current = ('        text: section.model ? (section.model.name || section.model.recipe) : '
                   'section.provider ? section.provider.providerName : ""')
        self.assertEqual(panel.count(old), 1)
        (source/'Panel.qml').write_text(panel.replace(old, current, 1))
        rendered = p.render(source, 'fixture.agents')['Panel.qml']
        expected = ('        text: section.model ? (section.model.name || section.model.recipe) : '
                    'root.providerHeading(section.provider)')
        self.assertEqual(rendered.count(expected), 1)
        expression = expected.split('text: ', 1)[1]
        javascript = '''
const expression = JSON.parse(process.argv[1]);
function show(model, provider) {
  const calls = [];
  const root = {providerHeading: p => {calls.push(p); return p ? "credential heading" : "";}};
  const section = {model, provider};
  return {value: eval(expression), calls: calls.length};
}
console.log(JSON.stringify([
  show({name:"native model",recipe:"recipe-id"}, {providerName:"not the model"}),
  show({name:"",recipe:"recipe-id"}, {providerName:"not the model"}),
  show(null, {readOnly:true,credentialProvider:"openai-codex"}),
  show(null, null)
]));
'''
        result = subprocess.run(['node', '-e', javascript, json.dumps(expression)],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            {'value': 'native model', 'calls': 0}, {'value': 'recipe-id', 'calls': 0},
            {'value': 'credential heading', 'calls': 1}, {'value': '', 'calls': 1}])
        mixed = panel + '\n' + current + '\n'
        with self.assertRaises(ValueError): p.patch_panel(mixed)

    def test_current_usage_note_refuses_partial_duplicate_or_mixed_interfaces(self):
        source = current_limits_fixture(self.root/'usage-note-source')
        original = (source/'Panel.qml').read_text()
        variants = {
            'missing record property': original.replace('    property var record: null\n', '', 1),
            'missing message binding': original.replace('    readonly property string message: root.usageNote(record)\n', '', 1),
            'changed note display': original.replace('    text: "󰋽"\n', '    text: ""\n', 1),
            'missing tooltip message': original.replace('      text: noteMark.message\n', '', 1),
            'wrong provider binding': original.replace('        record: section.provider\n', '        record: head.account\n', 1),
            'wrong account binding': original.replace('          record: head.account\n', '          record: section.provider\n', 1),
            'explicit delegate syntax': original.replace('      UsageNote {\n', '      delegate: UsageNote {\n', 1),
            'changed invocation indentation': original.replace('      UsageNote {\n', '     UsageNote {\n', 1),
            'doubled component': original + USAGE_NOTE_COMPONENT,
            'doubled provider instance': original + USAGE_NOTE_PROVIDER,
            'doubled account instance': original + USAGE_NOTE_ACCOUNT,
            'mixed legacy helper': original + '  function otherTrouble(item) { return "" }\n',
            'mixed legacy display': original + '      visible: !section.multi && root.otherTrouble(section.provider) !== ""\n',
            'changed helper semantics': original.replace('    return helpDisplayText(item.authHelpText) || statusDisplayText(status)', '    return status', 1),
            'provider note outside its component': original.replace(USAGE_NOTE_PROVIDER, '', 1) + USAGE_NOTE_PROVIDER,
            'account note outside its component': original.replace(USAGE_NOTE_ACCOUNT, '', 1) + USAGE_NOTE_ACCOUNT,
        }
        target = self.root/'never-staged'
        for label, variant in variants.items():
            with self.subTest(label=label):
                (source/'Panel.qml').write_text(variant)
                before = tree(self.root)
                with self.assertRaises(ValueError): p.patch_panel(variant)
                with self.assertRaises(ValueError): p.render(source, 'fixture.agents')
                with self.assertRaises(ValueError): p.stage(source, target, 'fixture.agents')
                self.assertFalse(target.exists())
                self.assertEqual(tree(self.root), before, 'refusal changes no source/destination')

    def test_installed_native_ui_contract(self):
        source = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))
        if not (source/'Panel.qml').is_file(): self.skipTest('native Omarchy source not installed')
        layout = p.patch_panel((source/'Panel.qml').read_text())[0]
        self.assert_staged(source, self.root/'installed/agents', layout)


if __name__ == '__main__': unittest.main(verbosity=2)
