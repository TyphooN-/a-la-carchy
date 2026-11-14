#!/usr/bin/env python3
"""Current native navigation: isolated generation and real JS, no desktop/model calls."""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path('/usr/share/omarchy/shell/plugins/agents')
def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
stage=load('current_navigation_stage',ROOT/'extras/local-router-stats/stage_plugin.py')
extract=load('current_navigation_extract',ROOT/'tests/grok-native-panel.py')

class Navigation(unittest.TestCase):
    def rendered(self):
        return stage.render(NATIVE,'fixture.navigation')

    def test_provider_row_keys_survive_values_and_reorder_without_reindexing_records(self):
        panel = self.rendered()['Panel.qml']
        self.assertIn('function syncProviderRows(', panel, 'Publications still replace the provider delegate model')
        function = extract.function(panel, 'syncPanelRows') + '\n' + extract.function(panel, 'syncProviderRows') + '\nroot.syncPanelRows=syncPanelRows;'
        script = r"""
const assert=require('node:assert/strict');
let serial=0, cells=[], operations=[];
const model={get count(){return cells.length},get:i=>cells[i],
 insert:(i,row)=>{operations.push('insert');cells.splice(i,0,{...row,serial:++serial})},
 remove:i=>{operations.push('remove');cells.splice(i,1)},
 move:(a,b,n)=>{assert.equal(n,1);operations.push('move');cells.splice(b,0,...cells.splice(a,1))}};
const root={providerRowsModel:model,providerRowsByKey:{},providers:[]};
FUNCTION
function step(ids, revision) {
 const source=ids.map(id=>Object.freeze({providerId:id,revision}));Object.freeze(source);
 const before=JSON.stringify(source);root.providers=source;syncProviderRows();
 assert.equal(JSON.stringify(source),before);
 assert.equal(cells.length,source.length);
 assert.deepEqual(cells.map(c=>JSON.parse(c.providerKey)[0]),ids);
 assert.equal(Object.keys(root.providerRowsByKey).length,source.length);
 cells.forEach((c,i)=>assert.equal(root.providerRowsByKey[c.providerKey],source[i]));
 return cells.map(c=>c.serial);
}
const first=step(['hidden','claude','local'],1);operations=[];
assert.deepEqual(step(['hidden','claude','local'],2),first);assert.deepEqual(operations,[]);
assert.deepEqual(step(['local','hidden','claude'],3),[first[2],first[0],first[1]]);
const replaced=step(['other','hidden','claude'],4);
assert.notEqual(replaced[0],first[2]);assert.deepEqual(replaced.slice(1),first.slice(0,2));
step([],5);assert.equal(Object.keys(root.providerRowsByKey).length,0);
step(['same','same'],6);assert.notEqual(cells[0].providerKey,cells[1].providerKey);
console.log('STABLE_PROVIDER_ROWS_OK');
""".replace('FUNCTION', function)
        child=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertEqual(child.stdout.strip(),'STABLE_PROVIDER_ROWS_OK')

    def test_nested_account_and_limit_rows_follow_semantic_identity(self):
        panel = self.rendered()['Panel.qml']
        self.assertTrue('function syncPanelRows(' in panel, 'Nested native models still reset on value publication')
        script = r"""
const assert=require('node:assert/strict');let serial=0,rows=[];
const model={get count(){return rows.length},get:i=>rows[i],
 insert:(i,v)=>rows.splice(i,0,{...v,serial:++serial}),remove:i=>rows.splice(i,1),
 move:(a,b,n)=>rows.splice(b,0,...rows.splice(a,n))};
const owner={records:{}};
FUNCTION
function update(items,kind) {
 const data=items.map(x=>Object.freeze(x));Object.freeze(data);const before=JSON.stringify(data);
 syncPanelRows(model,data,kind,owner,'records');assert.equal(JSON.stringify(data),before);
 assert.equal(rows.length,data.length);assert.equal(Object.keys(owner.records).length,data.length);
 rows.forEach((r,i)=>assert.equal(owner.records[r.providerKey],data[i]));
 return rows.map(r=>r.serial);
}
const initial=update([{id:'a',label:'before'},{id:'b',label:'other'}],'id');
assert.deepEqual(update([{id:'a',label:'new'},{id:'b',label:'new'}],'id'),initial);
assert.deepEqual(update([{id:'b'},{id:'a'}],'id'),[initial[1],initial[0]]);
const replaced=update([{id:'c'},{id:'a'}],'id');assert.notEqual(replaced[0],initial[1]);assert.equal(replaced[1],initial[0]);
update([],'id');
const window=update([{title:'Weekly',resetAt:'2026-11-10T00:00:00Z',percent:0.25}],'window');
assert.deepEqual(update([{title:'Weekly',resetAt:'2026-11-10T00:00:00Z',percent:0.5}],'window'),window);
assert.notDeepEqual(update([{title:'Weekly',resetAt:'2026-11-17T00:00:00Z',percent:0}],'window'),window);
update([],'window');assert.equal(rows.length,0);assert.deepEqual(Object.keys(owner.records),[]);
console.log('NESTED_ROWS_OK');
""".replace('FUNCTION',extract.function(panel,'syncPanelRows'))
        child=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertEqual(child.stdout.strip(),'NESTED_ROWS_OK')

    def test_native_rename_ownership_survives_obsolete_cleanup(self):
        panel = self.rendered()['Panel.qml']
        self.assertTrue('function setNativeRename(' in panel, 'No generation-owned native edit cleanup')
        script = r"""
const assert=require('node:assert/strict'),root={nativeRenameOwner:null,renaming:false};
FUNCTION
const a={},b={};
setNativeRename(a,true);assert.equal(root.renaming,true);assert.equal(root.nativeRenameOwner,a);
setNativeRename(b,true);setNativeRename(a,false);
assert.equal(root.renaming,true);assert.equal(root.nativeRenameOwner,b);
setNativeRename(b,false);assert.equal(root.renaming,false);assert.equal(root.nativeRenameOwner,null);
setNativeRename(a,false);assert.equal(root.renaming,false);
setNativeRename(a,true);setNativeRename(a,false);assert.equal(root.renaming,false);
console.log('RENAME_OWNER_OK');
""".replace('FUNCTION',extract.function(panel,'setNativeRename'))
        child=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertEqual(child.stdout.strip(),'RENAME_OWNER_OK')
        header=stage.limits_component_body(panel,'  component AccountHeader: Item {')
        self.assertIn('Component.onDestruction: { renameGeneration++; root.setNativeRename(head.nativeRenameToken, false) }',header)
        self.assertIn('onEditingChanged: root.setNativeRename(head.nativeRenameToken, editing)',header)

    def evaluate(self, mode, selected=0, dy=1):
        rendered=self.rendered();panel=rendered['Panel.qml']
        names=['windowIsLong','windowSpanMs','windowTitle','limitWindow','limitWindows','scopedPart',
               'displayWindows','hermesQuotaFresh','providerCardVisible','providerAccounts',
               'localActions','lineStop','reorderProvider','beginProviderDrag','dragProviderOver',
               'resetProviderDrag','finishProviderDrag','settleProviderDrag','dragDomainCurrent']
        functions='\n'.join(extract.function(panel,n) for n in names)
        rows=stage.limits_component_body(panel,'  readonly property var keyRows: {')
        now=1800000000000
        reset=datetime.datetime.fromtimestamp((now+60000)/1000,datetime.timezone.utc).isoformat()
        credential=lambda name,limits:dict(providerId=name,readOnly=True,credentialProvider='openai-codex',
            limitsFetchedAt=now,limitsStale=False,limits=limits)
        good=credential('codex-hermes-'+'a'*64,[{'label':'1-minute window','percent':0,'resetsAt':reset}])
        hidden=credential('codex-hermes-'+'b'*64,[])
        local={'providerId':'native-model','localModel':{'unit':'active','recipe':'EXACT_RECIPE'},'limits':[]}
        seed={'functions':functions,'rows':rows,'reorder':rendered['AgentsReorder.js'],
              'providers':[good,hidden,local],'now':now,'mode':mode,'selected':selected,'dy':dy}
        program=r'''
const vm=require('node:vm'),s=JSON.parse(require('fs').readFileSync(0,'utf8'));
const ctx={providers:s.providers,nowMs:s.now,calls:[],cursorActive:true,addButtonShown:false,picking:false,
  addStage:'',blankSlate:true,accountEntries:[],column:{spacing:10},dragProviders:null,dragProviderId:'',
  dragIndex:-1,dragTarget:-1,dragOffset:0,dragPressY:0,rowPitches:[],settling:false,
  dragVisibleIndices:[],dragVisiblePitches:[],dragPositions:[]};
ctx.root=ctx;ctx.usage={panelProviders:ctx.providers,setting:(_,f)=>f,hermesInventoryIndex:{},
  moveProvider:(id,to)=>ctx.calls.push({id,to})};
ctx.Qt={callLater:fn=>fn()};ctx.Style={duration:()=>0};ctx.dropAnimation={stop:()=>{}};
ctx.clamp=(v,a,b)=>Math.max(a,Math.min(b,v));ctx.needsSignIn=()=>false;
ctx.harnessFor=p=>p.localModel?'model-harness':'';ctx.providerIndexOfKey=()=>s.selected;ctx.pointAt=()=>{};
ctx.providerSections={count:ctx.providers.length,itemAt:i=>({height:[40,30,80][i],visible:ctx.providerCardVisible(ctx.providers[i])})};
vm.createContext(ctx);const re={};vm.createContext(re);vm.runInContext(s.reorder,re);ctx.Reorder=re;
vm.runInContext(s.functions+'\nfunction fixtureRows(){'+s.rows+'}',ctx,{timeout:1000});
const before=JSON.stringify(ctx.providers),rows=ctx.fixtureRows();let target=-1;
if(s.mode==='keyboard')ctx.reorderProvider(s.dy);
else {
  ctx.beginProviderDrag(s.selected,0);
  if(s.mode==='expiry')ctx.nowMs=s.now+60000;
  if(s.mode==='replacement')ctx.usage.panelProviders=ctx.providers.slice();
  ctx.dragProviderOver(s.selected===2?-80:100);target=ctx.dragTarget;
  ctx.settleProviderDrag(false);
}
console.log(JSON.stringify({rows,calls:ctx.calls,target,dragIndex:ctx.dragIndex,
  unchanged:JSON.stringify(ctx.providers)===before}));
'''
        run=subprocess.run(['node','-e',program],input=json.dumps(seed),capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        result=json.loads(run.stdout);self.assertTrue(result['unchanged']);return result

    def test_keyboard_preserves_raw_model_indices_and_skips_hidden_credentials(self):
        result=self.evaluate('keyboard')
        self.assertEqual(result['calls'],[{'id':'codex-hermes-'+'a'*64,'to':2}])
        self.assertEqual(result['rows'],[[{'kind':'provider','index':0}],
            [{'kind':'modelToggle','index':2},{'kind':'modelLaunch','index':2}]])
        self.assertEqual(self.evaluate('keyboard',selected=1)['calls'],[])
        self.assertEqual(self.evaluate('keyboard',dy=0)['calls'],[])

    def test_drag_uses_visible_geometry_without_reindexing_backing_providers(self):
        forward=self.evaluate('drag')
        self.assertEqual(forward['target'],2)
        self.assertEqual(forward['calls'],[{'id':'codex-hermes-'+'a'*64,'to':2}])
        reverse=self.evaluate('drag',selected=2)
        self.assertEqual(reverse['target'],0)
        self.assertEqual(reverse['calls'],[{'id':'native-model','to':0}])
        for mode,index in [('drag',1),('expiry',0),('replacement',0)]:
            with self.subTest(mode=mode,index=index):
                result=self.evaluate(mode,selected=index)
                self.assertEqual(result['calls'],[])
                self.assertEqual(result['dragIndex'],-1)

    def copied_source(self, folder):
        source=Path(folder)/'agents'
        shutil.copytree(NATIVE,source)
        dependency=source.parent/'panels/elsewhen/Model.js'
        dependency.parent.mkdir(parents=True)
        dependency.write_bytes((NATIVE.parent/'panels/elsewhen/Model.js').read_bytes())
        return source,dependency

    def test_dependency_and_mixed_navigation_refuse_without_staging(self):
        original=(NATIVE/'Panel.qml').read_text()
        for text in (original.replace('function localActions(model)', 'function unknownActions(model)',1),
                     original.replace('import "../panels/elsewhen/Model.js" as Reorder',
                                      'import "unknown.js" as Reorder',1)):
            with self.subTest(kind='navigation'):
                with self.assertRaises(ValueError):stage.patch_panel(text)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as folder:
            source,dependency=self.copied_source(folder)
            dependency.write_bytes(dependency.read_bytes()+b'\n// unreviewed dependency\n')
            target=Path(folder)/'output'
            with self.assertRaises(ValueError):stage.stage(source,target,'fixture.navigation')
            self.assertFalse(target.exists())

    def test_external_dependency_counts_against_complete_tree_entry_budget(self):
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as folder:
            source,_=self.copied_source(folder)
            existing=len(list(source.rglob('*')))
            for i in range(stage.MAX_SOURCE_ENTRIES-existing):
                (source/('padding-'+str(i))).mkdir()
            self.assertEqual(len(list(source.rglob('*'))),stage.MAX_SOURCE_ENTRIES)
            with self.assertRaises(ValueError):stage.render(source,'fixture.navigation')

    def test_generated_clone_bundles_the_exact_reorder_dependency(self):
        rendered=self.rendered()
        self.assertIn('import "AgentsReorder.js" as Reorder',rendered['Panel.qml'])
        self.assertNotIn('../panels/elsewhen/Model.js',rendered['Panel.qml'])
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as folder:
            target=Path(folder)/'clone';stage.write(NATIVE,target,rendered)
            self.assertEqual((target/'AgentsReorder.js').read_text(),rendered['AgentsReorder.js'])
            self.assertIn('function dragTargetFor(',rendered['AgentsReorder.js'])
            self.assertIn('function dropOffset(',rendered['AgentsReorder.js'])
            self.assertIn('function rowShift(',rendered['AgentsReorder.js'])

if __name__=='__main__':unittest.main(verbosity=2)
