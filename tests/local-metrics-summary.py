#!/usr/bin/env python3
"""Passive /metrics projection is distinct from protected task/slot rates."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('metrics_collector', ROOT / 'extras/local-router-stats/collector.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

LISTING = json.dumps({'data': [{'id': 'heretic', 'status': {'value': 'loaded'}}]})
METRICS = '\n'.join(('llamacpp:prompt_tokens_total 100', 'llamacpp:tokens_predicted_total 20',
                     'llamacpp:prompt_tokens_seconds 1234.5', 'llamacpp:predicted_tokens_seconds 32.25',
                     'llamacpp:requests_processing 1'))


class MetricsSummaryTests(unittest.TestCase):
    def test_metrics_summary_survives_slot_refusal_without_extra_requests(self):
        def worker(model, pid, for_slots=False):
            if for_slots:
                raise ValueError('synthetic slot refusal')
            return 'fixture-boot:20:888', 9001
        with tempfile.TemporaryDirectory() as td:
            ledger = c.Ledger(Path(td) / 'synthetic.db')
            self.addCleanup(ledger.db.close)
            sampler = c.LiveSampler()
            with patch.object(c, 'get', side_effect=[LISTING, METRICS]) as get, \
                 patch.object(c, 'worker', side_effect=worker), patch.object(c, 'router_pid', return_value=10):
                live = c.sample(ledger, 100, sampler=sampler)
            row = live['models'][0]
            summary = row.get('engineMetrics')
            self.assertIsInstance(summary, dict, 'already fetched engine averages are discarded when /slots refuses')
            self.assertEqual(summary['contract'], 'llamacpp-metrics-summary/1')
            self.assertEqual(summary['workerIdentity'], 'fixture-boot:20:888')
            self.assertEqual(summary['observedAt'], 100)
            self.assertEqual(summary['activeRequests'], 1)
            self.assertEqual((summary['promptTokPerSec'], summary['generationTokPerSec']), (1234.5, 32.25))
            self.assertEqual([x.args[1] for x in get.call_args_list], ['/models', '/metrics'])
            self.assertEqual(row['slotsState'], 'unavailable')
            self.assertIsNone(row['promptTokPerSec'])
            self.assertIsNone(row['generationTokPerSec'])
            self.assertIsNone(row['activeRequests'])
            self.assertNotIn('heretic', sampler.previous)
            record = ledger.record(100, live)
            self.assertEqual(record['localLive']['models'][0]['engineMetrics'], summary)
            self.assertNotIn('SECRET', json.dumps(record))

    def test_optional_numeric_grammar_does_not_normalize_non_prometheus_values(self):
        for value in ('١', '1_000', '1e-10000'):
            with self.subTest(value=value):
                text = 'llamacpp:prompt_tokens_seconds ' + value
                self.assertIsNone(c.engine_metrics(text, 'fixture:20:888', 100))
    def test_optional_missing_zero_and_malformed_gauges_are_distinct(self):
        base = 'llamacpp:prompt_tokens_total 100\nllamacpp:tokens_predicted_total 20'
        self.assertIsNone(c.engine_metrics(base, 'fixture:20:888', 100))
        empty = c.engine_metrics(base + '\nllamacpp:prompt_tokens_seconds 0\nllamacpp:predicted_tokens_seconds 0\nllamacpp:requests_processing 0', 'fixture:20:888', 100)
        self.assertEqual(empty['activeRequests'], 0)
        self.assertIsNone(empty['promptTokPerSec'])
        self.assertIsNone(empty['generationTokPerSec'])
        for suffix in ('llamacpp:requests_processing 0.5', 'llamacpp:requests_processing 257',
                       'llamacpp:requests_processing -1', 'llamacpp:prompt_tokens_seconds NaN',
                       'llamacpp:prompt_tokens_seconds inf', 'llamacpp:prompt_tokens_seconds 1e99',
                       'llamacpp:prompt_tokens_seconds 1\nllamacpp:prompt_tokens_seconds 2'):
            with self.subTest(suffix=suffix):
                self.assertIsNone(c.engine_metrics(base + '\n' + suffix, 'fixture:20:888', 100))
        for identity, now in ((None, 100), ('', 100), ('bad\nidentity', 100), ('ok', True), ('ok', 0), ('ok', float('nan'))):
            with self.subTest(identity=identity, now=now):
                self.assertIsNone(c.engine_metrics(METRICS, identity, now))

    def test_untrusted_optional_gauges_do_not_discard_history_or_allow_slots(self):
        def worker(model, pid, for_slots=False):
            if for_slots:
                raise ValueError('synthetic slot refusal')
            return 'fixture-boot:20:888', 9001
        broken = METRICS.replace('1234.5', 'NaN')
        newer = broken.replace('tokens_total 100', 'tokens_total 120').replace('predicted_total 20', 'predicted_total 22')
        with tempfile.TemporaryDirectory() as td:
            ledger = c.Ledger(Path(td) / 'synthetic.db')
            self.addCleanup(ledger.db.close)
            sampler = c.LiveSampler()
            with patch.object(c, 'get', side_effect=[LISTING, broken, LISTING, newer]) as get, \
                 patch.object(c, 'worker', side_effect=worker), patch.object(c, 'router_pid', return_value=10):
                c.sample(ledger, 1700000000, sampler=sampler)
                live = c.sample(ledger, 1700000005, sampler=sampler)
            self.assertIsNone(live['models'][0]['engineMetrics'])
            self.assertEqual(ledger.record(1700000005, live)['todayTotalTokens'], (120 - 100) + (22 - 20))
            self.assertEqual([x.args[1] for x in get.call_args_list], ['/models', '/metrics'] * 2)

    def test_worker_change_discards_metrics_before_any_slot_request(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = c.Ledger(Path(td) / 'synthetic.db')
            self.addCleanup(ledger.db.close)
            with patch.object(c, 'get', side_effect=[LISTING, METRICS]) as get, \
                 patch.object(c, 'worker', side_effect=[('fixture:20:888', 9001), ('fixture:20:999', 9001)]), \
                 patch.object(c, 'router_pid', return_value=10):
                live = c.sample(ledger, 100, sampler=c.LiveSampler())
            self.assertEqual(live['models'][0]['state'], 'unavailable')
            self.assertIsNone(live['models'][0].get('engineMetrics'))
            self.assertEqual([x.args[1] for x in get.call_args_list], ['/models', '/metrics'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
