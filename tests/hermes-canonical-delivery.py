#!/usr/bin/env python3
"""Dormant canonical feed delivery, all roots/commands and databases synthetic."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
def load(path,name):
    spec = importlib.util.spec_from_file_location(name,path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module
f = load(ROOT/'tests/agents-clone.py','dormant_delivery_fixture')
m = load(ROOT/'extras/agents-clone/manage.py','dormant_feed_manager')
s = load(ROOT/'tests/hermes-canonical-feed.py','synthetic_db_fixture')
DEPENDENCIES = ('local-router-stats/canonical_feed.py','local-router-stats/hermes_accounting.py',
                'local-router-stats/usage_history.py','local-router-stats/canonical-feed.service.in',
                'local-router-stats/canonical-feed.timer')

def command_from_unit(data):
    # Independently decode the quoted C/JSON subset our systemd renderer emits.
    # This is not shell parsing and performs neither shell evaluation nor expansion.
    command = next(line[len('ExecStart='):] for line in data.splitlines() if line.startswith('ExecStart='))
    result, decoder = [], json.JSONDecoder()
    while command:
        value,end = decoder.raw_decode(command)
        assert isinstance(value,str)
        result.append(value.replace('%%','%').replace('$$','$'))
        command = command[end:].lstrip(' ')
    return result

class DeliveryTests(f.Fixture):
    def setUp(self):
        super().setUp()
        s.SYNTHETIC_ROOTS.add(self.root)
        self.addCleanup(s.SYNTHETIC_ROOTS.discard,self.root)

    def feed_options(self):
        dest = self.state/'omarchy/hermes-token-accounting'
        return ['--accounting-source',str(self.root/'synthetic % $ \' " \\ db.sqlite'),
                '--accounting-profile','default','--accounting-timezone','UTC',
                '--accounting-canonical-output',str(dest/'canonical.json'),
                '--accounting-state',str(dest/'state.json'),
                '--accounting-history-output',str(dest/'history.json'),
                '--accounting-output',str(dest/'usage.json')]

    def install(self, script=None):
        return subprocess.run([sys.executable,'-I','-B',str(script or ROOT/'extras/agents-clone/manage.py'),
            '--source',str(self.source),'--shell',str(self.shell),'--plugins',str(self.plugins),
            '--install-accounting-feed',*self.feed_options(),'local'],cwd=self.root,env=self.env,
            capture_output=True,text=True,timeout=30)

    def test_dormant_explicit_install_and_render(self):
        self.assertIn('local-router-stats/canonical_feed.py',m.TOOL_FILES,'installed feed CLI is missing')
        self.ok('local','enable')
        self.ok('local','install-bridge')
        units = self.home/'.config/systemd/user'
        bridge = {p.name:p.read_bytes() for p in units.glob('hermes-codex-usage.*')}
        self.ok('local','install-tools')
        self.assertFalse((units/'hermes-canonical-feed.service').exists())
        self.assertFalse((units/'hermes-canonical-feed.timer').exists())
        shell,clone = self.shell.read_bytes(),f.tree(self.clone)
        result = self.install(); self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        service,timer = units/'hermes-canonical-feed.service',units/'hermes-canonical-feed.timer'
        argv = command_from_unit(service.read_text())
        options = self.feed_options()
        expected = {options[i][2:]:options[i+1] for i in range(0,len(options),2)}
        self.assertEqual(argv[:3],['/usr/bin/python3','-I','-B'])
        self.assertEqual(argv[3],str(self.home/'.local/share/a-la-carchy/local-router-stats/canonical_feed.py'))
        for key,value in (('--source-db',expected['accounting-source']),('--profile','default'),('--timezone','UTC'),
                          ('--canonical-output',expected['accounting-canonical-output']),
                          ('--history-state',expected['accounting-state']),
                          ('--history-output',expected['accounting-history-output']),('--output',expected['accounting-output'])):
            self.assertEqual(argv[argv.index(key)+1],value)
        self.assertIn('%% $$',service.read_text())
        self.assertIn('TimeoutStartSec=20s',service.read_text())
        self.assertIn('UMask=0077',service.read_text())
        self.assertIn('Unit=hermes-canonical-feed.service',timer.read_text())
        self.assertFalse((units/'timers.target.wants').exists())
        self.assertEqual(self.systemctl_calls(),[])
        self.assertEqual(self.shell.read_bytes(),shell); self.assertEqual(f.tree(self.clone),clone)
        self.assertEqual({p.name:p.read_bytes() for p in units.glob('hermes-codex-usage.*')},bridge)
        self.assertFalse(Path(expected['accounting-canonical-output']).exists(),'install must not collect')
        files = json.loads((self.home/'.local/share/a-la-carchy/managed-files.json').read_text())['files']
        for p in (service,timer): self.assertEqual(files[str(p)],m.digest(p.read_bytes()))
        before = f.tree(self.home)
        again = self.install(); self.assertEqual(again.returncode,0,again.stdout+again.stderr)
        self.assertEqual(f.tree(self.home),before)
        self.new_omarchy(); self.ok('local','upgrade')
        self.assertEqual(service.read_bytes(),before[service.relative_to(self.home).as_posix()])
        self.assertEqual(self.systemctl_calls(),[])
        print('DORMANT_UNIT_PROOF='+json.dumps({'argv':argv,'service':service.read_text(),'timer':timer.read_text(),
            'systemctl_calls':self.systemctl_calls(),'wants_directory':False,'collected_on_install':False,
            'plain_upgrade_preserved_feed_unit':True},sort_keys=True))

    def test_dormant_explicit_source_ceiling_round_trip(self):
        self.ok('local','enable')
        first = self.install()
        self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        unit = self.home/'.config/systemd/user/hermes-canonical-feed.service'
        default = unit.read_bytes()
        self.assertNotIn('--max-source-bytes',command_from_unit(unit.read_text()))
        shell,clone = self.shell.read_bytes(),f.tree(self.clone)
        original = self.feed_options
        for value in ('2147483648','4294967296'):
            with self.subTest(limit=value):
                with patch.object(self,'feed_options',side_effect=lambda:[*original(),'--accounting-max-source-bytes',value]):
                    result = self.install()
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                argv = command_from_unit(unit.read_text())
                self.assertEqual(argv.count('--max-source-bytes'),1)
                self.assertEqual(argv[argv.index('--max-source-bytes')+1],value)
                self.assertEqual(self.shell.read_bytes(),shell)
                self.assertEqual(f.tree(self.clone),clone)
                self.assertEqual(self.systemctl_calls(),[])
        again = self.install()
        self.assertEqual(again.returncode,0,again.stdout+again.stderr)
        self.assertEqual(unit.read_bytes(),default,'omitting the opt-in must preserve the original default unit')

    def test_dormant_source_ceiling_refusals_preserve_all_files(self):
        self.ok('local','enable')
        first = self.install()
        self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        original = self.feed_options
        before = f.tree(self.home)
        for value in ('0','-1','4294967297','1.5','bad\nPRIVATE_VALUE'):
            with self.subTest(limit=value):
                with patch.object(self,'feed_options',side_effect=lambda:[*original(),'--accounting-max-source-bytes',value]):
                    result = self.install()
                self.assertNotEqual(result.returncode,0)
                self.assertIn('invalid accounting source byte ceiling',result.stdout)
                self.assertNotIn('PRIVATE_VALUE',result.stdout+result.stderr)
                self.assertEqual(f.tree(self.home),before)
        result = subprocess.run([sys.executable,'-I','-B',str(ROOT/'extras/agents-clone/manage.py'),
            '--shell',str(self.shell),'--install-tools','--accounting-max-source-bytes','2147483648','local'],
            capture_output=True,text=True,env=self.env,timeout=20)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('accounting options require --install-accounting-feed',result.stderr)
        self.assertEqual(f.tree(self.home),before)
        self.assertEqual(self.systemctl_calls(),[])

    def test_render_refuses_missing_controls_and_out_of_mode_options(self):
        before=f.tree(self.home)
        result=subprocess.run([sys.executable,'-I','-B',str(ROOT/'extras/agents-clone/manage.py'),
            '--shell',str(self.shell),'--install-tools',*self.feed_options(),'local'],
            capture_output=True,text=True,env=self.env,timeout=20)
        self.assertNotEqual(result.returncode,0,'accounting options must not be silently ignored')
        self.assertEqual(f.tree(self.home),before)
        with patch.dict(os.environ,self.env,clear=True):
            selected=m.Paths(type('Args',(),{'source':self.source,'shell':self.shell,'plugins':self.plugins})())
            with self.assertRaises(m.Refused):m.accounting_units(selected)
            opts=self.feed_options()
            selected.accounting_config={opts[i][len('--accounting-'):]:opts[i+1] for i in range(0,len(opts),2)}
            for field in ('source','profile','timezone','output'):
                old=selected.accounting_config[field];selected.accounting_config[field]='bad\nPRIVATE_VALUE'
                with self.assertRaises(m.Refused):m.accounting_units(selected)
                selected.accounting_config[field]=old
        self.assertEqual(self.systemctl_calls(),[])

    def test_installed_wal_feed_without_repository(self):
        checkout = self.root/'disposable-installer'
        for relative in m.TOOL_FILES:
            target = checkout/'extras'/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/'extras'/relative,target)
        installed = self.install(checkout/'extras/agents-clone/manage.py')
        self.assertEqual(installed.returncode,0,installed.stdout+installed.stderr)
        shutil.rmtree(checkout); self.assertFalse(checkout.exists())
        unit = self.home/'.config/systemd/user/hermes-canonical-feed.service'
        argv = command_from_unit(unit.read_text())
        script,opts = Path(argv[3]),argv[4:]
        source = Path(opts[opts.index('--source-db')+1])
        self.assertTrue(source.resolve().is_relative_to(self.root),'fixture source escape')
        db = sqlite3.connect(source); self.addCleanup(db.close)
        db.executescript(s.SCHEMA); source.chmod(0o600)
        db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA wal_autocheckpoint=0')
        s.insert(db,s.row()); db.commit()
        for p in self.root.glob(source.name+'-*'): p.chmod(0o600)
        canonical,state,history,usage = (Path(opts[opts.index(k)+1]) for k in
            ('--canonical-output','--history-state','--history-output','--output'))
        canonical.parent.mkdir(parents=True,mode=0o700)
        deny = '''
import os,sys
forbidden = FORBIDDEN
removed = REMOVED
assert not os.path.exists(removed)
def repository_guard(event, values):
    if event in ('open','os.listdir','os.scandir','os.chdir') and values:
        p=values[0]
        if isinstance(p,(str,bytes,os.PathLike)):
            p=os.path.realpath(os.fsdecode(p))
            if any(p==r or p.startswith(r+os.sep) for r in (forbidden,removed)):
                raise PermissionError('repository denied')
sys.addaudithook(repository_guard)
try: open(forbidden+'/extras/agents-clone/manage.py','rb')
except PermissionError: pass
else: raise AssertionError('repository guard unexercised')
'''.replace('FORBIDDEN',repr(str(ROOT))).replace('REMOVED',repr(str(checkout)))
        calls = []
        def invoke(path,args,expected=0):
            command = [sys.executable,'-I','-B','-c',deny+s.GUARD,str(self.root),str(path),*args]
            child = subprocess.run(command,cwd=self.root,env=self.env,capture_output=True,text=True,timeout=20)
            calls.append({'script':path.name,'options':args,'exit':child.returncode,'stdout':child.stdout,'stderr':child.stderr})
            self.assertEqual(child.returncode,expected,child.stdout+child.stderr)
            if expected == 0: self.assertEqual(child.stdout+child.stderr,'')
        invoke(script,[*opts,'--observed-at','2024-03-04T10:00:00Z'])
        baseline = json.loads(history.read_text())
        self.assertEqual(baseline['coverage']['status'],'baseline-only')
        db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+10,output_tokens=output_tokens+4,'
                   'cache_read_tokens=cache_read_tokens+3,cache_write_tokens=cache_write_tokens+2,'
                   'reasoning_tokens=reasoning_tokens+1,api_call_count=api_call_count+1'); db.commit()
        invoke(script,[*opts,'--observed-at','2024-03-04T10:10:00Z'])
        actual = json.loads(history.read_text())
        self.assertEqual(actual['coverage']['observed'],dict(input_tokens=10,output_tokens=4,cache_read_tokens=3,
            cache_write_tokens=2,reasoning_tokens=1,api_call_count=1,prompt_tokens=15,total_tokens=19))
        self.assertEqual(json.loads(usage.read_text())['sources'][0]['windows']['history'],174)
        self.assertEqual(actual['buckets']['day'][0]['sources'][0]['counts']['total_tokens'],19)
        outputs = {p.name:p.read_bytes() for p in (canonical,state,history,usage)}
        db.execute('UPDATE schema_version SET version=999'); db.commit()
        invoke(script,[*opts,'--observed-at','2024-03-04T10:20:00Z'],2)
        self.assertEqual({p.name:p.read_bytes() for p in (canonical,state,history,usage)},outputs)
        db.execute('UPDATE schema_version SET version=31'); db.commit()
        adapter = script.with_name('hermes_accounting.py')
        invoke(adapter,['--recover-history','--history-state',str(state),'--history-output',str(history),'--output',str(usage)])
        self.assertEqual({p.name:p.read_bytes() for p in (canonical,state,history,usage)},outputs)
        # Execute the installed service arguments with its real UTC clock too.
        invoke(script,opts)
        clocked=json.loads(history.read_text())
        self.assertRegex(clocked['observedAt'],r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(clocked['coverage']['observed']['total_tokens'],19)
        outputs={p.name:p.read_bytes() for p in (canonical,state,history,usage)}
        registry = json.loads((self.home/'.local/share/a-la-carchy/managed-files.json').read_text())['files']
        bundle = self.home/'.local/share/a-la-carchy'
        for relative in DEPENDENCIES:
            p = bundle/relative
            self.assertEqual(p.read_bytes(),(ROOT/'extras'/relative).read_bytes())
            self.assertEqual(registry[str(p)],m.digest(p.read_bytes()))
        for p in (canonical,state,history,usage):
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            for secret in ('PRIVATE_SESSION','PRIVATE_MODEL','PRIVATE_TASK','PRIVATE_PROMPT','PRIVATE_MESSAGE','api.openai.com'):
                self.assertNotIn(secret,p.read_text())
        self.assertEqual(self.systemctl_calls(),[])
        self.assertFalse((unit.parent/'timers.target.wants').exists())
        print('INSTALLED_CANONICAL_FEED_PROOF='+json.dumps({'source':'synthetic SQLite+committed WAL',
            'removed_checkout':str(checkout),'repository_read_denied':str(ROOT),'audit_guard_exercised':True,
            'calls':calls,'baseline':baseline,'usage':json.loads(usage.read_text()),'delta_history':actual,
            'history':clocked,'clocked_observation_without_override':True,
            'output_sha256':{n:m.digest(b) for n,b in outputs.items()},
            'installed_sha256':{n:registry[str(bundle/n)] for n in DEPENDENCIES},'systemctl_calls':[]},sort_keys=True))

    def test_managed_feed_update_refusal_and_rollback(self):
        result=self.install(); self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        bundle = self.home/'.local/share/a-la-carchy'
        registry = bundle/'managed-files.json'
        units = self.home/'.config/systemd/user'
        paths = [bundle/n for n in DEPENDENCIES]+[units/'hermes-canonical-feed.service',units/'hermes-canonical-feed.timer']
        # Registered version replacement + repeat no-op through the real installer.
        data=json.loads(registry.read_text())
        for p in paths:
            p.write_bytes(b'# older owned feed delivery\n'); p.chmod(0o600)
            data['files'][str(p)]=m.digest(p.read_bytes())
        registry.write_text(json.dumps(data)+'\n')
        result=self.install(); self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        before=f.tree(self.home); self.assertEqual(self.install().returncode,0)
        self.assertEqual(f.tree(self.home),before)
        for p in paths:
            old=p.read_bytes(); p.write_bytes(b'# manual edit\n')
            pre=f.tree(self.home); result=self.install()
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(f.tree(self.home),pre)
            p.write_bytes(old)
        # Existing transaction mechanism must restore first-time AND replacement files/modes.
        u=load(ROOT/'extras/agents-clone/upgrade.py','feed_install_transaction')
        args=type('Args',(),{'source':self.source,'shell':self.shell,'plugins':self.plugins})()
        with patch.dict(os.environ,self.env,clear=True):
            selected=m.Paths(args)
            config=self.feed_options()
            selected.accounting_config={config[i][len('--accounting-'):]:config[i+1] for i in range(0,len(config),2)}
            replacements=[(bundle/n,(ROOT/'extras'/n).read_bytes()) for n in DEPENDENCIES]+m.accounting_units(selected)
            data=json.loads(registry.read_text())
            p=paths[0]; p.write_bytes(b'# old before transaction\n'); p.chmod(0o600)
            data['files'][str(p)]=m.digest(p.read_bytes())
            missing=paths[-1]; missing.unlink(); data['files'].pop(str(missing))
            registry.write_text(json.dumps(data)+'\n')
            pre={p:(p.read_bytes(),p.stat().st_mode & 0o777) if p.exists() else None for p in [*paths,registry]}
            atomic=u.manage.atomic_write; written=[]
            def fail(path,data,mode):
                if path==registry: raise OSError('synthetic registration failure')
                written.append(path); return atomic(path,data,mode)
            with patch.object(u.manage,'atomic_write',side_effect=fail), self.assertRaises(u.manage.Refused):
                u.install_files(selected,replacements)
            self.assertIn(missing,written)
            self.assertEqual({p:(p.read_bytes(),p.stat().st_mode & 0o777) if p.exists() else None for p in pre},pre)
        self.assertEqual(self.systemctl_calls(),[])

if __name__ == '__main__': unittest.main(verbosity=2)
