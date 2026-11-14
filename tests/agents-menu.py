#!/usr/bin/env python3
"""Drive the real Agents category/renderer/selection in a private terminal."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import shutil
import subprocess
import termios
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
SOURCE = (REPO / 'a-la-carchy.sh').read_text()
spec = importlib.util.spec_from_file_location('providers_test_fixture', REPO / 'tests/agents-providers.py')
assert spec is not None and spec.loader is not None
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def definitions():
    colors = SOURCE.split('# Default packages offered', 1)[0]
    body = SOURCE.split('# HELPER FUNCTIONS FOR TWO-PANEL UI', 1)[1].split('# Main selection loop', 1)[0]
    arrays = []
    for name in ('CATEGORIES', 'AGENTS_ITEMS'):
        found = re.findall(r'^declare -a ' + name + r'=\(\n.*?^\)', SOURCE, re.M | re.S)
        arrays.append(found[0] if found else 'declare -a ' + name + '=()')
    opener = ''
    if '# Native Agents provider menu' in SOURCE:
        opener = '# Native Agents provider menu' + SOURCE.split('# Native Agents provider menu', 1)[1].split('# Bounded security menus', 1)[0]
    return colors + '\n'.join(arrays) + '\n' + opener + '\n' + body


class Menus(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.Providers()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.home, self.env = self.fx.home, dict(self.fx.env)
        native = self.fx.root / 'shell/plugins/agents'
        native.parent.mkdir(parents=True)
        shutil.copytree(self.fx.source, native)
        self.env.update(TERM='xterm-256color', ALC_AGENTS_CLONE_DIR=str(REPO / 'extras/agents-clone'),
                        OMARCHY_DIR=str(self.fx.root), SHELL_JSON=str(self.fx.shell))
        self.prefix = definitions() + '\nclear(){ :; }; stty(){ :; }; tput(){ case "$1" in cols) echo 120;; lines) echo 32;; *) :;; esac; }\n'
        self.prefix += 'declare -a SUMMARY_LOG=(); declare -A TOGGLE_SELECTIONS=(); CONFIRM_ALL=true\n'
        self.prefix += 'CATEGORY_CURSOR=26; ITEM_CURSOR=0; CURRENT_PANEL=1; ITEM_SCROLL_OFFSET=0; CAT_SCROLL_OFFSET=14\n'

    def menu(self, responses):
        gate = subprocess.run(['bash', '-c', self.prefix + '\nget_category_items 26\n'], env=self.env,
                              capture_output=True, text=True, timeout=5)
        self.assertEqual(gate.stdout.strip(), 'AGENTS', gate.stdout + gate.stderr)
        script = self.home / 'surface.sh'
        script.write_text(self.prefix + '\ndraw_interface\ntoggle_current_item\nrc=$?\ndeclare -p SUMMARY_LOG\nexit "$rc"\n')
        master, slave = pty.openpty()
        def setup():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        proc = subprocess.Popen(['bash', str(script)], env=self.env, cwd=self.home, stdin=slave,
                                stdout=slave, stderr=slave, preexec_fn=setup)
        os.close(slave)
        data = b''
        pending = list(responses)
        cursor = 0
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    data += chunk
                if pending:
                    prompt, answer = pending[0]
                    if data.find(prompt.encode(), cursor) >= 0:
                        os.write(master, answer.encode() + b'\n')
                        cursor = len(data)
                        pending.pop(0)
                if proc.poll() is not None:
                    while select.select([master], [], [], 0)[0]:
                        try:
                            data += os.read(master, 65536)
                        except OSError:
                            break
                    break
            else:
                proc.kill()
                self.fail('Private menu timed out: ' + data.decode(errors='replace'))
            proc.wait(timeout=5)
        finally:
            os.close(master)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        output = data.decode(errors='replace')
        self.assertEqual(proc.returncode, 0, output)
        self.assertFalse(pending, output)
        self.assertIn('own confirmation', output)
        return output

    def test_renderer_selection_disable_enable_and_cancel(self):
        before = self.fx.shell.read_bytes()
        self.menu([('Provider number', '0')])
        self.assertEqual(self.fx.shell.read_bytes(), before)
        out = self.menu([('Provider number', '2'), ('Action:', '2'), ('Continue?', 'no')])
        self.assertEqual(self.fx.shell.read_bytes(), before)
        self.assertNotIn('✓', out)
        self.menu([('Provider number', '2'), ('Action:', '2'), ('Continue?', 'yes'), ('Press Enter', '')])
        self.assertFalse(json.loads(self.fx.shell.read_text())['bar']['layout']['right'][0]['providers']['fireworks']['enabled'])
        self.menu([('Provider number', '2'), ('Action:', '1'), ('Continue?', 'yes'), ('Press Enter', '')])
        self.assertTrue(json.loads(self.fx.shell.read_text())['bar']['layout']['right'][0]['providers']['fireworks']['enabled'])
        self.assertEqual(len(list(self.home.glob('shell.json.backup.agents-*'))), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
