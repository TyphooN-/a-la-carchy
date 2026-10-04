#!/usr/bin/env python3
"""Small explicit privacy controls; no imports execute mutations."""
import argparse
import configparser
import io
import json
import os
import stat
from pathlib import Path
import subprocess
import sys

from safeio import Home, Journal, Refused, decode, encode

SETTINGS = {
    "recent": ("org.gnome.desktop.privacy", "remember-recent-files", "/org/gnome/desktop/privacy/", "false", {"true", "false"}),
    "thumbnails": ("org.gnome.nautilus.preferences", "show-image-thumbnails", "/org/gnome/nautilus/preferences/", "'never'", {"'always'", "'local-only'", "'never'"}),
}
UNITS = {"localsearch-3.service": "/usr/lib/localsearch-3",
         "localsearch-control-3.service": "/usr/lib/localsearch-control-3"}


def command(*args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refused(f"Command unavailable/timed out: {args[0]}") from exc
    if p.returncode:
        # Do not echo arbitrary command output, file names or sensitive values.
        raise Refused(f"Command failed: {args[0]} {args[1]} (exit {p.returncode})")
    return p.stdout.strip()


def manager():
    if command("xdg-mime", "query", "default", "inode/directory") != "org.gnome.Nautilus.desktop":
        raise Refused("Unsupported default file manager; Nautilus is required")


class Setting:
    def __init__(self, feature):
        self.feature = feature
        self.schema, self.key, self.path, self.desired, self.values = SETTINGS[feature]
        self.id = feature + ":gsettings"
        if self.schema not in command("gsettings", "list-schemas").splitlines() or self.key not in command("gsettings", "list-keys", self.schema).splitlines():
            raise Refused("Required setting schema/key unavailable")
        if command("gsettings", "writable", self.schema, self.key) != "true":
            raise Refused("Setting is locked/unavailable")

    def current(self):
        effective = command("gsettings", "get", self.schema, self.key)
        raw = command("dconf", "read", self.path + self.key)
        if effective not in self.values or raw not in self.values | {""} or raw and raw != effective:
            raise Refused("Unsupported setting backend/value; dconf readback disagrees")
        return {"raw": raw, "effective": effective}

    def target(self, original):
        self.validate(original)
        return {"raw": self.desired, "effective": self.desired}

    def validate(self, value):
        if not isinstance(value, dict) or set(value) != {"raw", "effective"} or value["effective"] not in self.values or value["raw"] not in self.values | {""}:
            raise Refused("Malformed setting ownership record")

    def equal(self, a, b):
        # Restore ownership of an unset key, not a frozen schema default.
        return a["raw"] == b["raw"] and (not b["raw"] or a["effective"] == b["effective"])

    def put(self, value, expected):
        if not self.equal(self.current(), expected):
            raise Refused("Setting changed before write")
        if value["raw"]:
            command("gsettings", "set", self.schema, self.key, value["raw"])
        else:
            command("gsettings", "reset", self.schema, self.key)
        if not self.equal(self.current(), value):
            raise Refused("GSettings command returned success but readback failed")


def gtk_text(data):
    text = (data or b"").decode("utf-8")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_file(io.StringIO(text))
    except configparser.Error as exc:
        raise Refused("Unknown/duplicate GTK INI layout") from exc
    if parser.defaults():
        raise Refused("GTK DEFAULT inheritance is unsupported")
    if parser.has_option("Settings", "gtk-recent-files-enabled") and parser.get("Settings", "gtk-recent-files-enabled").lower() not in {"true", "false", "0", "1"}:
        raise Refused("Unsupported GTK recent setting value")
    return text, parser


class Gtk:
    def __init__(self, home, version):
        self.home = home
        self.id = f"recent:gtk{version}"
        self.path = home.rel("config", f"gtk-{version}.0/settings.ini")

    def current(self):
        data = self.home.read(self.path)
        gtk_text(data)
        return encode(data)

    def validate(self, value):
        if value is not None and not isinstance(value, str):
            raise Refused("Malformed GTK ownership record")
        gtk_text(decode(value))

    def target(self, original):
        self.validate(original)
        text, parser = gtk_text(decode(original))
        if "# >>> a-la-carchy privacy recent" in text:
            raise Refused("GTK contains an unowned privacy marker")
        block = "# >>> a-la-carchy privacy recent\ngtk-recent-files-enabled=false\n# <<< a-la-carchy privacy recent\n"
        lines = text.splitlines(keepends=True)
        if not parser.has_section("Settings"):
            text += ("\n" if text and not text.endswith("\n") else "") + "[Settings]\n" + block
        else:
            out, section, inserted = [], "", False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    if section == "Settings" and not inserted:
                        out.append(block); inserted = True
                    section = stripped[1:-1]
                if section == "Settings" and stripped.startswith("gtk-recent-files-enabled") and "=" in stripped and stripped.split("=", 1)[0].strip() == "gtk-recent-files-enabled":
                    out.append(block); inserted = True
                else:
                    out.append(line if line.endswith("\n") else line + "\n")
            if section == "Settings" and not inserted:
                out.append(block)
            text = "".join(out)
        gtk_text(text.encode())
        return encode(text.encode())

    @staticmethod
    def equal(a, b):
        return a == b

    def put(self, value, expected):
        # Keep the caller-checked bytes; never adopt an intervening edit.
        self.home.write(self.path, decode(value), decode(expected))
        if self.current() != value:
            raise Refused("GTK configuration readback failed")


class IndexUnit:
    def __init__(self, home, name):
        self.home = home
        self.mask = home.rel("config", "systemd/user/" + name)
        self.name, self.id = name, "indexing:" + name
        source = Path("/usr/lib/systemd/user") / name
        # Fixed package-owned definitions only; no custom units or drop-ins.
        if source.is_symlink():
            raise Refused("Indexer package unit is a symlink")
        st = source.stat()
        if st.st_uid != 0 or st.st_mode & 0o022 or f"ExecStart={UNITS[name]}\n" not in source.read_text():
            raise Refused("Unsupported indexer package unit")

    def masked_fragment(self, fragment):
        # Both systemd renderings need the same exact persistent user mask.
        # A /dev/null property alone is not evidence of safe mask provenance.
        if fragment not in {"/dev/null", str(self.home.path / self.mask)}: return False
        try:
            with self.home.parent(self.mask) as (fd, leaf):
                before = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
                if (not stat.S_ISLNK(before.st_mode) or before.st_uid != os.getuid()
                        or before.st_nlink != 1 or os.readlink(leaf, dir_fd=fd) != "/dev/null"):
                    return False
                after = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
                return Home.version(before) == Home.version(after)
        except FileNotFoundError:
            return False

    def current(self):
        out = command("systemctl", "--user", "show", self.name, "--property=Id,LoadState,ActiveState,UnitFileState,FragmentPath,DropInPaths", "--no-pager")
        props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if props.get("Id") != self.name or props.get("DropInPaths") != "":
            raise Refused("Dedicated indexer unavailable or has custom overrides")
        unit, active = props.get("UnitFileState"), props.get("ActiveState")
        fragment = props.get("FragmentPath")
        # Masked units are not loaded: require the entire stock/mask tuple,
        # rather than accepting a custom fragment or contradictory properties.
        if (unit not in {"static", "masked"} or active not in {"active", "inactive"}
                or props.get("LoadState") != ("masked" if unit == "masked" else "loaded")
                or not (self.masked_fragment(fragment) if unit == "masked"
                        else fragment == "/usr/lib/systemd/user/" + self.name)):
            raise Refused("Unsupported indexer state/definition (only stock static units)")
        value = {"unit": unit, "active": active}
        self.validate(value)
        return value

    def validate(self, value):
        if not isinstance(value, dict) or set(value) != {"unit", "active"} or value["unit"] not in {"static", "masked"} or value["active"] not in {"active", "inactive"} or value["unit"] == "masked" and value["active"] != "inactive":
            raise Refused("Malformed indexer ownership record")

    def target(self, original):
        self.validate(original)
        return {"unit": "masked", "active": "inactive"}

    @staticmethod
    def equal(a, b):
        return a == b

    def unmasked_parent(self):
        # Absence plus the directory version detects replacement/re-mask edits;
        # a stock systemd tuple alone is not ownership of a completed unmask.
        with self.home.parent(self.mask) as (fd, leaf):
            before = Home.version(os.fstat(fd))
            try:
                os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise Refused("Indexer mask path appeared after unmask")
            if Home.version(os.fstat(fd)) != before:
                raise Refused("Indexer mask directory changed during readback")
            return list(before)

    def put(self, value, expected, checkpoint=None):
        if self.current() != expected:
            raise Refused("Indexer changed before write")
        if value["unit"] == "masked":
            command("systemctl", "--user", "mask", "--now", self.name)
        else:
            if expected["unit"] == "masked":
                command("systemctl", "--user", "unmask", self.name)
                if self.current() != {"unit": "static", "active": "inactive"}:
                    raise Refused("Indexer unmask readback failed")
            parent = self.unmasked_parent()
            if value["active"] == "active":
                if checkpoint is not None:
                    checkpoint({"id": self.id, "parent": parent})
                # Journal persistence is another interleaving boundary. Recheck
                # before start; this still is not atomic CAS against same-UID
                # writers changing the service/path after the final check.
                if self.unmasked_parent() != parent or self.current() != {"unit": "static", "active": "inactive"}:
                    raise Refused("Indexer changed after Restore checkpoint")
                command("systemctl", "--user", "start", self.name)
        if self.current() != value:
            raise Refused("Indexer command returned success but readback failed")


def resources(home, feature):
    manager()
    if feature == "indexing":
        return [IndexUnit(home, n) for n in UNITS]
    result = [Setting(feature)]
    if feature == "recent":
        result += [Gtk(home, n) for n in (3, 4)]
    return result


def records(journal, items):
    data = journal.data
    if (not isinstance(data, dict) or set(data) not in ({"context", "version", "phase", "items"},
                                                      {"context", "version", "phase", "items", "resume"})
            or type(data.get("version")) is not int or data["version"] != 1
            or data.get("phase") not in {"prepared", "changing", "applied", "failed"}
            or not isinstance(data.get("items"), list) or len(data["items"]) != len(items)):
        raise Refused("Malformed privacy ownership journal")
    for item, saved in zip(items, data["items"]):
        if not isinstance(saved, dict) or set(saved) != {"id", "original", "target"} or saved["id"] != item.id:
            raise Refused("Privacy ownership record does not match fixed targets")
        item.validate(saved["original"])
        if saved["target"] != item.target(saved["original"]):
            raise Refused("Privacy ownership target was modified")
    if "resume" in data:
        resume = data["resume"]
        if (data["phase"] not in {"changing", "failed"} or not isinstance(resume, dict)
                or set(resume) != {"id", "parent"}):
            raise Refused("Malformed indexer Restore checkpoint")
        owners = [(i, s) for i, s in zip(items, data["items"]) if i.id == resume["id"]]
        parent = resume["parent"]
        if (len(owners) != 1 or not isinstance(owners[0][0], IndexUnit)
                or owners[0][1]["original"] != {"unit": "static", "active": "active"}
                or not isinstance(parent, list) or len(parent) != 8
                or any(type(v) is not int or v < 0 for v in parent)
                or not stat.S_ISDIR(parent[5]) or parent[5] & 0o022
                or parent[6] != os.getuid() or parent[1] == 0 or parent[7] == 0):
            raise Refused("Invalid indexer Restore checkpoint ownership")
    return data["items"]


def owned(item, saved, now, journal, action):
    resume = journal.data.get("resume")
    if resume is not None and resume["id"] == item.id:
        # Only a durable, verified Restore unmask owns static/inactive. Never
        # infer it from phase=failed (which can also describe a failed Stop).
        if (action != "restore" or item.unmasked_parent() != resume["parent"]
                or now not in (saved["original"], {"unit": "static", "active": "inactive"})):
            return False
        return True
    if isinstance(item, IndexUnit) and now["unit"] == "static":
        try:
            item.unmasked_parent()
        except FileNotFoundError:
            # An untouched stock original may have no user-unit directory.
            pass
    return item.equal(now, saved["original"]) or item.equal(now, saved["target"])


def _change(home, feature, action):
    items = resources(home, feature)
    journal = Journal(home, "privacy/" + feature)
    if journal.data is None:
        if action == "restore":
            print("Not managed; nothing to restore")
            return
        saved = [{"id": i.id, "original": i.current()} for i in items]
        for i, s in zip(items, saved):
            s["target"] = i.target(s["original"])
        journal.save({"version": 1, "phase": "prepared", "items": saved})
    saved = records(journal, items)
    # All targets preflight before any write. A failed/partial transaction is
    # recoverable only to its originals, never silently re-applied.
    if action == "stop" and journal.data["phase"] not in {"prepared", "applied"}:
        raise Refused("Previous change failed; use Restore before retrying")
    if action == "stop" and journal.data["phase"] == "applied":
        if all(i.equal(i.current(), s["target"]) for i, s in zip(items, saved)):
            print("Already stopped (owned settings verified)")
            return
        raise Refused("Managed settings were edited; left unchanged")
    for i, s in zip(items, saved):
        now = i.current()
        if not owned(i, s, now, journal, action):
            raise Refused("Managed target has newer/conflicting changes; left unchanged")
    journal.save(dict(journal.data, phase="changing"))
    try:
        for i, s in zip(items, saved):
            now = i.current()
            dest = s["target"] if action == "stop" else s["original"]
            if not owned(i, s, now, journal, action):
                raise Refused("Target changed during apply; manual review needed")
            if not i.equal(now, dest):
                if action == "restore" and isinstance(i, IndexUnit):
                    def checkpoint(receipt):
                        data = journal.data
                        if not isinstance(data, dict):
                            raise Refused("Indexer Restore journal disappeared")
                        previous = data.get("resume")
                        if previous is not None and previous != receipt:
                            raise Refused("Indexer Restore checkpoint changed before start")
                        journal.save(dict(data, resume=receipt))
                    i.put(dest, now, checkpoint)
                else:
                    i.put(dest, now)
            data = journal.data
            if not isinstance(data, dict):
                raise Refused("Privacy ownership journal disappeared")
            if data.get("resume", {}).get("id") == i.id:
                data = dict(data)
                del data["resume"]
                journal.save(data)
        if action == "restore":
            for i, s in zip(items, saved):
                now = i.current()
                if not i.equal(now, s["original"]) or not owned(i, s, now, journal, action):
                    raise Refused("Original configuration changed during Restore readback")
            journal.save(None)
            print("Original configuration and user-service activity restored")
        else:
            journal.save(dict(journal.data, phase="applied"))
            print("Stopped; settings/readback verified. Existing history is NOT deleted.")
            if feature == "recent":
                print("Restart GTK applications yourself; independent app history is outside this scope.")
            if feature == "indexing":
                print("Only LocalSearch indexer + indexing proxy masked/stopped. Existing index retained; search may degrade.")
    except Exception:
        journal.save(dict(journal.data, phase="failed"))
        raise


def change(home, feature, action):
    with home.lock("privacy"):
        return _change(home, feature, action)


def status(home, feature):
    items = resources(home, feature)
    journal = Journal(home, "privacy/" + feature)
    saved = records(journal, items) if journal.data else None
    values = [i.current() for i in items]
    if saved:
        state = "managed/stopped" if journal.data["phase"] == "applied" and all(i.equal(v, s["target"]) for i, v, s in zip(items, values, saved)) else "conflict or incomplete; restore/review required"
    elif feature == "recent":
        enabled = values[0]["effective"] == "true"
        gtk_off = all(gtk_text(decode(v))[1].get("Settings", "gtk-recent-files-enabled", fallback="true").lower() in {"false", "0"} for v in values[1:])
        state = "not managed; GNOME recording " + ("on" if enabled else "off") + "; GTK " + ("off" if gtk_off else "default/custom")
    elif feature == "thumbnails":
        state = "not managed; Nautilus thumbnails " + values[0]["effective"]
    else:
        state = "not managed; " + ", ".join(f"{i.name}: {v['unit']}/{v['active']}" for i, v in zip(items, values))
    print(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feature", choices=("recent", "thumbnails", "indexing"))
    parser.add_argument("action", choices=("status", "stop", "restore", "purge"))
    parser.add_argument("--confirm", choices=("CHANGE", "PURGE"))
    args = parser.parse_args()
    home = None
    try:
        if os.getuid() == 0:
            raise Refused("Run as your normal user, not root")
        if args.action != "status" and args.confirm != ("PURGE" if args.action == "purge" else "CHANGE"):
            raise Refused("Explicit action confirmation required")
        home = Home()
        if args.action == "purge":
            if args.feature == "indexing":
                raise Refused("Index database deletion is unsupported")
            manager()
            with home.lock("privacy"):
                home.purge(home.rel("data", "recently-used.xbel") if args.feature == "recent" else home.rel("cache", "thumbnails"), directory=args.feature == "thumbnails")
            print("One-shot purge complete/absent. No backup, not secure erasure; recording settings unchanged.")
        elif args.action == "status":
            status(home, args.feature)
        else:
            change(home, args.feature, args.action)
        return 0
    except (Refused, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print("Unavailable/refused/failed: " + (str(exc) if isinstance(exc, Refused) else type(exc).__name__) + ". Partial changes may remain; use Restore/review before retrying.", file=sys.stderr)
        return 1
    finally:
        if home:
            home.close()


if __name__ == "__main__":
    sys.exit(main())
