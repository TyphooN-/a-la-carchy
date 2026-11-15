#!/usr/bin/env python3
"""Bind a staged package's ELF and absolute-path launcher; never run Folio."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def digest(path, limit, elf=False):
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or not 0 < value.st_size <= limit:
        raise ValueError("Unsupported staged package member")
    result = hashlib.sha256()
    with path.open("rb") as source:
        if elf and source.read(4) != b"\x7fELF":
            raise ValueError("Staged Folio is not ELF")
        source.seek(0)
        for chunk in iter(lambda: source.read(65536), b""):
            result.update(chunk)
    return result.hexdigest()


def main():
    root = Path(sys.argv[1]).resolve(strict=True)
    if root.name != "usr" or root == Path("/usr"):
        raise ValueError("Only a staged package usr root is supported")
    desktop = root / "share/applications/folio.desktop"
    lines = desktop.read_text().splitlines()
    if lines.count("Exec=/usr/bin/folio %U") != 1 or "DBusActivatable=true" in lines:
        raise ValueError("Staged launcher does not select the fixed system binary")
    metadata = {"version": 1, "policy": "org.gnome.desktop.privacy/remember-recent-files",
                "binary_sha256": digest(root / "bin/folio", 64 * 1024 * 1024, elf=True),
                "desktop_sha256": digest(desktop, 65536)}
    target = root / "share/folio/alacarchy-privacy.json"
    # The recipe creates the parent with its final package mode. Never replace
    # an existing capability or write to the live system package location.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        os.fchmod(out.fileno(), 0o644)
        json.dump(metadata, out, sort_keys=True)
        out.write("\n")


if __name__ == "__main__":
    main()
