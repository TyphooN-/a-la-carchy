#!/usr/bin/env python3
"""Real sampled publication -> actual generated JavaScript; no endpoints."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

producer = load('summary_producer_fixture', ROOT / 'tests/local-metrics-summary.py')
consumer = load('summary_consumer_fixture', ROOT / 'tests/local-live-consumer.py')
c = producer.c


class MetricsConsumer(unittest.TestCase):
    def test_already_fetched_gauges_are_labeled_engine_averages_not_task_rates(self):
        def worker(model, pid, for_slots=False):
            if for_slots:
                raise ValueError('synthetic unavailable slots')
            return 'fixture-boot:20:888', 9001
        with tempfile.TemporaryDirectory() as td:
            ledger = c.Ledger(Path(td) / 'synthetic.db')
            self.addCleanup(ledger.db.close)
            with patch.object(c, 'get', side_effect=[producer.LISTING, producer.METRICS]) as get, \
                 patch.object(c, 'worker', side_effect=worker), patch.object(c, 'router_pid', return_value=10):
                live = c.sample(ledger, 100, sampler=c.LiveSampler())
            record = ledger.record(100, live)
            out = consumer.consumer(record, 100)
            self.assertIn('Engine averages', out)
            self.assertIn('prompt 1234.5 tok/s', out)
            self.assertIn('generation 32.25 tok/s', out)
            self.assertIn('1 processing', out)
            self.assertNotIn('activity unknown', out)
            self.assertNotIn('Sampled wall-clock', out)
            self.assertIn('not current-task rates', out)
            self.assertEqual([x.args[1] for x in get.call_args_list], ['/models', '/metrics'])
            self.assertNotIn('SECRET', json.dumps(record))
    def synthetic_record(self):
        return {'localLive': {'contract': 'local-task-rates/1', 'observedAt': 100, 'staleAfterSec': 30,
                'models': [{'model': 'heretic', 'workerIdentity': 'fixture:20:888', 'observedAt': 100,
                            'state': 'loaded (activity unknown)', 'slotsState': 'unavailable',
                            'promptTokPerSec': None, 'generationTokPerSec': None,
                            'engineMetrics': c.engine_metrics(producer.METRICS, 'fixture:20:888', 100)}]}}

    def test_independent_expiry_missing_phases_and_measured_zero_activity(self):
        base = self.synthetic_record()
        for clock in (99, 130, 131):
            with self.subTest(clock=clock):
                self.assertNotIn('tok/s', consumer.consumer(base, clock))
        zero = json.loads(json.dumps(base))
        zero['localLive']['models'][0]['engineMetrics']['activeRequests'] = 0
        self.assertIn('0 processing (metrics)', consumer.consumer(zero, 100))
        missing = json.loads(json.dumps(base))
        metrics = missing['localLive']['models'][0]['engineMetrics']
        metrics['activeRequests'] = None
        metrics['generationTokPerSec'] = None
        out = consumer.consumer(missing, 100)
        self.assertIn('activity unknown', out)
        self.assertIn('prompt 1234.5 tok/s · generation —', out)
        self.assertNotIn('0 tok/s', out)
        # A newer envelope cannot renew an older metrics snapshot.
        older = json.loads(json.dumps(base))
        older['localLive']['observedAt'] = 105
        older['localLive']['models'][0]['observedAt'] = 105
        self.assertNotIn('tok/s', consumer.consumer(older, 131))

    def test_malformed_engine_sample_cannot_supply_rates_or_activity(self):
        base = self.synthetic_record()
        for key, value in (('contract', 'unknown'), ('workerIdentity', 'other'), ('workerIdentity', ''),
                           ('observedAt', True), ('observedAt', 101), ('observedAt', 0),
                           ('activeRequests', True), ('activeRequests', '1'), ('activeRequests', 0.5),
                           ('activeRequests', 257), ('activeRequests', -1),
                           ('promptTokPerSec', True), ('promptTokPerSec', '1'),
                           ('generationTokPerSec', 0), ('generationTokPerSec', -1)):
            with self.subTest(key=key, value=value):
                record = json.loads(json.dumps(base))
                record['localLive']['models'][0]['engineMetrics'][key] = value
                out = consumer.consumer(record, 100)
                self.assertNotIn('tok/s', out)
                self.assertIn('activity unknown', out)


if __name__ == '__main__':
    unittest.main(verbosity=2)
