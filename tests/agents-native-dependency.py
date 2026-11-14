#!/usr/bin/env python3
"""Real guarded upgrade/rebuild with private captured-native dependencies."""
from pathlib import Path
import importlib.util,json,os,shutil,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
CAPTURED=Path(os.environ['ALACARCHY_AGENTS_SOURCE'])
spec=importlib.util.spec_from_file_location('dependency_upgrade_fixture',Path(__file__).with_name('agents-upgrade.py'))
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
f,m=u.f,u.m

class DependencyDriftTests(f.Fixture):
    LAYOUT='limits'
    def setUp(self):
        super().setUp()
        self.ok('local','enable')
        self.registry=self.home/'.local/share/a-la-carchy/managed-files.json'
    def paths(self):
        return m.Paths(type('Args',(),{'source':self.source,'shell':self.shell,'plugins':self.plugins})())
    def dependency_case(self,unused,state):
        if unused:self.ok('local','disable')
        shell=self.root/'captured-native-shell'
        self.assertTrue(self.root in shell.parents)
        shutil.copytree(CAPTURED.parent.parent,shell,symlinks=True)
        self.source=shell/'plugins/agents'
        icon=self.source.parent/'panels/tailscale/TailscaleIcon.qml'
        original_icon=icon.read_bytes()
        self.assertEqual(m.digest(original_icon),'05142bf1863a671c3b6b39f4a2455d556e2a75b4a373aa1a559c36c918aa8329')
        clone_before=f.tree(self.clone)
        registry_before=self.registry.read_bytes();shell_before=self.shell.read_bytes()
        managed=json.loads(registry_before)['files']
        helpers={Path(p):(Path(p).read_bytes(),Path(p).stat().st_mode) for p in managed
                 if self.clone not in Path(p).parents and Path(p).is_file()}
        calls_before=self.systemctl_calls();staged=[]
        upgrader=None if unused else u.UpgradeTests.upgrader(self)
        owner=m if unused else upgrader.manage
        with patch.dict(os.environ,self.env,clear=True):
            paths=self.paths();plan=owner.clone_plan(paths);local,rendered,_=plan
            self.assertEqual(rendered['AgentsTailscaleIcon.qml'].encode(),original_icon)
            self.assertEqual(owner.current_source(paths),rendered['manifest']['omarchy']['alaCarchySource'])
            write=local.write
            def write_then_change(source,target,cached):
                write(source,target,cached)
                self.assertEqual(Path(source),self.source)
                self.assertEqual((Path(target)/'AgentsTailscaleIcon.qml').read_bytes(),original_icon)
                staged.append(state)
                if state=='changed':icon.write_bytes(original_icon+b'\n// synthetic post-render drift\n')
                elif state=='missing':icon.unlink()
            rebuild=[];load=owner.load
            def observe_load(name,path):
                module=load(name,path)
                if name=='alc_agents_rebuild':rebuild.append(module)
                return module
            with patch.object(owner,'clone_plan',return_value=plan),patch.object(local,'write',side_effect=write_then_change),patch.object(owner,'load',side_effect=observe_load):
                if state=='unchanged':
                    m.enable(paths,'local') if unused else upgrader.upgrade(paths)
                else:
                    with self.assertRaisesRegex(owner.Refused,r'^upgrade refused/rolled back; private backup retained at /tmp/') as caught:
                        m.enable(paths,'local') if unused else upgrader.upgrade(paths)
                    reason=caught.exception.__context__
                    refusal=owner.Refused
                    if unused:
                        self.assertEqual(len(rebuild),1)
                        refusal=rebuild[0].manage.Refused
                        self.assertIsInstance(reason,refusal)
                        self.assertEqual(str(reason),str(caught.exception))
                        reason=reason.__context__
                    self.assertIsInstance(reason,refusal)
                    self.assertEqual(str(reason),'native source changed while staging; preserved')
        self.assertEqual(staged,[state],'regression must reach the real staged cached-icon boundary')
        if state=='unchanged':
            self.assertEqual((self.clone/'AgentsTailscaleIcon.qml').read_bytes(),original_icon)
            registered=json.loads(self.registry.read_bytes())['files']
            self.assertEqual(registered[str(self.clone/'AgentsTailscaleIcon.qml')],m.digest(original_icon))
        else:
            self.assertEqual(f.tree(self.clone),clone_before)
            self.assertEqual(self.registry.read_bytes(),registry_before)
            self.assertEqual(self.shell.read_bytes(),shell_before)
            self.assertEqual(self.systemctl_calls(),calls_before)
            for p,(data,mode) in helpers.items():
                self.assertEqual(p.read_bytes(),data,str(p));self.assertEqual(p.stat().st_mode,mode,str(p))
            self.assertEqual(icon.exists(),state!='missing')
            if state=='changed':self.assertEqual(icon.read_bytes(),original_icon+b'\n// synthetic post-render drift\n')
    def test_enabled_unchanged_icon_upgrade_succeeds(self):self.dependency_case(False,'unchanged')
    def test_enabled_changed_icon_after_render_refuses(self):self.dependency_case(False,'changed')
    def test_enabled_missing_icon_after_render_refuses(self):self.dependency_case(False,'missing')
    def test_unused_unchanged_icon_rebuild_succeeds(self):self.dependency_case(True,'unchanged')
    def test_unused_changed_icon_after_render_refuses(self):self.dependency_case(True,'changed')
    def test_unused_missing_icon_after_render_refuses(self):self.dependency_case(True,'missing')

if __name__=='__main__':
    unittest.main(verbosity=2)
