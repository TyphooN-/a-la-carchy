#!/usr/bin/env python3
"""Offline managed packaging of the passive helper; no units are activated."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


f=load(ROOT/'tests/agents-clone.py','quota_package_fixture')
c=load(ROOT/'tests/hermes-quota-calibration.py','quota_input_fixture')
m=load(ROOT/'extras/agents-clone/manage.py','quota_package_manager')


class DeliveryTests(f.Fixture):
    LAYOUT='limits'

    def test_managed_install_is_dormant_idempotent_and_independent_of_checkout(self):
        for name in ('quota_calibration.py','quota-calibration.service.in','quota-calibration.timer'):
            self.assertIn('local-router-stats/'+name,m.TOOL_FILES,'passive helper missing from managed distribution')
        disposable=self.root/'installer'
        for relative in m.TOOL_FILES:
            target=disposable/'extras'/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/'extras'/relative,target)
        argv=[sys.executable,'-I','-B',str(disposable/'extras/agents-clone/manage.py'),
              '--source',str(self.source),'--shell',str(self.shell),'--plugins',str(self.plugins),
              '--install-tools','local']
        first=subprocess.run(argv,capture_output=True,text=True,env=self.env,timeout=30)
        self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        before=f.tree(self.home)
        second=subprocess.run(argv,capture_output=True,text=True,env=self.env,timeout=30)
        self.assertEqual(second.returncode,0,second.stdout+second.stderr)
        self.assertEqual(f.tree(self.home),before)
        self.assertEqual(self.systemctl_calls(),[])
        self.assertFalse((self.home/'.config/systemd/user/hermes-quota-calibration.service').exists())
        self.assertFalse(self.clone.exists())
        installed=self.home/'.local/share/a-la-carchy/local-router-stats/quota_calibration.py'
        shutil.rmtree(disposable)
        fixture=c.CalibrationTests('test_first_observation_is_only_baseline_for_distinct_principals')
        fixture.setUp();self.addCleanup(fixture.doCleanups);fixture.inputs()
        argv=[sys.executable,'-I','-B',str(installed),'--canonical',str(fixture.canonical),
              '--quota-dir',str(fixture.quota),'--profile','default','--state',str(fixture.state),
              '--output',str(fixture.output),'--now',c.stamp(fixture.time)]
        result=subprocess.run(argv,capture_output=True,text=True,env=self.env,timeout=8)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(fixture.output.read_text())['accounts'][0]['status'],'calibrating')
        self.assertEqual(self.systemctl_calls(),[])
        installed.write_text(installed.read_text()+'\n# manual fixture edit\n')
        preimage=f.tree(self.home)
        # A real managed installer must not adopt manually edited helper bytes.
        refused=self.run_manage('local','install-tools')
        self.assertNotEqual(refused.returncode,0)
        self.assertEqual(f.tree(self.home),preimage)
        print('PASSIVE_PACKAGE_PROOF='+json.dumps(dict(first=first.returncode,repeat=second.returncode,
            installed_cli=result.returncode,manual_edit_refusal=refused.returncode,
            systemctl_calls=self.systemctl_calls(),source_checkout_removed=not disposable.exists())))


if __name__ == '__main__':
    unittest.main(verbosity=2)
