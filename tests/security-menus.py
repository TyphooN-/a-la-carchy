#!/usr/bin/env python3
"""Real renderer/selection/dialogs through an isolated PTY; never launch top-level TUI."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import subprocess
import termios
import time
import unittest

REPO = Path(__file__).resolve().parents[1]
SOURCE = (REPO / "a-la-carchy.sh").read_text()
spec = importlib.util.spec_from_file_location("privacy_fixture", REPO / "tests/privacy.py")
fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)


def definitions():
    # Inspected function-only span, not the installed inventory or main loop.
    assert SOURCE.count("# HELPER FUNCTIONS FOR TWO-PANEL UI") == 1
    body = SOURCE.split("# HELPER FUNCTIONS FOR TWO-PANEL UI", 1)[1].split("# Main selection loop", 1)[0]
    colors = SOURCE.split("# Default packages offered", 1)[0]
    arrays = []
    for name in ("CATEGORIES", "PRIVACY_ITEMS", "HARDENING_ITEMS", "CUSTOM_KERNEL_ITEMS"):
        matches = re.findall(r"^declare -a " + name + r"=\(\n.*?^\)", SOURCE, re.M | re.S)
        assert len(matches) == 1, name
        arrays += matches
    confirm = SOURCE.split("confirm_continue() {", 1)[1].split("\nbackup_file()", 1)[0]
    adapter = SOURCE.split("open_bounded_dialog() {", 1)[1].split("# TWO-PANEL TUI DATA STRUCTURES", 1)[0]
    return colors + "\n".join(arrays) + "\nconfirm_continue() {" + confirm + "\nopen_bounded_dialog() {" + adapter + body


SYSCTL = '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
p=Path(os.environ['HOME'])/'mock.json'; s=json.loads(p.read_text()); a=sys.argv[1:]
s['calls'].append(['sysctl']+a); p.write_text(json.dumps(s))
if a[0]=='-n' and a[1] in s['sysctl']: print(s['sysctl'][a[1]])
else: sys.exit(97)
'''
SUDO = '''#!/usr/bin/env python3
import json,os,sys,shutil
from pathlib import Path
home=Path(os.environ['HOME']); p=home/'mock.json'; s=json.loads(p.read_text()); a=sys.argv[1:]
s['calls'].append(['sudo']+a); p.write_text(json.dumps(s))
if s.get('sudo_fail'): sys.exit(7)
if a[:2]==['--','/usr/bin/install']:
 target=Path(a[-1]); assert target.is_relative_to(home/'etc/sysctl.d'); shutil.copyfile(a[-2],target); target.chmod(0o644)
elif a[:3]==['--','/usr/bin/rm','--']:
 target=Path(a[-1]); assert target.is_relative_to(home/'etc/sysctl.d'); target.unlink()
elif a[:3]==['--','/usr/bin/sysctl','-w']:
 key,v=a[3].split('='); assert key in s['sysctl']; s['sysctl'][key]=v; p.write_text(json.dumps(s))
else: sys.exit(97)
'''


class Menus(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.Privacy(); self.fx.setUp(); self.addCleanup(self.fx.doCleanups)
        self.home, self.env = self.fx.home, self.fx.env
        self.env.update(TERM="xterm-256color", ALC_EXTRAS_DIR=str(REPO / "extras"))
        self.fx.change(sysctl={"kernel.kptr_restrict": "0", "kernel.dmesg_restrict": "0", "kernel.yama.ptrace_scope": "0", "kernel.unprivileged_bpf_disabled": "0"})
        for name, code in (("sysctl", SYSCTL), ("sudo", SUDO)):
            p = self.fx.bin / name; p.write_text(code); p.chmod(0o700)
        etc = self.home / "etc/sysctl.d"; etc.mkdir(parents=True)
        driver = self.home / "hardening-driver.py"
        driver.write_text("import importlib.util,os,sys\nfrom pathlib import Path\np=" + repr(str(REPO / "extras/hardening/control.py")) + "\ns=importlib.util.spec_from_file_location('hardening_fixture',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)\nm.CONFIG_DIR=Path(os.environ['HOME'])/'etc/sysctl.d'; m.CONFIG_SEARCH=[m.CONFIG_DIR]; m.ROOT_OWNER=os.getuid()\nsys.exit(m.main())\n")
        launcher = self.fx.bin / "python3"
        launcher.write_text("#!/bin/bash\nif [[ \"${1:-}\" == " + shlex.quote(str(REPO / "extras/hardening/control.py")) + " ]]; then shift; exec /usr/bin/python3 \"$HOME/hardening-driver.py\" \"$@\"; fi\nexec /usr/bin/python3 \"$@\"\n")
        launcher.chmod(0o700)
        self.repo = self.home / "linux-tkg"; self.repo.mkdir()
        subprocess.run(["git", "init", str(self.repo)], env=self.env, capture_output=True, check=True)
        (self.repo / "README.md").write_text("# Linux-tkg\nArch & derivatives\nmakepkg -si\n_EXT_CONFIG_PATH\n")
        (self.repo / "PKGBUILD").write_text("touch SHOULD_NEVER_EXECUTE\n")
        (self.repo / "customization.cfg").write_text('_cpusched=bore\n_compiler="$(touch SHOULD_NEVER_EXECUTE)"\n')

    def menu(self, category, item, responses, expected_exit=0, path=None):
        script = self.home / "surface.sh"
        script.write_text(definitions() + "\nclear(){ :; }; stty(){ :; }; tput(){ case \"$1\" in cols) echo 120;; lines) echo 32;; *) :;; esac; }\n"
                          "declare -a SUMMARY_LOG=(); CONFIRM_ALL=true\nCATEGORY_CURSOR=" + str(category) + "; ITEM_CURSOR=" + str(item) + "; CURRENT_PANEL=1; ITEM_SCROLL_OFFSET=0; CAT_SCROLL_OFFSET=10\n"
                          + ("ALC_LINUX_TKG_PATH=" + shlex.quote(str(path)) + "\n" if path else "")
                          + "draw_interface\ntoggle_current_item\nrc=$?\ndeclare -p SUMMARY_LOG\nexit \"$rc\"\n")
        master, slave = pty.openpty()
        def setup():
            os.setsid(); fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        proc = subprocess.Popen(["bash", str(script)], env=self.env, cwd=self.home, stdin=slave, stdout=slave, stderr=slave, preexec_fn=setup)
        os.close(slave)
        data = b""; pending = list(responses); cursor = 0; deadline = time.monotonic() + 45
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try: chunk = os.read(master, 65536)
                    except OSError: break
                    if not chunk: break
                    data += chunk
                if pending:
                    prompt, answer = pending[0]
                    index = data.find(prompt.encode(), cursor)
                    if index >= 0:
                        os.write(master, answer.encode() + b"\n"); cursor = len(data); pending.pop(0)
                if proc.poll() is not None:
                    # Drain remaining buffered terminal output without waiting.
                    while select.select([master], [], [], 0)[0]:
                        try: data += os.read(master, 65536)
                        except OSError: break
                    break
            else:
                proc.kill(); self.fail("Menu timed out: " + data.decode(errors="replace"))
            proc.wait(timeout=5)
        finally:
            os.close(master)
            if proc.poll() is None: proc.kill(); proc.wait()
        output = data.decode(errors="replace")
        self.assertEqual(proc.returncode, expected_exit, output)
        self.assertFalse(pending, output)
        self.assertIn("own confirmation", output)
        return output

    def test_privacy_stop_restore_and_explicit_purge(self):
        out = self.menu(23, 0, [("Choice:", "1"), ("Continue?", "yes"), ("Press Enter", "")])
        self.assertIn("Privacy & OPSEC", out); self.assertIn("readback verified", out)
        self.assertEqual(self.fx.state()["values"]["remember-recent-files"], "false")
        self.menu(23, 0, [("Choice:", "2"), ("Continue?", "yes"), ("Press Enter", "")])
        self.assertEqual(self.fx.state()["values"]["remember-recent-files"], "true")
        f = self.home / ".local/share/recently-used.xbel"; f.parent.mkdir(parents=True, exist_ok=True); f.write_text("synthetic history")
        self.menu(23, 3, [("Continue?", "yes"), ("Type PURGE", "cancel")])
        self.assertTrue(f.exists())
        out = self.menu(23, 3, [("Continue?", "yes"), ("Type PURGE", "PURGE"), ("Press Enter", "")])
        self.assertIn("not secure erasure", out); self.assertFalse(f.exists())

    def test_hardening_audit_enable_restore_and_privilege_refusal(self):
        out = self.menu(24, 0, [("Press Enter", "")]); self.assertIn("bounded", out)
        self.assertFalse(any(c[0] == "sudo" for c in self.fx.state()["calls"]))
        out = self.menu(24, 1, [("Choice:", "1"), ("Continue?", "yes"), ("Press Enter", "")])
        self.assertIn("OS Hardening", out); self.assertEqual(self.fx.state()["sysctl"]["kernel.kptr_restrict"], "2")
        self.menu(24, 1, [("Choice:", "2"), ("Continue?", "yes"), ("Press Enter", "")])
        self.assertEqual(self.fx.state()["sysctl"]["kernel.kptr_restrict"], "0")
        self.fx.change(sudo_fail=True)
        out = self.menu(24, 2, [("Choice:", "1"), ("Continue?", "yes"), ("Press Enter", "")], expected_exit=1)
        self.assertIn("failed/refused", out)
        self.assertEqual(self.fx.state()["sysctl"]["kernel.dmesg_restrict"], "0")

    def test_custom_kernel_unavailable_explicit_path_preview_no_execution(self):
        out = self.menu(25, 1, [("Press Enter", "")], expected_exit=1)
        self.assertIn("/home/git/linux-tkg", out); self.assertIn("unavailable/refused", out)
        out = self.menu(25, 0, [("Absolute checkout path", str(self.repo)), ("Press Enter", "")])
        self.assertIn("unknown/dynamic", out); self.assertIn("Custom Kernel", out)
        out = self.menu(25, 3, [("Press Enter", "")], path=self.repo)
        self.assertIn("NOT EXECUTED", out); self.assertIn("makepkg", out)
        self.assertFalse((self.home / "SHOULD_NEVER_EXECUTE").exists())
        self.assertFalse(any(c[0] in {"sudo", "systemctl", "hyprctl", "omarchy"} for c in self.fx.state()["calls"]))


if __name__ == "__main__": unittest.main(verbosity=2)
