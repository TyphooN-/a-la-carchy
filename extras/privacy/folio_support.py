"""Effect-free admission for the maintained native Folio privacy package.

A desktop ID/version is not a capability. Trust only a non-user-writable,
root-owned manifest bound to the actual system ELF and selected desktop file.
Never execute an unknown Folio flag, read XBEL, or mutate desktop settings.
"""
from contextlib import ExitStack
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from safeio import Refused

POLICY = "org.gnome.desktop.privacy/remember-recent-files"
REFUSAL = ("Folio native Recents capability is missing/invalid; an unverified build does not hide "
           "its XBEL list. No settings were changed by this preflight; previously owned "
           "GNOME/GTK settings can be restored")


def _stamp(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_mode, value.st_uid, value.st_nlink)


def _directory(value):
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != 0 or value.st_mode & 0o022:
        raise ValueError("Untrusted package directory")
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate capability field")
        result[key] = value
    return result


def _selected_desktop(expected):
    data = os.environ.get("XDG_DATA_HOME") or str(Path(os.environ["HOME"]) / ".local/share")
    bases = [data, *(os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")]
    for base in bases:
        if not base:
            continue
        if not Path(base).is_absolute():
            raise ValueError("Relative desktop search path")
        candidate = Path(base) / "applications/folio.desktop"
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            continue
        if candidate != expected:
            # User/local overrides can launch an entirely different program
            # under the same desktop ID. Do not inspect or execute that file.
            raise ValueError("Overridden Folio launcher")
        return
    raise ValueError("System Folio launcher is not selected")


def require_capability(root):
    """Validate a fixed package tree; root is fixed to /usr by the controller.

    Hashing the ELF is streaming and bounded to 64 MiB, not an O(1) claim.
    Held descriptor and pathname checks detect observed replacement/drift;
    they are not atomic CAS against a concurrent privileged package upgrade.
    """
    try:
        root = Path(root)
        _selected_desktop(root / "share/applications/folio.desktop")
        with ExitStack() as stack:
            checks = []
            rootfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
            stack.callback(os.close, rootfd)
            root_stamp = _directory(os.fstat(rootfd))

            def read(parts, limit, executable=False):
                parent = rootfd
                for name in parts[:-1]:
                    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                                 dir_fd=parent)
                    stack.callback(os.close, fd)
                    identity = _directory(os.fstat(fd))
                    checks.append((parent, name, fd, identity, True))
                    parent = fd
                name = parts[-1]
                fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
                stack.callback(os.close, fd)
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_mode & 0o022
                        or before.st_nlink != 1 or not 0 < before.st_size <= limit
                        or executable and not before.st_mode & 0o111):
                    raise ValueError("Untrusted package file")
                identity = _stamp(before)
                checks.append((parent, name, fd, identity, False))
                digest, data, first, total = hashlib.sha256(), bytearray(), b"", 0
                while True:
                    if _stamp(os.fstat(fd)) != identity:
                        raise ValueError("Package file changed before read")
                    chunk = os.read(fd, min(65536, before.st_size - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > before.st_size:
                        raise ValueError("Package file grew")
                    digest.update(chunk)
                    if executable:
                        first = (first + chunk[:4])[:4]
                    else:
                        data.extend(chunk)
                if total != before.st_size or _stamp(os.fstat(fd)) != identity:
                    raise ValueError("Incomplete or changed package file")
                if executable and first != b"\x7fELF":
                    raise ValueError("Folio is not the packaged ELF")
                return bytes(data), digest.hexdigest()

            raw, _ = read(("share", "folio", "alacarchy-privacy.json"), 8192)
            metadata = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
            if (not isinstance(metadata, dict)
                    or set(metadata) != {"version", "policy", "binary_sha256", "desktop_sha256"}
                    or type(metadata["version"]) is not int or metadata["version"] != 1
                    or metadata["policy"] != POLICY
                    or any(not isinstance(metadata[key], str)
                           or not re.fullmatch(r"[0-9a-f]{64}", metadata[key])
                           for key in ("binary_sha256", "desktop_sha256"))):
                raise ValueError("Unknown capability contract")
            desktop, desktop_hash = read(("share", "applications", "folio.desktop"), 65536)
            _, binary_hash = read(("bin", "folio"), 64 * 1024 * 1024, executable=True)
            if desktop_hash != metadata["desktop_sha256"] or binary_hash != metadata["binary_sha256"]:
                raise ValueError("Stale or mismatched capability")
            parser = configparser.ConfigParser(interpolation=None, strict=True)
            parser.optionxform = lambda optionstr: optionstr
            parser.read_string(desktop.decode("utf-8"))
            if (parser.defaults() or parser.get("Desktop Entry", "Type") != "Application"
                    or parser.get("Desktop Entry", "Exec") != "/usr/bin/folio %U"
                    or parser.get("Desktop Entry", "DBusActivatable", fallback="false") != "false"):
                raise ValueError("Unsupported launcher routing")
            for parent, name, fd, identity, directory in checks:
                check = _directory if directory else _stamp
                if (check(os.fstat(fd)) != identity
                        or check(os.stat(name, dir_fd=parent, follow_symlinks=False)) != identity):
                    raise ValueError("Package pathname changed")
            if (_directory(os.fstat(rootfd)) != root_stamp
                    or _directory(os.stat(root, follow_symlinks=False)) != root_stamp):
                raise ValueError("Package root changed")
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, configparser.Error) as exc:
        raise Refused(REFUSAL) from exc
