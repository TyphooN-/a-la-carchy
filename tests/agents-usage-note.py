#!/usr/bin/env python3
"""Complete native status-note witnesses, with fail-closed staging."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_note_stager', ROOT / 'extras/local-router-stats/stage_plugin.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
fixture_spec = importlib.util.spec_from_file_location('note_fixture', Path(__file__).with_name('agents_fixture.py'))
fixture = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixture)
NATIVE = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def component(text):
    # The known native two-space component boundary, not a general QML parser.
    start = text.index('  component UsageNote: Text {')
    end = text.index('\n  }', start) + len('\n  }')
    return text[start:end]


class NativeUsageNote(unittest.TestCase):
    def assert_refused_before_staging(self, text, legacy=False):
        with tempfile.TemporaryDirectory(prefix='un-') as tmp:
            root = Path(tmp)
            source = fixture.make_source(root/'source', 'limits') if legacy else root/'source'
            if not legacy:
                shutil.copytree(NATIVE, source)
            (source/'Panel.qml').write_text(text)
            before = tree(source)
            destination = root/'absent-parent'/'clone'
            with self.assertRaises(ValueError) as error:
                s.render(source, 'fixture.metrics')
            self.assertEqual(str(error.exception), s.UNSUPPORTED)
            self.assertEqual(tree(source), before)
            with self.assertRaises(ValueError) as error:
                s.stage(source, destination, 'fixture.metrics')
            self.assertEqual(str(error.exception), s.UNSUPPORTED)
            self.assertFalse(destination.parent.exists())
            self.assertEqual(tree(source), before)

    def test_current_complete_usage_note_layout_is_supported(self):
        rendered = s.render(NATIVE, 'fixture.metrics')
        panel = rendered['Panel.qml']
        self.assertIn('function usageNote(item)', panel)
        self.assertIn(component((NATIVE/'Panel.qml').read_text()), panel)
        self.assertEqual(panel.count('objectName: "localTaskRates"'), 1)
        self.assertIn('property var providerRowsByKey: ({})', panel)
        self.assertIn('readonly property var modelData: root.providerRowsByKey[providerKey]', panel)
        self.assertIn('if (p && p.readOnly === true) return', panel)

    def test_record_property_removed_is_refused(self):
        text = (NATIVE/'Panel.qml').read_text()
        witness = component(text)
        self.assertEqual(witness.count('    property var record: null\n'), 1)
        self.assert_refused_before_staging(text.replace(witness, witness.replace('    property var record: null\n', '')))

    def test_component_text_binding_removed_is_refused(self):
        text = (NATIVE/'Panel.qml').read_text()
        witness = component(text)
        self.assertEqual(witness.count('    text: "󰋽"\n'), 1)
        self.assert_refused_before_staging(text.replace(witness, witness.replace('    text: "󰋽"\n', '')))

    def test_component_tooltip_binding_removed_is_refused(self):
        text = (NATIVE/'Panel.qml').read_text()
        witness = component(text)
        self.assertEqual(witness.count('      text: noteMark.message\n'), 1)
        self.assert_refused_before_staging(text.replace(witness, witness.replace('      text: noteMark.message\n', '')))

    def test_record_line_elsewhere_cannot_complete_component_witness(self):
        text = (NATIVE/'Panel.qml').read_text()
        witness = component(text)
        changed = text.replace(witness, witness.replace('    property var record: null\n', ''))
        # Keep the old whole-file line count: the component itself is incomplete.
        changed += '\n// unrelated scope\n    property var record: null\n'
        self.assert_refused_before_staging(changed)

    def test_duplicate_or_partial_modern_witnesses_are_refused(self):
        original = (NATIVE/'Panel.qml').read_text()
        witness = component(original)
        variants = {
            'duplicate-component': original + '\n' + witness,
            'missing-component': original.replace(witness, ''),
            'missing-message': original.replace('    readonly property string message: root.usageNote(record)\n', '', 1),
            'partial-function': original.replace('    var stale = item.limitsStale === true || item.stale === true\n', '', 1),
            'missing-instance': original.replace('        record: section.provider\n', '', 1),
            'mixed-functions': original + '\n  function otherTrouble(item) { return "" }\n',
        }
        for name, changed in variants.items():
            with self.subTest(variant=name):
                self.assert_refused_before_staging(changed)

    def test_complete_legacy_fixture_remains_supported(self):
        with tempfile.TemporaryDirectory(prefix='ul-') as tmp:
            source = fixture.make_source(Path(tmp)/'source', 'limits')
            panel = s.render(source, 'fixture.metrics')['Panel.qml']
            self.assertIn('function otherTrouble(item)', panel)
            self.assertIn('objectName: "localTaskRates"', panel)
            self.assertNotIn('component UsageNote', panel)

    def test_partial_modern_component_in_legacy_layout_is_refused(self):
        witness = component((NATIVE/'Panel.qml').read_text())
        self.assert_refused_before_staging(fixture.PANEL_LIMITS + '\n' + witness, legacy=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
