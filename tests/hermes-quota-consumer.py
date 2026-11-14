#!/usr/bin/env python3
"""Execute generated calibration consumer helpers; no native shell/socket is used."""
import copy
import json
import os
from pathlib import Path
import subprocess
import unittest
import importlib.util

ROOT=Path(__file__).resolve().parents[1]


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


f=load(ROOT/'tests/hermes-quota-calibration.py','calibration_consumer_fixture')
source_fixture=load(ROOT/'tests/agents_fixture.py','calibration_source_fixture')
stager=load(ROOT/'extras/local-router-stats/stage_plugin.py','calibration_consumer_stager')
extract=load(ROOT/'tests/grok-native-panel.py','calibration_extract')


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=f.CalibrationTests('test_first_observation_is_only_baseline_for_distinct_principals')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.fixture.inputs(); self.fixture.cli()
        for _ in range(3): self.fixture.advance()
        self.record=json.loads(self.fixture.output.read_text())
        self.source=source_fixture.make_source(self.fixture.root/'native','limits')
        self.rendered=stager.render(self.source,'fixture.calibration')
        self.main,self.panel=self.rendered['Main.qml'],self.rendered['Panel.qml']
        self.provider=json.loads((self.fixture.quota/('codex-hermes-'+f.A+'.json')).read_text())

    def consumer(self, record=None, provider=None, now=None):
        self.assertTrue('function quotaCalibrationFor(' in self.main,'generated consumer has no passive per-principal seam')
        functions='\n'.join(extract.function(self.main,name) for name in ('quotaCalibrationFor',))
        functions+='\n'+extract.function(self.panel,'quotaCalibrationText')
        program='''const vm=require('node:vm');
const seed=JSON.parse(require('fs').readFileSync(0,'utf8'));
const ctx={quotaCalibration:seed.record,hermesInventory:{generation:seed.provider.inventoryGeneration},
    nowMs:seed.now*1000,usage:{formatTokenCount:n=>String(n)}}; ctx.root=ctx;
vm.createContext(ctx);vm.runInContext(seed.functions,ctx,{timeout:1000});
const value=ctx.quotaCalibrationFor(seed.provider);
console.log(JSON.stringify({value,text:ctx.quotaCalibrationText({providerId:seed.provider.id,quotaCalibration:value})}));'''
        import shutil
        node=shutil.which('node')
        self.assertIsNotNone(node,'Node is required for generated JavaScript proof')
        child=subprocess.run([node,'-e',program],capture_output=True,text=True,timeout=5,
            input=json.dumps(dict(record=self.record if record is None else record,
                                 provider=self.provider if provider is None else provider,
                                 now=self.fixture.time if now is None else now,functions=functions)))
        self.assertEqual(child.returncode,0,child.stderr)
        return json.loads(child.stdout)

    def test_rounded_quota_learning_reaches_the_real_generated_card_text(self):
        case=f.CalibrationTests('test_first_observation_is_only_baseline_for_distinct_principals')
        case.setUp(); self.addCleanup(case.doCleanups)
        case.inputs(); case.cli()
        record=None
        for minute in range(1,31):
            record=case.advance(seconds=60,tokens=1000,points=1 if minute%10==0 else 0)
        provider=json.loads((case.quota/('codex-hermes-'+f.A+'.json')).read_text())
        assert isinstance(record,dict)
        rendered=self.consumer(record=record,provider=provider,now=case.time)
        self.assertIn('Weekly tokens ≈ 130000',rendered['text'])
        self.assertIn('3 samples',rendered['text'])
        self.assertIn('workload-dependent',rendered['text'])
        other=json.loads((case.quota/('codex-hermes-'+f.B+'.json')).read_text())
        self.assertNotIn('≈',self.consumer(record=record,provider=other,now=case.time)['text'])
        pending=case.advance(seconds=60,tokens=1000,points=0)
        provider=json.loads((case.quota/('codex-hermes-'+f.A+'.json')).read_text())
        withheld=self.consumer(record=pending,provider=provider,now=case.time)
        self.assertIn('calibrating',withheld['text'])
        self.assertNotIn('≈',withheld['text'])

    def test_generated_consumer_matches_principal_and_expires_without_a_write(self):
        result=self.consumer()
        self.assertIn('Weekly tokens ≈ 160000',result['text'])
        self.assertIn('workload-dependent',result['text'])
        self.assertIn('3 samples',result['text'])
        self.assertIn('correlation',result['text'])
        b=json.loads((self.fixture.quota/('codex-hermes-'+f.B+'.json')).read_text())
        self.assertIn('calibrating',self.consumer(provider=b)['text'])
        self.assertNotIn('≈',self.consumer(provider=b)['text'])
        self.assertIn('calibrating',self.consumer(now=self.fixture.time+121)['text'])
        self.assertIn('text: root.quotaCalibrationText(section.provider)',self.panel)
        self.assertIn('quotaCalibration: quotaCalibrationFor(record)',self.main)
        self.assertIn('quotaCalibrationMaxBytes: 1048576',self.main)
        self.assertIn('["/usr/bin/head", "-c", "1048577", "--", root.quotaCalibrationPath]',self.main)
        self.assertNotIn('quotaCalibration',extract.function(self.main,'localSnapshot'))
        self.assertNotIn('quotaCalibration',extract.function(self.main,'recordedTokenTotalsFor'))
        stager.write(self.source,self.fixture.root/'candidate',self.rendered)
        self.assertEqual((self.fixture.root/'candidate/Main.qml').read_text(),self.main)

    def test_wrong_generation_reset_quota_or_principal_is_not_borrowed(self):
        for update in ({'principalId':f.B},{'inventoryGeneration':'f'*64}):
            with self.subTest(update=update):
                p=copy.deepcopy(self.provider);p.update(update)
                self.assertIn('calibrating',self.consumer(provider=p)['text'])
        p=copy.deepcopy(self.provider);p['limits'][0]['percent']=.9
        self.assertIn('calibrating',self.consumer(provider=p)['text'])
        record=copy.deepcopy(self.record);record['accounts'].append(record['accounts'][0])
        self.assertIn('calibrating',self.consumer(record=record)['text'])
        for value in (True,'10000',-1,None):
            with self.subTest(value=value):
                record=copy.deepcopy(self.record)
                record['accounts'][0]['estimate']['tokensPerQuotaPoint']=value
                self.assertIn('calibrating',self.consumer(record=record)['text'])

    def test_fractional_producer_resets_join_the_real_backend_export(self):
        for microseconds in (1,1000,123456,999999):
            with self.subTest(microseconds=microseconds):
                self.fixture.reset=f.NOW+600000+microseconds/1000000
                self.fixture.advance()  # a changed reset establishes a new window baseline
                for _ in range(3):self.fixture.advance()
                self.record=json.loads(self.fixture.output.read_text())
                self.provider=json.loads((self.fixture.quota/('codex-hermes-'+f.A+'.json')).read_text())
                self.assertEqual(self.record['accounts'][0]['status'],'estimated')
                result=self.consumer()
                self.assertIn('Weekly tokens ≈',result['text'])
                self.assertIsNotNone(result['value'])

    def test_quota_clock_must_be_observed_aligned_and_covered_by_expiry(self):
        at=self.fixture.time
        for label,clock in (('zero',0),('negative',-1),('stale',at-121),
                            ('future',at+1),('unaligned',at+121),('uncovered',at-90)):
            with self.subTest(label=label):
                record=copy.deepcopy(self.record);provider=copy.deepcopy(self.provider)
                record['accounts'][0]['quotaObservedAt']=clock
                provider['limitsFetchedAt']=clock*1000
                result=self.consumer(record=record,provider=provider)
                self.assertIn('calibrating',result['text'])
                self.assertNotIn('≈',result['text'])
        record=copy.deepcopy(self.record);provider=copy.deepcopy(self.provider)
        record['accounts'][0]['quotaObservedAt']=at-90
        record['validUntil']=at+30
        provider['limitsFetchedAt']=(at-90)*1000
        self.assertIn('≈',self.consumer(record=record,provider=provider)['text'])
        self.assertIn('≈',self.consumer(record=record,provider=provider,now=at+29)['text'])
        self.assertIn('calibrating',self.consumer(record=record,provider=provider,now=at+30)['text'])

    def test_quota_clock_join_avoids_millisecond_round_trip_loss(self):
        for offset in range(1,10):
            with self.subTest(offset=offset):
                record=copy.deepcopy(self.record);provider=copy.deepcopy(self.provider)
                provider['limitsFetchedAt']=self.fixture.time*1000+offset
                record['accounts'][0]['quotaObservedAt']=provider['limitsFetchedAt']/1000
                self.assertIn('≈',self.consumer(record=record,provider=provider,now=self.fixture.time+1)['text'])

    def test_coercible_quota_timestamps_are_not_a_valid_estimate(self):
        for field in ('quotaObservedAt','resetsAt'):
            with self.subTest(field=field):
                record=copy.deepcopy(self.record)
                record['accounts'][0][field]=str(record['accounts'][0][field])
                self.assertIn('calibrating',self.consumer(record=record)['text'])

    def test_reader_waits_for_both_successful_exit_and_complete_stream(self):
        functions=extract.function(self.main,'applyQuotaCalibrationRead')
        program='''const vm=require('node:vm');const s=JSON.parse(require('fs').readFileSync(0,'utf8'));
const c={quotaCalibration:{old:true},quotaCalibrationStreamDone:false,quotaCalibrationExitDone:false,
 quotaCalibrationExitOk:false,quotaCalibrationBuffer:JSON.stringify(s.record),quotaCalibrationMaxBytes:1048576,dataRevision:0};
vm.createContext(c);vm.runInContext(s.functions,c,{timeout:1000});
c.quotaCalibrationStreamDone=true;c.applyQuotaCalibrationRead();const waiting=c.quotaCalibration.old===true;
c.quotaCalibrationExitDone=true;c.applyQuotaCalibrationRead();const failed=c.quotaCalibration===null;
c.quotaCalibrationBuffer=JSON.stringify(s.record);c.quotaCalibrationExitOk=true;c.applyQuotaCalibrationRead();const accepted=c.quotaCalibration.contract;
c.quotaCalibrationBuffer=' '.repeat(1048577);c.applyQuotaCalibrationRead();const bound=c.quotaCalibration===null;
console.log(JSON.stringify({waiting,failed,accepted,bound}));'''
        import shutil
        child=subprocess.run([shutil.which('node'),'-e',program],capture_output=True,text=True,timeout=5,
            input=json.dumps(dict(record=self.record,functions=functions)))
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertEqual(json.loads(child.stdout),dict(waiting=True,failed=True,accepted='hermes-quota-calibration/1',bound=True))
        file=self.fixture.root/'oversized-output'
        file.write_bytes(b' '*1048577)
        command=subprocess.run(['/usr/bin/head','-c','1048577','--',str(file)],capture_output=True,timeout=5)
        self.assertEqual(command.returncode,0)
        self.assertEqual(len(command.stdout),1048577)


if __name__ == '__main__':
    unittest.main(verbosity=2)
