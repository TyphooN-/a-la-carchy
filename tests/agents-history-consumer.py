#!/usr/bin/env python3
"""Real synthetic accounting CLI -> staged JavaScript consumer; no live inputs."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXTRA = ROOT / 'extras/local-router-stats'


def consume(text):
    consumer = EXTRA / 'history_consumer.js'
    assert consumer.is_file(), 'history consumer is not implemented'
    program = consumer.read_text() + "\nconsole.log(JSON.stringify(parseHistory(require('fs').readFileSync(0, 'utf8'))));"
    node = shutil.which('node')
    assert node, 'Node is required for shared consumer proof'
    child = subprocess.run([node, '-e', program], input=text, capture_output=True, text=True, timeout=10)
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


def fixture(folder, case='observed', *, provider='openai-codex', include_local=False, history=True):
    """Both calls use the actual explicitly-input CLI and private destinations."""
    endpoint = {'openai-codex': 'https://api.openai.com/v1',
                'anthropic': 'https://api.anthropic.com'}[provider]
    row = dict(session_id='PRIVATE_SESSION', model='PRIVATE_MODEL', billing_provider=provider,
               billing_base_url=endpoint, billing_mode='subscription_included', task='',
               api_call_count=1, input_tokens=100, output_tokens=20, cache_read_tokens=30,
               cache_write_tokens=5, reasoning_tokens=7, first_seen=0, last_seen=1)
    destination = folder / 'omarchy/hermes-token-accounting'
    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
    for when in ['2024-02-29T15:00:00Z', '2024-02-29T15:05:00Z'][:1 if case == 'baseline' else 2]:
        if when.endswith('05:00Z') and case not in ('zero', 'cumulative-zero'):
            row.update(input_tokens=110, output_tokens=25, cache_read_tokens=32,
                       cache_write_tokens=6, reasoning_tokens=9, api_call_count=2)
        if case == 'cumulative-zero':
            row.update({field: 0 for field in ('input_tokens', 'output_tokens', 'cache_read_tokens',
                                              'cache_write_tokens', 'reasoning_tokens', 'api_call_count')})
        rows = [] if case == 'empty' else [row]
        if include_local:
            # Synthetic overlap only: no endpoint is ever contacted. A Local-route
            # canonical row must not become a second Cloud/Local contribution.
            rows.append(dict(row, session_id='PRIVATE_LOCAL_SESSION', billing_provider='openai',
                             billing_base_url='http://127.0.0.1:18080/v1',
                             input_tokens=999999, output_tokens=0, cache_read_tokens=0,
                             cache_write_tokens=0, reasoning_tokens=0))
        snapshot = folder / 'synthetic-snapshot.json'
        snapshot.write_text(json.dumps({'contract':'hermes-session-model-usage/1',
                                        'counterSemantics':'hermes-canonical/1', 'rows':rows}))
        argv = [sys.executable, str(EXTRA / 'hermes_accounting.py'), '--json', str(snapshot),
                '--profile', 'default', '--output', str(destination/'usage.json')]
        if history:
            argv += ['--history-state', str(destination/'history-state.json'),
                     '--history-output', str(destination/'history.json'), '--observed-at', when,
                     '--timezone', 'America/New_York']
        env = dict(os.environ, HOME=str(folder), XDG_STATE_HOME=str(folder), XDG_CONFIG_HOME=str(folder),
                   XDG_CACHE_HOME=str(folder), PYTHONDONTWRITEBYTECODE='1')
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
            env.pop(name, None)
        child = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10)
        assert child.returncode == 0, (argv, child.returncode, child.stderr)
        outputs = [destination/'usage.json']
        if history:
            outputs += [destination/'history.json', destination/'history-state.json']
        print('ACCOUNTING_CLI ' + json.dumps({'argv':argv, 'exit':child.returncode,
              'snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),
              'outputs':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in outputs}}), flush=True)
    return destination/('history.json' if history else 'usage.json')


class ConsumerTests(unittest.TestCase):
    def test_cli_publishes_native_cumulative_destination(self):
        with tempfile.TemporaryDirectory(prefix='hc-', dir=os.environ['TMPDIR']) as td:
            history_path = fixture(Path(td))
            cumulative_path = history_path.with_name('usage.json')
            self.assertTrue(cumulative_path.is_file(),
                            'actual CLI fixture must publish the cumulative file watched by native Main/Agent')
            record = json.loads(cumulative_path.read_text())
            self.assertEqual(record['contract'], 'hermes-token-accounting/1')
            self.assertEqual(len(record['sources']), 1)
            self.assertEqual(record['sources'][0]['windows'],
                             dict(today=None, week=None, history=sum((110, 25, 32, 6))))
            self.assertFalse(history_path.with_name('accounting.json').exists())

    def test_states_and_invalid_records_fail_closed(self):
        self.assertEqual(consume('')['state'], 'malformed')
        for case, state in [('baseline','baseline'), ('empty','empty'), ('zero','zero')]:
            with tempfile.TemporaryDirectory(prefix='hc-', dir=os.environ['TMPDIR']) as td:
                data = fixture(Path(td), case).read_text()
                result = consume(data)
                self.assertEqual(result['state'], state)
                if case == 'baseline':
                    self.assertTrue(all(x['value'] is None for x in result['summaries']))
                if case == 'zero':
                    self.assertTrue(all(x['value'] == 0 for x in result['summaries']))
        with tempfile.TemporaryDirectory(prefix='hc-', dir=os.environ['TMPDIR']) as td:
            data = json.loads(fixture(Path(td)).read_text())
        import copy
        mutations = [lambda x: x.update(contract='other'),
                     lambda x: x['sources'][0].update(id='secret'),
                     lambda x: x['sources'][0].update(provider='local'),
                     lambda x: x['buckets']['week'][0].update(key='2024-02-27'),
                     lambda x: x['buckets']['day'][0].update(key='2024-02-30'),
                     lambda x: x['buckets']['day'][0]['sources'][0]['counts'].update(total_tokens=9007199254740992),
                     lambda x: x['buckets']['day'][0]['sources'][0]['counts'].update(reasoning_tokens=999),
                     lambda x: x['buckets']['day'][0]['sources'][0].update(id='0'*64),
                     lambda x: x['sources'].append(copy.deepcopy(x['sources'][0])),
                     lambda x: x['coverage'].update(missingHistory=False),
                     lambda x: x['events'][-1]['counts'].update(input_tokens=11, prompt_tokens=14, total_tokens=19),
                     lambda x: x.update(events=[])]
        for mutate in mutations:
            broken = copy.deepcopy(data)
            mutate(broken)
            with self.subTest(mutation=mutate):
                result = consume(json.dumps(broken))
                self.assertEqual(result['state'], 'malformed')
                self.assertEqual(result['bars'], [])
        duplicate = json.dumps(data).replace('"generation": 2', '"generation": 2, "generation": 2')
        self.assertNotEqual(duplicate, json.dumps(data))
        self.assertEqual(consume(duplicate)['state'], 'malformed')
        self.assertEqual(consume(' '* (4*1024*1024+1))['state'], 'malformed')

    def test_retention_gaps_and_partial_sources_use_actual_backend_projection(self):
        def load(name):
            spec = importlib.util.spec_from_file_location(name, EXTRA/(name+'.py'))
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        adapter, history = load('hermes_accounting'), load('usage_history')
        row = dict(session_id='synthetic', model='synthetic', billing_provider='openai-codex',
                   billing_base_url='https://api.openai.com/v1', billing_mode='subscription_included', task='',
                   api_call_count=1, input_tokens=100, output_tokens=20, cache_read_tokens=30,
                   cache_write_tokens=5, reasoning_tokens=7, first_seen=0, last_seen=1)
        state = history.observe(None, adapter.canonical_snapshot([row], 'default'),
                                observed_at='2024-01-01T12:00:00Z', timezone='UTC')
        import datetime as dt
        for day in range(35):
            time = dt.datetime(2024,1,1,12,tzinfo=dt.timezone.utc)+dt.timedelta(days=day)
            if day:
                state = history.observe(state, adapter.canonical_snapshot([row], 'default'),
                                        observed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ'), timezone='UTC')
            row['input_tokens'] += 10
            state = history.observe(state, adapter.canonical_snapshot([row], 'default'),
                                    observed_at=(time+dt.timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ'), timezone='UTC')
        data = history.project_history(state)
        self.assertEqual(len(data['buckets']['day']),31)
        self.assertGreater(data['coverage']['pruned']['day']['total_tokens'],0)
        result = consume(json.dumps(data))
        self.assertEqual(result['state'],'observed')
        self.assertIn('day: 31 retained', result['help'])
        self.assertIn('pruned 40', result['help'])
        self.assertEqual(len(result['bars']),7)
        self.assertTrue(all(b['value']==10 for b in result['bars']))
        # An absent source leaves its old retained recording, not a new zero.
        state = history.observe(state, adapter.canonical_snapshot([], 'default'),
                                observed_at='2024-02-04T12:10:00Z', timezone='UTC')
        self.assertEqual(consume(json.dumps(history.project_history(state)))['bars'],result['bars'])
        with self.subTest(case='gap without date allocation'):
            initial = history.observe(None,adapter.canonical_snapshot([row],'default'),
                                      observed_at='2024-02-29T00:00:00Z',timezone='UTC')
            row['input_tokens'] += 10
            public = history.project_history(history.observe(initial,adapter.canonical_snapshot([row],'default'),
                                             observed_at='2024-02-29T07:00:00Z',timezone='UTC'))
            unknown = consume(json.dumps(public))
            self.assertEqual(unknown['state'],'observed')
            self.assertEqual(unknown['bars'],[])
            self.assertTrue(all(s['value'] is None for s in unknown['summaries']))

    def test_newly_baselined_selection_never_inherits_another_sources_zero(self):
        with tempfile.TemporaryDirectory(prefix='hc-',dir=os.environ['TMPDIR']) as td:
            folder=Path(td);destination=folder/'history';destination.mkdir(mode=0o700)
            row=dict(session_id='synthetic-first',model='fixture',billing_provider='openai-codex',
                billing_base_url='https://api.openai.com/v1',billing_mode='',task='',
                input_tokens=100,output_tokens=20,cache_read_tokens=30,cache_write_tokens=5,
                reasoning_tokens=7,api_call_count=1,first_seen=0,last_seen=1)
            source=folder/'input.json'
            for when,rows in [('2024-02-29T15:00:00Z',[row]),
                              ('2024-02-29T15:05:00Z',[row,dict(row,session_id='synthetic-new')])]:
                source.write_text(json.dumps({'contract':'hermes-session-model-usage/1',
                    'counterSemantics':'hermes-canonical/1','rows':rows}));source.chmod(0o600)
                argv=[sys.executable,'-I','-B',str(EXTRA/'hermes_accounting.py'),'--json',str(source),
                    '--profile','default','--output',str(destination/'usage.json'),
                    '--history-state',str(destination/'state.json'),'--history-output',str(destination/'history.json'),
                    '--timezone','America/New_York','--observed-at',when]
                child=subprocess.run(argv,capture_output=True,text=True,timeout=10)
                self.assertEqual(child.returncode,0,child.stderr)
            text=(destination/'history.json').read_text();data=json.loads(text)
            fresh=next(s['id'] for s in data['sources'] if s['coverage']['events']['measured_zero']==0)
            old=next(s['id'] for s in data['sources'] if s['coverage']['events']['measured_zero']==1)
            def module(path,name):
                spec=importlib.util.spec_from_file_location(name,path)
                assert spec is not None and spec.loader is not None
                value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
            stager=module(EXTRA/'stage_plugin.py','selected_history_stage')
            fixture_source=module(ROOT/'tests/agents_fixture.py','selected_history_fixture')
            panel=stager.render(fixture_source.make_source(folder/'native','limits'),'fixture.history')['Panel.qml']
            # The panel's own status binding; the source filter below is backend API only.
            label='              text: ({observed:'
            self.assertEqual(panel.count(label), 1, 'unique current generated status binding required')
            expression=label.strip()[6:]+panel.split(label,1)[1].split('\n              color:',1)[0]
            program=(EXTRA/'history_consumer.js').read_text()+'''
const seed=JSON.parse(require('fs').readFileSync(0,'utf8'));const report=parseHistory(seed.text);
function view(ids){const selected=selectHistory(report,{sources:ids});
 const root={observedHistory:report,historyView:selected};
 return {state:selected.state,label:eval(seed.expression),summaries:selected.summaries,bars:selected.bars};}
console.log(JSON.stringify({global:report.state,fresh:view([seed.fresh]),old:view([seed.old]),empty:view([])}));'''
            node=shutil.which('node');assert node is not None
            child=subprocess.run([node,'-e',program],capture_output=True,text=True,timeout=10,
                input=json.dumps(dict(text=text,fresh=fresh,old=old,expression=expression)))
            self.assertEqual(child.returncode,0,child.stderr);result=json.loads(child.stdout)
            self.assertEqual(result['global'],'zero')
            self.assertEqual(result['fresh']['state'],'baseline')
            self.assertEqual(result['fresh']['label'],'Undated baseline')
            self.assertTrue(all(s['value'] is None for s in result['fresh']['summaries']))
            self.assertTrue(all(b['value'] is None for b in result['fresh']['bars']))
            self.assertEqual((result['old']['state'],result['old']['label']),('zero','Measured zero'))
            # An explicit empty backend filter stays unknown; the panel never filters sources.
            self.assertEqual(result['empty']['state'],'empty')
            self.assertTrue(all(s['value'] is None for s in result['empty']['summaries']))
            self.assertEqual(result['empty']['bars'],[])

    def test_exact_reader_byte_bound_preserves_valid_json_and_refuses_tail(self):
        with tempfile.TemporaryDirectory(prefix='hc-',dir=os.environ['TMPDIR']) as td:
            original=fixture(Path(td)).read_text()
        # Pinned public reader contract: 512 identities, 61 buckets, 16 MiB.
        cap=16*1024*1024
        exact=original+' '*(cap-len(original.encode()))
        self.assertEqual(len(exact.encode()),cap)
        self.assertEqual(consume(exact)['state'],'observed')
        self.assertEqual(consume(exact+' ')['state'],'malformed')
        self.assertEqual(consume(original+' '*cap+'{}')['state'],'malformed')
        # Valid JSON and ID syntax cannot evade the distinct public source cap.
        from copy import deepcopy
        with tempfile.TemporaryDirectory(prefix='hc-',dir=os.environ['TMPDIR']) as td:
            baseline=json.loads(fixture(Path(td),'baseline').read_text())
        base=baseline['sources'][0]
        for number in (512,513):
            data=deepcopy(baseline)
            data['sources']=[dict(deepcopy(base),id=format(i,'064x')) for i in range(number)]
            data['events']=[]
            for source in data['sources']: source['coverage']['prunedEvents']=1
            data['coverage']['events']['baseline']=number;data['coverage']['prunedEvents']=number
            self.assertEqual(consume(json.dumps(data))['state'],'baseline' if number==512 else 'malformed')
        print('CAPACITY_READER_PROOF='+json.dumps({'exact_bytes':cap,'accepted':'observed',
            'beyond_bytes':cap+1,'accepted_source_count':512,'rejected_source_count':513,'tail_refused':True}))

    def test_real_cli_baseline_then_later_to_consumer(self):
        with tempfile.TemporaryDirectory(prefix='hc-', dir=os.environ['TMPDIR']) as td:
            path = fixture(Path(td))
            text = path.read_text()
            result = consume(text)
            self.assertEqual(result['state'], 'observed')
            self.assertEqual([s['value'] for s in result['summaries']], [18]*4)
            self.assertEqual([s['key'] for s in result['summaries']],
                             ['2024-02-29','2024-02-26','2024-02','2024'])
            self.assertEqual(result['bars'][-1]['value'], 18)
            self.assertNotIn('PRIVATE_', json.dumps(result))
            self.assertTrue(result['missingHistory'])


if __name__ == '__main__':
    unittest.main()
