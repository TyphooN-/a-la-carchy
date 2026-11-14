#!/usr/bin/env python3
"""Production sampler/export -> generated consumer, offline only."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
c=load('rate_producer',ROOT/'extras/local-router-stats/collector.py')
s=load('rate_generator',ROOT/'extras/local-router-stats/stage_plugin.py')
w=load('rate_wire',ROOT/'tests/local-live.py')
SOURCE=Path('/usr/share/omarchy/shell/plugins/agents')

def fixture_live(ledger, now, case='rates'):
    sampler=c.LiveSampler()
    listing=json.dumps({'data':[{'id':'task/model <b>A</b>','status':{'value':'loaded'}}]})
    metrics='llamacpp:prompt_tokens_total 100\nllamacpp:tokens_predicted_total 20'
    bodies=[w.wire(), w.wire(30,12)]
    times=[now-10,now-5]
    if case=='generation': bodies.append(w.wire(30,22)); times.append(now)
    elif case=='cancelled': bodies.append(w.wire(30,12,active=False)); times.append(now)
    elif case=='changed': bodies.append(w.wire(40,22,task=8)); times.append(now)
    else: times=[now-5,now]
    responses=[]
    for body in bodies: responses.extend([listing,metrics,json.dumps(body)])
    with patch.object(c,'get',side_effect=responses), patch.object(c,'worker',return_value=('fixture-boot:20:888',9001)), patch.object(c,'router_pid',return_value=10):
        for at in times: live=c.sample(ledger,at,sampler=sampler)
    if case=='mismatch': live['models'][0]['promptIdentity']=['fixture-boot:20:999',[[0,7]]]
    if case=='malformed': live['models'][0]['generationTokPerSec']='2'
    if case=='unavailable':
        def refused(model,pid,for_slots=False):
            if for_slots: raise ValueError('unqualified')
            return ('fixture-boot:20:888',9001)
        with patch.object(c,'get',side_effect=[listing,metrics]), patch.object(c,'worker',side_effect=refused), patch.object(c,'router_pid',return_value=10):
            live=c.sample(ledger,now,sampler=sampler)
    return ledger.record(now,live)

def consumer(record, now):
    rendered=s.render(SOURCE,'fixture.agents')
    panel=rendered['Panel.qml']
    assert 'objectName: "localTaskRates"' in panel, 'visible generated rate Text missing'
    functions=panel.split('  // a-la-carchy: current-task live consumer\n',1)[1].split('  // a-la-carchy: what the Local section says.',1)[0]
    script='var root={localRateNowMs:'+json.dumps(now*1000)+'};\n'+functions+'\nroot.localLiveFresh=localLiveFresh; root.localLiveRate=localLiveRate; root.localLiveText=localLiveText;\nconsole.log(JSON.stringify(localLiveText('+json.dumps(dict(record,providerId='local'))+')));'
    with tempfile.TemporaryDirectory() as td:
        result=subprocess.run([shutil.which('node'),'-'],input=script,text=True,capture_output=True,timeout=5,env={'HOME':td,'PATH':os.environ.get('PATH',''),'LANG':'C.UTF-8'})
    if result.returncode: raise AssertionError(result.stderr)
    return json.loads(result.stdout)

class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ledger=c.Ledger(Path(self.tmp.name)/'synthetic.db'); self.addCleanup(self.ledger.db.close)
    def record(self, now, case='rates'):
        with tempfile.TemporaryDirectory() as td:
            ledger=c.Ledger(Path(td)/'synthetic.db')
            try: return fixture_live(ledger,now,case)
            finally: ledger.db.close()
    def test_actual_export_and_generated_text_rates_and_independent_expiry(self):
        record=fixture_live(self.ledger,110,'generation')
        target=Path(self.tmp.name)/'local.json'; c.publish(target,record)
        record=json.loads(target.read_text())
        self.assertIn('prompt 4 tok/s · generation 2 tok/s',consumer(record,110))
        self.assertIn('prompt — · generation 2 tok/s',consumer(record,135))
        self.assertIn('stale',consumer(record,140))
        self.assertNotIn('tok/s',consumer(record,140))
        self.assertNotIn('SECRET',json.dumps(record))
    def test_cancellation_task_identity_and_unavailable_are_not_zero(self):
        for case in ('cancelled','changed','unavailable','mismatch','malformed'):
            with self.subTest(case=case):
                text=consumer(self.record(110,case),110)
                self.assertNotIn('prompt 4 tok/s',text)
                self.assertNotIn('0 tok/s',text)
                if case in ('cancelled','changed','unavailable'): self.assertNotIn('generation 2 tok/s',text)
    def test_invalid_missing_stale_future_and_boolean_times(self):
        record=fixture_live(self.ledger,110)
        for bad in (None,True,'110',0,-1,111):
            with self.subTest(time=bad):
                broken=json.loads(json.dumps(record)); broken['localLive']['observedAt']=bad
                self.assertNotIn('tok/s',consumer(broken,110))
        self.assertIn('unavailable',consumer(dict(record,localLive=None),110))
        self.assertNotIn('tok/s',consumer(record,109))
    def test_phase_time_and_identity_validation(self):
        record=fixture_live(self.ledger,110)
        for key,bad in (('promptObservedAt',111),('promptWindowSec',0),('promptTokPerSec',True),
                        ('promptIdentity',['different',[[0,7]]]),('taskIdentity',[[0,8]])):
            with self.subTest(key=key):
                broken=json.loads(json.dumps(record)); broken['localLive']['models'][0][key]=bad
                self.assertNotIn('prompt 4 tok/s',consumer(broken,110))
    def test_malformed_rate_rejects_entire_model_row(self):
        record=fixture_live(self.ledger,110)
        for phase in ('prompt', 'generation'):
            for bad in (True, False, '2', 0, -1, 2**53, [], {}):
                with self.subTest(phase=phase, bad=bad):
                    broken=json.loads(json.dumps(record))
                    broken['localLive']['models'][0][phase+'TokPerSec']=bad
                    text=consumer(broken,110)
                    self.assertIn('unavailable',text)
                    self.assertNotIn('tok/s',text)

    def test_missing_phase_preserves_valid_other_phase(self):
        original=fixture_live(self.ledger,110)
        record=json.loads(json.dumps(original))
        record['localLive']['models'][0]['generationTokPerSec']=None
        self.assertIn('prompt 4 tok/s · generation —',consumer(record,110))
        record=json.loads(json.dumps(original))
        record['localLive']['models'][0]['promptTokPerSec']=None
        self.assertIn('prompt — · generation 2 tok/s',consumer(record,110))

    def test_sampler_scope_does_not_use_engine_rate_fields(self):
        record=fixture_live(self.ledger,110)
        self.assertIn('Sampled wall-clock',consumer(record,110))
        self.assertIn('current tasks',consumer(record,110))
        self.assertNotIn('0 tok/s',consumer(record,110))

if __name__=='__main__': unittest.main(verbosity=2)
