#!/usr/bin/env python3
"""Read-only linux-tkg inspection and build preview; never evaluate checkout code."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "privacy"))
from safeio import Refused, lexical

DEFAULT_CHECKOUT = "/home/git/linux-tkg"
HINTS = ("_version", "_cpusched", "_compiler", "_lto_mode", "_processor_opt", "_configfile",
         "_EXT_CONFIG_PATH", "_kernel_on_diet", "_modprobeddb", "_user_patches", "_default_commandline")


def safe_path(path, directory=False):
    p = lexical(str(path))
    ancestor = Path("/")
    for part in p.parts[1:]:
        ancestor /= part
        s = ancestor.lstat()
        if stat.S_ISLNK(s.st_mode):
            raise Refused("Symlinked checkout/source paths are refused")
        if s.st_uid not in {0, os.getuid()} or s.st_mode & 0o022:
            raise Refused("Untrusted checkout/source ownership")
    s = p.lstat()
    if directory:
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid():
            raise Refused("Checkout must be an owned directory")
    elif not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
        raise Refused("Source/package must be a single-link regular file")
    return p


def text_file(path, limit=1024 * 1024):
    p = safe_path(path)
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if os.fstat(fd).st_size > limit:
            raise Refused("Source metadata exceeds the supported size")
        with os.fdopen(os.dup(fd), "rb") as f:
            data = f.read(limit + 1)
        if len(data) > limit:
            raise Refused("Source metadata grew beyond the supported size")
        return data.decode("utf-8")
    finally:
        os.close(fd)


def command(*args):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refused("Read-only command unavailable/timed out") from exc
    if p.returncode:
        raise Refused("Read-only command failed (exit " + str(p.returncode) + ")")
    return p.stdout.strip()


def git_identity(path):
    """Bounded HEAD/ref hints only. Never start Git or interpret its config."""
    directory = path / ".git"
    try:
        head = text_file(directory / "HEAD", 4096).strip()
    except FileNotFoundError as exc:
        raise Refused("Checkout HEAD metadata is missing") from exc
    oid = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
    if re.fullmatch(oid, head):
        return head, "detached/unknown"
    if not head.startswith("ref: refs/heads/"):
        return "unknown/unsupported HEAD", "detached/unknown"
    ref = head.removeprefix("ref: ")
    branch = ref.removeprefix("refs/heads/")
    # A deliberately narrow refname subset, not Git's executable/configured
    # ref backend. Reject traversal and control characters before path access.
    if len(branch) > 1024 or ".." in branch or any(
        not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part)
        or part.endswith((".lock", ".")) for part in branch.split("/")
    ):
        return "unknown/unsupported HEAD", "detached/unknown"
    try:
        try:
            value = text_file(directory / ref, 4096).strip()
        except FileNotFoundError:
            value = None
        if value is not None:
            if not re.fullmatch(oid, value):
                raise Refused("Unsupported loose ref")
            return value, branch
        try:
            packed = text_file(directory / "packed-refs")
        except FileNotFoundError:
            return "unknown/unborn", branch
        matches = []
        for line in packed.splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            fields = line.split(" ")
            if len(fields) != 2 or not re.fullmatch(oid, fields[0]):
                raise Refused("Unsupported packed ref")
            if fields[1] == ref:
                matches.append(fields[0])
        if len(matches) > 1:
            raise Refused("Ambiguous packed ref")
        return (matches[0] if matches else "unknown/unborn"), branch
    except (Refused, OSError, UnicodeError):
        return "unknown/unsupported ref metadata", branch


def literal(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not re.fullmatch(r"[A-Za-z0-9_./~:+ -]*", value) or len(value) > 256:
        return "unknown/dynamic"
    return value


def source_hints(text):
    result = {name: "unknown/not specified" for name in HINTS}
    seen = set()
    for line in text.splitlines():
        m = re.match(r"^\s*(_[A-Za-z0-9_]+)\s*=(.*)$", line)
        if not m or m[1] not in result:
            continue
        if m[1] in seen:
            result[m[1]] = "unknown/multiple assignments"
        else:
            result[m[1]] = literal(m[2])
        seen.add(m[1])
    # Conditions/functions can make even literal assignments non-effective.
    return result


def inspect_checkout(requested):
    p = safe_path(lexical(requested), directory=True)
    safe_path(p / ".git", directory=True)  # linked worktrees intentionally unsupported
    root = str(p)  # exact owned metadata directory, not configured Git worktree semantics
    readme = text_file(p / "README.md")
    safe_path(p / "PKGBUILD")
    hints = source_hints(text_file(p / "customization.cfg"))
    head, branch = git_identity(p)
    dirty = "unknown/not computed: Git status can execute checkout-configured filters"
    env_overrides = [name for name in HINTS if name in os.environ]
    return {"requested_path": requested, "verified_root": root, "head": head, "branch": branch,
            "identity_scope": "bounded HEAD/refs data hints; objects, config, effective worktree and ref backend not verified",
            "tracked_dirty": dirty, "untracked": "not recursively inventoried; generated pkg/ may be unreadable",
            "hints": hints, "environment_override_names": env_overrides,
            "external_config": "_EXT_CONFIG_PATH (file or environment), otherwise ~/.config/frogminer/linux-tkg.cfg; not evaluated/read",
            "effective_config": "unknown: README precedence customization.cfg < external config < environment; shell logic, interactive choices, .myfrag files and patches may override hints",
            "arch_recipe_observed": "Arch & derivatives" in readme and "makepkg -si" in readme,
            "source": str(p / "README.md")}


def preview(info):
    if not info["arch_recipe_observed"]:
        raise Refused("Local README does not contain the supported Arch build recipe")
    return ("NOT EXECUTED — build-only preview derived from local README Arch recipe.\n"
            "Review PKGBUILD/patches/config and dependencies manually first; builds execute untrusted code and may download sources.\n"
            "cd -- " + shlex.quote(info["verified_root"]) + " && makepkg\n"
            "Upstream dependency sync and package installation flags deliberately omitted.\n"
            "No initramfs, signing, boot entry, fallback or default changes; no bootability claim.")


def package_metadata(path):
    p = safe_path(path)
    def deadline(*_):
        raise Refused("Local package metadata inspection timed out")
    previous = signal.signal(signal.SIGALRM, deadline)
    signal.alarm(10)
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream, tarfile.open(fileobj=stream, mode="r|*") as archive:
            for index, member in enumerate(archive):
                if index > 64:
                    raise Refused("Package metadata not in supported early archive entries")
                if member.name not in {".PKGINFO", "./.PKGINFO"}:
                    continue
                if not member.isfile() or member.size > 65536:
                    raise Refused("Unsafe/oversized package metadata member")
                f = archive.extractfile(member)
                if f is None:
                    raise Refused("Package metadata unavailable")
                text = f.read(65537).decode("utf-8")
                result = {}
                for line in text.splitlines():
                    if " = " not in line:
                        continue
                    key, v = line.split(" = ", 1)
                    if key in {"pkgname", "pkgver", "arch"}:
                        if key in result or not re.fullmatch(r"[A-Za-z0-9_.:+-]{1,256}", v):
                            raise Refused("Unsupported package metadata value")
                        result[key] = v
                if set(result) != {"pkgname", "pkgver", "arch"}:
                    raise Refused("Incomplete package metadata")
                return result
        raise Refused("No package metadata found")
    except tarfile.TarError as exc:
        raise Refused("Unsupported/corrupt local package archive") from exc
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def packages(info):
    result = []
    p = Path(info["verified_root"])
    for f in sorted(p.iterdir()):
        if not re.search(r"\.pkg\.tar\.(zst|xz|gz)$", f.name):
            continue
        if len(result) >= 32:
            result.append({"status": "more than 32 archives; remainder not inspected"}); break
        try:
            result.append({"file": f.name, "metadata": package_metadata(f), "status": "metadata only; payload/bootability unverified"})
        except (Refused, OSError, ValueError) as exc:
            result.append({"file": f.name, "status": "unavailable/refused: " + (str(exc) if isinstance(exc, Refused) else type(exc).__name__)})
    return {"scope": "top-level LOCAL package archives only; no extraction/scripts/install", "packages": result}


def system_status():
    result = {}
    for key, args in (("running_kernel", ("uname", "-r")), ("installed_packages", ("pacman", "-Q"))):
        try:
            out = command(*args)
            result[key] = out if key == "running_kernel" else [line for line in out.splitlines() if re.match(r"linux(?:[0-9]+|-lts|-zen|-hardened|-tkg)(?:[- ]|$)", line)]
        except Refused:
            result[key] = "unknown/unavailable"
    result["boot"] = "not verified; installed packages/running kernel do not prove next boot or fallback readiness"
    result["tradeoffs"] = ["BORE/scheduler, LTO and CPU tuning are performance choices, NOT hardening.",
                           "Disabling mitigations weakens protection; no boot command-line edits are offered.",
                           "Trimmed module lists/modprobed-db can omit storage, encryption or recovery drivers; preserve a known-good fallback.",
                           "Kernel config fragments and hardened patch compatibility depend on exact kernel release; source hints are not running CONFIG evidence."]
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("inspect", "packages", "preview"))
    p.add_argument("--checkout", default=os.environ.get("ALC_LINUX_TKG_PATH", DEFAULT_CHECKOUT))
    args = p.parse_args()
    try:
        info = inspect_checkout(args.checkout)
        if args.action == "inspect":
            print(json.dumps({"checkout": info, "system": system_status()}, indent=2, ensure_ascii=True))
        elif args.action == "packages":
            print(json.dumps(packages(info), indent=2, ensure_ascii=True))
        else:
            # JSON quoting also prevents terminal-control injection from path names.
            print(json.dumps({"preview": preview(info)}, indent=2, ensure_ascii=True))
        return 0
    except (Refused, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"requested_path": args.checkout, "status": "unavailable/refused", "reason": str(exc) if isinstance(exc, Refused) else type(exc).__name__, "system": system_status()}, indent=2, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
