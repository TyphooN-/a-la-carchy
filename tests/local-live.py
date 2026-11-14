#!/usr/bin/env python3
"""Discriminating managed live contract, offline synthetic responses only."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('live_collector', ROOT/'extras/local-router-stats/collector.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def wire(inp=10, out=2, task=7, active=True):
    return [{'id':0, 'id_task':task, 'is_processing':active, 'n_ctx':4096,
             'n_prompt_tokens':30, 'n_prompt_tokens_processed':inp, 'n_prompt_tokens_cache':4,
             'next_token':[{'n_decoded':out}], 'prompt':'SECRET_PROMPT', 'params':{'api_key':'SECRET_KEY'},
             'response':'SECRET_RESPONSE'}]


class LiveContract(unittest.TestCase):
    def test_two_observations_publish_current_task_rates_not_engine_averages(self):
        self.assertTrue(hasattr(c, 'LiveSampler'), 'managed collector lacks the accepted live producer')
        sampler = c.LiveSampler()
        first = sampler.observe('m', 'boot:20:888', c.slots(wire()), {}, 100)
        self.assertIsNone(first['promptTokPerSec'])
        second = sampler.observe('m', 'boot:20:888', c.slots(wire(30, 12)), {}, 105)
        self.assertEqual((second['promptTokPerSec'], second['generationTokPerSec']), (4, 2))
        self.assertEqual(second['rateWindowSec'], 5)
        with tempfile.TemporaryDirectory() as td:
            ledger = c.Ledger(Path(td)/'test.db')
            try:
                live = {'observedAt':105, 'staleAfterSec':30, 'status':'1 loaded model(s) · 1 processing', 'models':[second]}
                record = ledger.record(105, live)
                self.assertEqual(record.get('localLive'), live)
                self.assertNotIn('tok/s', record['usageStatusText'], 'compatibility status must never carry immortal rates')
                self.assertIn('calendarWeek', record)
                self.assertNotIn('SECRET', json.dumps(record))
            finally:
                ledger.db.close()


class LiveSamplingEdges(unittest.TestCase):
    def setUp(self):
        self.sampler = c.LiveSampler()

    def observation(self, now=100, identity='boot:20:888', **kwargs):
        return self.sampler.observe('m', identity, c.slots(wire(**kwargs)), {'engine_average':999999}, now)

    def test_task_worker_idle_rollback_and_gap_rebaseline(self):
        for change in ({'task':8}, {'identity':'boot:20:999'}, {'active':False}, {'inp':1}, {'now':140}, {'now':99}):
            with self.subTest(change=change):
                self.sampler = c.LiveSampler()
                self.observation()
                row = self.observation(now=105, inp=30, out=12)
                self.assertEqual(row['promptTokPerSec'], 4)
                args = dict(now=110, inp=40, out=20)
                args.update(change)
                row = self.observation(**args)
                self.assertIsNone(row['promptTokPerSec'])
                self.assertIsNone(row['generationTokPerSec'])

    def test_phase_progress_does_not_renew_other_rate(self):
        self.observation()
        both = self.observation(now=105, inp=30, out=12)
        generation = self.observation(now=110, inp=30, out=22)
        self.assertEqual((generation['promptObservedAt'], generation['generationObservedAt']), (105,110))
        self.assertEqual(generation['promptIdentity'], both['promptIdentity'])
        for now in (120,130,135):
            last = self.observation(now=now, inp=30, out=now)
        self.assertIsNone(last['promptTokPerSec'])
        self.assertIsNotNone(last['generationTokPerSec'])

    def test_malformed_missing_counters_and_time_never_publish_zero(self):
        for now in (None, True, float('nan'), float('inf'), -1, 0):
            with self.subTest(now=now), self.assertRaises(ValueError):
                self.observation(now=now)
        for raw in ({}, [], wire()+wire(), wire(inp=True), wire(inp=-1), wire(inp=2**53)):
            with self.subTest(raw=raw), self.assertRaises(ValueError): c.slots(raw)
        raw=wire(); del raw[0]['id_task']; del raw[0]['n_prompt_tokens_processed']
        row=self.sampler.observe('m','boot:20:888',c.slots(raw),{},100)
        row=self.sampler.observe('m','boot:20:888',c.slots(raw),{},105)
        self.assertIsNone(row['promptTokPerSec']); self.assertIsNone(row['generationTokPerSec'])

    def test_sampler_memory_and_projected_rows_are_bounded(self):
        for i in range(c.MAX_MODELS):
            self.sampler.observe(str(i),'boot:20:888',c.slots(wire()),{},100)
        with self.assertRaises(ValueError):
            self.sampler.observe('overflow','boot:20:888',c.slots(wire()),{},101)
        self.sampler.retain(['0'])
        self.assertEqual(set(self.sampler.previous), {'0'})
        invalid=c.slots(wire())*2
        with self.assertRaises(ValueError):
            self.sampler.observe('0','boot:20:888',invalid,{},105)
        self.assertNotIn('0',self.sampler.previous)

    def test_production_poll_export_and_default_slot_admission_refusal(self):
        listing=json.dumps({'data':[{'id':'m','status':{'value':'loaded'}}]})
        metrics='llamacpp:prompt_tokens_total 100\nllamacpp:tokens_predicted_total 20'
        with tempfile.TemporaryDirectory() as td:
            ledger=c.Ledger(Path(td)/'test.db')
            self.addCleanup(ledger.db.close)
            # Only this offline mock grants slot admission; real worker remains unqualified.
            with patch.object(c,'get',side_effect=[listing,metrics,json.dumps(wire()),listing,metrics,json.dumps(wire(30,12))]) as get, \
                 patch.object(c,'worker',return_value=('boot:20:888',9001)), patch.object(c,'router_pid',return_value=10):
                first=c.sample(ledger,100,sampler=self.sampler)
                second=c.sample(ledger,105,sampler=self.sampler)
                self.assertIsNone(first['models'][0]['promptTokPerSec'])
                self.assertEqual(second['models'][0]['promptTokPerSec'],4)
                self.assertEqual(second['models'][0]['generationTokPerSec'],2)
                self.assertEqual([call.args[1] for call in get.call_args_list], ['/models','/metrics','/slots']*2)
                exported=ledger.record(105,second)
                c.publish(Path(td)/'local.json',exported)
                self.assertEqual(json.loads((Path(td)/'local.json').read_text())['localLive'],second)
                self.assertNotIn('tok/s',exported['usageStatusText'])
            with self.assertRaises(ValueError): c.worker({'id':'m','status':{}},10,Path(td),for_slots=True)
            def admitted(model,pid,for_slots=False):
                if for_slots: raise ValueError('unqualified engine')
                return ('boot:20:888',9001)
            with patch.object(c,'get',side_effect=[listing,metrics]) as get, patch.object(c,'worker',side_effect=admitted), patch.object(c,'router_pid',return_value=10):
                unavailable=c.sample(ledger,110,sampler=self.sampler)
                self.assertIn('unknown',unavailable['status'])
                self.assertEqual(unavailable['models'][0]['slotsState'],'unavailable')
                self.assertIsNone(unavailable['models'][0]['activeRequests'])
                self.assertEqual(len(get.call_args_list),2)
                self.assertNotIn('m',self.sampler.previous)

    def test_invalid_model_states_refuse_before_worker_or_slots(self):
        with tempfile.TemporaryDirectory() as td:
            ledger=c.Ledger(Path(td)/'test.db'); self.addCleanup(ledger.db.close)
            for status in ({},None,{'value':'unexpected'},'loaded'):
                with self.subTest(status=status), patch.object(c,'get',return_value=json.dumps({'data':[{'id':'m','status':status}]})), patch.object(c,'worker') as worker:
                    row=c.sample(ledger,100,sampler=self.sampler)
                    self.assertIn('unknown',row['status']); worker.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
