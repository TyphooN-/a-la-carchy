"""Descriptor-relative, no-follow user file operations for bounded controls."""
import base64
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat


class Refused(RuntimeError):
    pass


def lexical(value):
    p = Path(value)
    if not p.is_absolute() or ".." in p.parts or "\x00" in value or "\n" in value:
        raise Refused("Unsafe or non-absolute path")
    return p


class Home:
    def __init__(self):
        self.path = lexical(os.environ.get("HOME", ""))
        if self.path == Path("/"):
            raise Refused("HOME cannot be the filesystem root")
        # Reject symlinks even in HOME's ancestors, without following them.
        p = Path("/")
        for part in self.path.parts[1:]:
            p /= part
            s = p.lstat()
            if not stat.S_ISDIR(s.st_mode) or s.st_mode & 0o022:
                raise Refused("Unsafe HOME ancestor")
        s = self.path.lstat()
        if s.st_uid != os.getuid():
            raise Refused("HOME is not owned by the current user")
        self.roots = {}
        for name, default in (("config", ".config"), ("data", ".local/share"),
                              ("cache", ".cache"), ("state", ".local/state")):
            p = lexical(os.environ.get(f"XDG_{name.upper()}_HOME", str(self.path / default)))
            if not p.is_relative_to(self.path) or p == self.path:
                raise Refused("XDG roots must be dedicated directories inside HOME")
            self.roots[name] = str(p.relative_to(self.path))
        self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def close(self):
        os.close(self.fd)

    def rel(self, root, suffix):
        return self.roots[root] + "/" + suffix

    @contextlib.contextmanager
    def lock(self, name):
        rel = self.rel("state", "a-la-carchy/" + name + "/operation.lock")
        with self.parent(rel, create=True) as (fd, leaf):
            f = os.open(leaf, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
            try:
                self.regular(os.fstat(f))
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Refused("Another configurator operation owns this control group") from exc
                yield
            finally:
                os.close(f)

    @contextlib.contextmanager
    def parent(self, rel, create=False):
        if Path(rel).is_absolute() or any(v in ("..", ".") for v in rel.split("/")):
            raise Refused("Unsafe relative target")
        parts = rel.split("/")
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                try:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                s = os.fstat(nxt)
                if s.st_uid != os.getuid() or s.st_mode & 0o022:
                    os.close(nxt)
                    raise Refused("Untrusted user directory")
                os.close(fd)
                fd = nxt
            yield fd, parts[-1]
        finally:
            os.close(fd)

    @staticmethod
    def regular(s):
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_nlink != 1 or s.st_mode & 0o022:
            raise Refused("Target is not a private, owned, single-link regular file")

    def read(self, rel):
        try:
            with self.parent(rel) as (fd, name):
                f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    s = os.fstat(f)
                    self.regular(s)
                    if s.st_size > 1024 * 1024:
                        raise Refused("Configuration exceeds the supported size")
                    with os.fdopen(os.dup(f), "rb") as stream:
                        return stream.read(1024 * 1024 + 1)
                finally:
                    os.close(f)
        except FileNotFoundError:
            return None

    @staticmethod
    def version(s):
        return (s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns,
                s.st_size, s.st_mode, s.st_uid, s.st_nlink)

    def write(self, rel, data, expected):
        # Pin the inode BEFORE validating bytes, and retain that descriptor
        # through the last check. This is conflict detection, not universal
        # POSIX CAS: noncooperating writers must be closed during mutation.
        try:
            with self.parent(rel, create=data is not None) as (fd, name):
                source = None
                tmp = None
                try:
                    try:
                        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    except FileNotFoundError:
                        pass
                    before = os.fstat(source) if source is not None else None
                    if before is not None:
                        self.regular(before)
                        if before.st_size > 1024 * 1024:
                            raise Refused("Configuration exceeds the supported size")
                    elif expected is not None:
                        raise Refused("Configuration disappeared; refusing to replace it")

                    def checked():
                        if source is None:
                            try:
                                os.stat(name, dir_fd=fd, follow_symlinks=False)
                            except FileNotFoundError:
                                return
                            raise Refused("Configuration appeared; refusing to overwrite it")
                        if self.version(os.fstat(source)) != self.version(before) or os.pread(source, 1024 * 1024 + 1, 0) != expected or self.version(os.fstat(source)) != self.version(before):
                            raise Refused("Configuration changed; refusing to overwrite it")
                        try:
                            now = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        except FileNotFoundError as exc:
                            raise Refused("Configuration disappeared during write") from exc
                        if self.version(now) != self.version(before):
                            raise Refused("Target changed during configuration write")

                    checked()
                    if data is None:
                        if source is not None:
                            checked()
                            os.unlink(name, dir_fd=fd)
                        return
                    mode = stat.S_IMODE(before.st_mode) if before is not None else 0o600
                    tmp = ".alacarchy-" + secrets.token_hex(12)
                    f = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd)
                    with os.fdopen(f, "wb") as out:
                        os.fchmod(out.fileno(), mode)  # only the new inode
                        out.write(data)
                        out.flush()
                        os.fsync(out.fileno())
                    checked()
                    if source is not None:
                        os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
                    else:
                        # Atomic no-clobber creation, even after the last check.
                        os.link(tmp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                finally:
                    if source is not None:
                        os.close(source)
                    if tmp is not None:
                        try:
                            os.unlink(tmp, dir_fd=fd)
                        except FileNotFoundError:
                            pass
        except FileNotFoundError as exc:
            if data is None and expected is None:
                return
            raise Refused("Configuration path disappeared during write") from exc

    def purge(self, rel, directory=False):
        """Preflight the complete tree without reading data; never follow links."""
        try:
            with self.parent(rel) as (fd, name):
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not directory:
                    self.regular(s)
                    os.unlink(name, dir_fd=fd)
                    return
                d = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    budget = [100000]
                    tree = self._inventory(d, budget)
                    self._delete(d, tree)
                    now = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if (s.st_dev, s.st_ino) != (now.st_dev, now.st_ino):
                        raise Refused("Cache root changed during purge")
                    os.rmdir(name, dir_fd=fd)
                finally:
                    os.close(d)
        except FileNotFoundError:
            return

    def _inventory(self, fd, budget):
        s = os.fstat(fd)
        if s.st_uid != os.getuid() or s.st_mode & 0o022:
            raise Refused("Untrusted cache directory")
        tree = []
        with os.scandir(fd) as entries:
            for e in entries:
                budget[0] -= 1
                if budget[0] < 0:
                    raise Refused("Cache exceeds supported entry count")
                s = e.stat(follow_symlinks=False)
                children = None
                if stat.S_ISDIR(s.st_mode):
                    sub = os.open(e.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        children = self._inventory(sub, budget)
                    finally:
                        os.close(sub)
                else:
                    self.regular(s)
                tree.append((e.name, s.st_dev, s.st_ino, children))
        return tree

    def _delete(self, fd, tree):
        for name, dev, ino, children in tree:
            now = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (dev, ino) != (now.st_dev, now.st_ino):
                raise Refused("Cache changed during purge")
            if children is None:
                self.regular(now)
                os.unlink(name, dir_fd=fd)
            else:
                sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if (dev, ino) != (os.fstat(sub).st_dev, os.fstat(sub).st_ino):
                        raise Refused("Cache changed during purge")
                    self._delete(sub, children)
                finally:
                    os.close(sub)
                os.rmdir(name, dir_fd=fd)


def encode(data):
    return None if data is None else base64.b64encode(data).decode("ascii")


def decode(data):
    return None if data is None else base64.b64decode(data, validate=True)


class Journal:
    def __init__(self, home, name):
        self.home = home
        self.path = home.rel("state", "a-la-carchy/" + name + ".json")
        self.raw = home.read(self.path)
        self.data = json.loads(self.raw) if self.raw is not None else None
        self.context = {"home": str(home.path), "roots": home.roots}
        if self.data is not None and (not isinstance(self.data, dict) or self.data.get("context") != self.context):
            raise Refused("State belongs to another HOME/XDG layout")

    def save(self, data):
        if data is not None:
            data = dict(data, context=self.context)
        raw = None if data is None else (json.dumps(data, indent=2) + "\n").encode()
        if raw is None and self.raw is None:
            return
        self.home.write(self.path, raw, self.raw)
        self.raw, self.data = raw, data
