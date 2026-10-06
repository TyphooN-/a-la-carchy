#!/usr/bin/env python3
"""Hermes quota bridge fixtures: synthetic credentials only, no network."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'extras/hermes-codex-usage/collector.py'
NOW = 1801566000


def token(workspace='fixture-workspace', subject='member-a', expires=NOW + 3600, **extra):
    auth = {'chatgpt_account_id': workspace, **extra}
    claims = {'sub': subject, 'exp': expires, 'https://api.openai.com/auth': auth}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return 'e30.' + body + '.fixture_signature'


def entry(**kwargs):
    return {'id': 'synthetic-entry', 'access_token': token(**kwargs), 'refresh_token': 'FIXTURE_REFRESH_NEVER_EXPORTED'}


def quota(primary=18000, secondary=604800, used=54):
    return {'plan_type': 'plus', 'rate_limit': {
        'primary_window': {'limit_window_seconds': primary, 'used_percent': used, 'reset_at': NOW + primary},
        'secondary_window': {'limit_window_seconds': secondary, 'used_percent': 79, 'reset_at': NOW + secondary}},
        'access_token': 'FIXTURE_RESPONSE_SECRET', 'email': 'fixture@example.invalid'}


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = None
        if SOURCE.is_file():
            spec = importlib.util.spec_from_file_location('fixture_hermes_codex', SOURCE)
            cls.m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.m)

    def setUp(self):
        self.assertIsNotNone(self.m, 'read-only Hermes quota collector must exist')
        self.temp = tempfile.TemporaryDirectory(prefix='hc-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.auth = self.base/'auth.json'
        self.auth.write_text(json.dumps({'credential_pool': {'openai-codex': [entry()]}}))
        self.auth.chmod(0o600)
        self.calls = []

    def probe(self, bearer, identity):
        self.calls.append(identity['key'])
        return self.m.normalize_quota(quota(), NOW)

    def build(self, rows=None, previous=None, probe=None):
        return self.m.build_record(rows if rows is not None else [entry()], previous or {}, probe or self.probe, NOW)

    def test_oversized_numeric_claims_fail_closed(self):
        for value in (10**1000, -(10**1000)):
            with self.subTest(value_sign=value > 0), self.assertRaises(self.m.Unavailable):
                self.m.identity(token(expires=value))
            for field in ('used_percent', 'reset_at', 'reset_after_seconds'):
                q = quota()
                q['rate_limit'].pop('secondary_window')
                window = q['rate_limit']['primary_window']
                if field == 'reset_after_seconds':
                    window.pop('reset_at')
                window[field] = value
                with self.subTest(field=field), self.assertRaises(self.m.Unavailable):
                    self.m.normalize_quota(q, NOW)

    def test_malformed_expiry_cli_replaces_fresh_records_with_unavailable(self):
        state, scanner = self.base/'private', self.base/'scanner'
        old = self.build([entry(subject='auth0|last-known')])
        self.m.publish(state, old)
        self.m.publish_accounts(scanner, old)
        self.assertFalse(json.loads((scanner/'codex-hermes-1.json').read_bytes())['limitsStale'])
        self.auth.write_text(json.dumps({'credential_pool': {'openai-codex': [
            entry(subject='auth0|last-known', expires=10**1000)]}}))
        before = self.auth.read_bytes()
        run = subprocess.run([sys.executable, '-B', str(SOURCE), '--hermes-home', str(self.base),
                              '--state-dir', str(state), '--usage-dir', str(scanner)],
                             capture_output=True, text=True, timeout=8)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        self.assertEqual(self.auth.read_bytes(), before)
        current = json.loads((scanner/'codex-hermes-1.json').read_bytes())
        self.assertTrue(current['limitsStale'])
        self.assertEqual(current['limits'], [])
        self.assertIn('unavailable', current['usageStatusText'])
        self.assertNotIn('auth0|last-known', json.dumps(current))

    def test_opaque_subject_is_hashed_not_used_as_a_header(self):
        rows = [entry(subject='auth0|SYNTHETIC_SUBJECT_A'), entry(subject='auth0|SYNTHETIC_SUBJECT_B')]
        r = self.build(rows)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(not a['stale'] and a['fetchedAt'] == NOW*1000 for a in r['accounts']))
        self.assertEqual(len({a['id'] for a in r['accounts']}), 2)
        self.assertNotIn('SYNTHETIC_SUBJECT', json.dumps(r))
        for field in ('workspace', 'chatgpt_data_residency', 'chatgpt_compute_residency'):
            with self.subTest(field=field), self.assertRaises(self.m.Unavailable):
                self.m.identity(token(subject='auth0|valid', **{field: 'unsafe|header'}))
        for subject in ('', None, 7, [], 'bad\nsubject', 'bad\x00subject', 'bad\x7fsubject',
                        'bad\u200bsubject', 'bad subject', 'a'*1025, '\u00e9'*513, '\ud800'):
            with self.subTest(subject=repr(subject)), self.assertRaises(self.m.Unavailable):
                self.m.identity(token(subject=subject))

    def test_two_members_in_one_workspace_are_separate(self):
        r = self.build([entry(), entry(subject='member-b')])
        self.assertEqual(len(r['accounts']), 2)
        self.assertEqual(len(set(self.calls)), 2)
        self.assertNotEqual(r['accounts'][0]['id'], r['accounts'][1]['id'])
        self.assertTrue(r['readOnly'])

    def test_duplicate_principal_uses_newest_token_once(self):
        r = self.build([entry(expires=NOW-1), entry(expires=NOW+7200)])
        self.assertEqual(len(r['accounts']), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(r['accounts'][0]['stale'])

    def test_expired_token_is_not_refreshed_or_probed(self):
        r = self.build([entry(expires=NOW-1)])
        self.assertFalse(self.calls)
        self.assertEqual(r['accounts'][0]['limits'], [])
        self.assertIn('expired', r['accounts'][0]['usageStatusText'].lower())
        self.assertTrue(r['accounts'][0]['stale'])

    def test_partial_failure_keeps_only_matching_last_known(self):
        old = self.build([entry(), entry(subject='member-b')])
        def failing(bearer, ident):
            if ident['key'] == old['accounts'][0]['id']:
                raise self.m.Unavailable('network')
            return self.m.normalize_quota(quota(used=12), NOW+60)
        r = self.build([entry(), entry(subject='member-b')], old, failing)
        self.assertTrue(r['accounts'][0]['stale'])
        self.assertEqual(r['accounts'][0]['limits'], old['accounts'][0]['limits'])
        self.assertEqual(r['accounts'][0]['fetchedAt'], NOW*1000)
        self.assertFalse(r['accounts'][1]['stale'])
        self.assertEqual(r['accounts'][1]['limits'][0]['percent'], .12)

    def test_removed_account_does_not_linger(self):
        old = self.build([entry(), entry(subject='member-b')])
        r = self.build([entry()], old)
        self.assertEqual(len(r['accounts']), 1)

    def test_unknown_never_becomes_zero_or_full_allowance(self):
        for response in [{}, {'rate_limit': {}}, {'rate_limit': {'primary_window': {'used_percent': 0}}}]:
            with self.subTest(response=response):
                def bad(*args):
                    return self.m.normalize_quota(response, NOW)
                r = self.build(probe=bad)
                self.assertEqual(r['accounts'][0]['limits'], [])
                self.assertTrue(r['accounts'][0]['stale'])

    def test_windows_use_reported_duration_not_position(self):
        normal = self.m.normalize_quota(quota(), NOW)
        reversed_ = self.m.normalize_quota(quota(primary=604800, secondary=18000), NOW)
        self.assertEqual(normal['limits'][0]['label'], reversed_['limits'][0]['label'])
        self.assertIn('5', normal['limits'][0]['label'])
        self.assertIn('Weekly', normal['limits'][1]['title'])
        self.assertEqual(normal['limits'][0]['percent'], .54)
        self.assertEqual(normal['limits'][1]['percent'], .79)

    def test_zero_and_full_are_valid_only_when_measured(self):
        self.assertEqual(self.m.normalize_quota(quota(used=0), NOW)['limits'][0]['percent'], 0)
        self.assertEqual(self.m.normalize_quota(quota(used=100), NOW)['limits'][0]['percent'], 1)

    def test_invalid_percent_duration_and_reset_values(self):
        for key, value in [('used_percent', True), ('used_percent', -1), ('used_percent', 101),
                           ('used_percent', float('nan')), ('used_percent', '54'),
                           ('limit_window_seconds', 0), ('limit_window_seconds', True),
                           ('reset_at', 'FIXTURE_RESPONSE_SECRET'), ('reset_at', float('inf'))]:
            with self.subTest(key=key, value=value):
                q = quota(); q['rate_limit'].pop('secondary_window'); q['rate_limit']['primary_window'][key] = value
                with self.assertRaises(self.m.Unavailable):
                    self.m.normalize_quota(q, NOW)

    def test_residency_comes_from_same_jwt(self):
        ident = self.m.identity(token(chatgpt_compute_residency='us'))
        self.assertEqual(ident['residency'], 'us')
        ident = self.m.identity(token(chatgpt_data_residency='eu', chatgpt_compute_residency='us'))
        self.assertEqual(ident['residency'], 'eu')

    def test_malformed_identity_does_not_probe(self):
        for bad in ['not-a-jwt', token(subject=''), token(workspace='bad\r\nInjected: yes')]:
            r = self.build([{'access_token': bad}])
            self.assertEqual(r['accounts'][0]['limits'], [])
        self.assertFalse(self.calls)

    def test_no_credentials_or_raw_identity_in_output(self):
        r = self.build()
        text = json.dumps(r)
        for secret in [entry()['access_token'], 'FIXTURE_REFRESH_NEVER_EXPORTED', 'FIXTURE_RESPONSE_SECRET',
                       'fixture-workspace', 'member-a', 'fixture@example.invalid']:
            self.assertNotIn(secret, text)
        self.assertNotIn('resetCredits', text)
        self.assertNotIn('balance', text)

    def test_snapshot_is_bounded_regular_file_read_only(self):
        before = self.auth.read_bytes()
        rows = self.m.read_pool(self.auth)
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.auth.read_bytes(), before)
        self.assertEqual(set(self.base.iterdir()), {self.auth})
        link = self.base/'linked.json'; link.symlink_to(self.auth)
        with self.assertRaises(self.m.Unavailable): self.m.read_pool(link)
        fifo = self.base/'fifo'; os.mkfifo(fifo)
        with self.assertRaises(self.m.Unavailable): self.m.read_pool(fifo)
        self.auth.write_bytes(b' '*(self.m.MAX_AUTH_BYTES+1))
        with self.assertRaises(self.m.Unavailable): self.m.read_pool(self.auth)

    def test_pool_overflow_and_schema_refused(self):
        for body in [{'credential_pool': {'openai-codex': {}}},
                     {'credential_pool': {'openai-codex': [entry()]*(self.m.MAX_ACCOUNTS+1)}}]:
            self.auth.write_text(json.dumps(body))
            with self.assertRaises(self.m.Unavailable): self.m.read_pool(self.auth)

    def test_private_atomic_state_and_symlink_refusal(self):
        state = self.base/'state'
        record = self.build()
        self.assertTrue(self.m.publish(state, record))
        target = state/'usage.json'
        self.assertEqual(json.loads(target.read_text()), record)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)
        target.unlink(); target.symlink_to(self.auth)
        before = self.auth.read_bytes()
        with self.assertRaises(self.m.Unavailable): self.m.publish(state, record)
        self.assertEqual(self.auth.read_bytes(), before)

    def test_fetch_worker_receives_secret_only_over_stdin(self):
        result = self.m.normalize_quota(quota(), NOW)
        with patch.object(self.m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(result), '')) as run:
            self.m.fetch(entry()['access_token'], self.m.identity(entry()['access_token']))
        args, kwargs = run.call_args
        self.assertNotIn(entry()['access_token'], repr(args))
        self.assertIn(entry()['access_token'], kwargs['input'])
        self.assertLessEqual(kwargs['timeout'], 15)
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)

    def test_transport_fixed_verified_tls_no_redirect_or_proxy(self):
        class Response:
            status = 302
            def getheader(self, name, default=None): return default
        class Connection:
            def __init__(conn, host, **kwargs):
                self.assertEqual(host, 'chatgpt.com'); self.assertIsNotNone(kwargs.get('context')); conn.requests=[]
            def request(conn, method, path, headers):
                self.assertEqual(method, 'GET'); self.assertEqual(path, '/backend-api/wham/usage')
                self.assertEqual(headers['ChatGPT-Account-ID'], 'fixture-workspace'); conn.requests.append(path)
            def getresponse(conn): return Response()
            def close(conn): pass
        with patch.object(self.m.http.client, 'HTTPSConnection', Connection):
            with self.assertRaises(self.m.Unavailable): self.m.http_quota(entry()['access_token'], self.m.identity(entry()['access_token']), NOW)

    def test_cli_initialize_never_reads_auth_or_contacts_network(self):
        state = self.base/'out'
        run = subprocess.run([sys.executable, str(SOURCE), '--initialize', '--hermes-home', str(self.base/'missing'),
                              '--state-dir', str(state)], capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        r = json.loads((state/'usage.json').read_text())
        self.assertEqual(r['accounts'], [])
        self.assertIn('not checked', r['usageStatusText'].lower())


if __name__ == '__main__':
    unittest.main(verbosity=2)
