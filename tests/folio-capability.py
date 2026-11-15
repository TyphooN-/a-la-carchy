#!/usr/bin/env python3
"""Synthetic root-package metadata tests; no binary is executed."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "extras/privacy"))
ROOT = Path(os.environ["TMPDIR"]).resolve()


class RootMetadata:
    def __init__(self, value, uid):
        self.value, self.st_uid = value, uid

    def __getattr__(self, name):
        return getattr(self.value, name)


class FolioCapability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="alc-folio-package-", dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.pkg = self.base / "package"
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        self.binary = self.pkg / "bin/folio"
        self.desktop = self.pkg / "share/applications/folio.desktop"
        self.manifest = self.pkg / "share/folio/alacarchy-privacy.json"
        for p in (self.binary, self.desktop, self.manifest):
            p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.binary.write_bytes(b"\x7fELF\x02\x01\x01\0" + bytes(56) + b"synthetic-not-executed")
        self.binary.chmod(0o700)
        self.desktop.write_text("[Desktop Entry]\nType=Application\nName=Folio\nExec=/usr/bin/folio %U\nMimeType=inode/directory;\n")
        self.metadata = {"version": 1, "policy": "org.gnome.desktop.privacy/remember-recent-files",
                         "binary_sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest(),
                         "desktop_sha256": hashlib.sha256(self.desktop.read_bytes()).hexdigest()}
        self.manifest.write_text(json.dumps(self.metadata))
        spec = importlib.util.spec_from_file_location("folio_control_test", REPO / "extras/privacy/control.py")
        assert spec is not None and spec.loader is not None
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.env = {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / "data"),
                    "XDG_DATA_DIRS": str(self.pkg / "share"), "PATH": "/usr/bin:/bin"}
        self.owners = {}
        self.real_stat, self.real_fstat = os.stat, os.fstat
        self.fd_reads = []

    def metadata_stat(self, path, value):
        spelling = os.path.normpath(str(path))
        if spelling == str(self.pkg) or spelling.startswith(str(self.pkg) + "/"):
            return RootMetadata(value, self.owners.get(spelling, 0))
        return value

    def stat(self, path, *args, **kwargs):
        value = self.real_stat(path, *args, **kwargs)
        if isinstance(path, int):
            spelling = os.readlink("/proc/self/fd/" + str(path))
        elif kwargs.get("dir_fd") is not None:
            spelling = os.path.join(os.readlink("/proc/self/fd/" + str(kwargs["dir_fd"])), str(path))
        else:
            spelling = path
        return self.metadata_stat(spelling, value)

    def fstat(self, fd):
        value = self.real_fstat(fd)
        return self.metadata_stat(os.readlink("/proc/self/fd/" + str(fd)), value)

    def check(self):
        effects, active = [], [True]
        forbidden = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.remove",
                     "os.rename", "os.mkdir", "os.rmdir", "os.chmod", "os.chown", "os.link", "os.symlink"}
        def guard(event, args):
            if not active[0]:
                return
            write_open = (event == "open" and isinstance(args[2], int)
                          and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            history_open = (event == "open" and isinstance(args[0], (str, bytes))
                            and os.fsdecode(args[0]).endswith("recently-used.xbel"))
            override_open = (event == "open" and isinstance(args[0], (str, bytes))
                             and os.fsdecode(args[0]) == str(self.home / "data/applications/folio.desktop"))
            if event in forbidden or write_open or history_open or override_open:
                effects.append(event)
                raise AssertionError("Capability admission attempted a forbidden effect")
        sys.addaudithook(guard)
        try:
            with patch.dict(os.environ, self.env, clear=True), \
                    patch.object(self.mod, "FOLIO_ROOT", self.pkg, create=True), \
                    patch.object(self.mod, "command", return_value="folio.desktop") as command, \
                    patch.object(os, "stat", self.stat), patch.object(os, "fstat", self.fstat):
                self.mod.manager("recent")
                command.assert_called_once_with("xdg-mime", "query", "default", "inode/directory")
        finally:
            active[0] = False
            self.assertEqual(effects, [], "Refusal must precede any mutation, history read or native execution")

    def test_verified_native_package_is_admitted_without_executing_it(self):
        try:
            self.check()
        except self.mod.Refused as exc:
            self.fail("Verified compatible Folio was refused: " + str(exc))

    def test_missing_or_malformed_capability_refuses(self):
        valid = self.manifest.read_bytes()
        cases = [b"", b"{", b"null", b"[]", valid + b" trailing", b" " * 8193,
                 b'{"version":1,"version":1}',
                 json.dumps(dict(self.metadata, version=True)).encode(),
                 json.dumps(dict(self.metadata, version=2)).encode(),
                 json.dumps(dict(self.metadata, policy="other")).encode(),
                 json.dumps(dict(self.metadata, binary_sha256="f" * 63)).encode(),
                 json.dumps(dict(self.metadata, desktop_sha256="F" * 64)).encode(),
                 json.dumps(dict(self.metadata, unexpected=True)).encode()]
        self.check()  # Matching setup reaches the real admission boundary.
        for value in cases:
            with self.subTest(value=value[:45]):
                self.manifest.write_bytes(value)
                with self.assertRaises(self.mod.Refused):
                    self.check()
                self.assertEqual(self.manifest.read_bytes(), value)
        self.manifest.unlink()
        with self.assertRaises(self.mod.Refused):
            self.check()

    def test_binary_and_desktop_hashes_must_match(self):
        for target in (self.binary, self.desktop):
            with self.subTest(target=target.name):
                original = target.read_bytes()
                target.write_bytes(original + b"\nchanged")
                with self.assertRaises(self.mod.Refused):
                    self.check()
                self.assertEqual(target.read_bytes(), original + b"\nchanged")
                target.write_bytes(original)
        self.check()

    def test_untrusted_owner_and_writable_ancestors_refuse(self):
        paths = [self.pkg, self.pkg / "share", self.manifest.parent,
                 self.binary, self.desktop, self.manifest]
        for target in paths:
            with self.subTest(target=target.relative_to(self.pkg), kind="owner"):
                self.owners[str(target)] = 1000
                with self.assertRaises(self.mod.Refused):
                    self.check()
                self.owners.clear()
            for bit in (0o020, 0o002):
                with self.subTest(target=target.relative_to(self.pkg), writable=bit):
                    mode = target.stat().st_mode & 0o777
                    target.chmod(mode | bit)
                    with self.assertRaises(self.mod.Refused):
                        self.check()
                    target.chmod(mode)
        self.check()

    def test_symlink_and_hardlink_package_members_refuse(self):
        for target in (self.manifest, self.binary, self.desktop):
            original = target.read_bytes()
            mode = target.stat().st_mode & 0o777
            alternate = self.base / "alternate"
            alternate.write_bytes(original)
            target.unlink()
            target.symlink_to(alternate)
            with self.subTest(target=target.name, kind="symlink"), self.assertRaises(self.mod.Refused):
                self.check()
            target.unlink()
            target.write_bytes(original)
            target.chmod(mode)
            link = self.base / "hardlink"
            os.link(target, link)
            with self.subTest(target=target.name, kind="hardlink"), self.assertRaises(self.mod.Refused):
                self.check()
            link.unlink()
        self.check()

    def test_fifo_and_nonexecutable_binary_refuse(self):
        self.manifest.unlink()
        os.mkfifo(self.manifest, 0o600)
        with self.assertRaises(self.mod.Refused):
            self.check()
        self.manifest.unlink()
        self.manifest.write_text(json.dumps(self.metadata))
        self.binary.chmod(0o600)
        with self.assertRaises(self.mod.Refused):
            self.check()

    def test_non_elf_even_with_matching_metadata_refuses(self):
        self.binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.metadata["binary_sha256"] = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.manifest.write_text(json.dumps(self.metadata))
        with self.assertRaises(self.mod.Refused):
            self.check()

    def test_user_desktop_override_refuses_without_reading_it(self):
        target = self.home / "data/applications/folio.desktop"
        target.parent.mkdir(mode=0o700, parents=True)
        target.write_text("[Desktop Entry]\nExec=unknown-folio\n")
        with self.assertRaises(self.mod.Refused):
            self.check()
        self.assertEqual(target.read_text(), "[Desktop Entry]\nExec=unknown-folio\n")

    def test_launcher_path_and_dbus_routing_must_match(self):
        original = self.desktop.read_text()
        for changed in (original.replace("Exec=/usr/bin/folio", "Exec=folio"),
                        original + "DBusActivatable=true\n"):
            with self.subTest(launcher=changed.splitlines()[-1]):
                self.desktop.write_text(changed)
                self.metadata["desktop_sha256"] = hashlib.sha256(self.desktop.read_bytes()).hexdigest()
                self.manifest.write_text(json.dumps(self.metadata))
                with self.assertRaises(self.mod.Refused):
                    self.check()


if __name__ == "__main__":
    unittest.main(verbosity=2)
