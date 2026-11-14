#!/usr/bin/env python3
"""Offline real-admission regression; fake /proc, byte pins and transport only.

No real worker/native child, HTTP, live /proc or live database is admitted.
Synthetic byte pins are NOT build qualification or authority to install anything.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# Isolation is required before loading any production module.
HOME = Path(os.environ['HOME']).resolve()
TMP = Path(os.environ['TMPDIR']).resolve()
assert HOME != Path('/home/typhoon') and str(TMP).startswith('/home/typhoon/.hermes/cache/scratch/')
for key in ('HERMES_HOME','XDG_STATE_HOME','XDG_CONFIG_HOME'):
    assert Path(os.environ[key]).resolve() != Path('/home/typhoon')
assert not any(os.environ.get(k) for k in ('DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','HYPRLAND_INSTANCE_SIGNATURE'))
FORBIDDEN = []


def audit(event, args):
    if event.startswith('socket.') or event in ('subprocess.Popen','os.system','os.posix_spawn'):
        FORBIDDEN.append(event); raise PermissionError('offline admission fixture')
    if event == 'open' and isinstance(args[0], (str,bytes)):
        path = os.fsdecode(args[0])
        if path == '/proc' or path.startswith('/proc/'):
            FORBIDDEN.append('live-proc'); raise PermissionError('no live proc')
    if event == 'sqlite3.connect' and not Path(args[0]).resolve().is_relative_to(TMP):
        FORBIDDEN.append('live-db'); raise PermissionError('no live database')


sys.addaudithook(audit)
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('slot_admission_collector', ROOT/'extras/local-router-stats/collector.py')
assert spec is not None and spec.loader is not None
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)


class Admission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='slot-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root/'proc'
        self.child = self.proc/'20'
        for name in ('10/task/10','10/task/11','20/fd','20/ns','self/ns','net','sys/kernel/random'):
            (self.proc/name).mkdir(parents=True,exist_ok=True)
        (self.proc/'10/task/10/children').write_text('')
        (self.proc/'10/task/11/children').write_text('20')
        (self.proc/'sys/kernel/random/boot_id').write_text('fixture-boot')
        (self.proc/'self/ns/net').write_bytes(b'synthetic-network-namespace')
        os.link(self.proc/'self/ns/net',self.child/'ns/net')
        self.args = ['llama-server','--alias','m','--port','9001','--host','127.0.0.1','--sleep-idle-seconds','-1','--slots']
        self.set_args(self.args)
        self.start = '888'; self.set_start(self.start)
        self.bin = self.root/'runtime'; self.bin.mkdir()
        # Same names as associated runtime; different synthetic bytes, pinned only
        # within this test. Every relevant mapped object must still be verified.
        self.artifacts = {}
        for name in ('llama-server','libllama-server-impl.so','libllama-common.so.0.5.0',
                     'libllama.so.0.5.0','libggml.so.0.25.3','libggml-base.so.0.25.3'):
            raw = ('SYNTHETIC-ONLY:'+name).encode(); (self.bin/name).write_bytes(raw)
            self.artifacts[name] = {'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        (self.child/'exe').symlink_to(self.bin/'llama-server')
        self.map_lines = []
        for i,name in enumerate(n for n in self.artifacts if n != 'llama-server'):
            st = (self.bin/name).stat(); span = f'{4096*(i+1):x}-{4096*(i+2):x}'
            self.map_lines.append(f'{span} r-xp 00000000 {os.major(st.st_dev):x}:{os.minor(st.st_dev):x} {st.st_ino} {self.bin/name}')
        (self.child/'maps').write_text('\n'.join(self.map_lines)+'\n')
        self.tcp = self.proc/'net/tcp'
        self.tcp.write_text('sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n'
                            '0: 0100007F:2329 00000000:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 4242\n')
        (self.child/'fd/7').symlink_to('socket:[4242]')
        self.pins = patch.object(c,'RUNTIME_ARTIFACTS',self.artifacts,create=True)
        self.pins.start(); self.addCleanup(self.pins.stop)
        self.original = c.worker
        self.ledger = c.Ledger(self.root/'tokens.sqlite3')
        self.addCleanup(self.ledger.db.close)
        self.sampler = c.LiveSampler()
        self.transport = []
        self.counters = (100,20)
        self.slot_counts = (10,2)
        self.listed = []
        self.denied_clock = 110
        self.admission_calls = 0

    def tearDown(self):
        self.assertEqual(FORBIDDEN, [], 'forbidden attempts are checked outside product catches')

    def set_args(self,args):
        (self.child/'cmdline').write_bytes(('\0'.join(args)+'\0').encode())

    def set_start(self,value):
        (self.child/'stat').write_text('20 (llama server) '+' '.join(['S','10']+['0']*17+[value]))

    def transport_get(self,port,path):
        self.transport.append((port,path))
        if path == '/models':
            return json.dumps({'data':[{'id':'m','status':{'value':'loaded','args':self.listed}}]})
        if path == '/metrics':
            return f'llamacpp:prompt_tokens_total {self.counters[0]}\nllamacpp:tokens_predicted_total {self.counters[1]}'
        if path == '/slots':
            return json.dumps([{'id':0,'id_task':7,'is_processing':True,'n_ctx':4096,
                                'n_prompt_tokens':30,'n_prompt_tokens_processed':self.slot_counts[0],
                                'n_prompt_tokens_cache':4,'next_token':[{'n_decoded':self.slot_counts[1]}],
                                'prompt':'SECRET_NEVER_PUBLISHED'}])
        raise AssertionError('unexpected transport')

    def poll(self,now):
        def worker(model,pid,for_slots=False):
            if for_slots: self.admission_calls += 1
            return self.original(model,pid,self.proc,for_slots=for_slots)
        with patch.object(c,'get',side_effect=self.transport_get),patch.object(c,'worker',side_effect=worker),patch.object(c,'router_pid',return_value=10):
            return c.sample(self.ledger,now,sampler=self.sampler)

    def assert_denied(self):
        self.transport.clear()
        before = self.admission_calls
        live = self.poll(self.denied_clock)
        self.denied_clock += 5
        self.assertGreater(self.admission_calls,before,'denial did not exercise slot admission')
        self.assertNotIn((9001,'/slots'),self.transport)
        self.assertEqual(live['models'][0]['slotsState'],'unavailable')
        self.assertIsNone(live['models'][0]['promptTokPerSec'])
        self.assertIsNone(live['models'][0]['activeRequests'])
        self.assertNotIn('m',self.sampler.previous)

    def test_offline_audit_guard_detects_a_forbidden_attempt(self):
        import socket
        before = len(FORBIDDEN)
        try:
            with self.assertRaises(PermissionError): socket.socket()
            self.assertEqual(FORBIDDEN[before:],['socket.__new__'])
        finally:
            del FORBIDDEN[before:]  # only this explicit guard-control attempt

    def test_each_refusal_case_reaches_live_admission(self):
        self.set_args(self.args[:7])  # no explicit sleep, all other seams valid
        original = self.original
        calls = []
        def count(*args,**kwargs):
            if kwargs.get('for_slots'): calls.append(True)
            return original(*args,**kwargs)
        self.original = count
        self.assert_denied(); self.assert_denied()
        self.assertEqual(len(calls),2,'fixture timestamp reuse bypassed the second admission case')

    def test_byte_associated_real_worker_path_reaches_task_rates_and_publication(self):
        first = self.poll(100)
        self.counters = (140,30); self.slot_counts = (30,12)
        second = self.poll(105)
        self.assertIsNone(first['models'][0]['promptTokPerSec'])
        self.assertEqual((second['models'][0]['promptTokPerSec'],second['models'][0]['generationTokPerSec']), (4,2),
                         'byte-associated, sleep-disabled real admission never reached the slot sampler')
        self.assertEqual(self.transport,[(8080,'/models'),(9001,'/metrics'),(9001,'/slots')]*2)
        record = self.ledger.record(105,second); c.publish(self.root/'local.json',record)
        exported = json.loads((self.root/'local.json').read_bytes())
        self.assertEqual(exported['todayTotalTokens'],50)
        self.assertEqual(exported['localLive'],second)
        self.assertNotIn('SECRET',json.dumps(exported))

    def test_unassociated_or_changed_runtime_never_requests_slots(self):
        for name in self.artifacts:
            with self.subTest(name=name):
                target = self.bin/name; before = target.read_bytes()
                target.write_bytes(before+b'changed')
                self.assert_denied(); target.write_bytes(before)

    def test_missing_sleep_and_conflicting_aliases_or_options_deny(self):
        tail_cases = [[],['--sleep-idle-seconds','3'],['--sleep-idle-seconds','0'],
                      ['--sleep-idle-seconds','-2'],['--sleep-idle-seconds','-1','--sleep_idle_seconds','-1'],
                      ['--sleep-idle-seconds','-1','--sleep_idle_seconds=3'],
                      ['--sleep-idle-seconds','-1','--port','9001'],
                      ['--sleep-idle-seconds','-1','-a','m'],
                      ['--sleep-idle-seconds','-1','--slots','--no_slots'],
                      ['--sleep-idle-seconds','-1','--slots','--slots'],
                      ['--sleep-idle-seconds','-1','--unknown-option','safe']]
        for tail in tail_cases:
            with self.subTest(tail=tail):
                self.set_args(self.args[:7]+tail); self.assert_denied()

    def test_option_value_decoys_never_disable_sleep(self):
        for decoy in (['--path','--sleep-idle-seconds=-1'],['--path','--sleep-idle-seconds','--model','-1'],
                      ['--control-vector-layer-range','--sleep-idle-seconds','-1']):
            with self.subTest(decoy=decoy):
                self.set_args(self.args[:7]+decoy); self.assert_denied()

    def test_listed_ambiguity_is_not_overridden_by_safe_child(self):
        for listed in (['--port','9001','--port','9001'],['--alias','m','-a','m'],
                       ['--sleep-idle-seconds','-1','--sleep_idle_seconds','3'],
                       ['--path','--sleep-idle-seconds=-1','--sleep-idle-seconds','3'],
                       ['--slots','--no-slots']):
            with self.subTest(listed=listed):
                self.listed = listed; self.assert_denied()

    def test_mapped_object_change_during_final_hash_refuses_before_request(self):
        # Model Btrfs's maps/stat device mismatch without namespaces/native code.
        target = self.bin/'libggml-base.so.0.25.3'
        lines = (self.child/'maps').read_text().splitlines()
        fields = lines[-1].split(None,5); fields[3] = '0:0'
        lines[-1] = ' '.join(fields)
        (self.child/'maps').write_text('\n'.join(lines)+'\n')
        held = self.child/'map_files'/fields[0]
        held.parent.mkdir(); held.symlink_to(target)
        other = self.root/'other-mapped-object'; other.write_bytes(target.read_bytes())
        original_hash = c.runtime_bytes
        def change(path,expected):
            result = original_hash(path,expected)
            if path == target:
                held.unlink(); held.symlink_to(other)
            return result
        with patch.object(c,'runtime_bytes',side_effect=change):
            self.assert_denied()

    def test_real_disabled_policy_survives_decoy_values_and_alias_normalization(self):
        self.args[1] = '-a'
        self.args[7] = '--sleep_idle_seconds'
        self.set_args(self.args+['--path','--sleep-idle-seconds=3',
                                 '--control-vector-layer-range','--sleep-idle-seconds','3'])
        live = self.poll(100)
        self.assertEqual(live['models'][0]['slotsState'],'available')
        self.assertIn((9001,'/slots'),self.transport)

    def test_hash_cache_never_hides_same_size_content_change(self):
        self.assertEqual(self.poll(100)['models'][0]['slotsState'],'available')
        target = self.bin/'libllama-common.so.0.5.0'
        raw = target.read_bytes(); target.write_bytes(b'X'+raw[1:])
        self.assert_denied()
        target.write_bytes(raw)
        self.assertEqual(self.poll(120)['models'][0]['slotsState'],'available')
        self.assertLessEqual(len(c._RUNTIME_HASHES),64)

    def test_missing_deleted_unknown_or_rebound_mapping_denies(self):
        original = (self.child/'maps').read_text()
        lines = original.splitlines()
        first = lines[0].split(None,5)
        wrong_inode = first.copy(); wrong_inode[4] = str(int(first[4])+1)
        for modified in ('\n'.join(lines[1:])+'\n',original.replace(first[5],first[5]+' (deleted)'),
                         original.replace(first[5],str(self.bin/'libllama-new.so')),
                         ' '.join(wrong_inode)+'\n'+'\n'.join(lines[1:])+'\n'):
            with self.subTest(modified=modified):
                (self.child/'maps').write_text(modified); self.assert_denied()
        (self.child/'maps').write_text(original)
        self.assertEqual(self.poll(self.denied_clock)['models'][0]['slotsState'],'available')

    def test_device_mismatch_requires_matching_kernel_held_object(self):
        lines = (self.child/'maps').read_text().splitlines()
        fields = lines[-1].split(None,5); fields[3] = '0:0'
        lines[-1] = ' '.join(fields); (self.child/'maps').write_text('\n'.join(lines)+'\n')
        self.assert_denied()  # map_files is unavailable
        held = self.child/'map_files'/fields[0]; held.parent.mkdir()
        wrong = self.root/'wrong-object'; wrong.write_bytes(b'not the mapped inode')
        held.symlink_to(wrong); self.assert_denied()
        held.unlink(); held.symlink_to(fields[5])
        self.assertEqual(self.poll(self.denied_clock)['models'][0]['slotsState'],'available')

    def test_listener_and_network_namespace_must_belong_to_worker(self):
        original = self.tcp.read_text()
        for bad in (original.replace('0100007F','00000000'),original.replace('4242','4243'),
                    original+original.splitlines()[1]+'\n'):
            with self.subTest(tcp=bad):
                self.tcp.write_text(bad); self.assert_denied()
        self.tcp.write_text(original)
        (self.child/'ns/net').unlink(); (self.child/'ns/net').write_bytes(b'different namespace')
        self.assert_denied()

    def test_worker_change_before_slots_never_enters_transport(self):
        original_get = self.transport_get
        def change(port,path):
            value = original_get(port,path)
            if path == '/metrics': self.set_start('999')
            return value
        self.transport_get = change
        live = self.poll(100)
        self.assertNotIn((9001,'/slots'),self.transport)
        self.assertIsNone(live['models'][0]['activeRequests'])
        self.assertNotIn('m',self.sampler.previous)

    def test_new_worker_generation_rebaselines_without_losing_history(self):
        self.poll(100)
        self.counters = (140,30); self.slot_counts = (30,12)
        self.assertEqual(self.poll(105)['models'][0]['generationTokPerSec'],2)
        self.set_start('999'); self.slot_counts = (50,22)
        changed = self.poll(110)
        self.assertIsNone(changed['models'][0]['generationTokPerSec'])
        self.assertEqual(self.ledger.record(110,changed)['todayTotalTokens'],50)

    def test_worker_change_after_slot_read_discards_the_observation(self):
        original_get = self.transport_get
        def change(port,path):
            value = original_get(port,path)
            if path == '/slots': self.set_start('999')
            return value
        self.transport_get = change
        live = self.poll(100)
        self.assertIn((9001,'/slots'),self.transport,'control must actually cross admission')
        self.assertEqual(live['models'][0]['slotsState'],'unavailable')
        self.assertNotIn('m',self.sampler.previous)


if __name__ == '__main__':
    unittest.main(verbosity=2)
