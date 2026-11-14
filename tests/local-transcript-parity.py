#!/usr/bin/env python3
"""Actual Main.qml JavaScript contracts, synthetic records, no live services.

Run only in the maintained private HOME/network/filesystem test wrapper.
This exercises native and generated discovery/model functions in Node, not
Qt file watching, native graphics, or an installed desktop.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path(os.environ['OMARCHY_PARITY_NATIVE_ROOT'])
SPEC = importlib.util.spec_from_file_location('parity_stager', ROOT / 'extras/local-router-stats/stage_plugin.py')
STAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGER)
NATIVE_MODEL = 'native-history-only-model'


def native_record():
    # Literal current-native producer/base_record + Stats snapshot contract.
    # No native collector is run: its real entry point scans private histories.
    return {'schemaVersion': 1, 'id': 'local', 'name': 'Local', 'ready': True,
            'hasLocalStats': True, 'hasPromptStats': True,
            'todayPrompts': 3, 'todaySessions': 1, 'todayTotalTokens': 53,
            'todayTokensByModel': {NATIVE_MODEL: 53}, 'totalPrompts': 9,
            'totalSessions': 2, 'activeDays': 2,
            'recentDays': [{'date': '2026-11-07', 'messageCount': 53}],
            'modelUsage': {NATIVE_MODEL: {'inputTokens': 40, 'outputTokens': 13}},
            'accounts': [], 'limits': [], 'tierLabel': ''}


def evaluate(main, native=None, router=None, snapshots=None, settings=None):
    functions = re.findall(r'^  function [^\n]+\{\n[\s\S]*?^  }(?=\n)', main, re.M)
    block = re.search(r'^  (?:readonly )?property var enabledProviders: \{\n([\s\S]*?)^  }', main, re.M)
    if not block or not functions:
        raise AssertionError('Missing complete real discovery/provider consumer bodies')
    required = ('applyAgentListing', 'displayProvider', 'localSnapshot', 'aggregateSnapshots')
    for name in required:
        if not any(re.match(r'  function '+name+r'\(', f) for f in functions):
            raise AssertionError('Missing real consumer function: '+name)
    payload = {'functions': '\n\n'.join(functions), 'body': block.group(1),
               'native': native, 'router': router, 'snapshots': snapshots, 'settings': settings or {}}
    program = r'''
const vm = require('vm');
const p = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const s = {
  settings: p.settings, agentIds: [], agents: [], providerOrder: [],
  dataRevision: 0, syncRevision: 0, aggregateData: {},
  syncEnabled: p.snapshots !== null, syncDir: '/fixture/sync', syncEffectiveDeviceId: 'fixture',
  hermesInventory: null, quotaCalibration: null,
  localRouterAgent: {record: p.router}, localStatsAgent: {record: p.router}, hermesAccountingAgent: {record: null},
  I18n: {formatNumber: (n) => String(n), t: (key) => key},
  Quickshell: {env: () => {throw new Error('Unexpected environment lookup');}}
};
s.root = s;
vm.createContext(s);
vm.runInContext(p.functions, s, {timeout: 1000});
s.testSnapshots = p.snapshots;
if (p.snapshots !== null) s.aggregateData = vm.runInContext('aggregateSnapshots(testSnapshots)', s, {timeout: 1000});
s.listing = p.native ? 'local.json\n' : '';
vm.runInContext('applyAgentListing(listing)', s, {timeout: 1000});
// Synthetic reader adoption follows the actual Instantiator's discovered IDs.
// No test-defined discovery or provider formatter substitutes for production.
s.agents = s.agentIds.map(id => ({agentId: id, record: id === 'local' ? p.native : null}));
const providers = vm.runInContext('(function(){'+p.body+'})()', s, {timeout: 1000});
const snapshot = vm.runInContext('localSnapshot()', s, {timeout: 1000});
s.testProviders = providers;
const totals = typeof s.recordedTokenTotalsFor === 'function'
  ? vm.runInContext('recordedTokenTotalsFor(testProviders, null, null, null)', s, {timeout: 1000}) : null;
const subscribed = providers.filter(p => typeof s.providerHasSubscription !== 'function' || s.providerHasSubscription(p));
console.log(JSON.stringify({ids: s.agentIds, providers, snapshot, totals, subscribed}));
'''
    child = subprocess.run(['node', '-e', program], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=10)
    if child.returncode:
        raise AssertionError('Actual consumer JS failed: '+child.stderr)
    return json.loads(child.stdout)


class NativeTranscriptParityTests(unittest.TestCase):
    def test_native_positive_control_reaches_distinctive_history(self):
        for name in ('running-shell', 'packaged-shell'):
            with self.subTest(source=name):
                source = NATIVE/name/'plugins/agents'
                result = evaluate((source/'Main.qml').read_text(), native_record())
                self.assertEqual(result['ids'], ['local'])
                self.assertEqual(len(result['providers']), 1)
                provider = result['providers'][0]
                self.assertEqual(provider['modelUsage'], native_record()['modelUsage'])
                self.assertEqual(provider['totalSessions'], 2)
                self.assertEqual(result['snapshot']['providers']['local']['modelUsage'], native_record()['modelUsage'])

    def test_generated_consumer_preserves_native_history_without_router(self):
        for name in ('running-shell', 'packaged-shell'):
            with self.subTest(source=name):
                source = NATIVE/name/'plugins/agents'
                rendered = STAGER.render(source, 'parity.agents')
                result = evaluate(rendered['Main.qml'], native_record())
                native = [p for p in result['providers'] if NATIVE_MODEL in p['modelUsage']]
                self.assertEqual(len(native), 1,
                                 'Valid native Local ledger lost by generated discovery/provider consumer')
                self.assertEqual(native[0]['modelUsage'], native_record()['modelUsage'])
                self.assertEqual(native[0]['recentDays'], native_record()['recentDays'])
                self.assertEqual(native[0]['totalSessions'], 2)
                self.assertEqual(native[0]['providerId'], 'local-transcripts')
                self.assertIn(native[0], result['subscribed'], 'Native history has no accessible panel section')

    def test_router_overlap_does_not_add_native_transcript_ledger(self):
        source = NATIVE/'running-shell/plugins/agents'
        main = STAGER.render(source, 'parity.agents')['Main.qml']
        router = native_record()
        router.update(name='Local router', hasPromptStats=False, readOnly=True,
                      todayTotalTokens=21,
                      recentDays=[{'date': '2026-11-07', 'messageCount': 21, 'recorded': True}],
                      modelUsage={NATIVE_MODEL: {'inputTokens': 11, 'outputTokens': 10}},
                      localLive={'contract': 'local-task-rates/1', 'models': []})
        baseline = evaluate(main, None, router)
        self.assertEqual([p['providerId'] for p in baseline['providers']], ['local'])
        together = evaluate(main, native_record(), router)
        self.assertEqual({p['providerId'] for p in together['providers']}, {'local', 'local-transcripts'})
        self.assertEqual(together['totals']['total'], baseline['totals']['total'],
                         'Native transcript history overlaps router telemetry and must not be added to it')
        self.assertEqual(together['totals']['cloud'], baseline['totals']['cloud'])
        self.assertEqual(main.count('id: localRouterAgent'), 1)

    def test_private_router_record_is_not_adopted_from_native_directory(self):
        source = NATIVE/'running-shell/plugins/agents'
        main = STAGER.render(source, 'parity.agents')['Main.qml']
        misplaced = native_record()
        misplaced.update(hasPromptStats=False, readOnly=True,
                         localLive={'contract': 'local-task-rates/1', 'models': []})
        result = evaluate(main, misplaced)
        self.assertEqual(result['providers'], [])
        self.assertNotIn('local', result['snapshot']['providers'])


    def test_native_sync_retains_wire_identity_and_excludes_private_observer(self):
        source = NATIVE/'running-shell/plugins/agents'
        main = STAGER.render(source, 'parity.agents')['Main.qml']
        record = native_record()
        together = evaluate(main, record, record | {'hasPromptStats': False, 'readOnly': True,
                            'localLive': {'contract': 'local-task-rates/1', 'models': []}})
        self.assertIn('local', together['snapshot']['providers'], 'Native Local wire snapshot was discarded')
        row = together['snapshot']['providers']['local']
        self.assertEqual(row['modelUsage'], record['modelUsage'])
        self.assertEqual(row['sourceKind'], 'native-local-transcripts')
        self.assertNotIn('localLive', row)
        self.assertNotIn('local-transcripts', together['snapshot']['providers'])

    def test_native_sync_only_source_is_accessible_and_private_sync_is_refused(self):
        source = NATIVE/'running-shell/plugins/agents'
        stock = (source/'Main.qml').read_text()
        wire = evaluate(stock, native_record())['snapshot']
        stock_synced = evaluate(stock, snapshots=[wire])['providers'][0]
        # Stock aggregation normalizes absent cache fields to explicit zeros.
        # Compare its complete real output, not the raw pre-aggregation record.
        self.assertEqual(stock_synced['modelUsage'], {NATIVE_MODEL: {
            'inputTokens': 40, 'outputTokens': 13,
            'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0}})
        main = STAGER.render(source, 'parity.agents')['Main.qml']
        result = evaluate(main, snapshots=[wire])
        self.assertEqual([p['providerId'] for p in result['providers']], ['local-transcripts'])
        self.assertEqual(result['providers'][0]['modelUsage'], stock_synced['modelUsage'])
        self.assertTrue(result['providers'][0]['syncEnabled'])
        bad = json.loads(json.dumps(wire))
        bad['providers']['local'].update(hasPromptStats=False, localLive={'contract': 'local-task-rates/1'})
        self.assertEqual(evaluate(main, snapshots=[bad])['providers'], [])

    def test_native_toggle_is_independent_of_private_router_toggle(self):
        source = NATIVE/'running-shell/plugins/agents'
        main = STAGER.render(source, 'parity.agents')['Main.qml']
        result = evaluate(main, native_record(), settings={'providers': {'local': {'enabled': False}}})
        self.assertEqual([p['providerId'] for p in result['providers']], ['local-transcripts'])
        self.assertIn('local', result['snapshot']['providers'])
        result = evaluate(main, native_record(), settings={'providers': {'local-transcripts': {'enabled': False}}})
        self.assertEqual(result['providers'], [])
        self.assertNotIn('local', result['snapshot']['providers'])

    def test_canonical_counterpart_preserves_native_and_private_sources(self):
        main = (Path(os.environ['OMARCHY_PATH'])/'shell/plugins/agents/Main.qml').read_text()
        result = evaluate(main, native_record())
        self.assertEqual([p['providerId'] for p in result['providers']], ['local-transcripts'])
        self.assertEqual(result['providers'][0]['modelUsage'], native_record()['modelUsage'])
        self.assertIn(result['providers'][0], result['subscribed'])
        self.assertEqual(result['snapshot']['providers']['local']['modelUsage'], native_record()['modelUsage'])
        router = native_record() | {'readOnly': True, 'hasPromptStats': False,
                                   'localLive': {'contract': 'local-task-rates/1', 'models': []}}
        result = evaluate(main, native_record(), router)
        self.assertEqual({p['providerId'] for p in result['providers']}, {'local', 'local-transcripts'})
        self.assertEqual(result['snapshot']['providers']['local']['sourceKind'], 'native-local-transcripts')
        misplaced = evaluate(main, router)
        self.assertEqual(misplaced['providers'], [])
        self.assertNotIn('local', misplaced['snapshot']['providers'])

    def test_native_reload_shape_drift_is_refused(self):
        main = (NATIVE/'running-shell/plugins/agents/Main.qml').read_text()
        STAGER.transform_main(main)  # Healthy control reaches the same transform.
        old = '  function reloadRecords() {\n    for (var i = 0; i < agents.length; i++)\n      if (agents[i]) agents[i].reload()\n  }'
        self.assertEqual(main.count(old), 1)
        for label, mutated in (
            ('missing', main.replace(old, '')),
            ('changed', main.replace('if (agents[i]) agents[i].reload()', 'agents[i].reload()')),
            ('duplicate', main.replace(old, old+'\n'+old))):
            with self.subTest(shape=label):
                with self.assertRaisesRegex(ValueError, 'unsupported Agents source'):
                    STAGER.transform_main(mutated)

    def test_canonical_sync_only_matches_stock_native_source(self):
        source = NATIVE/'running-shell/plugins/agents'
        stock = (source/'Main.qml').read_text()
        wire = evaluate(stock, native_record())['snapshot']
        expected = evaluate(stock, snapshots=[wire])['providers'][0]
        main = (Path(os.environ['OMARCHY_PATH'])/'shell/plugins/agents/Main.qml').read_text()
        result = evaluate(main, snapshots=[wire])
        self.assertEqual([p['providerId'] for p in result['providers']], ['local-transcripts'])
        self.assertEqual(result['providers'][0]['modelUsage'], expected['modelUsage'])
        self.assertTrue(result['providers'][0]['syncEnabled'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
