#!/usr/bin/env python3
"""Keyed Local cards: only exact native card shapes are patched; synthetic data only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))
STAGER = ROOT / 'extras/local-router-stats/stage_plugin.py'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = load('keyed_cards_stage', STAGER)
fixture = load('keyed_cards_fixture', ROOT / 'tests/agents_fixture.py')
manage = load('keyed_cards_manage', ROOT / 'extras/agents-clone/manage.py')

NATIVE_CARD = '            ProviderSection {\n'
# Valid QML the keyed transform does not recognise; each must refuse unchanged.
NATIVE_VARIANTS = {
    'explicit delegate property': '            delegate: ProviderSection {\n',
    'delegate property on its own line': '            delegate:\n            ProviderSection {\n',
    'deeper indentation': '              ProviderSection {\n',
    'shallower indentation': '          ProviderSection {\n',
    'unspaced brace': '            ProviderSection{\n',
}
FIXTURE_REPEATER = '            model: root.providers\n          }\n'
FIXTURE_VARIANTS = {
    'explicit delegate property': '            model: root.providers\n\n            delegate: ProviderSection {\n'
                                  '              required property var modelData\n'
                                  '              provider: modelData\n            }\n          }\n',
    'indented card': '            model: root.providers\n\n              ProviderSection {\n'
                     '              required property var modelData\n'
                     '              provider: modelData\n            }\n          }\n',
    'card outside the repeater': FIXTURE_REPEATER + '          ProviderSection { provider: null }\n',
}


def tree(path):
    """Every entry under path: kind, mode and content digest."""
    path = Path(path)
    out = {}
    for item in sorted(path.rglob('*')):
        info = item.lstat()
        rel = item.relative_to(path).as_posix()
        out[rel] = ('dir', info.st_mode) if item.is_dir() else \
            ('file', info.st_mode, hashlib.sha256(item.read_bytes()).hexdigest())
    return out


class DelegateShapes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='keyed-cards-'))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def native_copy(self, name, old=None, new=None):
        source = self.tmp / name / 'agents'
        shutil.copytree(NATIVE, source, symlinks=True)
        # Modern navigation imports this sibling; keep the real dependency in
        # both positive and refusal fixtures instead of failing before admission.
        dependency = source.parent/'panels/elsewhen/Model.js'
        dependency.parent.mkdir(parents=True)
        shutil.copy2(NATIVE.parent/'panels/elsewhen/Model.js', dependency)
        if old is not None:
            panel = (source / 'Panel.qml').read_text()
            self.assertEqual(panel.count(old), 1, 'the native card shape moved; update this regression')
            (source / 'Panel.qml').write_text(panel.replace(old, new, 1))
        return source

    def fixture_copy(self, name, old=None, new=None):
        source = fixture.make_source(self.tmp / name / 'agents', 'limits')
        if old is not None:
            panel = (source / 'Panel.qml').read_text()
            self.assertEqual(panel.count(old), 1, 'the fixture card shape moved; update this regression')
            (source / 'Panel.qml').write_text(panel.replace(old, new, 1))
        return source

    def assert_refused(self, source):
        """render, the stager and the maintained clone plan all refuse; nothing is written."""
        source_before = tree(source)
        with self.assertRaises(ValueError) as caught:
            stage.render(source, 'fixture.keyed')
        self.assertEqual(str(caught.exception), stage.UNSUPPORTED)
        destination = self.tmp / ('plugins-' + source.parent.name)
        destination.mkdir()
        (destination / 'unrelated.qml').write_text('Item {}\n')
        destination_before = tree(destination)
        with self.assertRaises(ValueError) as caught:
            stage.stage(source, destination / 'fixture.keyed', 'fixture.keyed')
        self.assertEqual(str(caught.exception), stage.UNSUPPORTED)
        cli = subprocess.run([sys.executable, '-I', '-B', str(STAGER), '--source', str(source),
                              '--id', 'fixture.keyed', str(destination / 'fixture.cli')],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual((cli.returncode, cli.stdout, cli.stderr), (1, '', stage.UNSUPPORTED + '\n'))
        with self.assertRaises(manage.Refused) as refused:
            manage.clone_plan(types.SimpleNamespace(source=source))
        self.assertEqual(str(refused.exception), 'installed Agents source is unsupported or unreadable; nothing staged')
        self.assertEqual(tree(destination), destination_before, 'a refused stage must leave its destination unchanged')
        self.assertEqual(tree(source), source_before)

    def test_native_card_shape_is_keyed(self):
        panel = (NATIVE / 'Panel.qml').read_text()
        self.assertEqual(panel.count(NATIVE_CARD), 1)
        rendered = stage.render(self.native_copy('native'), 'fixture.keyed')['Panel.qml']
        self.assertEqual(rendered.count('id: stableProviderRows'), 1)
        self.assertEqual(rendered.count('model: root.addStage === "" ? root.providerRowsModel : null'), 1)
        self.assertEqual(rendered.count('              required property string providerKey\n'
                                        '              required property int index\n'
                                        '              readonly property var modelData: root.providerRowsByKey[providerKey] || null\n'
                                        '              width: column.width\n              provider: modelData\n'
                                        '              providerIndex: index\n'), 1)
        self.assertIn('readonly property var providerRowInput: root.providers', rendered)

    def test_minimal_fixture_without_cards_stays_supported(self):
        rendered = stage.render(self.fixture_copy('fixture'), 'fixture.keyed')['Panel.qml']
        self.assertEqual(rendered.count(FIXTURE_REPEATER), 1)
        self.assertNotIn('ProviderSection {', rendered)
        self.assertNotIn('id: stableProviderRows', rendered)

    def test_unrecognised_native_card_shapes_refuse_unchanged(self):
        for name, card in NATIVE_VARIANTS.items():
            with self.subTest(name):
                self.assert_refused(self.native_copy(name.replace(' ', '-'), NATIVE_CARD, card))

    def test_native_card_extension_refuses_unchanged(self):
        old = '              providerIndex: index\n'
        new = old + '              opacity: modelData.ready ? 1 : 0.5\n'
        self.assert_refused(self.native_copy('extended-native-card', old, new))

    def test_fixture_exemption_needs_the_exact_card_free_shape(self):
        for name, shape in FIXTURE_VARIANTS.items():
            with self.subTest(name):
                self.assert_refused(self.fixture_copy(name.replace(' ', '-'), FIXTURE_REPEATER, shape))


class RenameIdentity(unittest.TestCase):
    """Execute generated JS + real action guard; binding callbacks are modeled,
    not Qt runtime proof. Every command is intercepted, never dispatched.
    """
    @classmethod
    def setUpClass(cls):
        cls.panel = stage.render(NATIVE, 'fixture.keyed')['Panel.qml']
        cls.extract = load('keyed_card_functions', ROOT / 'tests/grok-native-panel.py')

    def session(self, change, late=False, save=True, read_only=False):
        header = self.panel.split('  component AccountHeader: Item {\n', 1)[1]
        header = header.split('    // One line:', 1)[0]
        names = re.findall(r'^    function (\w+)\(', header, re.M)
        functions = '\n'.join(self.extract.function(header, name) for name in names)

        def handler(name):
            found = re.search(r'^    ' + name + r': (\{\n.*?^    \}|[^\n]+)', header, re.M | re.S)
            return found.group(1) if found else ''

        binding = re.search(r'^        readonly property var modelData: (.+)$', self.panel, re.M)
        assert binding is not None, 'Missing generated account data binding'
        self.assertEqual(binding.group(1), 'section.accountRowsByKey[providerKey] || ({})')
        defaults = re.findall(r'^    property string (\w+): ""$', header, re.M)
        seed = {'functions': functions, 'action': self.extract.function(self.panel, 'renameAccount'),
                'accountChanged': handler('onAccountChanged'), 'ownerChanged': handler('onOwnerChanged'),
                'defaults': defaults, 'binding': binding.group(1), 'change': change,
                'late': late, 'save': save, 'readOnly': read_only}
        program = r"""const vm = require('node:vm');
