#!/usr/bin/env python3
"""Opt-in narrow sysctl controls. Privilege is requested only after confirmation."""
import argparse
import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "privacy"))
from safeio import Home, Journal, Refused

CONFIG_DIR = Path("/etc/sysctl.d")
CONFIG_SEARCH = [Path(p) for p in ("/usr/lib/sysctl.d", "/usr/local/lib/sysctl.d", "/run/sysctl.d", "/etc/sysctl.d")]
ROOT_OWNER = 0
# key, supported reversible target, supported runtime values, explanation
CONTROLS = {
    "kptr": ("kernel.kptr_restrict", "2", {"0", "1", "2"}, "Zero kernel pointers printed with %pK regardless of privilege; not every address disclosure. Costs: kernel debugging/profiling."),
    "dmesg": ("kernel.dmesg_restrict", "1", {"0", "1"}, "Restrict kernel log access to CAP_SYSLOG. Costs: unprivileged troubleshooting."),
    "ptrace": ("kernel.yama.ptrace_scope", "1", {"0", "1", "2", "3"}, "Yama restricted ptrace: normally descendants/declared tracers only. Costs: attaching debuggers; requires CONFIG_SECURITY_YAMA. Values 2/3 are never lowered."),
    "bpf": ("kernel.unprivileged_bpf_disabled", "2", {"0", "1", "2"}, "Disable unprivileged BPF reversibly (2). Costs: unprivileged BPF tooling. Value 1 is irreversible until reboot and is NEVER written or lowered."),
}


