#!/usr/bin/env python3
"""Read-only kernel inspection never evaluates shell or executes package scripts."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zlib

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "extras/privacy"))


class Kernel(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("kernel_inspect", REPO / "extras/custom-kernel/control.py")
        self.mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.mod)
        self.tmp = tempfile.TemporaryDirectory(prefix="alc-kernel-", dir=os.environ["TMPDIR"])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.repo = self.root / "linux-tkg"; self.repo.mkdir()
        self.sentinel = self.root / "executed"
        self.run_git("init"); self.run_git("config", "user.name", "fixture"); self.run_git("config", "user.email", "fixture@example.invalid")
        (self.repo / "README.md").write_text("# Linux-tkg\n### Arch & derivatives\nmakepkg -si\n_EXT_CONFIG_PATH\n")
        (self.repo / "PKGBUILD").write_text(f"touch {self.sentinel}\n")
        (self.repo / "customization.cfg").write_text(f'_cpusched="bore"\n_compiler="llvm"\n_version="$(touch {self.sentinel})"\n_configfile=""\n')
        # Unborn fixture repository: no staging or commits anywhere.
        with (self.repo / "customization.cfg").open("a") as f: f.write("_lto_mode=full\n")
        (self.repo / "untracked.patch").write_text("keep user edits\n")

    def run_git(self, *args):
        # Fixture construction only, hermetic from the user's own Git configuration.
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        p = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)

    def born(self):
        """Valid born repository for real Git; objects are written as data, `git commit` never runs."""
        (self.repo / "tracked.txt").write_text("initial\n")
        self.run_git("add", "tracked.txt")  # disposable fixture index only
        def obj(kind, body):
            raw = kind + b" " + str(len(body)).encode() + b"\0" + body
            oid = hashlib.sha1(raw).hexdigest()
            d = self.repo / ".git/objects" / oid[:2]; d.mkdir(exist_ok=True)
            (d / oid[2:]).write_bytes(zlib.compress(raw))
            return oid
        blob = hashlib.sha1(b"blob 8\0initial\n").hexdigest()
        tree = obj(b"tree", b"100644 tracked.txt\0" + bytes.fromhex(blob))
        who = b"fixture <fixture@example.invalid> 0 +0000"
        commit = obj(b"commit", b"tree " + tree.encode() + b"\nauthor " + who + b"\ncommitter " + who + b"\n\nfixture\n")
        ref = (self.repo / ".git/HEAD").read_text().strip().removeprefix("ref: ")
        (self.repo / ".git" / ref).write_text(commit + "\n")
        return commit, ref.removeprefix("refs/heads/")

    def snapshot(self):
        # Every byte including .git: inspection must not refresh or rewrite anything.
        return {str(p.relative_to(self.repo)): p.read_bytes() for p in self.repo.rglob("*") if p.is_file() and not p.is_symlink()}

    def test_checkout_hints_injection_and_dirty_preservation(self):
        before = {str(p.relative_to(self.repo)): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        with patch.dict(os.environ, {"_compiler": "$(touch never)"}):
            info = self.mod.inspect_checkout(str(self.repo))
        self.assertEqual(info["hints"]["_cpusched"], "bore")
        self.assertEqual(info["hints"]["_version"], "unknown/dynamic")
        self.assertEqual(info["head"], "unknown/unborn")
        self.assertTrue(info["tracked_dirty"].startswith("unknown/not computed"), info["tracked_dirty"])
        self.assertIn("unknown", info["effective_config"])
        self.assertFalse(self.sentinel.exists())
        after = {str(p.relative_to(self.repo)): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_hostile_git_configuration_is_never_executed(self):
        # SEC-01: born HEAD + checkout-configured clean filter + changed tracked file.
        commit, branch = self.born()
        probe = self.root / "probe.py"
        probe.write_text("import pathlib,sys\npathlib.Path(" + repr(str(self.sentinel)) + ").write_text('checkout-configured command ran')\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n")
        hostile = "/usr/bin/python3 " + str(probe)
        self.run_git("config", "filter.review.clean", hostile)
        self.run_git("config", "filter.review.required", "true")
        self.run_git("config", "core.fsmonitor", hostile)
        (self.repo / ".gitattributes").write_text("tracked.txt filter=review\n")
        (self.repo / "tracked.txt").write_text("changed\n")
        self.assertFalse(self.sentinel.exists())
        before = self.snapshot()
        info = self.mod.inspect_checkout(str(self.repo))
        self.mod.packages(info); self.mod.preview(info)
        self.assertFalse(self.sentinel.exists(), "read-only inspection executed a checkout-configured Git command")
        self.assertEqual((info["head"], info["branch"]), (commit, branch))
        self.assertTrue(info["tracked_dirty"].startswith("unknown/not computed"), info["tracked_dirty"])
        self.assertEqual(before, self.snapshot())

    def test_inspection_spawns_no_process(self):
        # The execution surface is removed, not one known filter: nothing may be spawned at all.
        self.born()
        def refuse(*args, **kw):
            raise AssertionError("read-only checkout inspection spawned a process: " + repr(args))
        with patch.object(subprocess, "Popen", refuse), patch.object(subprocess, "run", refuse), patch.object(os, "system", refuse), patch.object(os, "posix_spawn", refuse):
            info = self.mod.inspect_checkout(str(self.repo))
            self.mod.packages(info); self.mod.preview(info)
        self.assertFalse(self.sentinel.exists())

    def test_head_and_branch_are_read_as_data(self):
        git = self.repo / ".git"
        branch = (git / "HEAD").read_text().strip().removeprefix("ref: refs/heads/")
        inspect = lambda: self.mod.inspect_checkout(str(self.repo))
        self.assertEqual((inspect()["head"], inspect()["branch"]), ("unknown/unborn", branch))
        (git / "packed-refs").write_text("# pack-refs with: peeled fully-peeled sorted \n" + "2" * 40 + " refs/heads/other\n" + "1" * 40 + " refs/heads/" + branch + "\n^" + "3" * 40 + "\n")
        self.assertEqual(inspect()["head"], "1" * 40)
        loose = git / "refs/heads" / branch
        loose.write_text("4" * 40 + "\n")  # a loose ref wins over the packed one
        self.assertEqual((inspect()["head"], inspect()["branch"]), ("4" * 40, branch))
        loose.write_text("ref: refs/heads/other\n")  # nested symbolic refs are not guessed
        self.assertTrue(inspect()["head"].startswith("unknown/unsupported"))
        loose.unlink(); loose.symlink_to(self.repo / "PKGBUILD")  # never followed
        self.assertTrue(inspect()["head"].startswith("unknown/unsupported"))
        loose.unlink()
        (git / "HEAD").write_text("5" * 64 + "\n")
        self.assertEqual((inspect()["head"], inspect()["branch"]), ("5" * 64, "detached/unknown"))
        for hostile in ("ref: refs/heads/../../PKGBUILD\n", "ref: refs/heads/.invalid\n", "ref: refs/tags/v1\n", "ref: refs/heads/a\x1b[31m\n", "garbage\n"):
            (git / "HEAD").write_text(hostile)
            info = inspect()
            self.assertTrue(info["head"].startswith("unknown/unsupported"), hostile)
            self.assertEqual(info["branch"], "detached/unknown")
        (git / "HEAD").unlink()
        with self.assertRaises(self.mod.Refused): inspect()

    def test_requested_missing_path_is_not_repaired(self):
        with self.assertRaises((self.mod.Refused, OSError)): self.mod.inspect_checkout(str(self.root / "absent"))
        with self.assertRaises(self.mod.Refused): self.mod.inspect_checkout(str(self.repo / ".." / "linux-tkg"))
        self.assertEqual(self.mod.DEFAULT_CHECKOUT, "/home/git/linux-tkg")

    def test_symlink_and_permission_refusal(self):
        link = self.root / "link"; link.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises(self.mod.Refused): self.mod.inspect_checkout(str(link))
        cfg = self.repo / "customization.cfg"; cfg.unlink(); cfg.symlink_to(self.repo / "PKGBUILD")
        with self.assertRaises(self.mod.Refused): self.mod.inspect_checkout(str(self.repo))
        cfg.unlink(); cfg.write_text("_cpusched=bore\n"); cfg.chmod(0)
        with self.assertRaises((self.mod.Refused, OSError)): self.mod.inspect_checkout(str(self.repo))
        cfg.chmod(0o600)

    def test_preview_build_only_and_quote_path(self):
        new = self.root / "kernel 'quoted'"; self.repo.rename(new); self.repo = new
        preview = self.mod.preview(self.mod.inspect_checkout(str(new)))
        self.assertIn("makepkg", preview)
        self.assertNotIn("-si", preview)
        self.assertIn("NOT EXECUTED", preview)
        self.assertFalse(self.sentinel.exists())

    def test_package_metadata_only_and_malicious_member(self):
        p = self.repo / "linux-fixture.pkg.tar.gz"
        with tarfile.open(p, "w:gz") as t:
            payload = b"pkgname = linux-fixture\npkgver = 7.3-fixture\narch = x86_64\n"
            m = tarfile.TarInfo(".PKGINFO"); m.size = len(payload); t.addfile(m, io.BytesIO(payload))
            m = tarfile.TarInfo(".INSTALL"); m.size = 20; t.addfile(m, io.BytesIO(b"touch SHOULD_NOT_RUN"))
        result = self.mod.package_metadata(p)
        self.assertEqual(result["pkgname"], "linux-fixture")
        self.assertFalse(self.sentinel.exists())
        evil = self.repo / "evil.pkg.tar.gz"
        with tarfile.open(evil, "w:gz") as t:
            m = tarfile.TarInfo(".PKGINFO"); m.type = tarfile.SYMTYPE; m.linkname = "/etc/passwd"; t.addfile(m)
        with self.assertRaises(self.mod.Refused): self.mod.package_metadata(evil)
        outside = self.repo / "link.pkg.tar.gz"; outside.symlink_to(p)
        with self.assertRaises((self.mod.Refused, OSError)): self.mod.package_metadata(outside)


if __name__ == "__main__": unittest.main(verbosity=2)