const seed = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const calls = [], a = {id:'A', label:'Account A'}, b = {id:'B', label:'Account B'};
const ctx = {owner:{providerId:'cloud',readOnly:seed.readOnly}, account:a, editing:false,
  section:{accounts:[a,b],accountRowsByKey:{A:a,B:b}}, providerKey:'A', index:0, renameGeneration:0, nameField:{text:'',forceActiveFocus(){},selectAll(){}},
  Util:{execArgv:cmd=>calls.push(cmd)}, focusKeys(){if(seed.late) change();}};
ctx.root = ctx;ctx.head=ctx;
for (const key of seed.defaults) ctx[key] = '';
Object.defineProperty(ctx, 'label', {get(){return ctx.renamedTo || String(ctx.account.label || ctx.account.id || '');}});
vm.createContext(ctx);
vm.runInContext(seed.functions+'\n'+seed.action,ctx,{timeout:1000});
function callback(code){if(code) vm.runInContext(code,ctx,{timeout:1000});}
function change(){
  if(seed.change==='reorder') ctx.section.accounts=[b,a];
  if(seed.change==='replace') ctx.section.accounts=[{id:'C',label:'Account C'},b];
  if(seed.change==='same') ctx.section.accounts=[{id:'A',label:'Refreshed label',tokens:123},b];
  if(seed.change==='missing') ctx.section.accounts=[{},b];
  if(seed.change==='provider'){
    ctx.owner={providerId:'different-provider',readOnly:false};callback(seed.ownerChanged);return;
  }
  if(seed.change==='same-owner'){
    ctx.owner={providerId:'cloud',readOnly:seed.readOnly,updatedAt:42};callback(seed.ownerChanged);return;
  }
  // Adversarial rebind: native keyed rows normally retain/recreate identity
  // (proved in Qt). Force a wrong record under the old key to exercise the
  // final action guard, including a synchronous focus callback before dispatch.
  ctx.section.accountRowsByKey.A=ctx.section.accounts[ctx.index];
  ctx.account=vm.runInContext(seed.binding,ctx,{timeout:1000});callback(seed.accountChanged);
}
vm.runInContext('startRename()',ctx,{timeout:1000});
ctx.nameField.text='  Edited A  ';
if(!seed.late) change();
const editingAfterChange=ctx.editing;
vm.runInContext('finishRename('+JSON.stringify(seed.save)+')',ctx,{timeout:1000});
console.log(JSON.stringify({calls,editingAfterChange,editingAfterFinish:ctx.editing,field:ctx.nameField.text}));
"""
        node = shutil.which('node')
        assert node is not None, 'Qualified fixture Node is unavailable'
        child = subprocess.run([node, '-e', program], input=json.dumps(seed), text=True,
                               capture_output=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr)
        return json.loads(child.stdout)

    def test_account_reordering_never_redirects_edit(self):
        value = self.session('reorder')
        self.assertEqual(value['calls'], [])
        self.assertFalse(value['editingAfterChange'])

    def test_equal_count_replacement_never_redirects_edit(self):
        value = self.session('replace')
        self.assertEqual(value['calls'], [])
        self.assertFalse(value['editingAfterChange'])

    def test_provider_identity_change_never_redirects_edit(self):
        value = self.session('provider')
        self.assertEqual(value['calls'], [])
        self.assertFalse(value['editingAfterChange'])

    def test_same_account_refresh_retains_edit_and_targets_original_id(self):
        value = self.session('same')
        self.assertTrue(value['editingAfterChange'])
        self.assertEqual(value['field'], '  Edited A  ')
        self.assertEqual(value['calls'], [['omarchy-agent-account-rename', 'cloud', 'A', 'Edited A']])
        self.assertFalse(value['editingAfterFinish'])

    def test_same_provider_refresh_retains_edit(self):
        value = self.session('same-owner')
        self.assertTrue(value['editingAfterChange'])
        self.assertEqual(value['calls'], [['omarchy-agent-account-rename', 'cloud', 'A', 'Edited A']])

    def test_late_focus_callback_cannot_redirect_edit(self):
        self.assertEqual(self.session('reorder', late=True)['calls'], [])
        self.assertEqual(self.session('provider', late=True)['calls'], [])

    def test_missing_identity_cancels_without_dispatch(self):
        value = self.session('missing')
        self.assertEqual(value['calls'], [])
        self.assertFalse(value['editingAfterChange'])

    def test_cancel_and_existing_read_only_guard_never_dispatch(self):
        self.assertEqual(self.session('same', save=False)['calls'], [])
        self.assertEqual(self.session('same', read_only=True)['calls'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
