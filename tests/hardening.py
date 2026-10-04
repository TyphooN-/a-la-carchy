#!/usr/bin/env python3
"""No privileged commands: test real journals/configs with a fixture sysctl driver."""
import errno
import fcntl
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "extras/privacy"))


class Hardening(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("hardening", REPO / "extras/hardening/control.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.tmp = tempfile.TemporaryDirectory(prefix="alc-hardening-", dir=os.environ["TMPDIR"])
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        home = root / "home"; home.mkdir(mode=0o700)
        self.etc = home / "etc/sysctl.d"; self.etc.mkdir(parents=True)
        self.values = {"kernel.kptr_restrict": "0", "kernel.dmesg_restrict": "0", "kernel.yama.ptrace_scope": "0", "kernel.unprivileged_bpf_disabled": "0"}
        self.calls, self.fault = [], None
        self.env = patch.dict(os.environ, {"HOME": str(home), "XDG_STATE_HOME": str(home / ".local/state"), "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"), "XDG_DATA_HOME": str(home / ".local/share")}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        for key, value in (("CONFIG_DIR", self.etc), ("CONFIG_SEARCH", [self.etc]), ("ROOT_OWNER", os.getuid()), ("command", self.command)):
            p = patch.object(self.mod, key, value); p.start(); self.addCleanup(p.stop)
        self.h = self.mod.Home(); self.addCleanup(self.h.close)

    def command(self, *args, stdin=None):
        self.calls.append(args)
        if self.fault and self.fault(args): raise self.mod.Refused("fixture permission/command failure")
        if args[0] == "sysctl" and args[1] == "-n":
            if args[2] not in self.values: raise self.mod.Refused("unsupported key")
            return self.values[args[2]]
        if args[:3] == ("sudo", "--", "/usr/bin/install"):
            if stdin is None:
                shutil.copyfile(args[-2], args[-1])
            else:
                self.assertEqual(args[-2], "/proc/self/fd/0")
                Path(args[-1]).write_bytes(os.pread(stdin, 4096, 0))
            Path(args[-1]).chmod(0o644); return ""
        if args[:3] == ("sudo", "--", "/usr/bin/rm"):
            Path(args[-1]).unlink(); return ""
        if args[:3] == ("sudo", "--", "/usr/bin/sysctl"):
            self.assertEqual(args[3], "-w")
            k, v = args[4].split("=", 1); self.assertIn(k, self.values); self.values[k] = v; return ""
        raise AssertionError(args)

    def change(self, name, action):
        self.mod.change(self.h, name, action)

    def test_all_roundtrips_preserve_original(self):
        for name, spec in self.mod.CONTROLS.items():
            self.change(name, "enable"); self.change(name, "enable")
            self.assertEqual(self.values[spec[0]], spec[1])
            self.assertEqual(self.mod.config_path(name).read_text(), self.mod.render(name))
            self.change(name, "restore"); self.change(name, "restore")
            self.assertEqual(self.values[spec[0]], "0")
            self.assertFalse(self.mod.config_path(name).exists())
        self.assertTrue(all(a[:3] in [("sudo", "--", "/usr/bin/install"), ("sudo", "--", "/usr/bin/rm"), ("sudo", "--", "/usr/bin/sysctl")] for a in self.calls if a[0] == "sudo"))

    def test_stricter_values_never_downgraded(self):
        self.values["kernel.yama.ptrace_scope"] = "3"
        self.values["kernel.unprivileged_bpf_disabled"] = "1"
        self.change("ptrace", "enable"); self.change("bpf", "enable")
        self.assertFalse(any(c[0] == "sudo" for c in self.calls))
        self.assertEqual(self.values["kernel.yama.ptrace_scope"], "3")
        self.assertEqual(self.values["kernel.unprivileged_bpf_disabled"], "1")

    def test_missing_feature_and_config_conflicts(self):
        del self.values["kernel.kptr_restrict"]
        with self.assertRaises(self.mod.Refused): self.change("kptr", "enable")
        self.values["kernel.kptr_restrict"] = "0"
        (self.etc / "custom.conf").write_text("kernel.kptr_restrict = 1\n")
        with self.assertRaises(self.mod.Refused): self.change("kptr", "enable")
        self.assertFalse(any(c[0] == "sudo" for c in self.calls))

    def test_failure_honesty_and_partial_restore(self):
        self.fault = lambda a: a[0] == "sudo"
        with self.assertRaises(self.mod.Refused): self.change("kptr", "enable")
        self.assertEqual(self.values["kernel.kptr_restrict"], "0")
        self.fault = None; self.change("kptr", "restore")
        self.fault = lambda a: a[:3] == ("sudo", "--", "/usr/bin/sysctl")
        with self.assertRaises(self.mod.Refused): self.change("kptr", "enable")
        self.assertTrue(self.mod.config_path("kptr").exists())
        self.fault = None; self.change("kptr", "restore")
        self.assertFalse(self.mod.config_path("kptr").exists())

    def test_newer_edits_and_symlinks_refused(self):
        self.change("kptr", "enable")
        self.values["kernel.kptr_restrict"] = "1"
        with self.assertRaises(self.mod.Refused): self.change("kptr", "restore")
        self.values["kernel.kptr_restrict"] = "2"
        f = self.mod.config_path("kptr"); f.write_text("custom newer edit\n")
        with self.assertRaises(self.mod.Refused): self.change("kptr", "restore")
        f.unlink(); f.symlink_to(self.etc / "sentinel")
        with self.assertRaises((self.mod.Refused, OSError)): self.change("kptr", "restore")
        self.assertEqual(self.values["kernel.kptr_restrict"], "2")

    def test_source_swap_never_persists_unapproved_bytes(self):
        # SEC-02: delay boundary then real native copying, without sudo/root.
        approved = self.mod.render("kptr").encode()
        foreign = self.etc / "synthetic-unapproved-input"
        foreign.write_bytes(approved + b"review.synthetic_unapproved_key = fixture\n")
        observed, blocked = [], []
        original = self.mod.command
        def delayed(*args, stdin=None):
            if args[:3] != ("sudo", "--", "/usr/bin/install"):
                return original(*args, stdin=stdin)
            if stdin is None:
                source = Path(args[-2]); source.unlink(); source.symlink_to(foreign)
            else:
                # Same-UID attempts, including reopening the descriptor, must
                # fail in the kernel before the consumer can copy any bytes.
                seals = fcntl.fcntl(stdin, fcntl.F_GET_SEALS)
                required = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
                self.assertEqual(seals & required, required)
                for operation in (lambda: os.pwrite(stdin, b"changed", 0), lambda: os.ftruncate(stdin, 0)):
                    with self.assertRaises(OSError) as failure: operation()
                    self.assertEqual(failure.exception.errno, errno.EPERM); blocked.append(True)
                reopened = os.open("/proc/self/fd/" + str(stdin), os.O_RDWR)
                try:
                    with self.assertRaises(OSError) as failure: os.pwrite(reopened, b"changed", 0)
                    self.assertEqual(failure.exception.errno, errno.EPERM); blocked.append(True)
                finally: os.close(reopened)
            q = subprocess.run(["/usr/bin/install", "--mode=0644", "-T", "--", args[-2], args[-1]],
                               stdin=stdin, capture_output=True, text=True)
            self.assertEqual(q.returncode, 0, q.stderr)
            observed.append(Path(args[-1]).read_bytes())
            return ""
        try:
            with patch.object(self.mod, "command", delayed): self.change("kptr", "enable")
        except (self.mod.Refused, OSError):
            pass  # A post-write refusal does not satisfy the persistence boundary.
        self.assertEqual(observed, [approved], "Native install persisted unapproved input before readback/refusal")
        self.assertEqual(blocked, [True, True, True])
        self.assertEqual(self.mod.config_path("kptr").read_bytes(), approved)
        self.assertFalse((self.h.path / self.h.rel("state", "a-la-carchy/hardening/payload-kptr.conf")).exists())

    def test_unsupported_sealed_input_fails_before_privilege(self):
        for target, attr, error in ((os, "memfd_create", OSError(errno.ENOSYS, "fixture")),
                                    (fcntl, "fcntl", OSError(errno.EINVAL, "fixture"))):
            with self.subTest(attr=attr), patch.object(target, attr, side_effect=error, create=True):
                with self.assertRaises(self.mod.Refused): self.mod.root_write(self.h, "kptr")
            self.assertFalse(any(c[0] == "sudo" for c in self.calls))
            self.assertFalse(self.mod.config_path("kptr").exists())

    def test_input_changed_before_sealing_fails_before_privilege(self):
        real_write = os.write
        def interleave(fd, data):
            n = real_write(fd, data)
            real_write(fd, b"review.synthetic_unapproved_key = fixture\n")
            return n
        with patch.object(os, "write", interleave):
            with self.assertRaises(self.mod.Refused): self.mod.root_write(self.h, "kptr")
        self.assertFalse(any(c[0] == "sudo" for c in self.calls))
        self.assertFalse(self.mod.config_path("kptr").exists())

    def test_noop_privileged_command_fails_readback(self):
        old = self.mod.command
        def no_install(*args, **kwargs):
            if args[:3] == ("sudo", "--", "/usr/bin/install"): return ""
            return old(*args, **kwargs)
        with patch.object(self.mod, "command", no_install):
            with self.assertRaises(self.mod.Refused): self.change("dmesg", "enable")


if __name__ == "__main__": unittest.main(verbosity=2)
