#!/usr/bin/env python3
"""Synthetic active-profile inventory -> producer -> publication -> real JS consumers.

No auth libraries, real credentials, databases, network, or installed writes.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load('inventory_bridge', ROOT/'extras/hermes-codex-usage/collector.py')
fixture = load('inventory_fixture', ROOT/'tests/hermes-codex-usage.py')
stager = load('inventory_stager', ROOT/'extras/local-router-stats/stage_plugin.py')
NATIVE = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))
NODE = shutil.which('node')  # Resolve before synthetic HOME/env isolation.


def consumer(records, inventory=None, synced=None, snapshots=None, main=None, actions=False, filenames=None):
    """Execute actual generated discovery/admission/normalization/export functions."""
    rendered = stager.render(NATIVE, 'fixture.credentials')
    main = main if main is not None else rendered['Main.qml']
    # Inline native helpers end on their own line; do not consume the next
    # QML property while looking for a multiline function's closing brace.
    functions = '\n'.join(re.findall(
        r'^  function \w+\([^\n]*\) \{[^\n]*\}$|^  function \w+\([^\n]*\) \{\n.*?^  \}',
        main, re.M | re.S))
    enabled = main.split('  property var enabledProviders: {', 1)[1].split('\n  }\n', 1)[0]
    listing = '\n'.join(r['id'] + '.json' for r in records)
    context = {'records': records, 'inventory': inventory, 'synced': synced or {},
               'snapshots': snapshots, 'listing': listing, 'filenames':filenames or [r['id'] for r in records]}
    action_program = 'var actionReport = null;'
    if actions:
        names = ('providerAccounts','renameAccount','useAccount','setSwitchMode','signInAgain','needsSignIn','autoSwitchFor','supportsSignIn')
        panel_functions = re.findall(r'^  function \w+\([^\n]*\) \{\n.*?^  \}', rendered['Panel.qml'], re.M | re.S)
        action_program = '\n'.join(fn for fn in panel_functions if fn.split('function ',1)[1].split('(',1)[0] in names)
        # Native sign-in support is a helper plus the actual packaged provider list.
        add_providers = re.search(r'^  readonly property var addProviders: (\[\n.*?^  \])',
                                  rendered['Panel.qml'], re.M | re.S)
        assert add_providers is not None
        action_program += '\nvar addProviders = ' + add_providers.group(1) + ';\n'
        action_program += '''
var calls=[], Util={execArgv: command=>calls.push(command)}, addStage='', addProcess={running:false};
var Commons={I18n:{t: text=>text}};
var account={id:'synthetic',active:false}, actionRows=[];
for (var p of selected()) {
  signInAgain(p, null); signInAgain(p, account); useAccount(p, account);
  renameAccount(p, account, 'synthetic'); setSwitchMode(p, 'auto');
  actionRows.push({id:p.providerId, needsSignIn:needsSignIn(p), accounts:providerAccounts(p)});
}
var actionReport={calls:calls.slice(), addStarted:addProcess.running, rows:actionRows};
var native={providerId:'claude', accounts:[account,{id:'second'}], usageStatusText:'Waiting for auth'};
renameAccount(native, account, 'synthetic'); useAccount(native, account); setSwitchMode(native,'auto'); signInAgain(native, account);
actionReport.nativeControl={calls:calls.length, addStarted:addProcess.running, needsSignIn:needsSignIn(native)};
'''
    program = '''const ctx = ''' + json.dumps(context) + ''';
var root = globalThis;
var dataRevision = 0, syncRevision = 0, settings = {}, providerOrder = [];
var agents = ctx.records.map((record,i) => ({record,agentId:ctx.filenames[i]})), agentIds = [];
var aggregateData = {providers: ctx.synced, deviceCount:1};
var hermesInventory = ctx.inventory;
var localRouterAgent = {record:null}, grokAgent = {record:null};
var syncEnabled = true, syncDir = '/synthetic', syncEffectiveDeviceId = 'synthetic-device';
var home = '/synthetic', detectedHostname = 'synthetic';
var Quickshell = {env: () => ''};
''' + functions + '''
var hermesInventoryIndex = validateHermesInventory(hermesInventory);
function selected() {''' + enabled + '''\n}
applyAgentListing(ctx.listing);
if (ctx.snapshots) aggregateData = aggregateSnapshots(ctx.snapshots);
''' + action_program + '''
console.log(JSON.stringify({ids:selected().map(p=>p.providerId), display:selected(),
  discovered:agentIds, snapshot:localSnapshot(), aggregate:aggregateData, actions:actionReport}));
'''
    node = NODE
    if not node:
        raise RuntimeError('Node is required for real generated admission proof')
    with tempfile.TemporaryDirectory(prefix='ci-') as td:
        child = subprocess.run([node, '-'], input=program, text=True, capture_output=True, timeout=10,
                               env={'HOME':td, 'PATH':os.environ.get('PATH', ''), 'LANG':'C.UTF-8'})
    if child.returncode:
        raise AssertionError((child.returncode, child.stderr))
    return json.loads(child.stdout)


class CredentialInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='iv-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root/'profiles/synthetic'
        self.home.mkdir(parents=True)
        self.state = self.root/'private-state'
        self.usage = self.root/'explicit-scanner'
        self.rows = [fixture.entry(subject='auth0|INVENTORY_PRIVATE_' + str(i)) for i in range(3)]
        for i, row in enumerate(self.rows):
            row['id'] = 'PRIVATE_CREDENTIAL_ID_' + str(i)
            row['label'] = 'private-email-' + str(i) + '@example.invalid'
        self.auth = self.home/'auth.json'
        self.write_pool({'openai-codex':self.rows})
        self.probes = []
        self.env = patch.dict(os.environ, {'HOME':str(self.root), 'HERMES_HOME':str(self.home),
                              'XDG_STATE_HOME':str(self.root/'xdg-state')}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        snapshot_read = bridge.read_json
        def confined_read(path, *args, **kwargs):
            self.assertTrue(Path(path).absolute().is_relative_to(self.root), 'fixture discovery escaped synthetic root')
            return snapshot_read(path, *args, **kwargs)
        self.read_guard = patch.object(bridge, 'read_json', side_effect=confined_read)
        self.read_guard.start()
        self.addCleanup(self.read_guard.stop)

    def test_fixture_guard_blocks_escaped_inventory_before_any_snapshot_open(self):
        # Reproduce the incident's boundary with another disposable root, never
        # a real credential path. Refusal must precede the actual JSON reader.
        with tempfile.TemporaryDirectory(prefix='og-') as outside:
            sentinel = Path(outside)/'auth.json'
            sentinel.write_text('{"credential_pool":{"anthropic":[{"id":"PRIVATE_OUTSIDE_SENTINEL"}]}}')
            original = sentinel.read_bytes()
            with patch.object(bridge.os, 'open', side_effect=AssertionError('snapshot reader reached')) as opened:
                with self.assertRaisesRegex(AssertionError, 'fixture discovery escaped synthetic root'):
                    bridge.inventory_pool(Path(outside))
                opened.assert_not_called()
            self.assertEqual(sentinel.read_bytes(), original)

    def write_pool(self, pool):
        self.auth.write_text(json.dumps({'version':1, 'credential_pool':pool}))
        self.auth.chmod(0o600)

    def measured(self, token, ident):
        self.probes.append(ident['key'])
        return bridge.normalize_quota(fixture.quota(), fixture.NOW)

    def collect(self):
        with patch.object(bridge, 'fetch', side_effect=self.measured), patch.object(bridge.time, 'time', return_value=fixture.NOW):
            self.assertTrue(bridge.run_once(self.home, self.state, usage_dir=self.usage))
        records = [json.loads(p.read_text()) for p in sorted(self.usage.glob('*.json'))]
        inventory_path = self.usage/'.hermes-credential-inventory'
        inventory = json.loads(inventory_path.read_text()) if inventory_path.exists() else None
        return records, inventory

    def test_dynamic_publication_ids_survive_reorder_and_removal(self):
        try:
            records, _ = self.collect()
        except bridge.Unavailable as exc:
            self.fail('Every valid inventoried credential must be published: ' + str(exc))
        expected = {'codex-hermes-' + bridge.identity(row['access_token'])['key'] for row in self.rows}
        self.assertEqual({r['id'] for r in records}, expected)
        names = {r['id']: r['name'] for r in records}
        self.rows.reverse()
        self.write_pool({'openai-codex': self.rows})
        reordered, _ = self.collect()
        self.assertEqual({r['id']: r['name'] for r in reordered}, names)
        foreign = self.usage/'codex-hermes-3.json'
        foreign.write_text('{"id":"foreign-manual-record"}\n')
        foreign_before = foreign.read_bytes()
        removed = 'codex-hermes-' + bridge.identity(self.rows.pop()['access_token'])['key']
        self.write_pool({'openai-codex': self.rows})
        remaining, _ = self.collect()
        self.assertEqual(foreign.read_bytes(), foreign_before)
        self.assertFalse((self.usage/(removed + '.json')).exists())
        actual = {r['id'] for r in remaining if r['id'] != 'foreign-manual-record'}
        self.assertEqual(actual, expected - {removed})

    def test_owned_legacy_slots_migrate_without_adopting_manual_records(self):
        self.usage.mkdir(mode=0o700)
        legacy = self.usage/'codex-hermes-1.json'
        legacy.write_text('{"id":"codex-hermes-1","readOnly":true}\n')
        manifest = {'version': 1, 'files': {legacy.name: hashlib.sha256(legacy.read_bytes()).hexdigest()}}
        (self.usage/bridge.PUBLICATION_MANIFEST).write_text(json.dumps(manifest))
        manual = self.usage/'codex-hermes-2.json'
        manual.write_text('{"id":"manual-sentinel"}\n')
        manual_before = manual.read_bytes()
        records, _ = self.collect()
        self.assertFalse(legacy.exists())
        self.assertEqual(manual.read_bytes(), manual_before)
        expected = {'codex-hermes-' + bridge.identity(row['access_token'])['key'] for row in self.rows}
        self.assertEqual({r['id'] for r in records} - {'manual-sentinel'}, expected)

    def test_dynamic_publication_failure_rolls_back_then_recovers(self):
        self.collect()
        before = {p.name: p.read_bytes() for p in self.usage.iterdir() if p.is_file()}
        self.rows.append(fixture.entry(subject='auth0|INVENTORY_PRIVATE_new'))
        self.write_pool({'openai-codex': self.rows})
        real_replace = bridge.os.replace
        replacements = 0

        def fail_second_public_file(src, dst, **kwargs):
            nonlocal replacements
            if kwargs.get('dst_dir_fd') is not None and str(dst).endswith('.json'):
                replacements += 1
                if replacements == 2:
                    raise OSError('synthetic publication interruption')
            return real_replace(src, dst, **kwargs)

        with patch.object(bridge.os, 'replace', side_effect=fail_second_public_file):
            with self.assertRaises(bridge.Unavailable) as caught:
                self.collect()
        self.assertEqual(caught.exception.reason, 'publication')
        self.assertEqual({p.name: p.read_bytes() for p in self.usage.iterdir() if p.is_file()}, before)
        records, _ = self.collect()
        self.assertEqual({r['id'] for r in records},
                         {'codex-hermes-' + bridge.identity(row['access_token'])['key'] for row in self.rows})

    def test_empty_codex_pool_removes_only_owned_publication(self):
        self.collect()
        foreign = self.usage/'foreign.json'
        foreign.write_text('{"id":"manual-sentinel"}\n')
        before = foreign.read_bytes()
        self.write_pool({'openai-codex': []})
        records, _ = self.collect()
        self.assertEqual(records, [{'id': 'manual-sentinel'}])
        self.assertEqual(foreign.read_bytes(), before)
        self.assertEqual(set(json.loads((self.usage/bridge.PUBLICATION_MANIFEST).read_text())['files']),
                         {bridge.PUBLIC_INVENTORY})
        self.assertEqual(json.loads((self.usage/bridge.PUBLIC_INVENTORY).read_text())['entries'], [])

    def test_profile_shadows_per_provider_and_mixed_unsupported_credentials_remain_unknown(self):
        global_auth = self.root/'auth.json'
        global_auth.write_text(json.dumps({'credential_pool': {
            'openai-codex': [fixture.entry(subject='PRIVATE_GLOBAL_SHADOWED')],
            'anthropic': [{'id':'PRIVATE_GLOBAL_ANTHROPIC', 'access_token':'PRIVATE_GLOBAL_KEY'}],
            'xai-oauth': [{'id':'PRIVATE_XAI_ID', 'access_token':'PRIVATE_XAI_TOKEN'}]}}))
        pool = {'openai-codex': self.rows + [copy.deepcopy(self.rows[0])], 'anthropic': [],
                'custom:https://PRIVATE_URL.invalid/secret': [
                    {'id':'PRIVATE_CUSTOM_ID', 'access_token':'PRIVATE_CUSTOM_KEY',
                     'label':'PRIVATE_NAME@example.invalid', 'extra':{'url':'PRIVATE_SECRET_URL'}}]}
        self.write_pool(pool)
        inputs = {p:p.read_bytes() for p in (self.auth, global_auth)}
        records, inventory = self.collect()
        self.assertEqual(len(records), 6)
        self.assertEqual(len(self.probes), 3, 'Only the existing fixed-host Codex quota flow may be called')
        result = consumer(records, inventory)
        self.assertEqual(set(result['ids']), {r['id'] for r in records})
        unsupported = [r for r in records if r['credentialProvider'] != 'openai-codex']
        self.assertEqual({r['credentialProvider'] for r in unsupported}, {'anthropic','xai-oauth','unsupported'})
        for r in unsupported:
            self.assertEqual(r['limits'], [])
            self.assertTrue(r['limitsStale'])
            self.assertFalse(r['hasLocalStats'] or r['hasPromptStats'])
            self.assertIn('unavailable', r['usageStatusText'].lower())
            self.assertNotIn('todayTotalTokens', result['snapshot']['providers'][r['id']])
        self.assertEqual({p:p.read_bytes() for p in inputs}, inputs)
        self.assertNotIn('PRIVATE_', json.dumps([records, inventory, result]))
        self.assertFalse(list(self.root.rglob('auth.json.corrupt*')))

    def test_three_inventoried_credentials_reach_real_producer_publication_and_admission(self):
        original = self.auth.read_bytes()
        records, inventory = self.collect()
        self.assertEqual(len(records), len(self.rows))
        self.assertEqual(len(self.probes), len(self.rows))
        result = consumer(records, inventory)
        self.assertEqual(set(result['ids']), {r['id'] for r in records})
        self.assertEqual(set(result['snapshot']['providers']), set(result['ids']))
        self.assertTrue(all(p['readOnly'] and p['accounts'] == [] and p['accountSwitch'] is None for p in result['display']))
        self.assertEqual(self.auth.read_bytes(), original)
        self.assertFalse((self.root/'xdg-state').exists(), 'implicit scanner destination')
        public = json.dumps([records, inventory, result])
        for secret in ('PRIVATE_CREDENTIAL_ID_', 'INVENTORY_PRIVATE_', 'private-email-', 'fixture-workspace',
                       'FIXTURE_REFRESH_NEVER_EXPORTED', 'FIXTURE_RESPONSE_SECRET', self.rows[0]['access_token']):
            self.assertNotIn(secret, public)

    def test_sync_round_trip_uses_inventory_and_does_not_export_quotas_or_make_counters(self):
        self.write_pool({'openai-codex':self.rows, 'anthropic':[{'id':'PRIVATE_API', 'access_token':'PRIVATE_KEY'}]})
        records, inventory = self.collect()
        exported = consumer(records, inventory)['snapshot']
        for stats in exported['providers'].values():
            for key in ('limits','accounts','accountSwitch','balance','todayTotalTokens','modelUsage','recentDays'):
                self.assertNotIn(key, stats)
        result = consumer([], inventory, snapshots=[exported])
        self.assertEqual(set(result['ids']), {r['id'] for r in records})
        for p in result['display']:
            self.assertTrue(p['readOnly'])
            self.assertFalse(p['hasLocalStats'] or p['hasPromptStats'])
            self.assertEqual(p['limits'], [])
            self.assertNotIn('todayTotalTokens', p['tokenCounters'])
        forged = copy.deepcopy(exported)
        first = next(iter(forged['providers']))
        forged['providers'][first]['inventoryGeneration'] = '0'*64
        forged['providers']['codex-hermes-' + 'f'*64] = copy.deepcopy(forged['providers'][first])
        forged['providers']['codex'] = {'providerName':'PRIVATE_NATIVE', 'todayTotalTokens':999999}
        forged['providers']['local'] = {'providerName':'PRIVATE_LOCAL', 'todayTotalTokens':999999}
        blocked = consumer([], inventory, snapshots=[forged])
        self.assertEqual(set(blocked['ids']), {r['id'] for r in records} - {first})
        self.assertEqual(set(blocked['aggregate']['providers']), set(blocked['ids']))

    def test_inventory_generation_binds_membership_not_changing_quota_samples(self):
        records, inventory = self.collect()
        assert inventory is not None
        with patch.object(bridge, 'fetch', side_effect=bridge.Unavailable('network')), \
             patch.object(bridge.time, 'time', return_value=fixture.NOW+60):
            bridge.run_once(self.home, self.state, usage_dir=self.usage)
        changed = json.loads((self.usage/bridge.PUBLIC_INVENTORY).read_text())
        self.assertEqual(changed, inventory, 'Reload/sync identity must not depend on quota sample clock')
        previous = consumer(records, inventory)['snapshot']
        self.assertEqual(set(consumer([], changed, snapshots=[previous])['ids']), {r['id'] for r in records})

    def test_unknown_stale_or_malformed_inventory_fails_closed_at_all_local_boundaries(self):
        records, inventory = self.collect()
        assert inventory is not None
        ghost = copy.deepcopy(records[0]); ghost.update(id='codex-hermes-' + 'f'*64, principalId='f'*64)
        self.assertEqual(set(consumer(records + [ghost], inventory)['ids']), {r['id'] for r in records})
        candidates = [None, [], {}, dict(inventory, entries=inventory['entries']*2),
                      dict(inventory, generation='0'*64), dict(inventory, contract='forged/1')]
        appended = copy.deepcopy(inventory)
        appended['entries'].append({'id':ghost['id'], 'provider':'openai-codex', 'principalId':'f'*64,
                                    'name':'Codex (Hermes ffffffff)', 'quota':'codex'})
        candidates.append(appended)  # Well-shaped unknown identity cannot reuse a valid generation.
        for key, value in (('name','PRIVATE_NAME@example.invalid'), ('id','codex-hermes-3'),
                           ('provider','https://PRIVATE_URL.invalid'), ('quota','invented')):
            bad = copy.deepcopy(inventory); bad['entries'][0][key] = value; candidates.append(bad)
        for bad in candidates:
            with self.subTest(case=str(bad)[:40]):
                result = consumer(records + [ghost], bad)
                self.assertEqual(result['ids'], [])
                self.assertEqual(result['snapshot']['providers'], {})
        tampered = copy.deepcopy(records)
        tampered[0]['inventoryGeneration'] = '0'*64
        result = consumer(tampered, inventory)
        self.assertEqual(set(result['ids']), {r['id'] for r in records[1:]})

    def test_normalization_preserves_read_only_empty_accounts_stale_status_and_action_negatives(self):
        self.write_pool({'openai-codex':self.rows, 'anthropic':[{'id':'PRIVATE_API','access_token':'PRIVATE_KEY'}]})
        records, inventory = self.collect()
        for r in records:
            r.update(readOnly=False, name='PRIVATE_EMAIL@example.invalid', accounts=[{'id':'PRIVATE_ACCOUNT'}],
                     accountSwitch={'mode':'auto'}, authHelpText='https://PRIVATE_SECRET_URL.invalid',
                     usageStatusText='Waiting for auth', todayTotalTokens=999999, hasLocalStats=True,
                     modelUsage={'PRIVATE_MODEL':{'inputTokens':999999}}, balance={'remaining':999999})
        result = consumer(records, inventory, actions=True)
        self.assertTrue(all(p['readOnly'] and p['accounts'] == [] and p['accountSwitch'] is None
                            and not p['hasLocalStats'] and p['balance'] is None for p in result['display']))
        self.assertNotIn('PRIVATE_', json.dumps(result))
        actions = result['actions']
        self.assertEqual(actions['calls'], [])
        self.assertFalse(actions['addStarted'])
        self.assertTrue(all(not p['needsSignIn'] and p['accounts'] == [] for p in actions['rows']))
        self.assertEqual(actions['nativeControl'], {'calls':3,'addStarted':True,'needsSignIn':True})

    def test_zero_one_many_mixed_reorder_add_remove_reload(self):
        known = {}
        for count in (0,1,3,4,2,0):
            api_rows = [{'id':'PRIVATE_API_' + str(i), 'access_token':'PRIVATE_KEY_' + str(i)} for i in range(count)]
            pool = {'anthropic':api_rows, 'openai-codex':self.rows[:min(count,3)]}
            self.write_pool(pool)
            records, inventory = self.collect()
            self.assertEqual(len(records), count + min(count,3))
            expected = {r['id']:r['name'] for r in records}
            for ident in expected.keys() & known.keys(): self.assertEqual(expected[ident], known[ident])
            self.assertEqual(set(consumer(records, inventory)['ids']), set(expected))
            self.write_pool({p:list(reversed(rows)) for p,rows in reversed(list(pool.items()))})
            reordered, reloaded = self.collect()
            self.assertEqual({r['id']:r['name'] for r in reordered}, expected)
            self.assertEqual(inventory, reloaded)
            known.update(expected)

    def test_malformed_oversized_corrupt_and_no_follow_snapshots_refuse_without_publication_or_backup(self):
        self.collect()
        saved = {p.name:p.read_bytes() for p in self.usage.iterdir()}
        valid = self.auth.read_bytes()
        bad_values = ['{bad', '{"credential_pool":{},"credential_pool":{}}', '[]',
                      json.dumps({'credential_pool':{'anthropic':None}}),
                      json.dumps({'credential_pool':{'anthropic':[None]}}),
                      json.dumps({'credential_pool':{'openai-codex':self.rows*3}}),
                      json.dumps({'credential_pool':{'anthropic':[{}]*(bridge.MAX_CREDENTIALS+1)}}),
                      ' '*(bridge.MAX_AUTH_BYTES+1)]
        for bad in bad_values:
            self.auth.write_text(bad)
            with self.assertRaises(bridge.Unavailable) as ctx: self.collect()
            self.assertEqual(ctx.exception.reason, 'inventory')
            self.assertEqual({p.name:p.read_bytes() for p in self.usage.iterdir()}, saved)
        self.auth.write_bytes(valid)
        for kind in ('symlink','fifo','hardlink','parent'):
            self.auth.unlink()
            target = self.root/'synthetic-sentinel'; target.write_bytes(valid)
            if kind == 'symlink': self.auth.symlink_to(target)
            elif kind == 'fifo': os.mkfifo(self.auth)
            elif kind == 'hardlink': os.link(target, self.auth)
            else:
                self.home.rename(self.home.with_name('held'))
                self.home.symlink_to(self.home.with_name('held'), target_is_directory=True)
                (self.home.with_name('held')/'auth.json').write_bytes(valid)
            with self.assertRaises(bridge.Unavailable): self.collect()
            self.assertEqual(target.read_bytes(), valid)
            if kind == 'parent':
                self.home.unlink(); self.home.with_name('held').rename(self.home)
            else: self.auth.unlink()
            self.auth.write_bytes(valid)
        self.assertFalse(list(self.root.rglob('auth.json.corrupt*')))
        self.collect()

    def test_global_fallback_is_per_provider_and_default_suffix_custom_override_are_confined(self):
        global_auth = self.root/'auth.json'
        global_auth.write_text(json.dumps({'credential_pool':{'anthropic':[{'id':'PRIVATE_GLOBAL'}],
                                                             'openai-codex':[fixture.entry(subject='PRIVATE_SHADOW')]}}))
        self.write_pool({'openai-codex':self.rows[:1], 'anthropic':[]})
        records, _ = self.collect()
        self.assertEqual(len(records), 2)
        self.write_pool({'openai-codex':self.rows[:1], 'anthropic':[{'id':'PRIVATE_PROFILE'}]})
        shadowed, _ = self.collect()
        self.assertEqual(len(shadowed),2)
        self.assertNotEqual({r['id'] for r in records}, {r['id'] for r in shadowed})
        native = self.root/'.hermes-test'
        selected = native/'profiles/selected'; selected.mkdir(parents=True)
        (native/'auth.json').write_text(json.dumps({'credential_pool':{'anthropic':[{'id':'PRIVATE_SUFFIX_GLOBAL'}]}}))
        (selected/'auth.json').write_text('{"credential_pool":{"anthropic":[]}}')
        with patch.dict(os.environ, {'HERMES_DATA_DIR_SUFFIX':'-test'}):
            self.assertEqual(bridge.inventory_pool(selected), {'anthropic':[{'id':'PRIVATE_SUFFIX_GLOBAL'}]})
        override = self.root/'standalone'; override.mkdir()
        (override/'auth.json').write_text('{"credential_pool":{}}')
        self.assertEqual(bridge.inventory_pool(override), {})

    def test_inventory_replace_failure_rolls_back_all_files_then_hash_collision_is_refused(self):
        self.collect()
        saved = {p.name:p.read_bytes() for p in self.usage.iterdir()}
        self.write_pool({'openai-codex':self.rows[:1], 'anthropic':[{'id':'PRIVATE_NEW'}]})
        real_replace = bridge.os.replace
        def fail_inventory(src, dst, **kwargs):
            if str(dst) == bridge.PUBLIC_INVENTORY and kwargs.get('dst_dir_fd') is not None:
                raise OSError('synthetic inventory publication failure')
            return real_replace(src,dst,**kwargs)
        with patch.object(bridge.os, 'replace', side_effect=fail_inventory):
            with self.assertRaises(bridge.Unavailable) as ctx: self.collect()
        self.assertEqual(ctx.exception.reason,'publication')
        self.assertEqual({p.name:p.read_bytes() for p in self.usage.iterdir()},saved)
        self.collect()
        inventory_path = self.usage/bridge.PUBLIC_INVENTORY
        inventory_path.write_text('{"sentinel":"unowned edit"}\n')
        modified = {p.name:p.read_bytes() for p in self.usage.iterdir()}
        with self.assertRaises(bridge.Unavailable): self.collect()
        self.assertEqual({p.name:p.read_bytes() for p in self.usage.iterdir()},modified)

    def test_known_identity_cannot_be_forged_under_another_inventoried_filename(self):
        records, inventory = self.collect()
        files = [r['id'] for r in records]
        records[0] = copy.deepcopy(records[1])
        result = consumer(records, inventory, filenames=files)
        self.assertEqual(result['ids'].count(records[1]['id']), 1)
        self.assertEqual(set(result['snapshot']['providers']), set(files[1:]))

    def test_duplicate_private_publication_records_are_refused_not_silently_overwritten(self):
        record = bridge.build_record(self.rows, {}, self.measured, fixture.NOW)
        record['accounts'].append(copy.deepcopy(record['accounts'][0]))
        with self.assertRaises(bridge.Unavailable): bridge.publish_accounts(self.usage, record)
        record = bridge.build_record([], {}, self.measured, fixture.NOW)
        record['credentials'] = bridge.unavailable_credentials({'anthropic':[{'id':'PRIVATE_API'}]})*2
        with self.assertRaises(bridge.Unavailable): bridge.publish_accounts(self.usage, record)


    def test_inventory_cardinality_bound_accepts_every_entry_and_overflow_refuses_without_truncation(self):
        rows = [{'id':'PRIVATE_BOUND_' + str(i), 'access_token':'PRIVATE_KEY_' + str(i)}
                for i in range(bridge.MAX_CREDENTIALS)]
        self.write_pool({'anthropic':rows})
        try: records, inventory = self.collect()
        except bridge.Unavailable as exc: self.fail('The explicit supported cardinality must be publishable: ' + exc.reason)
        self.assertEqual(len(records), bridge.MAX_CREDENTIALS)
        self.assertEqual(len(consumer(records, inventory)['ids']), bridge.MAX_CREDENTIALS)
        saved = {p.name:p.read_bytes() for p in self.usage.iterdir()}
        self.write_pool({'anthropic':rows + [{'id':'PRIVATE_OVERFLOW'}]})
        with self.assertRaises(bridge.Unavailable) as ctx: self.collect()
        self.assertEqual(ctx.exception.reason,'inventory')
        self.assertEqual({p.name:p.read_bytes() for p in self.usage.iterdir()},saved)


if __name__ == '__main__':
    unittest.main(verbosity=2)
