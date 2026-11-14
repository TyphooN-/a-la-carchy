#!/usr/bin/env python3
"""Passive calibration: isolated synthetic publications, never auth/DB/network."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT/'extras/local-router-stats/quota_calibration.py'
NOW = 1800000000
A, B = 'a'*64, 'b'*64


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def stamp(value):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n')
    path.chmod(0o600)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        outer = Path(os.environ['TMPDIR']).resolve()
        assert Path(os.environ['HOME']).is_relative_to(outer.parent), 'private outer HOME required'
        self.temp = tempfile.TemporaryDirectory(prefix='calibration-', dir=outer)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.quota = self.root/'scanner'; self.quota.mkdir(mode=0o700)
        self.canonical = self.root/'canonical.json'
        self.state = self.root/'state.json'
        self.output = self.root/'estimate.json'
        self.time = NOW
        self.reset = NOW + 600000
        self.principals = [A, B]
        self.used = {A: 0.10, B: 0.30}
        self.counter = 100000
        self.extra_rows = []
        self.principal_resets = {}
        self.principal_tiers = {}

    def inputs(self):
        entries = [dict(id='codex-hermes-'+p, provider='openai-codex', principalId=p,
                        name='Codex (Hermes '+p[:8]+')', quota='codex') for p in sorted(self.principals)]
        generation = digest(entries)
        inventory = dict(schemaVersion=1, contract='hermes-credential-inventory/1',
                         generation=generation, entries=entries)
        private_json(self.quota/'.hermes-credential-inventory', inventory)
        files = {'.hermes-credential-inventory':hashlib.sha256((self.quota/'.hermes-credential-inventory').read_bytes()).hexdigest()}
        for e in entries:
            record = dict(schemaVersion=1, id=e['id'], name=e['name'], principalId=e['principalId'],
                          credentialProvider='openai-codex', inventoryGeneration=generation,
                          readOnly=True, ready=True, hasLocalStats=False, hasPromptStats=False,
                          tierLabel=self.principal_tiers.get(e['principalId'],'pro'),
                          limitsStale=False, limitsFetchedAt=self.time*1000,
                          limits=[dict(label='7-day window', title='Weekly',
                                       percent=self.used[e['principalId']],
                                       resetsAt=stamp(self.principal_resets.get(e['principalId'],self.reset)))],
                          usageStatusText='', authHelpText='')
            target = self.quota/(e['id']+'.json'); private_json(target, record)
            files[target.name] = hashlib.sha256(target.read_bytes()).hexdigest()
        private_json(self.quota/'.hermes-codex-publication', dict(version=1, files=files))
        row = dict(session_id='c'*64, model='canonical-route', billing_provider='openai-codex',
                   billing_base_url='', billing_mode='', task='', first_seen=None, last_seen=None,
                   input_tokens=self.counter, output_tokens=100, cache_read_tokens=20,
                   cache_write_tokens=0, reasoning_tokens=50, api_call_count=1)
        private_json(self.canonical, dict(contract='hermes-session-model-usage/1',
            counterSemantics='hermes-canonical/1', feed='hermes-canonical-feed/1',
            sourceBinding='d'*64, configBinding='e'*64, observedAt=stamp(self.time),
            rows=[row,*copy.deepcopy(self.extra_rows)]))

    def cli(self, *extra, expected=0):
        result = subprocess.run([sys.executable, '-I', '-B', str(CLI), '--canonical',str(self.canonical),
            '--quota-dir',str(self.quota), '--profile','default','--state',str(self.state),
            '--output',str(self.output),'--now',stamp(self.time),*extra],
            capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,expected,result.stdout+result.stderr)
        return json.loads(self.output.read_text()) if self.output.exists() else None

    def test_first_observation_is_only_baseline_for_distinct_principals(self):
        self.inputs()
        record = self.cli()
        self.assertEqual(record['contract'],'hermes-quota-calibration/1')
        self.assertEqual([r['principalId'] for r in record['accounts']],[A,B])
        for account in record['accounts']:
            self.assertEqual(account['status'],'calibrating')
            self.assertEqual(account['sampleCount'],0)
            self.assertIsNone(account['estimate'])
        self.assertNotIn('windows',record)
        self.assertNotIn('sources',record)
        self.assertIn('correlation',record['attribution'])
        self.assertEqual(self.state.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.output.stat().st_mode & 0o777,0o600)

    def advance(self, principal=A, points=2, tokens=20000, seconds=600):
        self.time += seconds
        self.counter += tokens
        self.used[principal] += points/100
        self.inputs()
        return self.cli()

    def test_only_the_moving_principal_earns_a_workload_estimate(self):
        self.inputs(); self.cli()
        for _ in range(2):
            record = self.advance()
            self.assertIsNone(record['accounts'][0]['estimate'])
        record = self.advance()
        a,b = record['accounts']
        self.assertEqual(a['status'],'estimated')
        self.assertEqual(a['sampleCount'],3)
        self.assertEqual(a['estimate']['tokensPerQuotaPoint'],10000)
        self.assertAlmostEqual(a['estimate']['usedWeeklyTokens'],160000)
        self.assertEqual(a['estimate']['observedSeconds'],1800)
        self.assertEqual(a['estimate']['observedRange'],[10000,10000])
        self.assertEqual(b['sampleCount'],0)
        self.assertEqual(b['status'],'calibrating')
        self.assertIsNone(b['estimate'])
        self.assertNotIn('totalTokens',a)
        raw = self.state.read_bytes(), self.output.read_bytes()
        self.cli()
        self.assertEqual(raw,(self.state.read_bytes(),self.output.read_bytes()),'same observation must be byte-idempotent')

    def remanifest(self):
        path = self.quota/'.hermes-codex-publication'
        value = json.loads(path.read_text())
        value['files'] = {n:hashlib.sha256((self.quota/n).read_bytes()).hexdigest() for n in value['files']}
        private_json(path,value)

    def test_repeated_quota_clock_cannot_be_consumed_as_a_new_interval(self):
        self.inputs(); self.cli()
        old_state = self.state.read_bytes()
        self.time += 60
        self.inputs()
        for path in self.quota.glob('*.json'):
            value=json.loads(path.read_text()); value['limitsFetchedAt']=NOW*1000
            private_json(path,value)
        self.remanifest()
        self.cli(expected=2)
        self.assertEqual(self.state.read_bytes(),old_state)

    def test_malformed_state_refuses_without_replacing_last_good_export(self):
        self.inputs(); self.cli()
        raw = self.output.read_bytes()
        state=json.loads(self.state.read_text()); state['unexpected']='must not be silently adopted'
        private_json(self.state,state)
        self.time += 600; self.counter += 20000; self.used[A] += .02; self.inputs()
        self.cli(expected=2)
        self.assertEqual(self.output.read_bytes(),raw)
        self.assertEqual(json.loads(self.state.read_text()),state)

    def test_manual_output_edit_is_not_overwritten(self):
        self.inputs(); self.cli()
        state=self.state.read_bytes()
        private_json(self.output,{'owner':'manual'})
        raw=self.output.read_bytes()
        self.cli(expected=2)
        self.assertEqual(self.output.read_bytes(),raw)
        self.assertEqual(self.state.read_bytes(),state)

    def test_foreign_output_is_not_adopted_before_new_journal(self):
        from argparse import Namespace
        from contextlib import ExitStack
        from unittest.mock import patch
        for established in (False,True):
            with self.subTest(established=established):
                case=CalibrationTests('test_first_observation_is_only_baseline_for_distinct_principals')
                case.setUp();self.addCleanup(case.doCleanups)
                case.inputs()
                if established:
                    case.cli();case.time+=600;case.counter+=20000;case.used[A]+=.02;case.inputs()
                old_state=case.state.read_bytes() if case.state.exists() else None
                module=load(CLI,'calibration_output_race_'+str(established))
                adapter=module.sibling('hermes_accounting')
                original_sibling,original_publish=module.sibling,adapter.publish
                slot='replay' if established else 'canonical_snapshot'
                original_hook=getattr(module,slot)
                injected=[];state_writes=[]
                def hook(*args,**kwargs):
                    value=original_hook(*args,**kwargs)
                    if not injected:
                        private_json(case.output,{'owner':'newer-manual-file'})
                        injected.append(True)
                    return value
                def publish(record,path,**kwargs):
                    if path==case.state:state_writes.append(True)
                    return original_publish(record,path,**kwargs)
                args=Namespace(canonical=case.canonical,quota_dir=case.quota,profile='default',
                    state=case.state,output=case.output,now=stamp(case.time),recover=False)
                refused=False
                with ExitStack() as stack:
                    stack.enter_context(patch.object(module,'sibling',lambda name:adapter if name=='hermes_accounting' else original_sibling(name)))
                    stack.enter_context(patch.object(adapter,'publish',publish))
                    stack.enter_context(patch.object(module,slot,hook))
                    try:module.run(args)
                    except ValueError:refused=True
                self.assertEqual(injected,[True],'mutation seam was not reached')
                self.assertTrue(refused,'foreign output was adopted as an authorized preimage')
                self.assertEqual(state_writes,[],'new journal must not commit after detected output drift')
                self.assertEqual(json.loads(case.output.read_text()),{'owner':'newer-manual-file'})
                if old_state is None:self.assertFalse(case.state.exists())
                else:self.assertEqual(case.state.read_bytes(),old_state)

    def test_output_failure_replays_committed_state_without_double_count(self):
        from argparse import Namespace
        from unittest.mock import patch
        self.inputs(); self.cli()
        self.time += 600; self.counter += 20000; self.used[A] += .02; self.inputs()
        module=load(CLI,'calibration_replay')
        adapter=module.sibling('hermes_accounting')
        original_publish=adapter.publish
        def publish(record,path,**kw):
            if path == self.output:
                raise OSError('synthetic output commit failure')
            return original_publish(record,path,**kw)
        args=Namespace(canonical=self.canonical,quota_dir=self.quota,profile='default',
                       state=self.state,output=self.output,now=stamp(self.time),recover=False)
        original_sibling=module.sibling
        with patch.object(adapter,'publish',publish), patch.object(module,'sibling',lambda n:adapter if n=='hermes_accounting' else original_sibling(n)):
            with self.assertRaises(OSError): module.run(args)
        record=self.cli()
        self.assertEqual(record['accounts'][0]['sampleCount'],1)
        raw=self.state.read_bytes(),self.output.read_bytes()
        self.cli()
        self.assertEqual(raw,(self.state.read_bytes(),self.output.read_bytes()))

    def test_mixed_unresolved_and_reset_segments_do_not_yield_estimates(self):
        self.inputs(); self.cli()
        self.advance()
        self.time += 600; self.counter += 20000
        self.used[A] += .02; self.used[B] += .001; self.inputs()
        record=self.cli()
        self.assertTrue(all(a['status']=='ambiguous' and a['estimate'] is None for a in record['accounts']))
        self.assertEqual(record['accounts'][0]['sampleCount'],1)
        self.reset += 100; self.used[A]=0; self.used[B]=0
        self.time += 600; self.inputs()
        record=self.cli()
        self.assertEqual([a['sampleCount'] for a in record['accounts']],[0,0])
        self.assertTrue(all(a['estimate'] is None for a in record['accounts']))
        record=self.advance(points=.01)
        self.assertEqual(record['accounts'][0]['sampleCount'],0)
        self.assertEqual(record['accounts'][0]['reason'],'resolution')

    def test_inventory_rotation_never_borrows_another_principals_samples(self):
        self.inputs(); self.cli()
        for _ in range(3): self.advance()
        new='f'*64
        self.principals=[B,new]; self.used[new]=.1
        self.time += 600; self.counter += 20000; self.inputs()
        record=self.cli()
        self.assertEqual([a['principalId'] for a in record['accounts']],[B,new])
        self.assertEqual([a['sampleCount'] for a in record['accounts']],[0,0])
        self.assertTrue(all(a['estimate'] is None for a in record['accounts']))
        self.principals=[new,B]
        record=self.advance(principal=new)
        self.assertEqual(record['accounts'][1]['sampleCount'],1)
        self.assertEqual(record['accounts'][0]['sampleCount'],0)

    def test_absent_stale_and_malformed_inputs_preserve_last_good(self):
        self.inputs(); self.cli()
        baseline=(self.state.read_bytes(),self.output.read_bytes())
        valid=self.canonical.read_bytes()
        for value in (True,'100',-1,float('nan'),float('inf'),9007199254740992):
            with self.subTest(counter=value):
                record=json.loads(valid); record['rows'][0]['input_tokens']=value
                private_json(self.canonical,record)
                self.cli(expected=2)
                self.assertEqual((self.state.read_bytes(),self.output.read_bytes()),baseline)
        self.canonical.write_bytes(valid)
        for value in (True,'0.1',-1,1.1,float('nan'),float('inf')):
            with self.subTest(quota=value):
                self.inputs()
                path=self.quota/('codex-hermes-'+A+'.json')
                record=json.loads(path.read_text()); record['limits'][0]['percent']=value
                private_json(path,record); self.remanifest(); self.cli(expected=2)
                self.assertEqual((self.state.read_bytes(),self.output.read_bytes()),baseline)
        self.inputs()
        self.time += 121
        self.cli(expected=2)
        self.canonical.unlink()
        self.cli(expected=2)
        self.assertEqual((self.state.read_bytes(),self.output.read_bytes()),baseline)
        self.state.unlink(); self.output.unlink()
        self.cli(expected=2)
        self.assertFalse(self.output.exists())

    def test_decrease_out_of_order_association_and_source_loss_are_rejected(self):
        self.inputs(); self.cli()
        baseline=(self.state.read_bytes(),self.output.read_bytes())
        for change in ('decrease','time','binding','missing-source'):
            with self.subTest(change=change):
                self.time=NOW+600; self.inputs()
                record=json.loads(self.canonical.read_text())
                if change=='decrease': record['rows'][0]['input_tokens']-=1
                if change=='time': record['observedAt']=stamp(NOW-1); self.time=NOW
                if change=='binding': record['sourceBinding']='f'*64
                if change=='missing-source': record['rows']=[]
                private_json(self.canonical,record)
                self.cli(expected=2)
                self.assertEqual((self.state.read_bytes(),self.output.read_bytes()),baseline)

    def test_quantized_windows_include_unmoved_tokens_exactly_once(self):
        # used_percent/100 is published unrounded; integer percents tick one point
        # at a time. Half a point per interval leaves every other interval with
        # Hermes tokens but no visible quota movement (true rate 20000/point).
        self.inputs(); self.cli()
        half,steps=round(self.used[A]*200),[]
        for step in range(1,13):
            half+=1; self.time+=600; self.counter+=10000; self.used[A]=half//2/100
            self.inputs(); steps.append((step,half%2,self.cli()['accounts']))
        claimed=[(step,a['estimate']['tokensPerQuotaPoint']) for step,_,accounts in steps
                 for a in accounts if a['estimate'] is not None]
        self.assertEqual(claimed,[(step,20000) for step in (6,8,10,12)])
        self.assertEqual({a['reason'] for _,unmoved,accounts in steps if unmoved for a in accounts},{'resolution'})
        self.assertTrue(all(a['estimate'] is None for _,unmoved,accounts in steps if unmoved for a in accounts))
        self.assertEqual([s['tokens'] for s in json.loads(self.state.read_text())['samples'][A]],[20000]*6)
        for _ in range(3): record=self.advance(tokens=40000)  # sustained visible movement still calibrates
        a,b=record['accounts']
        self.assertEqual((a['status'],a['sampleCount']),('estimated',9))
        self.assertEqual(a['estimate']['tokensPerQuotaPoint'],20000)
        self.assertIsNone(b['estimate'])

    def test_unmoved_short_pairs_withhold_but_long_gaps_clear_samples(self):
        for seconds in (60,7201):
            with self.subTest(seconds=seconds):
                case=CalibrationTests('test_first_observation_is_only_baseline_for_distinct_principals')
                case.setUp();self.addCleanup(case.doCleanups)
                case.inputs();established=case.cli()
                for _ in range(3): established=case.advance(tokens=40000)
                assert isinstance(established,dict)
                self.assertEqual(established['accounts'][0]['status'],'estimated')
                skipped=case.advance(points=0,tokens=10000,seconds=seconds)
                assert isinstance(skipped,dict)
                self.assertEqual([a['sampleCount'] for a in skipped['accounts']],[3 if seconds==60 else 0,0])
                self.assertTrue(all(a['estimate'] is None for a in skipped['accounts']))
                self.assertEqual({a['reason'] for a in skipped['accounts']},{'resolution'})
                next_tick=case.advance(points=1,tokens=10000)
                assert isinstance(next_tick,dict)
                self.assertEqual(next_tick['accounts'][0]['sampleCount'],4 if seconds==60 else 1)
                sample=json.loads(case.state.read_text())['samples'][A][-1]
                self.assertEqual(sample['tokens'],20000 if seconds==60 else 10000)
                if seconds==60:self.assertEqual(next_tick['accounts'][0]['estimate']['tokensPerQuotaPoint'],20000)
                else:self.assertIsNone(next_tick['accounts'][0]['estimate'])

    def test_minute_cadence_includes_tokens_before_rounded_quota_ticks(self):
        self.inputs(); record=self.cli()
        for minute in range(1,31):
            record=self.advance(seconds=60,tokens=1000,points=1 if minute%10==0 else 0)
            assert isinstance(record,dict)
            a,b=record['accounts']
            self.assertEqual(a['sampleCount'],minute//10)
            self.assertEqual(b['sampleCount'],0)
            self.assertIsNone(b['estimate'])
            if minute<30:self.assertIsNone(a['estimate'])
        assert isinstance(record,dict)
        a=record['accounts'][0]
        self.assertEqual(a['status'],'estimated')
        self.assertEqual(a['estimate']['tokensPerQuotaPoint'],10000)
        self.assertEqual(a['estimate']['usedWeeklyTokens'],130000)
        self.assertEqual(a['estimate']['observedSeconds'],1800)
        samples=json.loads(self.state.read_text())['samples'][A]
        self.assertEqual([s['tokens'] for s in samples],[10000]*3)
        self.assertEqual([(s['start'],s['end']) for s in samples],
                         [(NOW+i*600,NOW+(i+1)*600) for i in range(3)])

    def test_learning_sample_ends_on_a_quota_tick_not_a_flat_poll(self):
        self.inputs(); self.cli()
        self.advance(seconds=60,tokens=1000,points=1)
        for _ in range(4):
            record=self.advance(seconds=60,tokens=1000,points=0)
            assert isinstance(record,dict)
            self.assertEqual(record['accounts'][0]['sampleCount'],0)
            self.assertIsNone(record['accounts'][0]['estimate'])
        self.advance(seconds=60,tokens=1000,points=1)
        sample=json.loads(self.state.read_text())['samples'][A][0]
        self.assertEqual((sample['start'],sample['end'],sample['tokens']),
                         (NOW,NOW+360,6000))
        self.assertAlmostEqual(sample['points'],2)

    def test_minute_cadence_coalesces_without_refreshing_sample_counts(self):
        self.inputs();record=self.cli()
        for minute in range(1,31):
            record=self.advance(seconds=60)
            assert isinstance(record,dict)
            self.assertEqual(record['accounts'][0]['sampleCount'],minute//5)
            self.assertIsNone(record['accounts'][1]['estimate'])
            if minute<30:self.assertIsNone(record['accounts'][0]['estimate'])
        assert isinstance(record,dict)
        self.assertEqual(record['accounts'][0]['status'],'estimated')
        self.assertEqual(record['accounts'][0]['estimate']['tokensPerQuotaPoint'],10000)
        self.assertEqual(record['accounts'][0]['estimate']['observedSeconds'],1800)
        record=self.advance(seconds=60)
        assert isinstance(record,dict)
        self.assertEqual(record['accounts'][0]['sampleCount'],6)
        self.assertEqual(record['accounts'][0]['status'],'estimated')

    def test_coalesced_segment_discards_mixed_principal_prefix(self):
        self.inputs();self.cli()
        self.advance(seconds=60)
        mixed=self.advance(principal=B,seconds=60)
        assert isinstance(mixed,dict)
        self.assertEqual({a['reason'] for a in mixed['accounts']},{'ambiguous'})
        for _ in range(4):
            partial=self.advance(seconds=60)
            assert isinstance(partial,dict)
            self.assertEqual([a['sampleCount'] for a in partial['accounts']],[0,0])
        completed=self.advance(seconds=60)
        assert isinstance(completed,dict)
        self.assertEqual([a['sampleCount'] for a in completed['accounts']],[1,0])
        sample=json.loads(self.state.read_text())['samples'][A][0]
        self.assertEqual((sample['start'],sample['end'],sample['tokens']),
                         (NOW+2*60,NOW+7*60,5*20000))
        self.assertAlmostEqual(sample['points'],5*2)
        self.assertTrue(all(a['estimate'] is None for a in completed['accounts']))

    def test_unresolved_pair_withholds_until_entire_pending_window_resolves(self):
        self.inputs();self.cli()
        for _ in range(3):self.advance(tokens=40000)
        start=self.time
        partial=self.advance(seconds=60,points=1,tokens=20000)
        assert isinstance(partial,dict)
        self.assertEqual((partial['accounts'][0]['status'],partial['accounts'][0]['sampleCount']),('estimated',3))
        unresolved=self.advance(seconds=60,points=0,tokens=10000)
        assert isinstance(unresolved,dict)
        self.assertEqual([a['sampleCount'] for a in unresolved['accounts']],[3,0])
        self.assertTrue(all(a['estimate'] is None for a in unresolved['accounts']))
        self.assertEqual({a['reason'] for a in unresolved['accounts']},{'resolution'})
        self.assertEqual(json.loads(self.state.read_text())['segment']['tokens'],30000)
        completed=self.advance(points=1,tokens=10000)
        assert isinstance(completed,dict)
        self.assertEqual([a['sampleCount'] for a in completed['accounts']],[4,0])
        sample=json.loads(self.state.read_text())['samples'][A][-1]
        self.assertEqual((sample['start'],sample['end'],sample['tokens']),(start,self.time,40000))
        self.assertAlmostEqual(sample['points'],2)
        self.assertEqual(completed['accounts'][0]['estimate']['tokensPerQuotaPoint'],20000)

    def test_legacy_state_recovers_idempotently_and_migrates_on_new_pair(self):
        module=load(CLI,'calibration_legacy')
        self.inputs();self.cli()
        for _ in range(3):self.advance(tokens=40000)
        legacy=json.loads(self.state.read_text());legacy.pop('segment')
        legacy['contract']=module.LEGACY_STATE_CONTRACT
        private_json(self.state,legacy)
        before=(self.state.read_bytes(),self.output.read_bytes())
        recovered=self.cli('--recover')
        assert isinstance(recovered,dict)
        self.assertEqual(recovered['accounts'][0]['status'],'estimated')
        self.assertEqual((self.state.read_bytes(),self.output.read_bytes()),before)
        self.cli()
        self.assertEqual((self.state.read_bytes(),self.output.read_bytes()),before)
        partial=self.advance(seconds=60,points=1,tokens=20000)
        assert isinstance(partial,dict)
        migrated=json.loads(self.state.read_text())
        self.assertEqual(migrated['contract'],module.STATE_CONTRACT)
        self.assertEqual(migrated['samples'],legacy['samples'])
        self.assertEqual(migrated['segment']['at'],legacy['anchor']['at'])
        self.assertEqual(migrated['segment']['tokens'],20000)
        self.assertEqual((partial['accounts'][0]['status'],partial['accounts'][0]['sampleCount']),('estimated',3))
        completed=self.advance(seconds=240,points=4,tokens=80000)
        assert isinstance(completed,dict)
        self.assertEqual(completed['accounts'][0]['sampleCount'],4)
        self.assertEqual(completed['accounts'][0]['estimate']['tokensPerQuotaPoint'],20000)
        self.assertEqual(completed['accounts'][0]['estimate']['observedSeconds'],1800+300)
        self.assertEqual(json.loads(self.state.read_text())['samples'][A][:3],legacy['samples'][A])

    def test_idle_poll_keeps_earned_rate_without_earning_more_coverage(self):
        self.inputs(); self.cli()
        for _ in range(3):self.advance(tokens=40000)
        before=json.loads(self.output.read_text())
        record=self.advance(seconds=60,points=0,tokens=0)
        assert isinstance(record,dict)
        self.assertEqual(record['accounts'][0]['status'],'estimated')
        self.assertEqual(record['accounts'][0]['estimate'],before['accounts'][0]['estimate'])
        self.assertEqual(record['accounts'][0]['sampleCount'],3)
        self.assertGreater(record['observedAt'],before['observedAt'])
        self.assertGreater(record['validUntil'],before['validUntil'])
        for _ in range(13):record=self.advance(seconds=7200,points=0,tokens=0)
        assert isinstance(record,dict)
        self.assertEqual(record['accounts'][0]['sampleCount'],0)
        self.assertIsNone(record['accounts'][0]['estimate'])

    def test_delayed_quota_tick_matches_previous_tokens_without_new_tokens(self):
        self.inputs(); self.cli()
        for _ in range(5):self.advance(seconds=60,points=0,tokens=1000)
        record=self.advance(seconds=60,points=1,tokens=0)
        assert isinstance(record,dict)
        self.assertEqual([a['sampleCount'] for a in record['accounts']],[1,0])
        sample=json.loads(self.state.read_text())['samples'][A][0]
        self.assertEqual((sample['start'],sample['end'],sample['tokens']),(NOW,NOW+360,5000))
        self.assertAlmostEqual(sample['points'],1)
        self.assertTrue(all(a['estimate'] is None for a in record['accounts']))

    def test_mixed_movement_discards_unassigned_plateau_without_reusing_it(self):
        self.inputs(); self.cli()
        for _ in range(2):self.advance(seconds=60,points=0,tokens=1000)
        self.advance(seconds=60,points=1,tokens=1000)
        mixed=self.advance(seconds=60,principal=B,points=1,tokens=1000)
        assert isinstance(mixed,dict)
        self.assertTrue(all(a['status']=='ambiguous' and a['estimate'] is None for a in mixed['accounts']))
        discarded_at=self.time
        self.assertEqual(json.loads(self.state.read_text())['segment']['tokens'],0)
        self.advance(seconds=600,points=1,tokens=10000)
        sample=json.loads(self.state.read_text())['samples'][A][0]
        self.assertEqual((sample['start'],sample['end'],sample['tokens']),(discarded_at,self.time,10000))

    def test_unassigned_plateau_has_bounded_lifetime(self):
        self.inputs(); self.cli()
        self.advance(seconds=60,points=0,tokens=1000)
        record=self.advance(seconds=7200,points=0,tokens=1000)
        assert isinstance(record,dict)
        self.assertEqual({a['reason'] for a in record['accounts']},{'interval'})
        self.assertEqual(json.loads(self.state.read_text())['segment']['tokens'],0)
        start=self.time
        self.advance(seconds=600,points=1,tokens=10000)
        sample=json.loads(self.state.read_text())['samples'][A][0]
        self.assertEqual((sample['start'],sample['end'],sample['tokens']),(start,self.time,10000))

    def test_v2_pending_state_recovers_and_migrates_without_losing_samples(self):
        module=load(CLI,'calibration_v2_legacy')
        self.inputs(); self.cli()
        for _ in range(3):self.advance(tokens=40000)
        self.advance(seconds=60,points=1,tokens=20000)
        legacy=json.loads(self.state.read_text());legacy['contract']=module.PREVIOUS_STATE_CONTRACT
        private_json(self.state,legacy)
        before=(self.state.read_bytes(),self.output.read_bytes())
        self.cli('--recover');self.cli()
        self.assertEqual((self.state.read_bytes(),self.output.read_bytes()),before)
        unresolved=self.advance(seconds=60,points=0,tokens=10000)
        assert isinstance(unresolved,dict)
        self.assertTrue(all(a['estimate'] is None for a in unresolved['accounts']))
        migrated=json.loads(self.state.read_text())
        self.assertEqual(migrated['contract'],module.STATE_CONTRACT)
        self.assertEqual(migrated['samples'],legacy['samples'])
        self.assertEqual(migrated['segment']['tokens'],30000)
        self.advance(seconds=600,points=1,tokens=10000)
        self.assertEqual(json.loads(self.state.read_text())['samples'][A][-1]['tokens'],40000)

    def test_v2_contract_cannot_adopt_new_unassigned_segment_shape(self):
        module=load(CLI,'calibration_v2_refusal')
        self.inputs(); self.cli()
        self.advance(seconds=60,points=0,tokens=1000)
        malformed=json.loads(self.state.read_text());malformed['contract']=module.PREVIOUS_STATE_CONTRACT
        private_json(self.state,malformed)
        before=(self.state.read_bytes(),self.output.read_bytes())
        self.cli(expected=2)
        self.assertEqual((self.state.read_bytes(),self.output.read_bytes()),before)

    def test_malformed_learning_segments_refuse_without_last_good_writes(self):
        module=load(CLI,'calibration_segment_validation')
        self.inputs();self.cli()
        for _ in range(3):self.advance(tokens=40000)
        self.advance(seconds=60,points=1,tokens=20000)
        valid=json.loads(self.state.read_text());output=self.output.read_bytes()
        total=sum(c['total_tokens'] for c in valid['anchor']['counters'].values())
        changes=[
            ('unknown-contract',('contract',),'hermes-quota-calibration-state/999'),
            ('segment-list',('segment',),[]),
            ('boolean-tokens',('segment','tokens'),True),
            ('overflow-tokens',('segment','tokens'),module.MAX_SAFE+1),
            ('excess-cumulative-tokens',('segment','tokens'),total+1),
            ('nonfinite-start',('segment','at'),float('nan')),
            ('future-start',('segment','at'),self.time+1),
            ('old-start',('segment','at'),self.time-module.MAX_GAP-1),
            ('overlapping-earned-sample',('segment','at'),valid['samples'][A][-1]['end']-1),
            ('zero-progress-with-interval',('segment','tokens'),0),
            ('tokens-without-interval',('segment','at'),self.time),
            ('account-membership',('segment','accounts'),{A:valid['segment']['accounts'][A]}),
            ('future-quota-clock',('segment','accounts',A,'fetched'),self.time+1),
            ('unobserved-quota-clock',('segment','accounts',A,'fetched'),0),
            ('quota-clock-outside-start',('segment','accounts',A,'fetched'),valid['segment']['at']-121),
            ('negative-used',('segment','accounts',A,'used'),-.1),
            ('decreasing-used',('segment','accounts',A,'used'),valid['anchor']['accounts'][A]['used']+.01),
            ('unmoved-eligible-progress',('segment','accounts',A,'used'),valid['anchor']['accounts'][A]['used']),
            ('mixed-progress',('segment','accounts',B,'used'),valid['anchor']['accounts'][B]['used']-.01),
        ]
        for name,keys,value in changes:
            with self.subTest(shape=name):
                bad=copy.deepcopy(valid);target=bad
                for key in keys[:-1]:target=target[key]
                target[keys[-1]]=value;private_json(self.state,bad)
                before=self.state.read_bytes()
                self.cli(expected=2)
                self.assertEqual(self.state.read_bytes(),before)
                self.assertEqual(self.output.read_bytes(),output)
        private_json(self.state,valid)
        self.cli()
        self.assertEqual(self.output.read_bytes(),output)
        print('COALESCED_STATE_REFUSALS='+str(len(changes)))

    def test_long_gaps_and_high_dispersion_withhold_estimates(self):
        self.inputs(); self.cli()
        self.advance(); self.advance()
        record=self.advance(tokens=200000)
        self.assertEqual(record['accounts'][0]['sampleCount'],3)
        self.assertIsNone(record['accounts'][0]['estimate'])
        record=self.advance(seconds=7201)
        self.assertIsNone(record['accounts'][0]['estimate'])
        self.assertEqual(record['accounts'][0]['reason'],'interval')

    def test_hardlink_and_temporary_aliases_refuse_before_source_changes(self):
        module=load(CLI,'calibration_alias')
        adapter=module.sibling('hermes_accounting')
        self.inputs()
        raw=self.canonical.read_bytes()
        # The deterministic output temporary is input, not owned cleanup storage.
        temporary=adapter.temporary_path(self.output)
        self.canonical.rename(temporary); self.canonical=temporary
        self.cli(expected=2)
        self.assertEqual(temporary.read_bytes(),raw)
        self.assertFalse(self.output.exists())
        original=self.root/'canonical-again.json'; temporary.rename(original); self.canonical=original
        os.link(original,self.state)
        self.cli(expected=2)
        self.assertEqual(original.read_bytes(),raw)
        self.assertEqual(self.state.read_bytes(),raw)

    def _establish_transition_estimate(self):
        self.inputs();self.cli()
        record=None
        for _ in range(3):record=self.advance(tokens=40000)
        assert isinstance(record,dict)
        self.assertEqual((record['accounts'][0]['status'],record['accounts'][0]['sampleCount']),('estimated',3))

    def _add_transition_source(self, tokens):
        row=copy.deepcopy(json.loads(self.canonical.read_text())['rows'][0])
        row.update(session_id='f'*64,input_tokens=tokens,output_tokens=0,
                   cache_read_tokens=0,cache_write_tokens=0,reasoning_tokens=0,api_call_count=1)
        self.extra_rows.append(row)

    def test_unresolved_transitions_require_fresh_contiguous_calibration(self):
        variants=('source','inventory','other-reset','other-tier','other-decrease')
        for variant in variants:
            with self.subTest(transition=variant):
                case=CalibrationTests('test_first_observation_is_only_baseline_for_distinct_principals')
                case.setUp();self.addCleanup(case.doCleanups)
                case._establish_transition_estimate()
                case.time+=600
                if variant=='source':case._add_transition_source(12000)
                else:case.counter+=12000
                if variant=='inventory':case.principals.remove(B)
                elif variant=='other-reset':case.principal_resets[B]=case.reset+60
                elif variant=='other-tier':case.principal_tiers[B]='plus'
                elif variant=='other-decrease':case.used[B]-=.1
                case.inputs();transition=case.cli()
                assert isinstance(transition,dict)
                self.assertEqual(transition['accounts'][0]['sampleCount'],0)
                self.assertIsNone(transition['accounts'][0]['estimate'])
                state=json.loads(case.state.read_text())
                self.assertEqual((state['segment']['at'],state['segment']['tokens']),(case.time,0))
                for count,tokens in enumerate((12000,20000,20000),1):
                    start=case.time;case.time+=600;case.used[A]+=.01
                    if variant=='source':case.extra_rows[0]['input_tokens']+=tokens
                    else:case.counter+=tokens
                    case.inputs();record=case.cli()
                    assert isinstance(record,dict)
                    account=record['accounts'][0]
                    self.assertEqual(account['sampleCount'],count)
                    sample=json.loads(case.state.read_text())['samples'][A][-1]
                    self.assertEqual((sample['start'],sample['end'],sample['tokens']),(start,case.time,tokens))
                    self.assertAlmostEqual(sample['points'],1)
                    if count<3:self.assertIsNone(account['estimate'])
                    else:
                        self.assertEqual(account['status'],'estimated')
                        self.assertEqual(account['estimate']['observedSeconds'],3*600)
        print('TRANSITION_RECALIBRATION_VARIANTS='+str(len(variants)))

    def test_zero_token_source_transition_preserves_earned_samples(self):
        self._establish_transition_estimate()
        self.time+=600;self._add_transition_source(0);self.inputs()
        record=self.cli()
        assert isinstance(record,dict)
        self.assertEqual(record['accounts'][0]['sampleCount'],3)
        self.assertEqual(record['accounts'][0]['reason'],'sources-changed')
        self.assertIsNone(record['accounts'][0]['estimate'])

    def test_visible_quota_source_transition_preserves_earned_samples(self):
        self._establish_transition_estimate()
        self.time+=600;self.used[A]+=.01;self._add_transition_source(12000);self.inputs()
        record=self.cli()
        assert isinstance(record,dict)
        self.assertEqual(record['accounts'][0]['sampleCount'],3)
        self.assertEqual(record['accounts'][0]['reason'],'sources-changed')
        self.assertIsNone(record['accounts'][0]['estimate'])

    def test_new_canonical_source_is_a_baseline_not_historic_usage(self):
        self.inputs(); self.cli()
        self.time += 600; self.used[A] += .02; self.inputs()
        record=json.loads(self.canonical.read_text())
        row=copy.deepcopy(record['rows'][0]);row['session_id']='f'*64;row['input_tokens']=10000000
        record['rows'].append(row);private_json(self.canonical,record)
        result=self.cli()
        self.assertEqual(result['accounts'][0]['sampleCount'],0)
        self.assertIsNone(result['accounts'][0]['estimate'])
        self.assertEqual(result['accounts'][0]['reason'],'sources-changed')

    def test_unit_render_is_dormant_and_can_drive_the_real_cli(self):
        argv=[sys.executable,'-I','-B',str(CLI),'--canonical',str(self.canonical),
              '--quota-dir',str(self.quota),'--profile','default','--state',str(self.state),
              '--output',str(self.output),'--render-service']
        child=subprocess.run(argv,capture_output=True,text=True,timeout=8)
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertFalse(self.state.exists());self.assertFalse(self.output.exists())
        self.assertIn('UMask=0077',child.stdout)
        self.assertNotIn('ExecStartPre=',child.stdout)
        self.assertNotIn('Requires=',child.stdout)
        command=next(line[len('ExecStart='):] for line in child.stdout.splitlines() if line.startswith('ExecStart='))
        decoder=json.JSONDecoder();args=[]
        while command:
            value,end=decoder.raw_decode(command)
            args.append(value.replace('%%','%').replace('$$','$'));command=command[end:].lstrip()
        self.assertEqual(args[1:3],['-I','-B'])
        self.assertNotIn('--now',args)
        self.assertNotIn('--render-service',args)
        self.inputs()
        # Replay the rendered real entry with explicit synthetic clock only here.
        child=subprocess.run(args+['--now',stamp(self.time)],capture_output=True,text=True,timeout=8)
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertEqual(json.loads(self.output.read_text())['accounts'][0]['status'],'calibrating')

    def test_capacity_bounds_are_serializable_and_corrupt_journals_are_not_adopted(self):
        module=load(CLI,'calibration_capacity');adapter=module.sibling('hermes_accounting')
        self.principals=[format(i,'064x') for i in range(module.MAX_ACCOUNTS)]
        self.reset=NOW+500000
        self.used={p:.5 for p in self.principals};self.inputs()
        record=self.cli()
        self.assertEqual(len(record['accounts']),module.MAX_ACCOUNTS)
        state=json.loads(self.state.read_text())
        original=copy.deepcopy(state)
        at=state['anchor']['at']
        sample=dict(start=at-7200,end=at-6900,tokens=999999999,points=1.2345678901234567)
        for principal in state['samples']:
            state['samples'][principal]=[dict(sample,start=at-7200+i*300,end=at-6900+i*300) for i in range(24)]
            state['status'][principal]='usable'
        counts=next(iter(state['anchor']['counters'].values()))
        state['anchor']['counters']={format(i,'064x'):copy.deepcopy(counts) for i in range(module.MAX_SOURCES)}
        state['transaction']['payload']=hashlib.sha256(adapter.payload_bytes(module.project(state))).hexdigest()
        module.validate_state(state,adapter,state['config'])
        self.assertLess(len(adapter.payload_bytes(state)),module.MAX_BYTES)
        self.assertLess(len(adapter.payload_bytes(module.project(state))),module.MAX_BYTES)
        print('PASSIVE_CAPACITY_PROOF='+json.dumps(dict(accounts=256,sources=512,samplesPerAccount=24,
            stateBytes=len(adapter.payload_bytes(state)),outputBytes=len(adapter.payload_bytes(module.project(state))))))
        state['samples'][self.principals[0]].append(sample)
        with self.assertRaises(ValueError):module.validate_state(state,adapter,state['config'])
        state=copy.deepcopy(original);state['anchor']['accounts']['f'*64]=copy.deepcopy(next(iter(state['anchor']['accounts'].values())))
        with self.assertRaises(ValueError):module.validate_state(state,adapter,state['config'])
        state=copy.deepcopy(original);state['anchor']['counters']['f'*64]={}
        with self.assertRaises(ValueError):module.validate_state(state,adapter,state['config'])

    def test_real_sanitized_producer_contract_uses_used_fraction(self):
        bridge=load(ROOT/'extras/hermes-codex-usage/collector.py','quota_producer_contract')
        raw=dict(rate_limit=dict(primary_window=dict(used_percent=25,limit_window_seconds=604800,reset_at=self.reset)))
        normalized=bridge.normalize_quota(raw,self.time)
        self.assertEqual(normalized['limits'][0]['percent'],.25)
        record=dict(accounts=[dict(id=A,**normalized,fetchedAt=self.time*1000,stale=False,usageStatusText='')])
        bridge.publish_accounts(self.quota,record)
        self.inputs()  # canonical fixture only; rewrite producer publications next
        # Remove only synthetic publications so the real producer can own them.
        for path in self.quota.iterdir():path.unlink()
        bridge.publish_accounts(self.quota,record)
        result=self.cli()
        self.assertEqual([a['principalId'] for a in result['accounts']],[A])
        self.assertEqual(result['accounts'][0]['quotaUsed'],.25)
        self.assertIsNone(result['accounts'][0]['estimate'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