def command(*args, stdin=None):
    try:
        p = subprocess.run(args, stdin=stdin, text=True, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refused(f"Command unavailable/timed out: {args[0]}") from exc
    if p.returncode:
        raise Refused(f"Command failed: {args[0]} (exit {p.returncode}); partial changes may remain")
    return p.stdout.strip()


def config_path(name):
    return CONFIG_DIR / f"99-a-la-carchy-{name}.conf"


def render(name):
    key, target, _, _ = CONTROLS[name]
    return "# >>> a-la-carchy OS Hardening " + name + "\n" + key + " = " + target + "\n# <<< a-la-carchy OS Hardening " + name + "\n"


def trusted_dir(path):
    p = Path("/")
    for part in path.parts[1:]:
        p /= part
        s = p.lstat()
        # Test fixtures override ROOT_OWNER only in the imported module.
        if not stat.S_ISDIR(s.st_mode) or s.st_mode & 0o022 or s.st_uid not in {0, ROOT_OWNER}:
            raise Refused("Untrusted root configuration directory")


def read_config(name):
    trusted_dir(CONFIG_DIR)
    p = config_path(name)
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != ROOT_OWNER or s.st_nlink != 1 or s.st_mode & 0o022 or s.st_size > 4096:
            raise Refused("Untrusted managed sysctl file")
        with os.fdopen(os.dup(fd), "r") as f:
            return f.read(4097)
    finally:
        os.close(fd)


def conflicts(name):
    key = CONTROLS[name][0]
    # Honor same-name directory precedence, but conservatively refuse ANY
    # other assignment/glob that could compete with this one key.
    effective = {}
    for directory in CONFIG_SEARCH:
        if not directory.exists():
            continue
        trusted_dir(directory)
        for p in directory.iterdir():
            if p.name.endswith(".conf"):
                effective[p.name] = p
    for p in effective.values():
        if p == config_path(name):
            continue
        if p.is_symlink():
            if str(p.resolve()) == "/dev/null":
                continue
            raise Refused("Other sysctl configuration is symlinked; manual persistence review required")
        st = p.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != ROOT_OWNER or st.st_mode & 0o022 or st.st_size > 1024 * 1024:
            raise Refused("Other sysctl configuration cannot be safely inspected")
        for line in p.read_text().splitlines():
            text = line.strip()
            if not text or text[0] in "#;" or "=" not in text:
                continue
            candidate = text.split("=", 1)[0].strip().lstrip("-").replace("/", ".")
            # Wildcard exclusion syntax also counts as an unresolved conflict.
            if candidate == key or any(c in candidate for c in "*?["):
                raise Refused("Competing sysctl assignment/glob; persistence refused")


def value(name):
    key, _, allowed, _ = CONTROLS[name]
    result = command("sysctl", "-n", key)
    if result not in allowed:
        raise Refused("Unsupported/missing sysctl or kernel value")
    return result


def already_strict(name, current):
    target = CONTROLS[name][1]
    return current == target or name == "ptrace" and current in {"2", "3"} or name == "bpf" and current == "1"


def root_write(home, name):
    # No mutable user paths/scripts cross the privilege boundary. stdin is a
    # kernel-sealed regular file; sudo uses the controlling tty, not -S.
    payload = render(name).encode()
    fd = None
    try:
        try:
            fd = os.memfd_create("a-la-carchy-sysctl", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
            if os.write(fd, payload) != len(payload):
                raise Refused("Incomplete persistence input")
            seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
            # Check AFTER sealing, including any edits during construction.
            if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & seals != seals or os.fstat(fd).st_size != len(payload) or os.pread(fd, len(payload) + 1, 0) != payload:
                raise Refused("Persistence input changed or is not sealed")
            os.lseek(fd, 0, os.SEEK_SET)
        except (AttributeError, OSError) as exc:
            raise Refused("Kernel-sealed persistence input unavailable; no privilege requested") from exc
        command("sudo", "--", "/usr/bin/install", "--mode=0644", "--owner=root", "--group=root", "-T", "--", "/proc/self/fd/0", str(config_path(name)), stdin=fd)
        if read_config(name) != render(name):
            raise Refused("Persistence install readback failed")
    finally:
        if fd is not None:
            os.close(fd)


def _change(home, name, action):
    original_now = value(name)
    journal = Journal(home, "hardening/" + name)
    file_now = read_config(name)
    conflicts(name)
    if journal.data is None:
        if action == "restore":
            if file_now is not None:
                raise Refused("No ownership journal for existing managed-path file; not removed")
            print("Not managed; nothing to restore")
            return
        if file_now is not None:
            raise Refused("Managed-path collision; existing sysctl file left unchanged")
        if already_strict(name, original_now):
            print("Already at least as restrictive; not taken over, persistence unchanged")
            return
        journal.save({"version": 1, "name": name, "original": original_now, "phase": "prepared"})
    saved = journal.data
    if not isinstance(saved, dict) or saved.get("version") != 1 or saved.get("name") != name or saved.get("original") not in CONTROLS[name][2] or already_strict(name, saved["original"]):
        raise Refused("Malformed/unsafe hardening ownership record")
    original, target = saved["original"], CONTROLS[name][1]
    if file_now not in (None, render(name)):
        raise Refused("Managed sysctl configuration has newer edits; left unchanged")
    if saved["phase"] == "applied":
        if original_now != target or file_now != render(name):
            raise Refused("Managed runtime/persistence has newer edits; left unchanged")
        if action == "enable":
            print("Already enabled; exact runtime and persistence verified")
            return
    elif original_now not in {original, target}:
        raise Refused("Runtime changed during incomplete apply; manual review required")
    elif action == "enable" and saved["phase"] != "prepared":
        raise Refused("Previous apply incomplete; Restore before retrying")
    journal.save(dict(saved, phase="changing"))
    try:
        if action == "enable":
            if read_config(name) is not None:
                raise Refused("Persistence changed before install")
            root_write(home, name)
            if value(name) != original:
                raise Refused("Runtime changed before write")
            command("sudo", "--", "/usr/bin/sysctl", "-w", CONTROLS[name][0] + "=" + target)
            if value(name) != target or read_config(name) != render(name):
                raise Refused("Runtime/persistence readback failed")
            journal.save(dict(journal.data, phase="applied"))
            print("Enabled; exact sysctl and dedicated persistence file verified")
        else:
            if value(name) not in {original, target}:
                raise Refused("Runtime changed before restoration")
            if read_config(name) == render(name):
                command("sudo", "--", "/usr/bin/rm", "--", str(config_path(name)))
            elif read_config(name) is not None:
                raise Refused("Persistence changed before restoration")
            if read_config(name) is not None:
                raise Refused("Persistence removal readback failed")
            if value(name) != original:
                command("sudo", "--", "/usr/bin/sysctl", "-w", CONTROLS[name][0] + "=" + original)
            if value(name) != original:
                raise Refused("Runtime restoration readback failed")
            journal.save(None)
            print("Original runtime restored; only our dedicated persistence file removed")
    except Exception:
        journal.save(dict(journal.data, phase="failed"))
        raise


def change(home, name, action):
    with home.lock("hardening"):
        return _change(home, name, action)


def audit(home):
    print("Bounded audit only; NOT a security certification. No privilege or writes.")
    for name, (key, target, _, description) in CONTROLS.items():
        try:
            current = value(name)
            journal = Journal(home, "hardening/" + name)
            file = read_config(name)
            state = "unmanaged"
            if journal.data:
                state = "managed/readback matches" if journal.data.get("phase") == "applied" and current == target and file == render(name) else "incomplete/conflict"
            try:
                conflicts(name)
                persistence = "no competing assignments detected"
            except Refused:
                persistence = "persistence requires manual review"
            print(f"{key}: runtime={current}; {state}; {persistence}")
        except (Refused, OSError, ValueError):
            print(f"{key}: unavailable/unknown")
        print("  " + description)
    print("No audit/authentication/sandbox/firewall services or user namespaces are changed.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("name", choices=("audit", *CONTROLS))
    p.add_argument("action", choices=("status", "enable", "restore"), default="status", nargs="?")
    p.add_argument("--confirm", choices=("CHANGE",))
    args = p.parse_args()
    home = None
    try:
        if os.getuid() == 0:
            raise Refused("Run as your normal user, not root")
        if args.action != "status" and args.confirm != "CHANGE":
            raise Refused("Explicit per-control confirmation required before privilege")
        if args.name == "audit" and args.action != "status":
            raise Refused("Audit is read-only")
        home = Home()
        if args.action == "status":
            audit(home)
        else:
            change(home, args.name, args.action)
        return 0
    except (Refused, OSError, ValueError, KeyError, TypeError) as exc:
        print("Unavailable/refused/failed: " + (str(exc) if isinstance(exc, Refused) else type(exc).__name__) + ". Restore/review any partial change before retrying.", file=sys.stderr)
        return 1
    finally:
        if home:
            home.close()


if __name__ == "__main__":
    sys.exit(main())
