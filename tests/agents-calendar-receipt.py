#!/usr/bin/env python3
"""Generated receipt JS + real consumer. Qt callbacks are synthetic, not native proof."""
import ast
import json
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]

class CalendarReceiptTests(unittest.TestCase):
    def receipt(self, scenario):
        tree=ast.parse((ROOT/'extras/local-router-stats/stage_plugin.py').read_text())
        values=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='HISTORY_MAIN' for t in n.targets)]
        self.assertEqual(len(values),1)
        qml=values[0];start=qml.index('  function finishCalendarClock() {')
        finish=qml[start:qml.index('\n  Process {',start)]
        program=r"""
const fs=require('fs'),vm=require('vm'),p=JSON.parse(fs.readFileSync(0,'utf8'));
const observed=Date.parse('2024-02-29T15:05:00Z');
let wall=observed+250,last=observed-750,shifts=0,refreshes=0,samples=0,sampledWhileActive=null;
let payloadObserved=observed;
if(p.scenario==='future') payloadObserved=observed+2000;
if(p.scenario==='expired') wall=observed+45000;
if(p.scenario==='rollback'){last=observed+1000;wall=observed-250;}
function stamp(n){return new Date(n).toISOString().replace('.000Z','Z');}
const clock={state:'ready',timezone:'UTC',observedAt:stamp(payloadObserved),expiresAt:stamp(payloadObserved+45000),
 keys:{day:'2024-02-29',week:'2024-02-26',month:'2024-02',year:'2024'},localCalendarAligned:true};
const prior=Object.assign({},clock,{observedAt:stamp(observed-15000),expiresAt:stamp(observed+30000)});
const ctx={calendarClockStreamDone:p.scenario!=='partial-stream',calendarClockExitDone:p.scenario!=='partial-exit',
 calendarRequestActive:true,calendarRequestGeneration:p.scenario==='obsolete'?6:7,calendarGeneration:7,
 calendarRequestTimezone:'UTC',calendarTimezone:'UTC',calendarClockExitCode:0,calendarClockText:JSON.stringify(clock),calendarClock:prior};
Object.defineProperty(ctx,'calendarNowMs',{configurable:true,get(){return last;},set(v){
 samples++;sampledWhileActive=ctx.calendarRequestActive;last=v;
 if(p.scenario==='reentry'&&shifts++===0){ctx.calendarGeneration++;ctx.calendarTimezone='Europe/Paris';ctx.calendarClock=null;
  if(!ctx.calendarRequestActive)ctx.refreshCalendarClock();}
}});
ctx.refreshCalendarClock=()=>{refreshes++;ctx.calendarRequestActive=true;};
ctx.Date=class extends Date{static now(){return wall;}};
vm.createContext(ctx);vm.runInContext(p.consumer,ctx);
const query="selectDatedHistory(null,{id:'local',readOnly:true,hasLocalStats:true,recentDays:[{date:'2024-02-29',messageCount:9,recorded:true}]},calendarClock,{},calendarNowMs)";
const before=vm.runInContext(query,ctx);
if(before.selectionError||before.periods.day.value!==9)throw Error('invalid positive fixture before receipt');
vm.runInContext('var History={selectDatedHistory:selectDatedHistory};'+p.finish,ctx);
vm.runInContext('finishCalendarClock()',ctx);
const view=vm.runInContext(query,ctx);
console.log(JSON.stringify({now:ctx.calendarNowMs,wall:wall,samples:samples,sampledWhileActive:sampledWhileActive,
 refreshes:refreshes,generation:ctx.calendarGeneration,active:ctx.calendarRequestActive,clock:ctx.calendarClock,
 selectionError:!!view.selectionError,day:view.periods.day,bars:view.bars}));
"""
        child=subprocess.run(['node','-e',program],input=json.dumps({'scenario':scenario,'finish':finish,
            'consumer':(ROOT/'extras/local-router-stats/history_consumer.js').read_text()}),capture_output=True,text=True,timeout=5)
        self.assertEqual(child.returncode,0,child.stderr)
        return json.loads(child.stdout)

    def test_fresh_receipt_keeps_known_dated_value_between_display_ticks(self):
        r=self.receipt('fresh')
        self.assertFalse(r['selectionError'],'fresh receipt compared with the previous UI tick')
        self.assertEqual(r['day']['value'],9)
        self.assertEqual(r['now'],r['wall'])
        self.assertEqual(r['refreshes'],0)

    def test_clock_resampling_precedes_reentrant_generation_validation(self):
        r=self.receipt('reentry')
        self.assertEqual(r['generation'],8,'receipt must resample actual wall time')
        self.assertTrue(r['sampledWhileActive'],'hold request custody across synchronous timezone callbacks')
        self.assertIsNone(r['clock'],'obsolete-zone payload replaced reentrant state')
        self.assertEqual(r['refreshes'],1)
        self.assertTrue(r['active'])

    def test_partial_stream_and_exit_preserve_valid_lease_and_request(self):
        for scenario in ('partial-stream','partial-exit'):
            with self.subTest(scenario=scenario):
                r=self.receipt(scenario)
                self.assertEqual(r['samples'],0)
                self.assertTrue(r['active'])
                self.assertFalse(r['selectionError'])
                self.assertEqual(r['day']['value'],9)
                self.assertEqual(r['refreshes'],0)

    def test_genuinely_future_payload_stays_unusable(self):
        r=self.receipt('future')
        self.assertTrue(r['selectionError'])
        self.assertIsNone(r['day'])
        self.assertEqual(r['bars'],[])

    def test_exact_expiry_stays_unusable_after_receipt(self):
        r=self.receipt('expired')
        self.assertTrue(r['selectionError'])
        self.assertIsNone(r['day'])
        self.assertEqual(r['bars'],[])

    def test_wall_clock_rollback_cannot_reuse_a_later_tick(self):
        r=self.receipt('rollback')
        self.assertTrue(r['selectionError'],'retained later tick concealed wall-clock rollback')
        self.assertIsNone(r['day'])
        self.assertEqual(r['bars'],[])

    def test_obsolete_request_cannot_adopt_the_completed_payload(self):
        r=self.receipt('obsolete')
        self.assertEqual(r['refreshes'],1)
        self.assertTrue(r['active'])
        self.assertEqual(r['clock']['observedAt'],'2024-02-29T15:04:45Z')

    def test_request_argv_uses_the_captured_timezone_despite_stale_qml_binding(self):
        tree=ast.parse((ROOT/'extras/local-router-stats/stage_plugin.py').read_text())
        values=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='HISTORY_MAIN' for t in n.targets)]
        self.assertEqual(len(values),1)
        qml=values[0];start=qml.index('  function refreshCalendarClock() {')
        refresh=qml[start:qml.index('\n  function finishCalendarClock()',start)]
        program=r"""
const fs=require('fs'),vm=require('vm'),p=JSON.parse(fs.readFileSync(0,'utf8'));
const base=['/usr/bin/python3','-I','-B','/fixture/plugin/CalendarClock.py'];
const bound=base.concat(p.bound), original=JSON.stringify(bound);
const ctx={calendarRequestActive:p.active,calendarGeneration:17,calendarRequestGeneration:-1,
 calendarTimezone:p.zone,calendarRequestTimezone:'prior',calendarClockCommand:bound,
 calendarRequestCommand:['prior'],calendarClockStreamDone:true,calendarClockExitDone:true,
 calendarClockExitCode:88,calendarClockText:'prior',calendarProcess:{running:false}};
vm.createContext(ctx);vm.runInContext(p.refresh+'\nrefreshCalendarClock();',ctx);
console.log(JSON.stringify({command:ctx.calendarRequestCommand,timezone:ctx.calendarRequestTimezone,
 generation:ctx.calendarRequestGeneration,active:ctx.calendarRequestActive,
 stream:ctx.calendarClockStreamDone,exit:ctx.calendarClockExitDone,code:ctx.calendarClockExitCode,
 text:ctx.calendarClockText,running:ctx.calendarProcess.running,unmutated:JSON.stringify(bound)===original}));
"""
        # Qt can invoke a timezone-change handler before reevaluating the
        # transitively bound argv. Include add, replace and remove transitions.
        matrix=[('UTC',[],['--timezone','UTC']),
                ('UTC',['--timezone','America/New_York'],['--timezone','UTC']),
                ('America/New_York',['--timezone','UTC'],['--timezone','America/New_York']),
                ('',['--timezone','UTC'],[]),
                ('UTC',['--timezone','UTC'],['--timezone','UTC'])]
        for zone,bound,expected in matrix:
            for active in (False,True):
                with self.subTest(zone=zone,bound=bound,active=active):
                    child=subprocess.run(['node','-e',program],input=json.dumps(
                        {'refresh':refresh,'zone':zone,'bound':bound,'active':active}),
                        capture_output=True,text=True,timeout=5)
                    self.assertEqual(child.returncode,0,child.stderr)
                    r=json.loads(child.stdout)
                    if active:
                        self.assertEqual(r,{'command':['prior'],'timezone':'prior','generation':-1,
                            'active':True,'stream':True,'exit':True,'code':88,'text':'prior',
                            'running':False,'unmutated':True})
                    else:
                        self.assertEqual(r,{'command':['/usr/bin/python3','-I','-B',
                            '/fixture/plugin/CalendarClock.py']+expected,'timezone':zone,'generation':17,
                            'active':True,'stream':False,'exit':False,'code':-1,'text':'',
                            'running':True,'unmutated':True})

if __name__=='__main__':
    unittest.main(verbosity=2)
