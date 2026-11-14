#!/usr/bin/env python3
"""Exercise canonical Supersonic packaging in a private fixture only.

ALACARCHY_CANONICAL_SUPERSONIC_SOURCE names the reviewed canonical source.
The source is never modified; mutated controls use private copies.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ['ALACARCHY_CANONICAL_SUPERSONIC_SOURCE'])
spec = importlib.util.spec_from_file_location('supersonic_delivery', ROOT/'extras/supersonic/stage_plugin.py')
DELIVERY = importlib.util.module_from_spec(spec)
spec.loader.exec_module(DELIVERY)
FILES = ('BarWidget.qml', 'Service.qml', 'IPCRequest.qml', 'ipc.py')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob('*') if p.is_file()}


class CanonicalDelivery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='s-delivery-')
        self.root = Path(self.temp.name)
        self.source = self.root/'canonical'
        shutil.copytree(SOURCE, self.source)
        self.target = self.root/'plugins/alacarchy.supersonic'

    def tearDown(self):
        self.temp.cleanup()

    def stage(self):
        error = None
        try:
            DELIVERY.stage(self.source, self.target)
        except ValueError as exc:
            error = type(exc).__name__
        self.assertTrue(self.target.is_dir(), 'canonical native delivery was refused: ' + str(error))

    def test_canonical_delivery_retains_native_manifest_and_component_bytes(self):
        before = tree(self.source)
        self.stage()
        manifest = json.loads((self.target/'manifest.json').read_text())
        original = json.loads((self.source/'manifest.json').read_text())
        self.assertEqual(manifest['id'], 'alacarchy.supersonic')
        self.assertEqual(manifest['omarchy']['derivedFrom'], 'omarchy.supersonic')
        self.assertNotIn('clonedFrom', manifest['omarchy'])
        for field in ('schemaVersion', 'entryPoints', 'kinds', 'barWidget'):
            self.assertEqual(manifest[field], original[field], field)
        for name in ('Service.qml', 'IPCRequest.qml', 'ipc.py'):
            self.assertEqual((self.target/name).read_bytes(), (self.source/name).read_bytes(), name)
        expected = (self.source/'BarWidget.qml').read_text().replace('moduleName: "omarchy.supersonic"', 'moduleName: "alacarchy.supersonic"')
        self.assertEqual((self.target/'BarWidget.qml').read_text(), expected)
        self.assertEqual(set(tree(self.target)), set(FILES) | {'manifest.json'})
        self.assertEqual(tree(self.source), before)
        self.assertTrue(DELIVERY.managed(self.target))

    def test_real_stager_cli_accepts_canonical_source(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT/'extras/supersonic/stage_plugin.py'), '--source', str(self.source), str(self.target)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(DELIVERY.managed(self.target))

    def test_unknown_source_bytes_refuse_before_creating_target_parent(self):
        (self.source/'Service.qml').write_text((self.source/'Service.qml').read_text() + '\n// unfamiliar source\n')
        with self.assertRaises(ValueError):
            DELIVERY.stage(self.source, self.target)
        self.assertFalse(self.target.parent.exists())

    def test_unexpected_source_entry_is_not_shipped(self):
        (self.source/'foreign.qml').write_text('unregistered')
        with self.assertRaises(ValueError):
            DELIVERY.stage(self.source, self.target)
        self.assertFalse(self.target.parent.exists())

    def test_symlink_source_entry_is_refused(self):
        helper = self.source/'ipc.py'
        original = self.root/'outside.py'
        helper.rename(original)
        helper.symlink_to(original)
        with self.assertRaises((ValueError, OSError)):
            DELIVERY.stage(self.source, self.target)
        self.assertFalse(self.target.parent.exists())

    def test_fifo_source_entry_refuses_without_blocking_open(self):
        helper = self.source/'ipc.py'
        helper.unlink()
        os.mkfifo(helper)
        timed_out = False
        try:
            result = subprocess.run([sys.executable, '-B', str(ROOT/'extras/supersonic/stage_plugin.py'), '--source', str(self.source), str(self.target)], capture_output=True, text=True, timeout=2)
        except subprocess.TimeoutExpired:
            timed_out = True
        self.assertFalse(timed_out, 'nonregular input blocked before its type could be refused')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.parent.exists())

    def test_hardlinked_source_entry_is_refused(self):
        os.link(self.source/'ipc.py', self.root/'helper-link')
        with self.assertRaises(ValueError):
            DELIVERY.stage(self.source, self.target)
        self.assertFalse(self.target.parent.exists())

    def test_linked_source_parent_is_refused(self):
        alias = self.root/'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            DELIVERY.stage(alias/'canonical', self.target)
        self.assertFalse(self.target.parent.exists())

    def test_existing_target_is_preserved(self):
        self.target.mkdir(parents=True)
        (self.target/'custom').write_text('newer user data')
        before = tree(self.target)
        with self.assertRaises(ValueError):
            DELIVERY.stage(self.source, self.target)
        self.assertEqual(tree(self.target), before)

    def test_target_symlink_parent_is_refused(self):
        outside = self.root/'outside'
        outside.mkdir()
        self.target.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            DELIVERY.stage(self.source, self.target)
        self.assertEqual(tree(outside), {})

    def test_manifest_cannot_authorize_modified_helper(self):
        self.stage()
        helper = self.target/'ipc.py'
        helper.write_text(helper.read_text() + '\n# foreign edit\n')
        path = self.target/'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['omarchy']['managedFiles']['ipc.py'] = digest(helper)
        path.write_text(json.dumps(manifest))
        self.assertFalse(DELIVERY.managed(self.target))

    def test_unknown_target_extra_is_not_owned(self):
        self.stage()
        (self.target/'extra').write_text('unregistered')
        self.assertFalse(DELIVERY.managed(self.target))

    def test_native_enable_repeat_disable_preserves_other_widgets(self):
        path = self.root/'shell.json'
        config = {'bar': {'layout': {'left': [{'id':'alacarchy.agents', 'settings':{'retained':True}}], 'center':[{'id':'omarchy.media','maxLabelWidth':900}], 'right':[{'id':'omarchy.clock'}]}}}
        path.write_text(json.dumps(config))
        self.assertTrue(DELIVERY.configure(self.source, self.target, path, 'enable'))
        first = path.read_bytes()
        self.assertTrue(DELIVERY.configure(self.source, self.target, path, 'enable'))
        self.assertEqual(path.read_bytes(), first)
        self.assertTrue(DELIVERY.configure(self.source, self.target, path, 'check-enabled'))
        self.assertTrue(DELIVERY.configure(self.source, self.target, path, 'disable'))
        self.assertEqual(json.loads(path.read_text()), config)

    def test_known_native_enabled_state_does_not_require_source_to_be_present(self):
        self.stage()
        path = self.root/'shell.json'
        path.write_text(json.dumps({'bar':{'layout':{'center':[{'id':'alacarchy.supersonic', '_alaCarchySupersonicOriginal':None}]}}}))
        before = path.read_bytes(), tree(self.target)
        missing = self.root/'missing-canonical-source'
        self.assertTrue(DELIVERY.configure(missing, self.target, path, 'check-enabled'))
        self.assertTrue(DELIVERY.configure(missing, self.target, path, 'enable'))
        self.assertEqual((path.read_bytes(), tree(self.target)), before)

    def test_configurator_copy_does_not_advertise_retired_library(self):
        script = (ROOT/'a-la-carchy.sh').read_text()
        function = 'enable_supersonic_music() {' + script.split('enable_supersonic_music() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        command = 'apply_shell_change() { printf "%s\\n" "$2"; }\n' + function + '\nenable_supersonic_music'
        result = subprocess.run(['/bin/bash', '-c', command], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('playback controls', result.stdout)
        self.assertNotIn('library search', result.stdout.lower())

    def test_appearance_description_uses_current_native_capabilities(self):
        script = (ROOT/'a-la-carchy.sh').read_text()
        declaration = 'APPEARANCE_ITEMS=(' + script.split('APPEARANCE_ITEMS=(', 1)[1].split('\n)', 1)[0] + '\n)\n'
        functions = []
        for name in ('get_current_item_count', 'parse_toggle_item', 'get_current_description'):
            header = name + '() {'
            functions.append(header + script.split(header, 1)[1].split('\n}\n', 1)[0] + '\n}\n')
        command = declaration + '\n'.join(functions) + '''
CATEGORY_CURSOR=8
for ITEM_CURSOR in "${!APPEARANCE_ITEMS[@]}"; do
  parse_toggle_item "${APPEARANCE_ITEMS[$ITEM_CURSOR]}"
  if [[ "$TOGGLE_ID" == supersonic ]]; then get_current_description; exit 0; fi
done
exit 3
'''
        result = subprocess.run(['/bin/bash', '-c', command], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('playback controls', result.stdout)
        self.assertNotIn('library search', result.stdout.lower())

    def test_configurator_uses_explicit_canonical_source(self):
        script = (ROOT/'a-la-carchy.sh').read_text()
        function = '_supersonic_tool() {' + script.split('_supersonic_tool() {', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        home = self.root/'home'
        home.mkdir()
        stock = self.root/'stock'
        stock.mkdir()
        path = self.root/'shell.json'
        path.write_text(json.dumps({'bar':{'layout':{'center':[{'id':'omarchy.media'}]}}}))
        env = dict(os.environ, HOME=str(home), ALC_SUPERSONIC_DIR=str(ROOT/'extras/supersonic'),
                   OMARCHY_DIR=str(stock), SHELL_JSON=str(path),
                   ALACARCHY_SUPERSONIC_SOURCE=str(self.source))
        result = subprocess.run(['/bin/bash', '-c', function + '\n_supersonic_tool --enable'], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        target = home/'.config/omarchy/plugins/alacarchy.supersonic'
        self.assertEqual(json.loads((target/'manifest.json').read_text())['omarchy']['derivedFrom'], 'omarchy.supersonic')
        self.assertTrue(DELIVERY.managed(target))

    def test_legacy_plugin_is_not_silently_reused_for_native_request(self):
        legacy = self.root/'media'
        legacy.mkdir()
        (legacy/'manifest.json').write_text(json.dumps({'id':'omarchy.media','entryPoints':{'barWidget':'BarWidget.qml'}}))
        (legacy/'BarWidget.qml').write_text('import qs.Ui\nBarWidget { PopupCard { }\n// Qt.MiddleButton wheel.angleDelta.y\n}')
        DELIVERY.stage(legacy, self.target)
        path = self.root/'shell.json'
        path.write_text(json.dumps({'bar':{'layout':{'center':[{'id':'omarchy.media'}]}}}))
        DELIVERY.configure(legacy, self.target, path, 'enable')
        before = tree(self.target), path.read_bytes()
        self.assertFalse(DELIVERY.configure(self.source, self.target, path, 'check-enabled'), 'legacy UI must not satisfy the canonical enabled check')
        with self.assertRaises(ValueError):
            DELIVERY.configure(self.source, self.target, path, 'enable')
        self.assertEqual((tree(self.target), path.read_bytes()), before)


if __name__ == '__main__':
    unittest.main()
