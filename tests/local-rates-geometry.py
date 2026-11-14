#!/usr/bin/env python3
"""Generated Local Text geometry in offscreen Qt; synthetic data, no desktop IPC."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('local_geometry_rates', ROOT / 'tests/local-recent-metrics.py')
assert SPEC is not None and SPEC.loader is not None
RATES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RATES)


def geometry(text, width):
    panel = RATES.stage.render(RATES.SOURCE, 'fixture.agents')['Panel.qml']
    match = re.search(r'    Text \{\n      objectName: "localTaskRates"\n.*?\n    \}', panel, re.S)
    assert match is not None
    body = match.group().replace('objectName: "localTaskRates"', 'id: rateText; objectName: "localTaskRates"')
    qml = '''import QtQuick
import Quickshell
ShellRoot {
  id: root
  readonly property color dim: "#aaaaaa"
  readonly property string fontFamily: "monospace"
  readonly property var section: ({provider:{providerId:"local"}})
  function localLiveText(p) { return TEXT_VALUE }
  Item {
    width: TEST_WIDTH; height: 300
TEXT_BODY
  }
  Timer {
    interval: 120; running: true
    onTriggered: {
      console.log("LOCAL_GEOMETRY " + JSON.stringify({text:rateText.text,
        lines:rateText.lineCount, width:rateText.width, contentWidth:rateText.contentWidth,
        truncated:rateText.truncated, pixelSize:rateText.fontInfo.pixelSize}));
      Qt.quit();
    }
  }
}
'''.replace('TEXT_VALUE', json.dumps(text)).replace('TEST_WIDTH', str(width)).replace('TEXT_BODY', body)
    with tempfile.TemporaryDirectory(prefix='lg-', dir=os.environ['TMPDIR']) as folder:
        path = Path(folder)
        (path / 'runtime').mkdir(mode=0o700)
        (path / 'shell.qml').write_text(qml)
        (path / 'qmldir').write_text('singleton Style 1.0 Style.qml\n')
        (path / 'Style.qml').write_text('pragma Singleton\nimport QtQuick\nQtObject { readonly property var font: ({caption:12}) }\n')
        env = os.environ.copy()
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'HYPRLAND_INSTANCE_SIGNATURE',
                    'QS_CONFIG_PATH', 'QS_CONFIG_NAME', 'QS_MANIFEST'):
            env.pop(key, None)
        env.update(HOME=folder, XDG_RUNTIME_DIR=str(path / 'runtime'), XDG_CONFIG_HOME=str(path / 'config'),
                   XDG_STATE_HOME=str(path / 'state'), XDG_CACHE_HOME=str(path / 'cache'),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
        result = subprocess.run(['/usr/bin/qs', '-p', folder, '--no-color'], capture_output=True,
                                text=True, timeout=10, env=env)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    payloads = [json.loads(line.split('LOCAL_GEOMETRY ', 1)[1]) for line in output.splitlines() if 'LOCAL_GEOMETRY ' in line]
    assert len(payloads) == 1, output
    return payloads[0]


class LocalGeometry(unittest.TestCase):
    def test_measured_and_retained_rates_do_not_wrap_or_lose_values(self):
        texts = RATES.sequence([{'now': 100, 'record': RATES.record(100, 1234.5, 32.25)},
                                {'now': 105, 'record': RATES.record(105, None, None)}])
        for width in (333, 260):
            for text in texts:
                with self.subTest(width=width, text=text):
                    result = geometry(text, width)
                    print(json.dumps(result), flush=True)
                    self.assertEqual(result['text'], text)
                    self.assertIn('prompt 1234.5 tok/s · generation 32.25 tok/s', result['text'])
                    self.assertEqual(result['lines'], len(text.splitlines()))
                    self.assertFalse(result['truncated'])
                    self.assertLessEqual(result['contentWidth'], width + 0.5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
