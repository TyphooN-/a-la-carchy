#!/usr/bin/env python3
"""Privacy controls: disposable HOME, no display/session bus, command mocks only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "extras/privacy/control.py"
ROOT = Path(os.environ["TMPDIR"]).resolve()

MOCK = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p=Path(os.environ["HOME"])/"mock.json"
s=json.loads(p.read_text()); cmd=Path(sys.argv[0]).name; a=sys.argv[1:]
s["calls"].append([cmd]+a); p.write_text(json.dumps(s))
if s.get("fail") == [cmd]+a: sys.exit(9)
if cmd == "xdg-mime": print(s.get("manager", "org.gnome.Nautilus.desktop"))
elif cmd == "gsettings":
    action=a[0]; key=a[-1] if action not in ("set",) else a[-2]
    if action == "list-schemas": print("\n".join(s.get("schemas",["org.gnome.desktop.privacy","org.gnome.nautilus.preferences"])))
    elif action == "list-keys": print("remember-recent-files\nshow-image-thumbnails")
    elif action == "writable": print("false" if s.get("locked") else "true")
    elif action == "get": print(s["values"][key])
    elif action in ("set","reset"):
        if not s.get("noop"):
            s["values"][key]=a[-1] if action=="set" else s["defaults"][key]
            s["raw"][key]=a[-1] if action=="set" else ""
        p.write_text(json.dumps(s))
    else: sys.exit(98)
elif cmd == "dconf": print(s["raw"][a[-1].split("/")[-1]])
elif cmd == "systemctl":
    action=a[1]; name=next((v for v in a if v.endswith(".service")),"")
    if name not in s["units"]: sys.exit(98)
    unit=s["units"][name]
    if [cmd]+a in s.get("noop_commands", []): sys.exit(0)
    if action == "show":
        for k,v in unit.items(): print(k+"="+v)
    elif action == "mask":
        mask=Path(os.environ["XDG_CONFIG_HOME"])/"systemd/user"/name
        mask.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not mask.is_symlink(): mask.symlink_to("/dev/null")
        unit["LoadState"]="masked"; unit["UnitFileState"]="masked"; unit["ActiveState"]="inactive"
        unit["FragmentPath"]="/dev/null" if s.get("mask_fragment") == "devnull" else str(mask)
    elif action == "unmask":
        mask=Path(os.environ["XDG_CONFIG_HOME"])/"systemd/user"/name
        if mask.is_symlink(): mask.unlink()
        unit["LoadState"]="loaded"; unit["UnitFileState"]="static"; unit["FragmentPath"]="/usr/lib/systemd/user/"+name
    elif action == "start": unit["ActiveState"]="active"
    else: sys.exit(98)
    p.write_text(json.dumps(s))
    if s.get("fail_after") == [cmd]+a: sys.exit(9)
else: sys.exit(98)
'''


class Privacy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="alc-privacy-", dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir(mode=0o700)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        for name in ("gsettings", "dconf", "systemctl", "xdg-mime", "hyprctl", "omarchy"):
            f = self.bin / name
            f.write_text(MOCK)
            f.chmod(0o700)
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "TMPDIR": str(ROOT),
                    "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_DATA_HOME": str(self.home / ".local/share"),
                    "XDG_STATE_HOME": str(self.home / ".local/state"), "XDG_CACHE_HOME": str(self.home / ".cache"),
                    "XDG_RUNTIME_DIR": str(self.home / "runtime"), "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        self.db = self.home / "mock.json"
        units = {n: {"Id": n, "LoadState": "loaded", "ActiveState": active, "UnitFileState": "static",
                     "FragmentPath": "/usr/lib/systemd/user/" + n, "DropInPaths": ""}
                 for n, active in [("localsearch-3.service", "active"), ("localsearch-control-3.service", "inactive")]}
        self.db.write_text(json.dumps({"values": {"remember-recent-files": "true", "show-image-thumbnails": "'local-only'"},
                                      "defaults": {"remember-recent-files": "true", "show-image-thumbnails": "'local-only'"},
                                      "raw": {"remember-recent-files": "", "show-image-thumbnails": "'local-only'"},
                                      "calls": [], "units": units}))

    def state(self):
        return json.loads(self.db.read_text())

    def change(self, **kw):
        s = self.state(); s.update(kw); self.db.write_text(json.dumps(s))

    def runctl(self, feature, action="status", success=True, confirm=True, env=None):
        args = ["/usr/bin/python3", "-B", str(BACKEND), feature, action]
        if action != "status" and confirm:
            args += ["--confirm", "PURGE" if action == "purge" else "CHANGE"]
        p = subprocess.run(args, env=env or self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(p.returncode == 0, success, p.stdout + p.stderr)
        return p

    def config(self, version, content):
        p = self.home / f".config/gtk-{version}.0/settings.ini"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def test_recent_roundtrip_unset_and_config_preservation(self):
        p = self.config(3, "# personal config\n[Settings]\ngtk-theme-name=keep\ngtk-recent-files-enabled=true\n")
        before = p.read_bytes()
        self.runctl("recent", "stop")
        after = p.read_bytes()
        self.assertIn(b"gtk-recent-files-enabled=false", after)
        self.assertEqual(self.state()["values"]["remember-recent-files"], "false")
        self.runctl("recent", "stop")
        self.assertEqual(after, p.read_bytes())
        self.runctl("recent", "restore")
        self.assertEqual(before, p.read_bytes())
        self.assertFalse((self.home / ".config/gtk-4.0/settings.ini").exists())
        self.assertEqual(self.state()["raw"]["remember-recent-files"], "")
        self.runctl("recent", "restore")

    def test_explicit_original_value_and_already_off(self):
        s = self.state(); s["values"]["remember-recent-files"] = "false"; s["raw"]["remember-recent-files"] = "false"; self.change(**s)
        self.runctl("recent", "stop"); self.runctl("recent", "restore")
        self.assertEqual(self.state()["raw"]["remember-recent-files"], "false")

    def test_thumbnail_roundtrip(self):
        self.runctl("thumbnails", "stop"); self.runctl("thumbnails", "stop")
        self.assertEqual(self.state()["values"]["show-image-thumbnails"], "'never'")
        self.runctl("thumbnails", "restore"); self.runctl("thumbnails", "restore")
        self.assertEqual(self.state()["raw"]["show-image-thumbnails"], "'local-only'")

    def test_conflicting_settings_and_config_refused(self):
        self.runctl("recent", "stop")
        p = self.home / ".config/gtk-3.0/settings.ini"
        p.write_text(p.read_text() + "gtk-font-name=changed\n")
        before = p.read_bytes()
        self.runctl("recent", "restore", success=False)
        self.assertEqual(before, p.read_bytes())
        self.assertEqual(self.state()["values"]["remember-recent-files"], "false")
        self.runctl("recent", "stop", success=False)
        self.runctl("thumbnails", "stop")
        s = self.state(); s["values"]["show-image-thumbnails"] = "'always'"; s["raw"]["show-image-thumbnails"] = "'always'"; self.change(**s)
        self.runctl("thumbnails", "restore", success=False)

    def test_missing_schema_locked_wrong_manager_and_tool(self):
        for change in ({"schemas": []}, {"locked": True}, {"manager": "other.desktop"}):
            self.change(**change); self.runctl("recent", "stop", success=False)
            self.assertEqual(self.state()["values"]["remember-recent-files"], "true")
            self.change(schemas=["org.gnome.desktop.privacy", "org.gnome.nautilus.preferences"], locked=False, manager="org.gnome.Nautilus.desktop")
        env = dict(self.env, PATH=str(self.bin))
        (self.bin / "gsettings").unlink()
        # Absolute interpreter: PATH must not accidentally find real gsettings.
        p = subprocess.run(["/usr/bin/python3", "-B", str(BACKEND), "recent", "stop", "--confirm", "CHANGE"], env=env, capture_output=True, timeout=30)
        self.assertNotEqual(p.returncode, 0)

    def test_failed_command_and_false_success_are_errors(self):
        self.change(fail=["gsettings", "set", "org.gnome.desktop.privacy", "remember-recent-files", "false"])
        self.runctl("recent", "stop", success=False)
        self.assertEqual(self.state()["values"]["remember-recent-files"], "true")
        self.change(fail=None); self.runctl("recent", "restore")
        self.change(noop=True); self.runctl("thumbnails", "stop", success=False)
        self.assertEqual(self.state()["values"]["show-image-thumbnails"], "'local-only'")

    def mask_path(self, name="localsearch-3.service"):
        return Path(self.env["XDG_CONFIG_HOME"]) / "systemd/user" / name

    def prepare_mask(self, name="localsearch-3.service", fragment=None):
        mask = self.mask_path(name)
        mask.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        mask.symlink_to("/dev/null")
        s = self.state()
        s["units"][name].update(LoadState="masked", UnitFileState="masked", ActiveState="inactive", FragmentPath=str(mask) if fragment is None else fragment)
        self.change(**s)
        return mask

    def test_index_exact_scope_and_original_activity(self):
        self.runctl("indexing", "stop"); self.runctl("indexing", "stop")
        self.runctl("indexing", "status")
        for name, u in self.state()["units"].items():
            mask = self.mask_path(name)
            self.assertEqual((u["LoadState"], u["UnitFileState"], u["ActiveState"], u["FragmentPath"]), ("masked", "masked", "inactive", str(mask)))
            self.assertTrue(mask.is_symlink())
            self.assertEqual(os.readlink(mask), "/dev/null")
        self.runctl("indexing", "restore"); self.runctl("indexing", "restore")
        s = self.state()
        self.assertEqual(s["units"]["localsearch-3.service"]["ActiveState"], "active")
        self.assertEqual(s["units"]["localsearch-control-3.service"]["ActiveState"], "inactive")
        mutation = [c for c in s["calls"] if c[0] == "systemctl" and c[2] != "show"]
        self.assertTrue(mutation)
        self.assertTrue(all(c[-1] in s["units"] for c in mutation))
        self.assertFalse(any("enable" in c or "disable" in c for c in mutation))
        self.assertEqual([c[-1] for c in mutation if c[2] == "mask"], list(s["units"]))
        self.assertEqual([c[-1] for c in mutation if c[2] == "unmask"], list(s["units"]))
        self.assertEqual([c[-1] for c in mutation if c[2] == "start"], ["localsearch-3.service"])
        self.assertTrue(all(u["LoadState"] == "loaded" for u in s["units"].values()))
        self.assertTrue(all(not self.mask_path(name).is_symlink() for name in s["units"]))
        self.assertFalse((self.home / ".local/state/a-la-carchy/privacy/indexing.json").exists())

    def test_index_restore_retry_after_unmask_start_failure(self):
        original = self.state()["units"]
        self.runctl("indexing", "stop")
        self.change(fail=["systemctl", "--user", "start", "localsearch-3.service"])
        first = self.runctl("indexing", "restore", success=False)
        self.assertIn("(exit 9)", first.stderr)
        self.assertEqual(self.state()["calls"][-1], ["systemctl", "--user", "start", "localsearch-3.service"])
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        self.assertEqual(json.loads(journal.read_text())["phase"], "failed")
        units = self.state()["units"]
        self.assertEqual((units["localsearch-3.service"]["LoadState"],
                          units["localsearch-3.service"]["UnitFileState"],
                          units["localsearch-3.service"]["ActiveState"]),
                         ("loaded", "static", "inactive"))
        self.assertFalse(self.mask_path().is_symlink())
        self.assertEqual(os.readlink(self.mask_path("localsearch-control-3.service")), "/dev/null")
        self.change(fail=None, calls=[])
        self.runctl("indexing", "restore")
        self.assertEqual(self.state()["units"], original)
        self.assertFalse(journal.exists())
        self.assertTrue(all(not self.mask_path(n).is_symlink() for n in original))
        mutation = [c for c in self.state()["calls"] if c[0] == "systemctl" and c[2] != "show"]
        self.assertEqual([c[2:] for c in mutation], [["start", "localsearch-3.service"],
                                                   ["unmask", "localsearch-control-3.service"]])
        self.runctl("indexing", "restore")

    def test_index_restore_refuses_mask_edit_after_checkpoint_save(self):
        self.runctl("indexing", "stop")
        mod = self.load_control()
        real_save = mod.Journal.save
        mask = self.mask_path()
        events = []
        def interleave(journal, data):
            real_save(journal, data)
            if data is not None and "resume" in data and not events:
                mask.symlink_to("/dev/null")
                events.append("new mask after durable unmask checkpoint")
        with patch.dict(os.environ, self.env, clear=True), patch.object(mod.Journal, "save", interleave):
            home = mod.Home()
            try:
                with self.assertRaises(mod.Refused): mod.change(home, "indexing", "restore")
            finally: home.close()
        self.assertEqual(events, ["new mask after durable unmask checkpoint"])
        self.assertEqual(os.readlink(mask), "/dev/null")
        self.assertFalse(any(c[:3] == ["systemctl", "--user", "start"] for c in self.state()["calls"]))
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        self.assertEqual(json.loads(journal.read_text())["phase"], "failed")
        self.assertEqual(self.state()["units"]["localsearch-control-3.service"]["UnitFileState"], "masked")

    def fresh_fixture(self):
        fixture = Privacy()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def failed_index_restore(self, name="localsearch-3.service", neighbor="inactive", fragment="path"):
        other = next(n for n in self.state()["units"] if n != name)
        state = self.state()
        state["units"][name]["ActiveState"] = "active"
        state["units"][other]["ActiveState"] = "active" if neighbor == "active" else "inactive"
        self.change(**state)
        self.change(mask_fragment=fragment)
        if neighbor == "masked": self.prepare_mask(other, "/dev/null" if fragment == "devnull" else None)
        original = self.state()["units"]
        self.runctl("indexing", "stop")
        self.change(fail=["systemctl", "--user", "start", name])
        failed = self.runctl("indexing", "restore", success=False)
        self.assertIn("(exit 9)", failed.stderr)
        self.assertEqual(self.state()["calls"][-1], ["systemctl", "--user", "start", name])
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        self.assertEqual(json.loads(journal.read_text())["phase"], "failed")
        self.assertFalse(self.mask_path(name).is_symlink())
        return original, journal

    def assert_index_restore_refused_unchanged(self, journal):
        before = journal.read_bytes()
        units = self.state()["units"]
        self.change(fail=None, calls=[])
        self.runctl("indexing", "restore", success=False)
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(self.state()["units"], units)
        self.assertFalse(any(c[0] == "systemctl" and c[2] != "show" for c in self.state()["calls"]))

    def test_index_restore_repeated_retry_both_units_originals_and_masks(self):
        for name in self.state()["units"]:
            for neighbor in ("active", "inactive", "masked"):
                for fragment in ("path", "devnull"):
                    with self.subTest(name=name, neighbor=neighbor, fragment=fragment):
                        f = self.fresh_fixture()
                        original, journal = f.failed_index_restore(name, neighbor, fragment)
                        receipt = json.loads(journal.read_text())["resume"]
                        f.change(calls=[])
                        f.runctl("indexing", "restore", success=False)
                        self.assertEqual(json.loads(journal.read_text())["resume"], receipt)
                        mutation = [c for c in f.state()["calls"] if c[0] == "systemctl" and c[2] != "show"]
                        self.assertEqual(mutation, [["systemctl", "--user", "start", name]])
                        f.runctl("indexing", "stop", success=False)
                        f.change(fail=None, calls=[])
                        f.runctl("indexing", "restore"); f.runctl("indexing", "restore")
                        self.assertEqual(f.state()["units"], original)
                        self.assertFalse(journal.exists())
                        for n, unit in original.items():
                            self.assertEqual(f.mask_path(n).is_symlink(), unit["UnitFileState"] == "masked")
                        mutation = [c for c in f.state()["calls"] if c[0] == "systemctl" and c[2] != "show"]
                        self.assertTrue(all(c[-1] in original for c in mutation))
                        self.assertEqual([c for c in mutation if c[2] == "start" and c[-1] == name],
                                         [["systemctl", "--user", "start", name]])

    def test_index_restore_unmask_start_failure_boundaries(self):
        for name in self.state()["units"]:
            for active in ("active", "inactive"):
                for mode in ("unmask-fail", "unmask-noop", "start-noop", "start-fail-after"):
                    if active == "inactive" and mode.startswith("start"): continue
                    with self.subTest(name=name, active=active, mode=mode):
                        f = self.fresh_fixture()
                        s = f.state(); s["units"][name]["ActiveState"] = active; f.change(**s)
                        original = f.state()["units"]
                        f.runctl("indexing", "stop")
                        command = ["systemctl", "--user", "unmask" if mode.startswith("unmask") else "start", name]
                        if mode.endswith("noop"): f.change(noop_commands=[command])
                        elif mode.endswith("after"): f.change(fail_after=command)
                        else: f.change(fail=command)
                        f.runctl("indexing", "restore", success=False)
                        journal = f.home / ".local/state/a-la-carchy/privacy/indexing.json"
                        data = json.loads(journal.read_text())
                        self.assertEqual(data["phase"], "failed")
                        self.assertEqual("resume" in data, mode.startswith("start"))
                        if mode.startswith("unmask"):
                            self.assertEqual(os.readlink(f.mask_path(name)), "/dev/null")
                            self.assertFalse(any(c == ["systemctl", "--user", "start", name] for c in f.state()["calls"]))
                        f.change(fail=None, fail_after=None, noop_commands=[], calls=[])
                        f.runctl("indexing", "restore")
                        self.assertEqual(f.state()["units"], original)
                        self.assertFalse(journal.exists())
                        if mode == "start-fail-after":
                            self.assertFalse(any(c == ["systemctl", "--user", "start", name] for c in f.state()["calls"]))

    def test_index_restore_ambiguous_unmask_error_does_not_claim_intermediate(self):
        original = self.state()["units"]
        self.runctl("indexing", "stop")
        self.change(fail_after=["systemctl", "--user", "unmask", "localsearch-3.service"])
        self.runctl("indexing", "restore", success=False)
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        self.assertNotIn("resume", json.loads(journal.read_text()))
        self.assertNotEqual(self.state()["units"], original)
        self.assert_index_restore_refused_unchanged(journal)

    def test_index_restore_checkpoint_refuses_newer_paths_and_units(self):
        kinds = ("new-mask", "regular", "fifo", "wrong-target", "hardlinked", "parent-replaced",
                 "unsafe-parent", "symlink-parent", "directory-edited", "alias", "dropin", "fragment",
                 "loadstate", "activity", "remasked")
        for kind in kinds:
            with self.subTest(kind=kind):
                f = self.fresh_fixture(); _, journal = f.failed_index_restore()
                mask = f.mask_path()
                if kind in ("new-mask", "remasked", "hardlinked"): mask.symlink_to("/dev/null")
                elif kind == "regular": mask.write_text("manual override\n")
                elif kind == "fifo": os.mkfifo(mask)
                elif kind == "wrong-target": mask.symlink_to(f.db)
                elif kind == "parent-replaced":
                    mask.parent.rename(mask.parent.with_name("old-user"))
                    mask.parent.mkdir(mode=0o700)
                    f.mask_path("localsearch-control-3.service").symlink_to("/dev/null")
                elif kind == "unsafe-parent": mask.parent.chmod(0o777)
                elif kind == "symlink-parent":
                    old = mask.parent.with_name("old-user"); mask.parent.rename(old); mask.parent.symlink_to(old)
                elif kind == "directory-edited":
                    probe = mask.parent / "manual-edit"; probe.write_text("newer"); probe.unlink()
                if kind == "hardlinked": os.link(mask, mask.with_name("alias.service"), follow_symlinks=False)
                edits = {"alias": {"Id": "unrelated.service"}, "dropin": {"DropInPaths": "/custom"},
                         "fragment": {"FragmentPath": "/custom"}, "loadstate": {"LoadState": "not-found"},
                         "activity": {"ActiveState": "activating"},
                         "remasked": {"LoadState": "masked", "UnitFileState": "masked", "FragmentPath": str(mask)}}
                if kind in edits:
                    s = f.state(); s["units"]["localsearch-3.service"].update(edits[kind]); f.change(**s)
                f.assert_index_restore_refused_unchanged(journal)

    def test_index_restore_preflights_remaining_mask_before_resuming_start(self):
        for kind in ("missing", "regular", "fifo", "wrong-target", "hardlinked", "alias", "dropin"):
            with self.subTest(kind=kind):
                f = self.fresh_fixture(); _, journal = f.failed_index_restore()
                mask = f.mask_path("localsearch-control-3.service")
                if kind in ("missing", "regular", "fifo", "wrong-target"): mask.unlink()
                if kind == "regular": mask.write_text("unowned\n")
                elif kind == "fifo": os.mkfifo(mask)
                elif kind == "wrong-target": mask.symlink_to(f.db)
                elif kind == "hardlinked": os.link(mask, mask.with_name("alias.service"), follow_symlinks=False)
                elif kind in ("alias", "dropin"):
                    s = f.state(); s["units"][mask.name].update({"Id": "other.service"} if kind == "alias" else {"DropInPaths": "/custom"}); f.change(**s)
                f.assert_index_restore_refused_unchanged(journal)

    def test_index_restore_refuses_forged_stale_contradictory_checkpoints(self):
        cases = ("no-receipt", "prepared", "applied", "unknown-phase", "wrong-version", "unknown-field",
                 "wrong-owner", "inactive-original", "masked-original", "modified-target", "duplicate-id",
                 "extra-item", "extra-receipt-key", "missing-parent", "short-parent", "boolean-parent",
                 "stale-parent", "foreign-parent-owner", "unsafe-parent-mode", "non-directory-parent")
        for kind in cases:
            with self.subTest(kind=kind):
                f = self.fresh_fixture(); _, journal = f.failed_index_restore()
                data = json.loads(journal.read_text())
                if kind == "no-receipt": del data["resume"]
                elif kind in ("prepared", "applied", "unknown-phase"): data["phase"] = kind
                elif kind == "wrong-version": data["version"] = True
                elif kind == "unknown-field": data["restore_in_progress"] = True
                elif kind == "wrong-owner": data["resume"]["id"] = "indexing:unrelated.service"
                elif kind == "inactive-original": data["items"][0]["original"]["active"] = "inactive"
                elif kind == "masked-original": data["items"][0]["original"] = {"unit": "masked", "active": "inactive"}
                elif kind == "modified-target": data["items"][0]["target"] = {"unit": "static", "active": "inactive"}
                elif kind == "duplicate-id": data["items"][1]["id"] = data["items"][0]["id"]
                elif kind == "extra-item": data["items"].append(data["items"][0])
                elif kind == "extra-receipt-key": data["resume"]["active"] = "inactive"
                elif kind == "missing-parent": del data["resume"]["parent"]
                elif kind == "short-parent": data["resume"]["parent"] = [1]
                elif kind == "boolean-parent": data["resume"]["parent"][0] = True
                elif kind == "stale-parent": data["resume"]["parent"][3] += 1
                elif kind == "foreign-parent-owner": data["resume"]["parent"][6] += 1
                elif kind == "unsafe-parent-mode": data["resume"]["parent"][5] |= 0o022
                elif kind == "non-directory-parent": data["resume"]["parent"][5] = 0o100600
                journal.write_text(json.dumps(data))
                f.assert_index_restore_refused_unchanged(journal)

    def test_non_index_restore_refuses_index_checkpoint(self):
        self.runctl("recent", "stop")
        journal = self.home / ".local/state/a-la-carchy/privacy/recent.json"
        data = json.loads(journal.read_text()); data["phase"] = "failed"
        data["resume"] = {"id": "recent:gtk3", "parent": [1, 1, 1, 1, 1, 0o40700, os.getuid(), 2]}
        journal.write_text(json.dumps(data))
        before = journal.read_bytes(); values = self.state()["values"]
        self.runctl("recent", "restore", success=False)
        self.assertEqual(journal.read_bytes(), before); self.assertEqual(self.state()["values"], values)

    def test_index_restore_checkpoint_write_failure_never_starts_without_receipt(self):
        self.runctl("indexing", "stop")
        mod = self.load_control(); real_save = mod.Journal.save
        events = []
        def fail_checkpoint(journal, data):
            if data is not None and "resume" in data:
                events.append("checkpoint persistence failed")
                raise mod.Refused("fixture checkpoint write failure")
            return real_save(journal, data)
        with patch.dict(os.environ, self.env, clear=True), patch.object(mod.Journal, "save", fail_checkpoint):
            home = mod.Home()
            try:
                with self.assertRaises(mod.Refused): mod.change(home, "indexing", "restore")
            finally: home.close()
        self.assertEqual(events, ["checkpoint persistence failed"])
        self.assertFalse(any(c[:3] == ["systemctl", "--user", "start"] for c in self.state()["calls"]))
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        self.assertEqual(json.loads(journal.read_text())["phase"], "failed")
        self.assertNotIn("resume", json.loads(journal.read_text()))
        self.assert_index_restore_refused_unchanged(journal)

    def test_index_restore_retains_journal_until_all_originals_verified(self):
        self.runctl("indexing", "stop")
        mod = self.load_control(); real_command = mod.command
        events = []
        def edit_completed_unit(*args):
            result = real_command(*args)
            if args == ("systemctl", "--user", "unmask", "localsearch-control-3.service"):
                state = self.state(); state["units"]["localsearch-3.service"]["ActiveState"] = "inactive"
                self.change(**state); events.append("manual edit after first unit restored")
            return result
        with patch.dict(os.environ, self.env, clear=True), patch.object(mod, "command", edit_completed_unit):
            home = mod.Home()
            try:
                with self.assertRaisesRegex(mod.Refused, "Original configuration changed"):
                    mod.change(home, "indexing", "restore")
            finally: home.close()
        self.assertEqual(events, ["manual edit after first unit restored"])
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        self.assertEqual(json.loads(journal.read_text())["phase"], "failed")
        self.assertNotIn("resume", json.loads(journal.read_text()))
        self.assert_index_restore_refused_unchanged(journal)

    def test_index_failed_stop_does_not_own_manual_unmask_intermediate(self):
        self.change(fail=["systemctl", "--user", "mask", "--now", "localsearch-control-3.service"])
        self.runctl("indexing", "stop", success=False)
        self.mask_path().unlink()
        state = self.state()
        state["units"]["localsearch-3.service"].update(LoadState="loaded", UnitFileState="static",
                                                     ActiveState="inactive", FragmentPath="/usr/lib/systemd/user/localsearch-3.service")
        self.change(**state)
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        self.assertNotIn("resume", json.loads(journal.read_text()))
        self.assert_index_restore_refused_unchanged(journal)

    def test_index_restore_retains_journal_if_mask_reappears_after_start(self):
        self.runctl("indexing", "stop")
        mod = self.load_control(); real_command = mod.command
        events = []
        def mask_completed_unit(*args):
            result = real_command(*args)
            if args == ("systemctl", "--user", "unmask", "localsearch-control-3.service"):
                self.mask_path().symlink_to("/dev/null")
                events.append("new mask after first service started")
            return result
        with patch.dict(os.environ, self.env, clear=True), patch.object(mod, "command", mask_completed_unit):
            home = mod.Home()
            try:
                with self.assertRaisesRegex(mod.Refused, "mask path appeared"):
                    mod.change(home, "indexing", "restore")
            finally: home.close()
        self.assertEqual(events, ["new mask after first service started"])
        self.assertEqual(os.readlink(self.mask_path()), "/dev/null")
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        self.assertEqual(json.loads(journal.read_text())["phase"], "failed")
        self.assert_index_restore_refused_unchanged(journal)

    def test_index_restore_preflights_static_tuple_with_new_mask(self):
        original = self.state()["units"]
        self.runctl("indexing", "stop")
        state = self.state(); state["units"]["localsearch-3.service"] = original["localsearch-3.service"]
        self.change(**state)
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        # Stale stock properties must not hide a newer persistent mask path.
        self.assertEqual(os.readlink(self.mask_path()), "/dev/null")
        self.assert_index_restore_refused_unchanged(journal)
        self.assertEqual(self.state()["units"]["localsearch-control-3.service"]["UnitFileState"], "masked")

    def test_index_restore_failed_first_stop_without_unit_directory(self):
        original = self.state()["units"]
        self.change(fail=["systemctl", "--user", "mask", "--now", "localsearch-3.service"])
        self.runctl("indexing", "stop", success=False)
        self.assertFalse(self.mask_path().parent.exists())
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        self.assertEqual(json.loads(journal.read_text())["phase"], "failed")
        self.change(fail=None, calls=[])
        self.runctl("indexing", "restore")
        self.assertEqual(self.state()["units"], original)
        self.assertFalse(journal.exists())
        self.assertFalse(any(c[0] == "systemctl" and c[2] != "show" for c in self.state()["calls"]))

    def test_index_preserves_preexisting_mask(self):
        mask = self.prepare_mask()
        before = self.state()["units"]
        self.runctl("indexing", "stop"); self.runctl("indexing", "stop")
        self.runctl("indexing", "restore"); self.runctl("indexing", "restore")
        self.assertEqual(self.state()["units"], before)
        self.assertEqual(os.readlink(mask), "/dev/null")
        mutation = [c for c in self.state()["calls"] if c[0] == "systemctl" and c[2] != "show"]
        self.assertEqual([c[-1] for c in mutation], ["localsearch-control-3.service"] * 2)

    def test_index_custom_in_home_xdg_mask_roundtrip(self):
        self.env["XDG_CONFIG_HOME"] = str(self.home / "private-config")
        self.runctl("indexing", "stop"); self.runctl("indexing", "status")
        self.assertTrue(all(self.mask_path(name).is_symlink() for name in self.state()["units"]))
        self.assertFalse((self.home / ".config/systemd/user").exists())
        self.runctl("indexing", "restore")
        self.assertTrue(all(not self.mask_path(name).is_symlink() for name in self.state()["units"]))

    def test_index_restore_retained_first_mask_failure_journal(self):
        # This is the state retained by the independently reproduced defect.
        before = self.state()["units"]
        mask = self.prepare_mask()
        journal = self.home / ".local/state/a-la-carchy/privacy/indexing.json"
        journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        context = {"home": str(self.home), "roots": {
            name: str(Path(self.env["XDG_" + name.upper() + "_HOME"]).relative_to(self.home))
            for name in ("config", "data", "cache", "state")}}
        journal.write_text(json.dumps({"context": context, "version": 1, "phase": "failed", "items": [
            {"id": "indexing:" + name,
             "original": {"unit": u["UnitFileState"], "active": u["ActiveState"]},
             "target": {"unit": "masked", "active": "inactive"}} for name, u in before.items()]}))
        journal.chmod(0o600)
        self.assertIn("incomplete", self.runctl("indexing", "status").stdout)
        self.runctl("indexing", "stop", success=False)
        self.assertTrue(journal.exists()); self.assertEqual(os.readlink(mask), "/dev/null")
        self.runctl("indexing", "restore"); self.runctl("indexing", "restore")
        self.assertEqual(self.state()["units"], before)
        self.assertFalse(journal.exists()); self.assertFalse(mask.is_symlink())
        mutation = [c for c in self.state()["calls"] if c[0] == "systemctl" and c[2] != "show"]
        self.assertEqual([c[2] for c in mutation], ["unmask", "start"])
        self.assertTrue(all(c[-1] == "localsearch-3.service" for c in mutation))

    def test_index_devnull_fragment_requires_real_persistent_mask(self):
        self.change(mask_fragment="devnull")
        mask = self.prepare_mask(fragment="/dev/null")
        self.runctl("indexing", "stop"); self.runctl("indexing", "status")
        self.runctl("indexing", "restore")
        self.assertEqual(os.readlink(mask), "/dev/null")
        self.assertFalse(self.mask_path("localsearch-control-3.service").is_symlink())

    def test_index_mask_requires_owned_nofollow_devnull_symlink(self):
        mask = self.mask_path()
        alias = mask.parent / "unrelated.service"
        mask.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        s = self.state()
        s["units"]["localsearch-3.service"].update(LoadState="masked", UnitFileState="masked", ActiveState="inactive", FragmentPath="/dev/null")
        self.change(**s)
        for kind in ("missing", "regular", "fifo", "wrong-target", "hardlinked"):
            with self.subTest(kind=kind):
                if kind == "regular": mask.write_text("unowned unit override\n")
                elif kind == "fifo": os.mkfifo(mask)
                elif kind == "wrong-target": mask.symlink_to(self.db)
                elif kind == "hardlinked":
                    mask.symlink_to("/dev/null")
                    alias = mask.parent / "unrelated.service"
                    os.link(mask, alias, follow_symlinks=False)
                try:
                    self.runctl("indexing", "status", success=False)
                    self.runctl("indexing", "stop", success=False)
                    self.assertFalse(any(c[0] == "systemctl" and c[2] != "show" for c in self.state()["calls"]))
                finally:
                    if mask.is_symlink() or mask.exists(): mask.unlink()
                    if kind == "hardlinked": alias.unlink()

    def test_index_runtime_custom_and_alias_masks_refused(self):
        mask = self.prepare_mask()
        for fragment in (str(self.home / "runtime/systemd/user/localsearch-3.service"), str(mask.parent / "unrelated.service"), "/custom"):
            with self.subTest(fragment=fragment):
                s = self.state(); s["units"]["localsearch-3.service"]["FragmentPath"] = fragment; self.change(**s)
                self.runctl("indexing", "stop", success=False)
                self.assertEqual(os.readlink(mask), "/dev/null")
        s = self.state(); s["units"]["localsearch-3.service"].update(Id="unrelated.service", FragmentPath=str(mask)); self.change(**s)
        self.runctl("indexing", "stop", success=False)
        self.assertFalse(any(c[0] == "systemctl" and c[2] != "show" for c in self.state()["calls"]))

    def test_index_mask_parent_and_active_tuple_refused(self):
        mask = self.prepare_mask()
        s = self.state(); s["units"]["localsearch-3.service"]["ActiveState"] = "active"; self.change(**s)
        self.runctl("indexing", "status", success=False)
        s = self.state(); s["units"]["localsearch-3.service"]["ActiveState"] = "inactive"; self.change(**s)
        mask.parent.chmod(0o777)
        try: self.runctl("indexing", "stop", success=False)
        finally: mask.parent.chmod(0o700)
        real = mask.parent.with_name("user-real")
        mask.parent.rename(real)
        mask.parent.symlink_to(real, target_is_directory=True)
        self.runctl("indexing", "stop", success=False)
        self.assertEqual(os.readlink(real / mask.name), "/dev/null")
        self.assertFalse(any(c[0] == "systemctl" and c[2] != "show" for c in self.state()["calls"]))

    def test_index_inconsistent_mask_properties_refused(self):
        for edit in ({"LoadState": "masked"}, {"FragmentPath": "/custom"}, {"UnitFileState": "masked"}, {"LoadState": "not-found"}):
            with self.subTest(edit=edit):
                s = self.state(); before = dict(s["units"]["localsearch-3.service"])
                s["units"]["localsearch-3.service"].update(edit); self.change(**s)
                self.runctl("indexing", "stop", success=False)
                self.assertFalse(any(c[0] == "systemctl" and c[2] != "show" for c in self.state()["calls"]))
                s = self.state(); s["units"]["localsearch-3.service"] = before; self.change(**s)

    def test_index_custom_unit_and_failure(self):
        s = self.state(); s["units"]["localsearch-3.service"]["DropInPaths"] = "/custom"; self.change(**s)
        self.runctl("indexing", "stop", success=False)
        s = self.state(); s["units"]["localsearch-3.service"]["DropInPaths"] = ""; self.change(**s)
        self.change(fail=["systemctl", "--user", "mask", "--now", "localsearch-control-3.service"])
        self.runctl("indexing", "stop", success=False)
        self.change(fail=None); self.runctl("indexing", "restore")
        self.assertEqual(self.state()["units"]["localsearch-3.service"]["ActiveState"], "active")

    def test_purge_confirmation_no_backup_and_idempotence(self):
        recent = self.home / ".local/share/recently-used.xbel"
        recent.parent.mkdir(parents=True); recent.write_text("synthetic PRIVATE HISTORY sentinel")
        self.runctl("recent", "purge", confirm=False, success=False)
        self.assertTrue(recent.exists())
        self.runctl("recent", "purge"); self.runctl("recent", "purge")
        self.assertFalse(recent.exists())
        for p in self.home.rglob("*"):
            if p.is_file(): self.assertNotIn(b"PRIVATE HISTORY", p.read_bytes())
        self.assertEqual(self.state()["values"]["remember-recent-files"], "true")
        thumb = self.home / ".cache/thumbnails/normal/test.png"
        thumb.parent.mkdir(parents=True); thumb.write_bytes(b"synthetic thumbnail")
        self.runctl("thumbnails", "purge"); self.runctl("thumbnails", "purge")
        self.assertFalse(thumb.exists())

    def test_malicious_symlinks_hardlinks_and_external_xdg(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir(); sentinel = outside / "sentinel"; sentinel.write_text("keep")
        recent = self.home / ".local/share/recently-used.xbel"
        recent.parent.mkdir(parents=True); recent.symlink_to(sentinel)
        self.runctl("recent", "purge", success=False)
        self.assertEqual(sentinel.read_text(), "keep")
        recent.unlink(); os.link(sentinel, recent)
        self.runctl("recent", "purge", success=False)
        recent.unlink()
        thumb = self.home / ".cache/thumbnails"
        thumb.mkdir(parents=True); (thumb / "escape").symlink_to(outside)
        self.runctl("thumbnails", "purge", success=False)
        self.assertTrue((thumb / "escape").is_symlink())
        env = dict(self.env, XDG_DATA_HOME=str(outside))
        self.runctl("recent", "purge", env=env, success=False)
        (self.home / ".config").symlink_to(outside)
        self.runctl("recent", "stop", success=False)
        self.assertEqual(self.state()["values"]["remember-recent-files"], "true")

    def load_control(self):
        sys.path.insert(0, str(REPO / "extras/privacy"))
        self.addCleanup(sys.path.pop, 0)
        spec = importlib.util.spec_from_file_location("privacy_race", BACKEND)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def restore_interleaving(self, original):
        target = self.home / ".config/gtk-3.0/settings.ini"
        if original is not None: self.config(3, original)
        self.runctl("recent", "stop")
        mod = self.load_control(); real_put = mod.Gtk.put
        newer = target.read_bytes() + b"gtk-font-name=newer-user-edit\n"
        events = []
        def interleave(item, value, *args, **kwargs):
            if item.id == "recent:gtk3":
                target.write_bytes(newer); events.append("edit after caller ownership check")
            return real_put(item, value, *args, **kwargs)
        with patch.dict(os.environ, self.env, clear=True), patch.object(mod.Gtk, "put", interleave):
            home = mod.Home()
            try:
                with self.assertRaises(mod.Refused): mod.change(home, "recent", "restore")
            finally: home.close()
        self.assertEqual(events, ["edit after caller ownership check"])
        self.assertEqual(target.read_bytes(), newer)
        journal = self.home / ".local/state/a-la-carchy/privacy/recent.json"
        self.assertEqual(json.loads(journal.read_text())["phase"], "failed", "Recovery journal must survive refused Restore")

    def test_restore_refuses_edit_after_ownership_check(self):
        self.restore_interleaving("[Settings]\ngtk-theme-name=original\n")

    def test_restore_delete_refuses_edit_after_ownership_check(self):
        self.restore_interleaving(None)

    def test_home_write_and_delete_pin_version_before_content_validation(self):
        mod = self.load_control()
        target = self.config(3, "[Settings]\ngtk-theme-name=original\n")
        expected = target.read_bytes()
        newer = b"[Settings]\ngtk-theme-name=newer-user-edit\n"
        for data in (b"[Settings]\ngtk-theme-name=configurator\n", None):
            with self.subTest(delete=data is None), patch.dict(os.environ, self.env, clear=True):
                target.write_bytes(expected)
                home = mod.Home(); real_stat = os.stat; events = []
                def interleave(name, *args, **kwargs):
                    if name == "settings.ini" and "dir_fd" in kwargs and not events:
                        target.write_bytes(newer); events.append("edit at first path stat")
                    return real_stat(name, *args, **kwargs)
                try:
                    with patch.object(os, "stat", interleave):
                        with self.assertRaises(mod.Refused):
                            home.write(home.rel("config", "gtk-3.0/settings.ini"), data, expected)
                finally: home.close()
                self.assertEqual(events, ["edit at first path stat"])
                self.assertEqual(target.read_bytes(), newer)

    def test_unknown_ini_state_symlink_and_unconfirmed_mutation(self):
        p = self.config(3, "[Settings]\ngtk-recent-files-enabled=true\ngtk-recent-files-enabled=false\n")
        self.runctl("recent", "stop", success=False)
        p.unlink(); p.symlink_to(self.db)
        self.runctl("recent", "stop", success=False)
        self.runctl("thumbnails", "stop", confirm=False, success=False)
        state = self.home / ".local/state/a-la-carchy/privacy"
        state.mkdir(parents=True, exist_ok=True)
        (state / "thumbnails.json").symlink_to(self.db)
        self.runctl("thumbnails", "stop", success=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
