#!/usr/bin/env python3
"""Synthetic offline publication. Every path, auth file and probe is isolated."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bridge_fixtures', Path(__file__).with_name('hermes-codex-usage.py'))
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
spec = importlib.util.spec_from_file_location('bridge_publication', ROOT/'extras/hermes-codex-usage/collector.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pub-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root/'bridge'
        self.usage = self.root/'explicit/scanner'
        self.home = self.root/'synthetic-hermes'
        self.home.mkdir()
        self.rows = [f.entry(subject='auth0|SECRET_SUBJECT_A'), f.entry(subject='auth0|SECRET_SUBJECT_B')]
        self.auth = self.home/'auth.json'
        self.auth.write_text(json.dumps({'credential_pool': {'openai-codex': self.rows}}))
        self.auth.chmod(0o600)
        self.env = patch.dict(os.environ, {'HOME': str(self.root/'home'), 'XDG_STATE_HOME': str(self.root/'global-state')}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def record(self, rows=None, old=None, failure=False):
        def probe(*args):
            if failure: raise m.Unavailable('network')
            return m.normalize_quota(f.quota(), f.NOW)
        return m.build_record(self.rows if rows is None else rows, old or {}, probe, f.NOW)

    def records(self):
        return {p.name: json.loads(p.read_text()) for p in self.usage.glob('codex-hermes-*.json')}

    def test_state_only_run_never_touches_scanner(self):
        with patch.object(m, 'fetch', return_value=m.normalize_quota(f.quota(), f.NOW)), patch.object(m.time, 'time', return_value=f.NOW):
            self.assertTrue(m.run_once(self.home, self.state))
        self.assertFalse((self.root/'global-state').exists(), 'implicit publication escaped --state-dir')
        self.assertFalse(self.usage.exists())

    def test_explicit_destination_publishes_native_read_only_contract(self):
        original = self.auth.read_bytes()
        with patch.object(m, 'fetch', return_value=m.normalize_quota(f.quota(), f.NOW)), patch.object(m.time, 'time', return_value=f.NOW):
            self.assertTrue(m.run_once(self.home, self.state, usage_dir=self.usage))
        self.assertFalse((self.root/'global-state').exists())
        records = self.records()
        self.assertEqual(sorted(records), ['codex-hermes-1.json', 'codex-hermes-2.json'])
        for i in (1, 2):
            r = records[f'codex-hermes-{i}.json']
            self.assertEqual(r['id'], f'codex-hermes-{i}')
            self.assertEqual(r['name'], f'Codex (Hermes {i})')
            self.assertTrue(r['readOnly'] and r['ready'])
            self.assertFalse(r['hasLocalStats'] or r['hasPromptStats'] or r['limitsStale'])
            self.assertEqual(r['limitsFetchedAt'], f.NOW*1000)
            self.assertEqual(r['limits'][0]['resetsAt'], m.iso(f.NOW+18000))
            self.assertEqual((self.usage/f'codex-hermes-{i}.json').stat().st_mode & 0o777, 0o600)
            for forbidden in ('accounts', 'accountSwitch', 'resetCredits', 'balance', 'todayTotalTokens'):
                self.assertNotIn(forbidden, r)
        text = ''.join(p.read_text() for p in self.root.rglob('*') if p.is_file() and p != self.auth)
        for secret in ('SECRET_SUBJECT', 'FIXTURE_RESPONSE_SECRET', 'FIXTURE_REFRESH_NEVER_EXPORTED', 'fixture-workspace', self.rows[0]['access_token']):
            self.assertNotIn(secret, text)
        self.assertEqual(self.auth.read_bytes(), original)

    def test_removal_order_matching_stale_and_owned_ghosts(self):
        old = self.record()
        m.publish_accounts(self.usage, old)
        native = self.usage/'codex.json'; native.write_text('NATIVE SENTINEL')
        foreign = self.usage/'codex-hermes-7.json'; foreign.write_text('{"sentinel":"FOREIGN SENTINEL"}')
        reversed_ = self.record(list(reversed(self.rows)), old, failure=True)
        m.publish_accounts(self.usage, reversed_)
        self.assertEqual(self.records()['codex-hermes-1.json']['principalId'], old['accounts'][1]['id'])
        self.assertTrue(self.records()['codex-hermes-1.json']['limitsStale'])
        self.assertEqual(self.records()['codex-hermes-1.json']['limitsFetchedAt'], f.NOW*1000)
        self.assertEqual(self.records()['codex-hermes-1.json']['usageStatusText'], m.REASONS['network'])
        m.publish_accounts(self.usage, self.record(self.rows[:1], old))
        self.assertFalse((self.usage/'codex-hermes-2.json').exists())
        self.assertEqual(native.read_text(), 'NATIVE SENTINEL')
        self.assertEqual(foreign.read_text(), '{"sentinel":"FOREIGN SENTINEL"}')
        m.publish_accounts(self.usage, self.record([]))
        self.assertFalse((self.usage/'codex-hermes-1.json').exists())
        self.assertEqual(foreign.read_text(), '{"sentinel":"FOREIGN SENTINEL"}')

    def test_unowned_modified_symlink_nonregular_and_parent_are_refused(self):
        record = self.record()
        self.usage.mkdir(parents=True)
        target = self.usage/'codex-hermes-1.json'
        sentinel = self.root/'sentinel'; sentinel.write_text('SECRET SENTINEL')
        for kind in ('foreign', 'link', 'fifo', 'directory'):
            with self.subTest(kind=kind):
                if kind == 'foreign': target.write_text('FOREIGN')
                elif kind == 'link': target.symlink_to(sentinel)
                elif kind == 'fifo': os.mkfifo(target)
                else: target.mkdir()
                with self.assertRaises(m.Unavailable): m.publish_accounts(self.usage, record)
                if target.is_dir(): target.rmdir()
                else: target.unlink()
                self.assertEqual(sentinel.read_text(), 'SECRET SENTINEL')
        m.publish_accounts(self.usage, record)
        target.write_text('MODIFIED')
        with self.assertRaises(m.Unavailable): m.publish_accounts(self.usage, self.record([]))
        self.assertEqual(target.read_text(), 'MODIFIED')
        linked = self.root/'linked'; linked.symlink_to(self.usage, target_is_directory=True)
        with self.assertRaises(m.Unavailable): m.publish_accounts(linked/'nested', record)
        self.assertFalse((self.usage/'nested').exists())

    def test_partial_replace_failure_rolls_back_and_surfaces_publication(self):
        old = self.record()
        m.publish_accounts(self.usage, old)
        before = {p.name: p.read_bytes() for p in self.usage.iterdir() if p.is_file()}
        replace = m.os.replace
        calls = []
        def failing(src, dst, **kwargs):
            calls.append(dst)
            if str(dst) == 'codex-hermes-2.json' and len(calls) == 2: raise OSError('SECRET ERROR SENTINEL')
            return replace(src, dst, **kwargs)
        with patch.object(m.os, 'replace', side_effect=failing):
            with self.assertRaises(m.Unavailable) as ctx: m.publish_accounts(self.usage, self.record(list(reversed(self.rows))))
        self.assertEqual(ctx.exception.reason, 'publication')
        self.assertEqual({p.name: p.read_bytes() for p in self.usage.iterdir() if p.is_file()}, before)
        self.assertFalse(any(p.name.startswith('.codex-') for p in self.usage.iterdir()))
        m.publish_accounts(self.usage, self.record(list(reversed(self.rows))))

    def test_publication_failure_leaves_matching_private_last_known(self):
        self.usage.mkdir(parents=True)
        (self.usage/'codex-hermes-1.json').write_text('FOREIGN')
        with patch.object(m, 'fetch', return_value=m.normalize_quota(f.quota(), f.NOW)), patch.object(m.time, 'time', return_value=f.NOW):
            with self.assertRaises(m.Unavailable) as ctx: m.run_once(self.home, self.state, usage_dir=self.usage)
        self.assertEqual(ctx.exception.reason, 'publication')
        self.assertFalse(json.loads((self.state/'usage.json').read_text())['accounts'][0]['stale'])
        self.assertEqual((self.usage/'codex-hermes-1.json').read_text(), 'FOREIGN')


if __name__ == '__main__':
    unittest.main(verbosity=2)
