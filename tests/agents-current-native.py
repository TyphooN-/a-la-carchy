#!/usr/bin/env python3
"""Explicit captured-native compatibility contract; no live inputs or commands."""
from pathlib import Path
import hashlib, importlib.util, os, re, tempfile, unittest, shutil
ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ['ALACARCHY_AGENTS_SOURCE'])
spec = importlib.util.spec_from_file_location('current_native_stager', ROOT / 'extras/local-router-stats/stage_plugin.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def body(text, name):
    p = re.search('^  function ' + re.escape(name) + '\\([^\\n]*\\) \\{\\n', text, re.M)
    assert p, name
    return text[p.start():text.index('\n  }', p.end()) + 4]

class CurrentNativeContract(unittest.TestCase):

    def test_full_current_native_generation_preserves_native_controls(self):
        rendered = m.render(SOURCE, 'fixture.current-native')
        original_main = (SOURCE / 'Main.qml').read_text()
        original_panel = (SOURCE / 'Panel.qml').read_text()
        for name in ('refreshNetwork', 'pair', 'parseNetwork', 'parseClients', 'localOffer', 'spendValue'):
            with self.subTest(surface='Main', function=name):
                self.assertEqual(body(rendered['Main.qml'], name), body(original_main, name))
        for name in ('localActions', 'modelId', 'runLocalAction', 'downloadLocal', 'pairFailure', 'showLocalLogs'):
            with self.subTest(surface='Panel', function=name):
                self.assertEqual(body(rendered['Panel.qml'], name), body(original_panel, name))
        self.assertIn('spend: spendValue(record.spend)', rendered['Main.qml'])
        self.assertNotIn('import "../panels/tailscale" as Tailscale', rendered['Panel.qml'])
        self.assertIn('AgentsTailscaleIcon {', rendered['Panel.qml'])
        self.assertEqual(hashlib.sha256(rendered['AgentsTailscaleIcon.qml'].encode()).hexdigest(), '05142bf1863a671c3b6b39f4a2455d556e2a75b4a373aa1a559c36c918aa8329')
        with tempfile.TemporaryDirectory(prefix='native-') as d:
            target = Path(d) / 'clone'
            m.write(SOURCE, target, rendered)
            self.assertEqual((target / 'AgentsTailscaleIcon.qml').read_text(), rendered['AgentsTailscaleIcon.qml'])
            self.assertEqual((target / 'AgentsReorder.js').read_text(), rendered['AgentsReorder.js'])

    def test_legacy_native_generation_stays_supported(self):
        source = ROOT.parent / 'native-inputs/supported-old/shell/plugins/agents'
        rendered = m.render(source, 'fixture.legacy-supported')
        self.assertEqual(rendered['layout'], 'limits')
        self.assertNotIn('AgentsTailscaleIcon.qml', rendered)

    def test_mixed_legacy_prefix_and_current_body_is_refused(self):
        old = ROOT.parent / 'native-inputs/supported-old/shell'
        current = (SOURCE / 'Panel.qml').read_text()

        def header(text):
            a = text.index('    content: PanelKeyCatcher {')
            b = text.index('      implicitHeight: column.implicitHeight', a) + len('      implicitHeight: column.implicitHeight')
            return text[a:b]

        def heading(text):
            a = text.index('        text: section.model ? (section.model.name || section.model.recipe)')
            return text[a:text.index('\n        HoverHandler', a)]
        for surface in ('header', 'heading', 'both'):
            with self.subTest(surface=surface), tempfile.TemporaryDirectory(prefix='hybrid-') as d:
                shell = Path(d) / 'shell'
                shutil.copytree(old, shell, symlinks=True)
                source = shell / 'plugins/agents'
                text = (source / 'Panel.qml').read_text()
                changed = text
                if surface in ('header', 'both'):
                    changed = changed.replace(header(text), header(current), 1)
                if surface in ('heading', 'both'):
                    changed = changed.replace(heading(text), heading(current), 1)
                self.assertNotEqual(changed, text)
                (source / 'Panel.qml').write_text(changed)
                with self.assertRaises(ValueError):
                    m.render(source, 'fixture.mixed-refused')

    def test_unknown_current_prefix_or_body_is_refused(self):
        for surface in ('prefix', 'body'):
            with self.subTest(surface=surface), tempfile.TemporaryDirectory(prefix='unknown-') as d:
                shell = Path(d) / 'shell'
                shutil.copytree(SOURCE.parent.parent, shell, symlinks=True)
                source = shell / 'plugins/agents'
                panel = source / 'Panel.qml'
                text = panel.read_text()
                panel.write_text('// unknown source\n' + text if surface == 'prefix' else text + '\n// unknown future body\n')
                with self.assertRaises(ValueError):
                    m.render(source, 'fixture.unknown-refused')

    def test_external_icon_missing_or_changed_is_refused(self):
        for state in ('missing', 'changed'):
            with self.subTest(state=state), tempfile.TemporaryDirectory(prefix='icon-') as d:
                shell = Path(d) / 'shell'
                shutil.copytree(SOURCE.parent.parent, shell, symlinks=True)
                icon = shell / 'plugins/panels/tailscale/TailscaleIcon.qml'
                if state == 'missing':
                    icon.unlink()
                else:
                    icon.write_bytes(icon.read_bytes() + b'\n// changed dependency\n')
                with self.assertRaises((ValueError, OSError)):
                    m.render(shell / 'plugins/agents', 'fixture.icon-refused')

    def test_generated_icon_name_collision_is_refused(self):
        with tempfile.TemporaryDirectory(prefix='collision-') as d:
            shell = Path(d) / 'shell'
            shutil.copytree(SOURCE.parent.parent, shell, symlinks=True)
            source = shell / 'plugins/agents'
            (source / 'AgentsTailscaleIcon.qml').write_text('Item {}\n')
            with self.assertRaises(ValueError):
                m.render(source, 'fixture.collision-refused')

    def test_both_external_dependencies_share_entry_and_byte_bounds(self):
        from unittest.mock import patch
        entries = len(list(SOURCE.rglob('*')))
        source_bytes = sum((p.stat().st_size for p in SOURCE.rglob('*') if p.is_file()))
        reorder = (SOURCE.parent / 'panels/elsewhen/Model.js').stat().st_size
        for kind in ('entries', 'bytes'):
            with self.subTest(kind=kind):
                key = 'MAX_SOURCE_ENTRIES' if kind == 'entries' else 'MAX_SOURCE_BYTES'
                value = entries + 1 if kind == 'entries' else source_bytes + reorder
                with patch.object(m, key, value):
                    m.source_files(SOURCE, reserved_entries=1, reserved_bytes=reorder)
                    with self.assertRaisesRegex(ValueError, 'native source tree is too large'):
                        m.render(SOURCE, 'fixture.combined-budget-refused')

    def test_private_observer_source_still_refused(self):
        source = ROOT.parent / 'canonical/shell/plugins/agents'
        with self.assertRaises(ValueError):
            m.render(source, 'fixture.private-negative')
if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CurrentNativeContract)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert result.testsRun == 8, 'Empty or changed compatibility contract'
    raise SystemExit(0 if result.wasSuccessful() else 1)
