"""Synthetic package-unit boundary for privacy tests; never host service proof."""
import json
import os
from pathlib import Path
from types import SimpleNamespace


def install_package_fixture(module):
    """Keep real IndexUnit admission, mocking only its fixed package sources.

    File bytes, symlink status and mode come from this case's private HOME.
    UID is explicitly synthetic: an unprivileged fixture cannot own root files.
    No production environment override or host package mutation is introduced.
    """
    real_path = module.Path

    class PackageSource:
        def __init__(self, name):
            if name not in module.UNITS:
                raise AssertionError("Unexpected package unit requested")
            self.source = Path(os.environ["HOME"]) / "package-units" / name

        def is_symlink(self):
            return self.source.is_symlink()

        def stat(self):
            current = self.source.stat()
            fixture = json.loads((Path(os.environ["HOME"]) / "mock.json").read_text())
            return SimpleNamespace(st_uid=fixture.get("package_unit_uid", 0), st_mode=current.st_mode)

        def read_text(self):
            return self.source.read_text()

    class PackageDirectory:
        def __truediv__(self, name):
            return PackageSource(name)

    def fixture_path(value):
        if str(value) == "/usr/lib/systemd/user":
            return PackageDirectory()
        return real_path(value)

    module.Path = fixture_path
